from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

from .schema import SyntheticSchema, ScreenSchema, FieldSchema
from .providers import ProviderRegistry, default_registry
from .dataset_builder import DatasetBuilder, Dataset
from .template_engine import TemplateEngine
from .inferencer import SyntheticInferencer, InferenceResult
from .screen_registry import ScreenRegistry
from ..source_analyzer.entity_catalog import EntityDefinition
from ..source_analyzer.parser import SourceParser


def _merge_entity_meta(ent):
    import json as _j
    try:
        e=_j.loads(ent.metadata_json) if ent.metadata_json else {}
    except: e={}
    e["indexes"]=ent.indexes
    return _j.dumps(e,ensure_ascii=False)


@dataclass
class StressConfig:
    scenario: str = ""
    dataset_name: str = ""
    concurrency: int = 1
    ramp_up_seconds: int = 5
    duration_seconds: int = 0
    target_host: str = ""
    target_user: str = ""
    target_command: str = ""
    capture_dir: str = ""
    mode: str = "parallel-sessions"


class SyntheticEngine:
    """Motor principal de geracao sintetica e stress.

    Orquestra todo o fluxo:
    analyze → infer → register → generate → template → replay
    """

    def __init__(self, db_connection: Optional[sqlite3.Connection] = None):
        self.registry = default_registry()
        self.builder = DatasetBuilder(self.registry)
        self.inferencer = SyntheticInferencer()
        self.template_engine = TemplateEngine()
        self.screen_registry: Optional[ScreenRegistry] = None

        if db_connection:
            self.screen_registry = ScreenRegistry(db_connection)

    # ------------------------------------------------------------------
    # Analyze
    # ------------------------------------------------------------------

    def analyze_source(self, source_dir: str) -> InferenceResult:
        """Analisa codigo-fonte e infere schemas sinteticos."""
        return self.inferencer.analyze_source(source_dir)

    # ------------------------------------------------------------------
    # Register screens
    # ------------------------------------------------------------------

    def register_screens(self, result: InferenceResult) -> dict[str, int]:
        """Registra telas inferidas no banco (limpa e recria)."""
        if not self.screen_registry:
            raise RuntimeError("screen_registry nao configurado (db_connection necessaria)")

        con = self.screen_registry.con
        con.execute("DELETE FROM screen_fields")
        con.execute("DELETE FROM screens")
        con.commit()

        mapping: dict[str, int] = {}
        for screen_schema in result.screens:
            sig = screen_schema.screen_signature or screen_schema.title
            screen_id = self.screen_registry.register_screen(
                screen_signature=sig,
                title=screen_schema.title,
                program_name=screen_schema.program_name,
            )
            self.screen_registry.register_fields_from_schema(screen_id, screen_schema)
            mapping[screen_schema.title] = screen_id
        return mapping

    # ------------------------------------------------------------------
    # Generate dataset
    # ------------------------------------------------------------------

    def generate_dataset(
        self,
        screen_schema: ScreenSchema,
        quantity: int = 100,
        seed: int = 0,
        entity_name: str = "",
        lookup_values: Optional[dict[str, list[Any]]] = None,
    ) -> Dataset:
        """Gera dataset sintetico para um schema de tela."""
        synth_schema = SyntheticSchema(
            screen=screen_schema,
            entity_name=entity_name or screen_schema.title,
            quantity=quantity,
            seed=seed,
        )
        return self.builder.build(synth_schema, lookup_values)

    def generate_dataset_by_screen_id(
        self,
        screen_id: int,
        quantity: int = 100,
        seed: int = 0,
    ) -> Optional[Dataset]:
        """Gera dataset a partir de uma tela registrada no banco."""
        if not self.screen_registry:
            raise RuntimeError("screen_registry nao configurado")

        screen_schema = self.screen_registry.get_screen_schema(screen_id)
        if not screen_schema:
            return None

        screen = self.screen_registry.get_screen_by_id(screen_id)
        entity_name = screen.title if screen else ""

        return self.generate_dataset(screen_schema, quantity, seed, entity_name)

    # ------------------------------------------------------------------
    # Template
    # ------------------------------------------------------------------

    def create_templates(self, capture_inputs: list[str]) -> list[str]:
        """Analisa entradas capturadas e sugere templates."""
        return self.template_engine.detect_placeholders(capture_inputs)

    def render_templates(
        self,
        templates: list[str],
        dataset: Dataset,
    ) -> list[list[str]]:
        """Renderiza templates com dados do dataset para multiplas sessoes."""
        records = [r.data for r in dataset.records]
        return self.template_engine.render_batch(templates, records)

    # ------------------------------------------------------------------
    # Persist dataset
    # ------------------------------------------------------------------

    def save_dataset(self, dataset: Dataset) -> int:
        """Salva dataset no banco e retorna o ID."""
        if not self.screen_registry:
            raise RuntimeError("screen_registry nao configurado")

        now = datetime.now().isoformat()
        cur = self.screen_registry.con.execute(
            """INSERT INTO synthetic_datasets
               (name, screen_id, entity_name, quantity, seed, params_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                dataset.name,
                int(dataset.screen_id) if dataset.screen_id else 0,
                dataset.entity_name,
                dataset.quantity,
                dataset.seed,
                dataset.params_json,
                now,
            ),
        )
        dataset_id = cur.lastrowid or 0

        for rec in dataset.records:
            self.screen_registry.con.execute(
                """INSERT INTO synthetic_records
                   (dataset_id, record_index, data_json, created_at)
                   VALUES (?, ?, ?, ?)""",
                (
                    dataset_id,
                    rec.record_index,
                    json.dumps(rec.data, ensure_ascii=False),
                    now,
                ),
            )

        self.screen_registry.con.commit()
        return dataset_id

    def load_dataset(self, dataset_id: int) -> Optional[Dataset]:
        """Carrega dataset do banco pelo ID."""
        if not self.screen_registry:
            raise RuntimeError("screen_registry nao configurado")

        row = self.screen_registry.con.execute(
            "SELECT * FROM synthetic_datasets WHERE id=?", (dataset_id,)
        ).fetchone()
        if not row:
            return None

        records_rows = self.screen_registry.con.execute(
            "SELECT * FROM synthetic_records WHERE dataset_id=? ORDER BY record_index",
            (dataset_id,),
        ).fetchall()

        return Dataset(
            name=row["name"],
            screen_id=str(row["screen_id"]),
            entity_name=row["entity_name"],
            quantity=row["quantity"],
            seed=row["seed"],
            params_json=row["params_json"],
            created_at=row["created_at"],
            records=[
                DatasetRecord(
                    record_index=r["record_index"],
                    data=json.loads(r["data_json"]) if r["data_json"] else {},
                    created_at=r["created_at"],
                )
                for r in records_rows
            ],
        )

    # Importado do dataset_builder para uso
    from .dataset_builder import DatasetRecord  # noqa: F811

    # ------------------------------------------------------------------
    # Save source entities
    # ------------------------------------------------------------------

    def save_entities(self, entities: list[EntityDefinition]) -> None:
        """Salva entidades descobertas no banco — truncate + rebuild completo.

        Limpa source_entity_fields, source_entities e journeys CRUD antes
        de reinserir. Garante que o banco sempre reflita fielmente o estado
        atual do código-fonte, sem acúmulo de dados órfãos.
        """
        if not self.screen_registry:
            raise RuntimeError("screen_registry nao configurado")

        con = self.screen_registry.con

        # Limpeza completa antes de reinserir
        con.execute("DELETE FROM source_entity_fields")
        con.execute("DELETE FROM source_entities")
        con.execute("DELETE FROM entity_tests")

        now = datetime.now().isoformat()
        for ent in entities:
            cur = con.execute(
                """INSERT INTO source_entities (name, storage_type, source, metadata_json, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    ent.name,
                    ent.storage_type,
                    ent.source,
                    _merge_entity_meta(ent),
                    now,
                ),
            )
            entity_id = cur.lastrowid or 0

            for ef in ent.fields:
                constraints = ef.constraints_json or json.dumps(
                    {"required": ef.required, "unique": ef.unique_flag}, ensure_ascii=False
                )
                con.execute(
                    """INSERT INTO source_entity_fields
                       (entity_id, field_name, datatype, required, unique_flag, constraints_json)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (
                        entity_id,
                        ef.name,
                        ef.datatype,
                        1 if ef.required else 0,
                        1 if ef.unique_flag else 0,
                        constraints,
                    ),
                )

        con.commit()

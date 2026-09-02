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
from ..db.connection import batch_insert, transaction


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

    def save_dataset(self, dataset: Dataset, *, chunk_size: int = 500) -> int:
        """Salva dataset no banco e retorna o ID.

        Cabeçalho em transação própria (precisa do lastrowid) e registros em
        lote (transação + executemany em chunks curtos): a conexão roda em
        autocommit e o INSERT por registro fazia um fsync por linha.
        """
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

        batch_insert(
            self.screen_registry.con,
            """INSERT INTO synthetic_records
               (dataset_id, record_index, data_json, created_at)
               VALUES (?, ?, ?, ?)""",
            (
                (
                    dataset_id,
                    rec.record_index,
                    json.dumps(rec.data, ensure_ascii=False),
                    now,
                )
                for rec in dataset.records
            ),
            chunk_size=chunk_size,
        )
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

        # Rebuild atômico: antes a limpeza e cada INSERT comitavam sozinhos
        # (autocommit — o commit() final era no-op), e uma falha no meio
        # deixava as tabelas já limpas e parcialmente recriadas.
        now = datetime.now().isoformat()
        with transaction(con):
            # Limpeza completa antes de reinserir
            con.execute("DELETE FROM source_entity_fields")
            con.execute("DELETE FROM source_entities")
            con.execute("DELETE FROM entity_tests")

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

                con.executemany(
                    """INSERT INTO source_entity_fields
                       (entity_id, field_name, datatype, required, unique_flag, constraints_json)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    [
                        (
                            entity_id,
                            ef.name,
                            ef.datatype,
                            1 if ef.required else 0,
                            1 if ef.unique_flag else 0,
                            ef.constraints_json or json.dumps(
                                {"required": ef.required, "unique": ef.unique_flag},
                                ensure_ascii=False,
                            ),
                        )
                        for ef in ent.fields
                    ],
                )

    # ------------------------------------------------------------------
    # Screen-entity bindings (knowledge base persistida)
    # ------------------------------------------------------------------

    def save_bindings(self, bindings: list) -> None:
        """Persiste bindings tela→entidade — truncate + rebuild completo.

        Antes disto os bindings só existiam em memória (recalculados a cada
        uso): qualquer consumidor (synthesize, relatórios) re-parseava o
        código-fonte inteiro. Com a tabela gravada no analyze-source, os
        consumidores leem do banco (v0.8.13).
        """
        if not self.screen_registry:
            raise RuntimeError("screen_registry nao configurado")

        con = self.screen_registry.con
        now = datetime.now().isoformat()
        with transaction(con):
            con.execute("DELETE FROM screen_entity_bindings")
            # Lote único dentro da transação do rebuild (volumes de
            # analyze-source); chunking fica para os caminhos de massa
            # (datasets, amostras de benchmark).
            con.executemany(
                """INSERT INTO screen_entity_bindings
                   (screen_title, program_name, source_file,
                    source_line_start, source_line_end, entity_name, operation,
                    matched_fields_json, unmatched_fields_json, confidence,
                    evidence_json, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                [
                    (
                        b.screen_title, b.program_name, b.source_file,
                        b.source_lines[0], b.source_lines[1], b.entity_name,
                        b.operation,
                        json.dumps(b.matched_fields, ensure_ascii=False),
                        json.dumps(b.unmatched_screen_fields, ensure_ascii=False),
                        b.confidence,
                        json.dumps(b.evidence, ensure_ascii=False),
                        now,
                    )
                    for b in bindings
                ],
            )

    def load_entities(self) -> list[EntityDefinition]:
        """Carrega entidades + campos do banco (base do analyze-source)."""
        from ..source_analyzer.entity_catalog import FieldDefinition

        if not self.screen_registry:
            raise RuntimeError("screen_registry nao configurado")

        con = self.screen_registry.con
        entities: list[EntityDefinition] = []
        rows = con.execute(
            "SELECT id, name, storage_type, source, metadata_json "
            "FROM source_entities ORDER BY name").fetchall()
        field_rows = con.execute(
            "SELECT entity_id, field_name, datatype, required, unique_flag, "
            "constraints_json FROM source_entity_fields").fetchall()
        fields_by_entity: dict[int, list] = {}
        for fr in field_rows:
            fields_by_entity.setdefault(fr[0], []).append(FieldDefinition(
                name=fr[1], datatype=fr[2], required=bool(fr[3]),
                unique_flag=bool(fr[4]), constraints_json=fr[5]))
        for row in rows:
            entities.append(EntityDefinition(
                name=row[1], storage_type=row[2], source=row[3] or "",
                fields=fields_by_entity.get(row[0], []),
                metadata_json=row[4]))
        return entities

    def load_bindings(self) -> list:
        """Carrega bindings tela→entidade do banco (base do analyze-source)."""
        from ..source_analyzer.screen_entity_linker import ScreenEntityBinding

        if not self.screen_registry:
            raise RuntimeError("screen_registry nao configurado")

        con = self.screen_registry.con
        bindings = []
        for row in con.execute(
                "SELECT screen_title, program_name, source_file, "
                "source_line_start, source_line_end, entity_name, operation, "
                "matched_fields_json, unmatched_fields_json, confidence, "
                "evidence_json FROM screen_entity_bindings").fetchall():
            bindings.append(ScreenEntityBinding(
                screen_title=row[0], program_name=row[1], source_file=row[2],
                source_lines=(row[3], row[4]), entity_name=row[5],
                operation=row[6],
                matched_fields=json.loads(row[7] or "[]"),
                unmatched_screen_fields=json.loads(row[8] or "[]"),
                confidence=row[9], evidence=json.loads(row[10] or "[]")))
        return bindings

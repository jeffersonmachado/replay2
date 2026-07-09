from __future__ import annotations

import json
import random
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

from .journey import JourneyDefinition, JourneyStep, JourneyDataset
from .schema import ScreenSchema, FieldSchema, SyntheticSchema
from .dataset_builder import DatasetBuilder, Dataset
from .providers import ProviderRegistry, default_registry, DataProvider
from .template_engine import TemplateEngine
from .screen_registry import ScreenRegistry


class JourneyBuilder:
    """Constrói e executa jornadas: sequências de telas com dados sintéticos cross-screen."""

    def __init__(
        self,
        registry: Optional[ProviderRegistry] = None,
        db_connection: Optional[sqlite3.Connection] = None,
    ):
        self.registry = registry or default_registry()
        self.dataset_builder = DatasetBuilder(self.registry)
        self.template_engine = TemplateEngine()
        self.screen_registry: Optional[ScreenRegistry] = None
        if db_connection:
            db_connection.row_factory = sqlite3.Row
            self.screen_registry = ScreenRegistry(db_connection)
        # Cache: (journey_id, session_count, seed) → JourneyDataset
        self._dataset_cache: dict[tuple, JourneyDataset] = {}

    # ------------------------------------------------------------------
    # Construção de JourneyDataset
    # ------------------------------------------------------------------

    def build_journey_dataset(
        self,
        journey: JourneyDefinition,
        session_count: int = 10,
        seed: int = 0,
        lookup_values: Optional[dict[str, list[Any]]] = None,
        use_cache: bool = True,
    ) -> JourneyDataset:
        """Gera dados sintéticos para cada passo da jornada, mantendo consistência cross-screen.

        Exemplo: Se o passo 1 gera cliente_id=100, o passo 3 que referencia cliente_id
        deve usar o mesmo valor 100 na mesma sessão.
        """
        # Cache check
        cache_key = (journey.journey_id, session_count, seed)
        if use_cache and cache_key in self._dataset_cache:
            return self._dataset_cache[cache_key]

        rng = random.Random(seed)
        lookup_values = lookup_values or {}

        # Agrupar passos por screen_id
        screen_steps: dict[str, list[JourneyStep]] = {}
        for step in journey.steps:
            screen_steps.setdefault(step.screen_id, []).append(step)

        # Mapa: screen_id -> schema registrado
        screen_schemas: dict[str, ScreenSchema] = {}
        if self.screen_registry:
            for screen_id in screen_steps:
                try:
                    row = self.screen_registry.con.execute(
                        "SELECT id FROM screens WHERE screen_signature=? OR title=? OR program_name=? LIMIT 1",
                        (screen_id, screen_id, screen_id),
                    ).fetchone()
                except Exception:
                    row = None
                if row:
                    schema = self.screen_registry.get_screen_schema(row["id"])
                    if schema:
                        screen_schemas[screen_id] = schema

        # Gerar dados por screen (cross-screen consistency)
        screen_datasets: dict[str, list[dict[str, Any]]] = {}
        shared_state: dict[str, dict[int, Any]] = {}  # entity_field -> {session_index: value}

        for screen_id, steps in screen_steps.items():
            schema = screen_schemas.get(screen_id)
            if not schema or not schema.fields:
                # Schema genérico: gera dados textuais
                screen_datasets[screen_id] = [
                    {f"campo_{j}": f"dado_{screen_id}_{i}_{j}" for j in range(5)}
                    for i in range(session_count)
                ]
                continue

            # Gerar dataset para esta screen
            synth_schema = SyntheticSchema(
                screen=schema,
                entity_name=screen_id,
                quantity=session_count,
                seed=seed + hash(screen_id) % 10000,
            )
            dataset = self.dataset_builder.build(synth_schema, lookup_values)

            records = [r.data for r in dataset.records]

            # Aplicar shared state: se um campo depende de passo anterior, usa valor compartilhado
            for step in steps:
                for dep in step.depends_on:
                    # dep format: "step_order.field_name" ou "screen_id.field_name"
                    if "." in dep:
                        src, field = dep.split(".", 1)
                        if src in shared_state:
                            for sess_idx in range(min(session_count, len(records))):
                                if sess_idx in shared_state[src]:
                                    # Tentar encontrar campo correspondente na screen atual
                                    for key in list(records[sess_idx].keys()):
                                        if field.upper() in key.upper():
                                            records[sess_idx][key] = shared_state[src][sess_idx]

            screen_datasets[screen_id] = records

            # Publicar campos que podem ser referenciados por passos futuros
            for field_schema in schema.fields:
                state_key = f"{screen_id}.{field_schema.name}"
                shared_state[state_key] = {
                    i: records[i].get(field_schema.name, "")
                    for i in range(min(session_count, len(records)))
                }

        # Montar JourneyDataset
        steps_data: dict[int, list[dict[str, Any]]] = {}
        for step in journey.steps:
            screen_data = screen_datasets.get(step.screen_id, [{}] * session_count)
            # Renderizar input_template com os dados da screen
            rendered: list[dict[str, Any]] = []
            for sess_idx in range(session_count):
                data = screen_data[sess_idx] if sess_idx < len(screen_data) else {}
                if step.input_template:
                    # Usar template engine para substituir placeholders
                    rendered_input = self.template_engine.render(step.input_template, data)
                    rendered.append({"input": rendered_input, **data})
                else:
                    rendered.append(data)
            steps_data[step.step_order] = rendered

        jds = JourneyDataset(
            journey_id=journey.journey_id,
            journey_name=journey.name,
            seed=seed,
            session_count=session_count,
            steps_data=steps_data,
        )

        # Store in cache
        if use_cache:
            self._dataset_cache[cache_key] = jds
            # Limitar cache a 50 entradas
            if len(self._dataset_cache) > 50:
                oldest = next(iter(self._dataset_cache))
                del self._dataset_cache[oldest]

        return jds

    # ------------------------------------------------------------------
    # Execução de jornada (geração de script replay)
    # ------------------------------------------------------------------

    def generate_replay_script(
        self,
        journey: JourneyDefinition,
        journey_dataset: JourneyDataset,
        session_index: int = 0,
    ) -> str:
        """Gera um script de replay para uma sessão específica da jornada."""
        lines: list[str] = []
        lines.append(f"# Jornada: {journey.name}")
        lines.append(f"# Sessão: {session_index + 1}/{journey_dataset.session_count}")
        lines.append(f"# Seed: {journey_dataset.seed}")
        lines.append("")

        for step in sorted(journey.steps, key=lambda s: s.step_order):
            step_data = journey_dataset.steps_data.get(step.step_order, [])
            data = step_data[session_index] if session_index < len(step_data) else {}

            lines.append(f"# --- Passo {step.step_order}: {step.screen_title or step.screen_id} ---")
            lines.append(f"# Ação: {step.action} | Trigger: {step.trigger}")

            if step.action == "navigate":
                if step.trigger and step.trigger.isdigit():
                    lines.append(f"# Seleciona opção {step.trigger}")
                    lines.append(f"{step.trigger}")
                elif step.trigger:
                    lines.append(f"# Envia tecla: {step.trigger}")
                    lines.append(f"{{KEY:{step.trigger}}}")

            elif step.action == "input":
                if step.input_template:
                    rendered = self.template_engine.render(step.input_template, data)
                    for input_line in rendered.split("\n"):
                        lines.append(input_line)
                else:
                    # Gerar inputs a partir dos campos
                    for key, value in data.items():
                        if key != "input" and value:
                            lines.append(str(value))

            elif step.action == "select":
                if step.trigger:
                    lines.append(f"# Seleciona: {step.screen_title}")
                    lines.append(step.trigger)

            elif step.action == "submit":
                lines.append("{ENTER}")

            elif step.action == "wait":
                lines.append(f"# Aguarda {step.wait_ms}ms")
                lines.append(f"{{WAIT:{step.wait_ms}}}")

            elif step.action == "verify":
                lines.append(f"# Verifica: {step.expected_signature}")

            lines.append("")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Persistência
    # ------------------------------------------------------------------

    def save_journey(self, journey: JourneyDefinition) -> int:
        """Salva jornada no banco e retorna ID."""
        if not self.screen_registry:
            raise RuntimeError("screen_registry não configurado")

        now = datetime.now().isoformat()
        cur = self.screen_registry.con.execute(
            """INSERT INTO journeys
               (journey_id, name, description, category, entry_screen,
                steps_json, dataset_bindings_json, tags_csv, metadata_json, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(journey_id) DO UPDATE SET
                 name=excluded.name,
                 description=excluded.description,
                 steps_json=excluded.steps_json,
                 dataset_bindings_json=excluded.dataset_bindings_json,
                 tags_csv=excluded.tags_csv,
                 updated_at=excluded.updated_at""",
            (
                journey.journey_id,
                journey.name,
                journey.description,
                journey.category,
                journey.entry_screen,
                json.dumps(journey.to_dict()["steps"], ensure_ascii=False),
                json.dumps(journey.dataset_bindings, ensure_ascii=False),
                ",".join(journey.tags),
                journey.metadata_json,
                now,
                now,
            ),
        )
        self.screen_registry.con.commit()

        row = self.screen_registry.con.execute(
            "SELECT id FROM journeys WHERE journey_id=?", (journey.journey_id,)
        ).fetchone()
        return row["id"] if row else 0

    def load_journey(self, journey_id: str) -> Optional[JourneyDefinition]:
        """Carrega jornada do banco."""
        if not self.screen_registry:
            return None

        row = self.screen_registry.con.execute(
            "SELECT * FROM journeys WHERE journey_id=?", (journey_id,)
        ).fetchone()
        if not row:
            return None

        steps_data = json.loads(row["steps_json"]) if row["steps_json"] else []
        bindings = json.loads(row["dataset_bindings_json"]) if row["dataset_bindings_json"] else {}
        tags = [t.strip() for t in (row["tags_csv"] or "").split(",") if t.strip()]

        return JourneyDefinition(
            journey_id=row["journey_id"],
            name=row["name"],
            description=row["description"] or "",
            category=row["category"] or "",
            entry_screen=row["entry_screen"] or "",
            steps=[
                JourneyStep(
                    step_order=s.get("step_order", 0),
                    screen_id=s.get("screen_id", ""),
                    screen_signature=s.get("screen_signature", ""),
                    screen_title=s.get("screen_title", ""),
                    action=s.get("action", "navigate"),
                    trigger=s.get("trigger", ""),
                    input_template=s.get("input_template", ""),
                    expected_signature=s.get("expected_signature", ""),
                    depends_on=s.get("depends_on", []),
                    wait_ms=s.get("wait_ms", 0),
                    description=s.get("description", ""),
                )
                for s in steps_data
            ],
            dataset_bindings=bindings,
            tags=tags,
            metadata_json=row["metadata_json"] if "metadata_json" in row.keys() else None,
        )

    def list_journeys(self) -> list[dict]:
        """Lista todas as jornadas salvas."""
        if not self.screen_registry:
            return []

        rows = self.screen_registry.con.execute(
            "SELECT journey_id, name, category, entry_screen, tags_csv, created_at FROM journeys ORDER BY name"
        ).fetchall()

        return [
            {
                "journey_id": r["journey_id"],
                "name": r["name"],
                "category": r["category"],
                "entry_screen": r["entry_screen"],
                "tags": [t.strip() for t in (r["tags_csv"] or "").split(",") if t.strip()],
                "created_at": r["created_at"],
            }
            for r in rows
        ]

    def save_journey_session(
        self, journey_id: str, session_index: int, data_json: str, status: str = "completed"
    ) -> int:
        """Registra uma execução de sessão da jornada."""
        if not self.screen_registry:
            raise RuntimeError("screen_registry não configurado")

        now = datetime.now().isoformat()
        cur = self.screen_registry.con.execute(
            """INSERT INTO journey_sessions
               (journey_id, session_index, data_json, status, started_at, finished_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (journey_id, session_index, data_json, status, now, now),
        )
        self.screen_registry.con.commit()
        return cur.lastrowid or 0

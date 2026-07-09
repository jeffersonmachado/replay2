from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class JourneyStep:
    """Um passo dentro de uma jornada: navegar, preencher campos, submeter."""
    step_order: int
    screen_id: str = ""
    screen_signature: str = ""
    screen_title: str = ""
    action: str = "navigate"  # navigate, input, select, submit, wait, verify
    trigger: str = ""  # tecla ou comando: ENTER, F10, ESC, ou opção de menu
    input_template: str = ""  # template com placeholders: {{cliente.nome}}\n{{cliente.cpf}}
    expected_signature: str = ""  # screen signature esperada após o passo
    depends_on: list[str] = field(default_factory=list)  # referências a campos de passos anteriores
    wait_ms: int = 0  # pausa após o passo (ms)
    description: str = ""


@dataclass
class JourneyDefinition:
    """Uma jornada completa: sequência de telas que modela um processo de negócio."""
    journey_id: str = ""
    name: str = ""
    description: str = ""
    category: str = ""  # cadastro, financeiro, operacional, relatorio, etc.
    entry_screen: str = ""  # tela inicial (menu, login, etc.)
    steps: list[JourneyStep] = field(default_factory=list)
    dataset_bindings: dict[str, str] = field(default_factory=dict)  # screen_id -> dataset_name
    tags: list[str] = field(default_factory=list)
    metadata_json: Optional[str] = None

    def screen_sequence(self) -> list[str]:
        """Retorna a sequência ordenada de screen_ids visitados."""
        seen: list[str] = []
        for step in sorted(self.steps, key=lambda s: s.step_order):
            if step.screen_id and step.screen_id not in seen:
                seen.append(step.screen_id)
        return seen

    def to_dict(self) -> dict:
        return {
            "journey_id": self.journey_id,
            "name": self.name,
            "description": self.description,
            "category": self.category,
            "entry_screen": self.entry_screen,
            "steps": [
                {
                    "step_order": s.step_order,
                    "screen_id": s.screen_id,
                    "screen_signature": s.screen_signature,
                    "screen_title": s.screen_title,
                    "action": s.action,
                    "trigger": s.trigger,
                    "input_template": s.input_template,
                    "expected_signature": s.expected_signature,
                    "depends_on": s.depends_on,
                    "wait_ms": s.wait_ms,
                    "description": s.description,
                }
                for s in self.steps
            ],
            "dataset_bindings": self.dataset_bindings,
            "tags": self.tags,
        }

    @staticmethod
    def from_dict(data: dict) -> JourneyDefinition:
        return JourneyDefinition(
            journey_id=data.get("journey_id", ""),
            name=data.get("name", ""),
            description=data.get("description", ""),
            category=data.get("category", ""),
            entry_screen=data.get("entry_screen", ""),
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
                for s in data.get("steps", [])
            ],
            dataset_bindings=data.get("dataset_bindings", {}),
            tags=data.get("tags", []),
            metadata_json=data.get("metadata_json"),
        )


@dataclass
class JourneyDataset:
    """Dataset que associa dados a cada passo da jornada para múltiplas sessões."""
    journey_id: str = ""
    journey_name: str = ""
    seed: int = 0
    session_count: int = 1
    steps_data: dict[int, list[dict[str, Any]]] = field(default_factory=dict)
    # step_order -> lista de registros (um por sessão)

    def get_session_inputs(self, session_index: int) -> list[str]:
        """Retorna a sequência de inputs para uma sessão específica."""
        inputs: list[str] = []
        for step_order in sorted(self.steps_data.keys()):
            records = self.steps_data[step_order]
            if session_index < len(records):
                record = records[session_index]
                # Cada campo vira uma linha de input
                for value in record.values():
                    inputs.append(str(value))
        return inputs

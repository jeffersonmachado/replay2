"""Mix de jornadas para stress sintetico com distribuicao ponderada.

Faz parte da entrega P2-A — Synthetic Knowledge Base.

Permite configurar um cenario de stress com multiplas jornadas,
cada uma com peso proporcional. Simula carga realista com:
- 30% consultas
- 20% cadastros
- 30% vendas/pedidos
- 10% alteracoes
- 10% cancelamentos/relatorios
"""
from __future__ import annotations

import json
import random
from dataclasses import dataclass, field
from typing import Any, Optional

from .journey import JourneyDefinition


@dataclass
class JourneyMixEntry:
    """Entrada no mix de jornadas: uma jornada com peso relativo."""
    journey_id: str = ""
    journey_name: str = ""
    weight: int = 10                        # peso relativo (ex: 30 de 100)
    category: str = ""                      # create, read, update, delete, report
    entity_scope: list[str] = field(default_factory=list)  # entidades envolvidas
    min_sessions: int = 1                   # minimo de sessoes desta jornada
    max_sessions: int = 0                   # maximo (0 = sem limite)
    delay_between_ms: int = 0               # delay entre sessoes desta jornada
    params: dict = field(default_factory=dict)


@dataclass
class JourneyMixConfig:
    """Configuracao completa de um cenario de stress com mix de jornadas."""
    name: str = ""
    description: str = ""
    concurrency: int = 30
    duration_minutes: int = 60
    ramp_up_seconds: int = 10
    seed: int = 0

    entries: list[JourneyMixEntry] = field(default_factory=list)

    target_host: str = ""
    target_user: str = ""
    target_command: str = ""
    mode: str = "parallel-sessions"

    verify_screens: bool = True
    on_error: str = "continue"

    # Metadata
    total_weight: int = 0
    categories_covered: list[str] = field(default_factory=list)
    entities_covered: list[str] = field(default_factory=list)


@dataclass
class JourneyMixSchedule:
    """Agenda de execucao gerada a partir do mix."""
    config_name: str = ""
    total_sessions: int = 0
    session_assignments: list[str] = field(default_factory=list)
    # session_assignments[i] = journey_id da sessao i
    journey_distribution: dict[str, int] = field(default_factory=dict)
    # journey_id → numero de sessoes


class JourneyMixBuilder:
    """Constroi e valida cenarios de mix de jornadas para stress."""

    # ── Cenarios pre-definidos ──

    @staticmethod
    def lojas_basico(
        target_host: str = "",
        target_user: str = "",
        target_command: str = "",
    ) -> JourneyMixConfig:
        """Cenario basico para sistema de lojas."""
        return JourneyMixConfig(
            name="lojas_basico",
            description="Stress basico: consultas, cadastros, vendas e cancelamentos",
            concurrency=30,
            duration_minutes=60,
            ramp_up_seconds=10,
            entries=[
                JourneyMixEntry(
                    journey_id="lojas_consulta_cliente",
                    journey_name="Consulta de Cliente",
                    weight=30,
                    category="read",
                    entity_scope=["CLIENTES"],
                ),
                JourneyMixEntry(
                    journey_id="lojas_cadastro_cliente",
                    journey_name="Cadastro de Cliente",
                    weight=20,
                    category="create",
                    entity_scope=["CLIENTES"],
                ),
                JourneyMixEntry(
                    journey_id="lojas_venda",
                    journey_name="Venda / Pedido",
                    weight=30,
                    category="create",
                    entity_scope=["CLIENTES", "PRODUTOS", "PEDIDOS", "ITENS_PEDIDO"],
                ),
                JourneyMixEntry(
                    journey_id="lojas_cancelamento",
                    journey_name="Cancelamento de Venda",
                    weight=10,
                    category="delete",
                    entity_scope=["PEDIDOS", "ITENS_PEDIDO"],
                ),
                JourneyMixEntry(
                    journey_id="lojas_relatorio",
                    journey_name="Relatorio de Vendas",
                    weight=10,
                    category="report",
                    entity_scope=["PEDIDOS"],
                ),
            ],
            target_host=target_host,
            target_user=target_user,
            target_command=target_command,
            total_weight=100,
            categories_covered=["create", "read", "delete", "report"],
            entities_covered=["CLIENTES", "PRODUTOS", "PEDIDOS", "ITENS_PEDIDO"],
        )

    @staticmethod
    def cadastro_intensivo(
        target_host: str = "",
        target_user: str = "",
        target_command: str = "",
    ) -> JourneyMixConfig:
        """Cenario focado em cadastros e alteracoes."""
        return JourneyMixConfig(
            name="cadastro_intensivo",
            description="Stress focado em operacoes de escrita: cadastros e alteracoes",
            concurrency=20,
            duration_minutes=30,
            entries=[
                JourneyMixEntry(
                    journey_id="cadastro_cliente", journey_name="Cadastro de Cliente",
                    weight=35, category="create", entity_scope=["CLIENTES"],
                ),
                JourneyMixEntry(
                    journey_id="cadastro_produto", journey_name="Cadastro de Produto",
                    weight=25, category="create", entity_scope=["PRODUTOS"],
                ),
                JourneyMixEntry(
                    journey_id="alteracao_cliente", journey_name="Alteracao de Cliente",
                    weight=25, category="update", entity_scope=["CLIENTES"],
                ),
                JourneyMixEntry(
                    journey_id="alteracao_produto", journey_name="Alteracao de Produto",
                    weight=15, category="update", entity_scope=["PRODUTOS"],
                ),
            ],
            target_host=target_host,
            target_user=target_user,
            target_command=target_command,
            total_weight=100,
            categories_covered=["create", "update"],
            entities_covered=["CLIENTES", "PRODUTOS"],
        )

    @staticmethod
    def consulta_leve(
        target_host: str = "",
        target_user: str = "",
        target_command: str = "",
    ) -> JourneyMixConfig:
        """Cenario leve: majoritariamente consultas."""
        return JourneyMixConfig(
            name="consulta_leve",
            description="Stress leve com predominancia de consultas",
            concurrency=50,
            duration_minutes=120,
            entries=[
                JourneyMixEntry(
                    journey_id="consulta_cliente", journey_name="Consulta de Cliente",
                    weight=50, category="read", entity_scope=["CLIENTES"],
                ),
                JourneyMixEntry(
                    journey_id="consulta_produto", journey_name="Consulta de Produto",
                    weight=40, category="read", entity_scope=["PRODUTOS"],
                ),
                JourneyMixEntry(
                    journey_id="cadastro_cliente", journey_name="Cadastro de Cliente",
                    weight=10, category="create", entity_scope=["CLIENTES"],
                ),
            ],
            target_host=target_host,
            target_user=target_user,
            target_command=target_command,
            total_weight=100,
            categories_covered=["read", "create"],
            entities_covered=["CLIENTES", "PRODUTOS"],
        )

    # ── Construcao ──

    def build_schedule(
        self,
        config: JourneyMixConfig,
        total_sessions: Optional[int] = None,
    ) -> JourneyMixSchedule:
        """Gera agenda de execucao a partir da configuracao de mix."""
        if total_sessions is None:
            total_sessions = config.concurrency * max(1, config.duration_minutes)

        total_weight = sum(e.weight for e in config.entries)
        if total_weight == 0:
            return JourneyMixSchedule(config_name=config.name)

        # Calcula quantas sessoes por jornada
        distribution: dict[str, int] = {}
        remaining = total_sessions

        for i, entry in enumerate(config.entries):
            if i == len(config.entries) - 1:
                # Ultimo leva o resto
                count = remaining
            else:
                count = max(entry.min_sessions, int(total_sessions * entry.weight / total_weight))
                if entry.max_sessions > 0:
                    count = min(count, entry.max_sessions)

            distribution[entry.journey_id] = count
            remaining -= count
            if remaining <= 0:
                break

        # Gera sequencia embaralhada
        rng = random.Random(config.seed)
        session_assignments: list[str] = []
        for jid, count in distribution.items():
            session_assignments.extend([jid] * count)

        rng.shuffle(session_assignments)

        return JourneyMixSchedule(
            config_name=config.name,
            total_sessions=len(session_assignments),
            session_assignments=session_assignments,
            journey_distribution=distribution,
        )

    def validate(self, config: JourneyMixConfig) -> list[str]:
        """Valida configuracao e retorna lista de problemas."""
        issues: list[str] = []

        if not config.name:
            issues.append("nome do cenario nao informado")
        if not config.entries:
            issues.append("nenhuma jornada definida no mix")
        if config.concurrency < 1:
            issues.append("concurrency deve ser >= 1")
        if config.duration_minutes < 1:
            issues.append("duracao deve ser >= 1 minuto")

        total_weight = sum(e.weight for e in config.entries)
        if total_weight == 0:
            issues.append("peso total das jornadas e zero")

        for entry in config.entries:
            if not entry.journey_id:
                issues.append("entrada sem journey_id")
            if entry.weight < 0:
                issues.append(f"peso negativo em '{entry.journey_name or entry.journey_id}'")

        return issues

    def to_config_json(self, config: JourneyMixConfig) -> str:
        """Serializa configuracao em JSON."""
        return json.dumps(
            {
                "name": config.name,
                "description": config.description,
                "concurrency": config.concurrency,
                "duration_minutes": config.duration_minutes,
                "ramp_up_seconds": config.ramp_up_seconds,
                "seed": config.seed,
                "target_host": config.target_host,
                "target_user": config.target_user,
                "target_command": config.target_command,
                "mode": config.mode,
                "verify_screens": config.verify_screens,
                "on_error": config.on_error,
                "total_weight": config.total_weight,
                "categories_covered": config.categories_covered,
                "entities_covered": config.entities_covered,
                "entries": [
                    {
                        "journey_id": e.journey_id,
                        "journey_name": e.journey_name,
                        "weight": e.weight,
                        "category": e.category,
                        "entity_scope": e.entity_scope,
                        "min_sessions": e.min_sessions,
                        "max_sessions": e.max_sessions,
                        "delay_between_ms": e.delay_between_ms,
                    }
                    for e in config.entries
                ],
            },
            ensure_ascii=False,
            indent=2,
        )

    @staticmethod
    def from_config_json(json_str: str) -> JourneyMixConfig:
        """Deserializa configuracao de JSON."""
        data = json.loads(json_str)
        entries = [
            JourneyMixEntry(
                journey_id=e.get("journey_id", ""),
                journey_name=e.get("journey_name", ""),
                weight=e.get("weight", 10),
                category=e.get("category", ""),
                entity_scope=e.get("entity_scope", []),
                min_sessions=e.get("min_sessions", 1),
                max_sessions=e.get("max_sessions", 0),
                delay_between_ms=e.get("delay_between_ms", 0),
            )
            for e in data.get("entries", [])
        ]
        return JourneyMixConfig(
            name=data.get("name", ""),
            description=data.get("description", ""),
            concurrency=data.get("concurrency", 30),
            duration_minutes=data.get("duration_minutes", 60),
            ramp_up_seconds=data.get("ramp_up_seconds", 10),
            seed=data.get("seed", 0),
            entries=entries,
            target_host=data.get("target_host", ""),
            target_user=data.get("target_user", ""),
            target_command=data.get("target_command", ""),
            mode=data.get("mode", "parallel-sessions"),
            verify_screens=data.get("verify_screens", True),
            on_error=data.get("on_error", "continue"),
            total_weight=data.get("total_weight", 0),
            categories_covered=data.get("categories_covered", []),
            entities_covered=data.get("entities_covered", []),
        )

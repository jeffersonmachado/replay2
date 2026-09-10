"""Safety guard do motor adaptativo (§9).

Impede qualquer otimização quando há risco semântico. O bloqueio NÃO é erro:
é o comportamento de segurança — a execução segue o caminho conservador
(evento a evento, com pacing/waits originais) e o motivo é registrado.

Estado de divergência: ``record_divergence`` coloca o guard em modo
conservador até ``record_resync`` (ex.: checkpoint posterior casou). Enquanto
divergente, toda decisão é bloqueada com ``DIVERGED_STATE``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from .action_classifier import ActionClass, BARRIER_CLASSES


class GuardBlockReason(str, Enum):
    UNKNOWN_ACTION = "UNKNOWN_ACTION"
    CHECKPOINT_PENDING = "CHECKPOINT_PENDING"
    DIVERGED_STATE = "DIVERGED_STATE"
    NO_EVIDENCE = "NO_EVIDENCE"
    AMBIGUOUS_CLASS = "AMBIGUOUS_CLASS"
    CRITICAL_TRANSITION = "CRITICAL_TRANSITION"
    UNEXPECTED_RESPONSE = "UNEXPECTED_RESPONSE"
    UNSEEN_SEQUENCE = "UNSEEN_SEQUENCE"
    SEMANTIC_RISK = "SEMANTIC_RISK"


@dataclass(frozen=True)
class GuardDecision:
    allowed: bool
    reason_code: str = ""
    detail: str = ""


class SafetyGuard:
    """Guardião de segurança das otimizações do scheduler adaptativo."""

    def __init__(self) -> None:
        self._diverged = False
        self._divergence_reasons: list[str] = []

    @property
    def diverged(self) -> bool:
        return self._diverged

    @property
    def divergence_reasons(self) -> list[str]:
        return list(self._divergence_reasons)

    def record_divergence(self, reason: str) -> None:
        """Marca perda de sincronização/divergência de estado."""
        self._diverged = True
        self._divergence_reasons.append(str(reason))

    def record_resync(self) -> None:
        """Marca ressincronização (checkpoint posterior casou)."""
        self._diverged = False

    def evaluate_batch(
        self,
        *,
        action_classes: list[ActionClass],
        checkpoint_pending: bool,
        delta_positive: bool,
        has_evidence: bool,
    ) -> GuardDecision:
        """Avalia se um batch/colapso de ações é seguro.

        Regras (ordem de prioridade):
        1. estado divergente → bloqueia tudo;
        2. checkpoint pendente na fronteira → bloqueia;
        3. ação desconhecida → bloqueia;
        4. classe crítica (barreira) no batch → bloqueia;
        5. colapso de delta de pacing positivo sem evidência → bloqueia.
        """
        if self._diverged:
            return GuardDecision(
                False, GuardBlockReason.DIVERGED_STATE.value,
                "; ".join(self._divergence_reasons[-3:]),
            )
        if checkpoint_pending:
            return GuardDecision(
                False, GuardBlockReason.CHECKPOINT_PENDING.value,
                "checkpoint pendente na fronteira do batch",
            )
        if any(cls is ActionClass.UNKNOWN for cls in action_classes):
            return GuardDecision(
                False, GuardBlockReason.UNKNOWN_ACTION.value,
                "acao desconhecida no batch",
            )
        if any(cls in BARRIER_CLASSES for cls in action_classes):
            return GuardDecision(
                False, GuardBlockReason.CRITICAL_TRANSITION.value,
                "classe barreira no batch",
            )
        if delta_positive and not has_evidence:
            return GuardDecision(
                False, GuardBlockReason.NO_EVIDENCE.value,
                "colapso de pacing positivo sem evidencia de type-ahead",
            )
        return GuardDecision(True)

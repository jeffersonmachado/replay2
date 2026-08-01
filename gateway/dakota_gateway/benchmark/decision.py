"""Decisão do benchmark (contrato §16/§20).

Ordem obrigatória das portas:

- PORTA 1 — equivalência funcional: qualquer divergência (visual, semântica,
  erro adicional, timeout adicional, estado final diferente) → ``FAIL``,
  MESMO que o ambiente alvo seja mais rápido;
- PORTA 2 — desempenho: só é avaliado depois da porta 1.

``PASS`` exige TUDO: equivalência OK, amostras mínimas completas, repetições
completas, coletores obrigatórios presentes, CI aceitável e limites
atendidos. ``WARN`` cobre diferenças não bloqueantes / variabilidade alta /
normalização incompleta. ``INCONCLUSIVE`` cobre ambiente inacessível,
amostras insuficientes, coletor ausente, execução interrompida ou configuração
não comparável — e INCONCLUSIVE NUNCA vira PASS (``recommendation`` é None).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .degradation import DegradationReport
from .normalize import NORMALIZATION_INCONCLUSIVE
from .stats import Stats

VERDICTS = ("PASS", "WARN", "FAIL", "INCONCLUSIVE")


@dataclass(frozen=True)
class Decision:
    """Veredito do experimento com razões auditáveis."""

    verdict: str
    recommendation: str | None  # None quando INCONCLUSIVE/FAIL
    reasons: list[str] = field(default_factory=list)


def decide(*, functional_ok: bool, functional_diffs: list[dict],
           stats_by_env: dict[str, Stats], samples_complete: bool,
           collectors_ok: bool, ci_acceptable: bool,
           degradation: DegradationReport,
           normalization_status: str,
           collectors_detail: str = "",
           functional_evidence_ok: bool = True,
           functional_basis: str = "shared",
           stop_reason: dict | None = None) -> Decision:
    """Aplica as portas de decisão (§16/§20) e emite o veredito do experimento.

    ``collectors_detail`` (opcional) detalha QUAIS ambientes ficaram sem
    coletor obrigatório; ``functional_evidence_ok=False`` indica que o alvo
    executou amostras sem NENHUMA comparação de assinatura de tela — a
    equivalência funcional não pode ser declarada comprovada.
    ``functional_basis="per_env"`` indica equivalência por baseline PRÓPRIO
    do ambiente (datasets divergentes): paridade de dados NÃO comprovada —
    veredito máximo WARN (§20: "dados diferentes" não gera PASS).
    ``stop_reason`` presente indica escada interrompida por stop_condition
    (saturação/limite encontrado): achado de capacidade — veredito máximo
    WARN, nunca PASS (a validação completa planejada não ocorreu).
    """
    razoes: list[str] = []

    # PORTA 1 — equivalência funcional ANTES de qualquer número de desempenho.
    if not functional_ok:
        for diff in functional_diffs[:10]:
            razoes.append(
                "divergência funcional: "
                f"journey={diff.get('journey_id', '?')} "
                f"step={diff.get('step_id', '?')} "
                f"baseline_sig={diff.get('baseline_sig', '?')} "
                f"target_sig={diff.get('target_sig', '?')}"
            )
        if not razoes:
            razoes.append("divergência funcional detectada (sem detalhes)")
        return Decision(verdict="FAIL", recommendation=None, reasons=razoes)

    # Sem amostras reais válidas NÃO há veredito positivo possível (§5.3).
    if not samples_complete or not stats_by_env:
        razoes.append("amostras reais insuficientes ou incompletas")
        return Decision(verdict="INCONCLUSIVE", recommendation=None, reasons=razoes)

    if not collectors_ok:
        razoes.append(collectors_detail
                      or "coletor obrigatório ausente (host/application metrics)")
        return Decision(verdict="INCONCLUSIVE", recommendation=None, reasons=razoes)

    if not functional_evidence_ok:
        razoes.append(
            "functional_evidence_missing: o ambiente alvo executou amostras, "
            "mas nenhuma comparação de assinatura de tela foi realizada — "
            "equivalência funcional não comprovada")
        return Decision(verdict="INCONCLUSIVE", recommendation=None, reasons=razoes)

    # PORTA 2 — desempenho (só depois da funcional e da completude).
    avisos: list[str] = []
    if not ci_acceptable:
        avisos.append("variabilidade alta: intervalo de confiança 95% fora do aceitável")
    if normalization_status == NORMALIZATION_INCONCLUSIVE:
        avisos.append("normalização inconclusiva: campo aplicável ausente/zero "
                      "(eficiência por capacidade não comparável)")
    if degradation.degradation_point is not None:
        avisos.append(
            f"degradação a partir de concorrência "
            f"{degradation.degradation_point} (limite seguro: "
            f"{degradation.safe_operational_limit})"
        )
    if functional_basis == "per_env":
        avisos.append(
            "equivalência funcional por baseline próprio do ambiente: os "
            "datasets divergem entre os ambientes — paridade de dados NÃO "
            "comprovada (§20: dados diferentes não geram PASS)"
        )
    if stop_reason:
        avisos.append(
            f"escada interrompida por stop_condition:"
            f"{stop_reason.get('condition', '?')} em concorrência "
            f"{stop_reason.get('concurrency', '?')} (limite de capacidade "
            "encontrado antes do plano completo)"
        )

    if avisos:
        return Decision(
            verdict="WARN",
            recommendation=(
                "Equivalência funcional comprovada, mas há ressalvas de "
                "desempenho/eficiência — revisar os avisos antes de aprovar."
            ),
            reasons=avisos,
        )

    return Decision(
        verdict="PASS",
        recommendation=(
            "Equivalência funcional comprovada e desempenho dentro dos "
            "limites: migração recomendada."
        ),
        reasons=["todas as portas atendidas"],
    )

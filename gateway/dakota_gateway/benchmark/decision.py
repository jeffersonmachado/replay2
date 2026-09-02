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
           stop_reason: dict | None = None,
           bottleneck_evidence_ok: bool = True,
           bottleneck_detail: str = "",
           clock_skew_ok: bool = True,
           clock_skew_detail: str = "",
           clock_skew_warnings: list[str] | None = None,
           stop_classification: dict | None = None,
           provenance_problems: list | None = None,
           functional_coverage: dict | None = None,
           functional_evidence_count: int | None = None) -> Decision:
    """Aplica as portas de decisão (§16/§20) e emite o veredito do experimento.

    ``collectors_detail`` (opcional) detalha QUAIS ambientes ficaram sem
    coletor obrigatório; ``functional_evidence_ok=False`` indica que o alvo
    executou amostras sem NENHUMA comparação de assinatura de tela — a
    equivalência funcional não pode ser declarada comprovada.
    ``functional_basis="per_env"`` indica equivalência por baseline PRÓPRIO
    do ambiente (datasets divergentes): paridade de dados NÃO comprovada —
    veredito máximo WARN (§20: "dados diferentes" não gera PASS).
    ``stop_reason`` presente indica escada interrompida por stop_condition:
    achado de capacidade — veredito máximo WARN, com a parada CLASSIFICADA
    pela evidência (``stop_classification``: licença/login/launcher/
    orquestrador/saturação comprovada/...), nunca rotulada "saturação" por
    default.
    ``bottleneck_evidence_ok=False``: cobertura de coletores insuficiente
    para declarar gargalo dominante → INCONCLUSIVE (nunca gargalo inventado).
    ``clock_skew_ok=False``: amostras de host válidas sem clock offset medido
    → correção de janela não comprovável → INCONCLUSIVE; skew alto com
    correção comprovável vem em ``clock_skew_warnings`` (veredito máx. WARN).
    ``provenance_problems`` (FASE 4): hashes de proveniência do contrato
    placeholder/ausentes/iguais sem justificativa → INCONCLUSIVE — a decisão
    não pode comparar o que não está identificado.
    ``functional_coverage`` (FASE 4): cobertura da verificação funcional do
    alvo (``{"registrado", "executed", "checked", "coverage",
    "exceptions"}``); cobertura < 100% sem exceções auditadas → INCONCLUSIVE,
    com exceções auditadas → veredito máximo WARN.
    ``functional_evidence_count == 1`` (FASE 4): UMA verificação de tela não
    aprova equivalência → INCONCLUSIVE.
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

    # Proveniência (FASE 4): artefatos comparados sem identidade válida
    # (hash placeholder/ausente/igual sem justificativa) → a comparação não
    # é auditable — INCONCLUSIVE, antes de qualquer outro gate.
    if provenance_problems:
        razoes.append(
            "proveniência dos artefatos não comprovada: "
            + "; ".join(f"{p.get('campo', '?')}: {p.get('problema', '?')}"
                        for p in provenance_problems[:8]))
        return Decision(verdict="INCONCLUSIVE", recommendation=None, reasons=razoes)

    # Parada da escada (stop_condition / teto de admissão): achado de
    # CAPACIDADE — veredito máximo WARN, nunca PASS (a validação completa
    # planejada não ocorreu). A razão cita a CLASSIFICAÇÃO pela evidência
    # (ex.: "limite_licenca"), não o rótulo genérico "saturação".
    if stop_reason:
        categoria = ((stop_classification or {}).get("category")
                     or "capacidade_nao_determinada")
        razoes.append(
            f"escada interrompida ({categoria}): stop_condition:"
            f"{stop_reason.get('condition', '?')} em concorrência "
            f"{stop_reason.get('concurrency', '?')} (limite de capacidade "
            "encontrado antes do plano completo)")
        return Decision(
            verdict="WARN",
            recommendation=(
                "Escada interrompida antes do plano completo — revisar a "
                "classificação da parada e os artefatos da run antes de "
                "aprovar."
            ),
            reasons=razoes,
        )

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

    # Evidência funcional ÚNICA não aprova equivalência (FASE 4): uma só
    # verificação de tela não tem poder de comprovação.
    if functional_evidence_count == 1:
        razoes.append(
            "evidência funcional única não aprova a equivalência: apenas 1 "
            "verificação de tela executada no ambiente alvo")
        return Decision(verdict="INCONCLUSIVE", recommendation=None, reasons=razoes)

    if not bottleneck_evidence_ok:
        razoes.append(
            bottleneck_detail
            or "evidência insuficiente para declarar o gargalo dominante "
               "(cobertura de coletores incompleta)")
        return Decision(verdict="INCONCLUSIVE", recommendation=None, reasons=razoes)

    if not clock_skew_ok:
        razoes.append(
            clock_skew_detail
            or "clock skew não medido: correção da janela temporal não "
               "comprovável — comparação temporal sem prova de alinhamento "
               "de relógio")
        return Decision(verdict="INCONCLUSIVE", recommendation=None, reasons=razoes)

    # Cobertura da verificação funcional do alvo (FASE 4): checkpoints com
    # assinatura esperada que NÃO foram checados sem razão auditada →
    # INCONCLUSIVE; com exceções auditadas → veredito máximo WARN.
    cobertura_aviso = ""
    cov = functional_coverage or {}
    if cov.get("registrado"):
        executados = int(cov.get("executed") or 0)
        checados = int(cov.get("checked") or 0)
        fracao = cov.get("coverage")
        if executados > 0 and fracao is not None and fracao < 1.0:
            excecoes = [e for e in (cov.get("exceptions") or [])
                        if isinstance(e, dict) and e.get("reason")]
            if not excecoes:
                razoes.append(
                    "cobertura da verificação funcional incompleta: "
                    f"{checados} de {executados} checkpoints checados no "
                    "ambiente alvo, sem exceções auditadas — equivalência "
                    "não comprovada")
                return Decision(verdict="INCONCLUSIVE", recommendation=None,
                                reasons=razoes)
            cobertura_aviso = (
                f"cobertura funcional parcial ({checados}/{executados} "
                "checkpoints checados) com exceções auditadas: "
                + "; ".join(sorted({str(e["reason"]) for e in excecoes})))

    # PORTA 2 — desempenho (só depois da funcional e da completude).
    avisos: list[str] = []
    if cobertura_aviso:
        avisos.append(cobertura_aviso)
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
    avisos.extend(clock_skew_warnings or [])

    if avisos:
        # NUNCA dizer "comprovada" quando a base é per_env (datasets
        # divergentes) — contradição real do relatório v7
        if functional_basis == "per_env":
            recomendacao = (
                "Sem divergências nas verificações realizadas, mas a "
                "paridade de dados NÃO foi comprovada (baselines próprios "
                "por ambiente) — revisar os avisos antes de aprovar."
            )
        else:
            recomendacao = (
                "Sem divergências funcionais nas verificações realizadas, "
                "mas há ressalvas — revisar os avisos antes de aprovar."
            )
        return Decision(
            verdict="WARN",
            recommendation=recomendacao,
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

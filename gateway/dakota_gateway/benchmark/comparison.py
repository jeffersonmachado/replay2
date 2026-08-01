"""Comparação entre ambientes e decisão do experimento (contrato §15–§20).

Compõe os módulos públicos (stats/normalize/degradation/decision) sobre o
``ExperimentResult`` do executor:

- agregação por POOLING das amostras brutas de MEASUREMENT (§15) — nunca
  média de médias;
- PORTA 1 funcional antes de desempenho (§16): divergência funcional, erro
  adicional ou timeout adicional no alvo derrubam o veredito para FAIL;
- TPS derivado da janela temporal REAL das amostras (nunca inventado);
- degradação por escada de concorrência (§18) e normalização (§19);
- decisão final via ``decision.decide`` (§20) — INCONCLUSIVE nunca vira PASS.
"""
from __future__ import annotations

import json
from dataclasses import asdict

from .decision import Decision, decide
from .degradation import DegradationCriteria, analyze_ladder
from .environments import EnvironmentModel
from .models import EnvironmentRunResult, ExperimentResult
from .normalize import normalize
from .stats import aggregate_samples, compute_stats

#: Largura relativa máxima do IC95 para considerar o CI aceitável (§20).
CI_MAX_REL_WIDTH = 0.10


def _latencias_medicao(result: ExperimentResult, env_id: str) -> list[list[float]]:
    """Latências de MEASUREMENT por run (uma lista por run) do ambiente."""
    por_run: list[list[float]] = []
    for run in result.runs:
        if run.environment_id != env_id or run.status != "COMPLETED":
            continue
        por_run.append([float(s.latency_ms) for s in run.samples])
    return [lst for lst in por_run if lst]


def _janela_segundos(result: ExperimentResult, env_id: str) -> float:
    """Janela temporal real (s) coberta pelas amostras de MEASUREMENT."""
    inicios: list[int] = []
    fins: list[int] = []
    for run in result.runs:
        if run.environment_id != env_id or run.status != "COMPLETED":
            continue
        for s in run.samples:
            inicios.append(int(s.started_ns))
            fins.append(int(s.finished_ns))
    if not inicios or not fins:
        return 0.0
    return max(0.0, (max(fins) - min(inicios)) / 1e9)


def _contagens(result: ExperimentResult, env_id: str) -> dict:
    """Contagens funcionais do ambiente (sucesso/erro/timeout/divergência).

    Apenas runs COMPLETED alimentam a porta 1: runs ABORTED (nível parado
    por stop_condition — saturação, limite de licença) são achado de
    CAPACIDADE, não regressão funcional; a evidência fica nos artefatos da
    run (§24).
    """
    total = sucesso = timeouts = divergencias = erros = 0
    for run in result.runs:
        if run.environment_id != env_id or run.status != "COMPLETED":
            continue
        for s in run.samples:
            total += 1
            sucesso += 1 if s.success else 0
            timeouts += 1 if s.timeout else 0
            divergencias += 1 if s.functional_divergence else 0
            erros += 1 if (not s.success and not s.timeout) else 0
    return {"total": total, "success": sucesso, "timeouts": timeouts,
            "divergences": divergencias, "errors": erros}


def _level_stats(result: ExperimentResult, env_id: str) -> list[dict]:
    """Estatísticas por nível de concorrência (escada) do ambiente."""
    niveis: dict[int, list] = {}
    for run in result.runs:
        if run.environment_id != env_id or run.status != "COMPLETED":
            continue
        niveis.setdefault(int(run.concurrency), []).append(run)
    saida: list[dict] = []
    for conc in sorted(niveis):
        runs = niveis[conc]
        latencias = aggregate_samples(
            [[float(s.latency_ms) for s in run.samples] for run in runs if run.samples]
        )
        if not latencias:
            continue
        total = sum(len(run.samples) for run in runs)
        erros = sum(1 for run in runs for s in run.samples
                    if not s.success or s.timeout)
        inicio = min(int(s.started_ns) for run in runs for s in run.samples)
        fim = max(int(s.finished_ns) for run in runs for s in run.samples)
        janela_s = max(1e-9, (fim - inicio) / 1e9)
        stats = compute_stats(latencias)
        saida.append({
            "concurrency": conc,
            "tps": total / janela_s,
            "p95_ms": stats.p95,
            "p99_ms": stats.p99,
            "error_pct": erros / total * 100.0 if total else 0.0,
        })
    return saida


def build_comparison(result: ExperimentResult,
                     env_models: dict[str, EnvironmentModel] | None = None,
                     *, baseline_env: str | None = None,
                     target_env: str | None = None,
                     criteria: DegradationCriteria | None = None) -> dict:
    """Monta a comparação completa entre baseline e alvo (§15–§19)."""
    env_ids: list[str] = []
    for run in result.runs:
        if run.environment_id not in env_ids:
            env_ids.append(run.environment_id)
    baseline = baseline_env or (env_ids[0] if env_ids else "")
    target = target_env or (env_ids[1] if len(env_ids) > 1
                            else (env_ids[0] if env_ids else ""))

    stats_por_env: dict[str, dict] = {}
    tps_por_env: dict[str, float] = {}
    for env_id in env_ids:
        pooled = aggregate_samples(_latencias_medicao(result, env_id))
        if pooled:
            stats = compute_stats(pooled)
            stats_por_env[env_id] = asdict(stats)
            janela = _janela_segundos(result, env_id)
            total = sum(len(r.samples) for r in result.runs
                        if r.environment_id == env_id and r.status == "COMPLETED")
            tps_por_env[env_id] = (total / janela) if janela > 0 else 0.0

    cont_base = _contagens(result, baseline) if baseline else {}
    cont_alvo = _contagens(result, target) if target else {}

    # PORTA 1 — divergências funcionais do alvo (erro/timeout adicionais),
    # com as assinaturas esperada/observadas como evidência auditável.
    # Apenas runs COMPLETED: divergência sob saturação (run ABORTED por
    # stop_condition) não é regressão funcional — é achado de capacidade.
    functional_diffs: list[dict] = []
    for run in result.runs:
        if run.environment_id != target or run.status != "COMPLETED":
            continue
        for s in run.samples:
            if s.functional_divergence:
                functional_diffs.append({
                    "journey_id": s.journey_id,
                    "step_id": s.step_id,
                    "baseline_sig": getattr(s, "expected_screen_sig", ""),
                    "target_sig": getattr(s, "observed_screen_sig", ""),
                })

    # Evidência funcional por ambiente: quantas amostras tiveram a comparação
    # de assinatura de tela DE FATO executada (sem ela, a equivalência não
    # pode ser declarada "comprovada" — porta em build_decision).
    functional_evidence: dict[str, int] = {}
    for env_id in env_ids:
        functional_evidence[env_id] = sum(
            1 for run in result.runs
            if run.environment_id == env_id
            for s in run.samples
            if getattr(s, "screen_sig_checked", False)
        )

    # Base da equivalência funcional: "per_env" se QUALQUER amostra usou
    # baseline próprio do ambiente (datasets divergentes — ex.: .est
    # endian-nativo). A decisão nunca deixa per_env virar PASS (§20:
    # "dados diferentes" não comprova paridade).
    functional_basis = "shared"
    for run in result.runs:
        for s in run.samples:
            if getattr(s, "screen_check_basis", "") == "env":
                functional_basis = "per_env"
                break
        if functional_basis == "per_env":
            break

    escadas = {env_id: _level_stats(result, env_id) for env_id in env_ids}
    degradacao_por_env = {}
    for env_id in env_ids:
        host_series: list[dict] = []
        degradacao_por_env[env_id] = analyze_ladder(
            escadas[env_id], criteria or DegradationCriteria(), host_series)

    normalizacao = None
    if env_models:
        normalizacao = normalize(
            {env_id: {"tps": tps} for env_id, tps in tps_por_env.items()},
            env_models,
        )

    return {
        "baseline_env": baseline,
        "target_env": target,
        "stats_by_env": stats_por_env,
        "tps_by_env": tps_por_env,
        "counts": {"baseline": cont_base, "target": cont_alvo},
        "functional_diffs": functional_diffs,
        "functional_evidence": functional_evidence,
        "functional_basis": functional_basis,
        "ladder_by_env": escadas,
        "degradation_by_env": {
            env_id: asdict(rep) for env_id, rep in degradacao_por_env.items()
        },
        "normalization": normalizacao,
    }


def build_capacity(result: ExperimentResult) -> dict:
    """Capacidade absoluta por ambiente (maior TPS e maior nível testados)."""
    capacidade: dict[str, dict] = {}
    env_ids: list[str] = []
    for run in result.runs:
        if run.environment_id not in env_ids:
            env_ids.append(run.environment_id)
    for env_id in env_ids:
        escada = _level_stats(result, env_id)
        maior_tps = max((n["tps"] for n in escada), default=0.0)
        maior_nivel = max((n["concurrency"] for n in escada), default=0)
        capacidade[env_id] = {
            "max_tps_observed": maior_tps,
            "max_concurrency_tested": maior_nivel,
            "levels": escada,
        }
    return capacidade


def _host_samples_validos(run: EnvironmentRunResult) -> int:
    """Conta amostras de host VÁLIDAS no arquivo do coletor da run.

    Válida = linha JSON parseável que NÃO seja o marcador de indisponibilidade
    (``{"available": false, ...}``). Um ``host_samples_path`` não-vazio sem
    nenhuma amostra válida NÃO comprova coleta — foi o furo do smoke real
    (PASS com zero amostras de host em um ambiente).
    """
    caminho = getattr(run, "host_samples_path", "") or ""
    if not caminho:
        return 0
    try:
        with open(caminho, encoding="utf-8") as fh:
            linhas = [l.strip() for l in fh if l.strip()]
    except OSError:
        return 0
    validas = 0
    for linha in linhas:
        try:
            dado = json.loads(linha)
        except ValueError:
            continue
        if isinstance(dado, dict) and dado.get("available") is False:
            continue
        validas += 1
    return validas


def build_decision(result: ExperimentResult, comparison: dict,
                   *, criteria: DegradationCriteria | None = None) -> Decision:
    """Aplica as portas de decisão (§16/§20) sobre a comparação montada."""
    from .degradation import DegradationReport
    from .stats import Stats

    target = comparison.get("target_env", "")
    baseline = comparison.get("baseline_env", "")

    cont_alvo = comparison.get("counts", {}).get("target", {})
    cont_base = comparison.get("counts", {}).get("baseline", {})
    functional_diffs = list(comparison.get("functional_diffs", []))
    erros_adicionais = (cont_alvo.get("errors", 0) - cont_base.get("errors", 0))
    timeouts_adicionais = (cont_alvo.get("timeouts", 0) - cont_base.get("timeouts", 0))
    if erros_adicionais > 0:
        functional_diffs.append({"journey_id": "*", "step_id": "*",
                                 "baseline_sig": "ok",
                                 "target_sig": f"{erros_adicionais} erros adicionais"})
    if timeouts_adicionais > 0:
        functional_diffs.append({"journey_id": "*", "step_id": "*",
                                 "baseline_sig": "ok",
                                 "target_sig": f"{timeouts_adicionais} timeouts adicionais"})
    functional_ok = not functional_diffs

    stats_by_env: dict[str, Stats] = {}
    for env_id, dados in comparison.get("stats_by_env", {}).items():
        stats_by_env[env_id] = Stats(**dados)

    # amostras mínimas completas: toda run não-ABORTED COMPLETED com >=1
    # amostra de medição. Runs ABORTED (nível parado por stop_condition)
    # não invalidam a completude dos níveis executados — a parada da escada
    # é sinalizada à decisão via stop_reason (WARN, nunca PASS).
    runs = [r for r in result.runs]
    runs_validas = [r for r in runs if r.status != "ABORTED"]
    samples_complete = bool(runs_validas) and all(
        r.status == "COMPLETED" and len(r.samples) >= 1 for r in runs_validas
    ) and result.status == "COMPLETED"

    # coletores obrigatórios: >=1 amostra de host VÁLIDA por AMBIENTE (um
    # path não-vazio sem amostras não comprova coleta — furo do smoke real).
    host_validas_por_env: dict[str, int] = {}
    for r in runs:
        host_validas_por_env[r.environment_id] = (
            host_validas_por_env.get(r.environment_id, 0)
            + _host_samples_validos(r))
    envs_sem_host = sorted(
        env for env, n in host_validas_por_env.items() if n < 1)
    collectors_ok = bool(runs) and not envs_sem_host
    collectors_detail = ""
    if runs and envs_sem_host:
        collectors_detail = (
            "coletor obrigatório ausente: host_metrics sem amostras válidas "
            f"({', '.join(envs_sem_host)})")

    # evidência funcional: o alvo executou amostras mas NENHUMA comparação
    # de assinatura de tela → equivalência não comprovada (INCONCLUSIVE).
    evidencia = comparison.get("functional_evidence", {})
    functional_evidence_ok = (
        cont_alvo.get("total", 0) == 0 or evidencia.get(target, 0) > 0)

    ci_acceptable = bool(stats_by_env) and all(
        (s.ci95_high - s.ci95_low) / 2.0 <= CI_MAX_REL_WIDTH * s.mean
        for s in stats_by_env.values() if s.mean > 0
    )

    deg = comparison.get("degradation_by_env", {}).get(target) or {}
    degradation = DegradationReport(
        degradation_point=deg.get("degradation_point"),
        safe_operational_limit=deg.get("safe_operational_limit"),
        maximum_observed_limit=deg.get("maximum_observed_limit"),
        dominant_bottleneck=deg.get("dominant_bottleneck", "unknown"),
        recovery_seconds=deg.get("recovery_seconds"),
    )

    normalizacao = comparison.get("normalization") or {}
    normalization_status = normalizacao.get("status", "OK")

    return decide(
        functional_ok=functional_ok,
        functional_diffs=functional_diffs,
        stats_by_env=stats_by_env,
        samples_complete=samples_complete,
        collectors_ok=collectors_ok,
        ci_acceptable=ci_acceptable,
        degradation=degradation,
        normalization_status=normalization_status,
        collectors_detail=collectors_detail,
        functional_evidence_ok=functional_evidence_ok,
        functional_basis=comparison.get("functional_basis", "shared"),
        stop_reason=getattr(result, "stop_reason", None),
    )

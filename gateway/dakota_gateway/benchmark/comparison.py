"""Comparação entre ambientes e decisão do experimento (contrato §15–§20).

Compõe os módulos públicos (stats/normalize/degradation/decision) sobre o
``ExperimentResult`` do executor:

- agregação por POOLING das amostras brutas de MEASUREMENT (§15) — nunca
  média de médias;
- PORTA 1 funcional antes de desempenho (§16): divergência funcional, erro
  adicional ou timeout adicional no alvo derrubam o veredito para FAIL;
- vazão POR NÍVEL de concorrência: ``operations_per_second`` = soma das
  operações válidas ÷ soma das durações REAIS de MEASUREMENT de cada run
  (``max(finished_ns) - min(started_ns)`` das amostras DAQUELA run) — nunca
  o intervalo entre repetições espaçadas e nunca um "TPS" agregando níveis
  de concorrência diferentes (metodologia corrigida na FASE 1: o cálculo
  antigo subestimava a vazão em ~10-12× quando as repetições eram
  espaçadas, ex.: 3h entre runs do cap13-aix-linux-oficial-v7);
- ``tps``/``tps_by_env`` seguem expostos como LEGADO (depreciados) para
  compatibilidade de leitura de relatórios antigos — carregam a vazão
  corrigida do nível de referência, com a base explícita em
  ``throughput_reference``;
- degradação por escada de concorrência (§18) e normalização (§19) sobre a
  vazão do limite operacional seguro — UM nível explicitamente
  identificado, nunca o agregado heterogêneo;
- decisão final via ``decision.decide`` (§20) — INCONCLUSIVE nunca vira PASS.
"""
from __future__ import annotations

import json
from dataclasses import asdict, replace

from .coverage import (
    analisar_cobertura,
    bottleneck_evidence,
    ler_amostras_host_validas,
)
from .decision import Decision, decide
from .degradation import DegradationCriteria, analyze_ladder
from .environments import EnvironmentModel
from .models import EnvironmentRunResult, ExperimentResult
from .normalize import normalize
from .stats import aggregate_samples, compute_stats

#: Largura relativa máxima do IC95 para considerar o CI aceitável (§20).
CI_MAX_REL_WIDTH = 0.10

#: Gate padrão de clock skew orquestrador×host (ms): acima disso a comparação
#: temporal exige correção de janela comprovável (offset registrado na coleta)
#: e o veredito é no máximo WARN (FASE 3 — caso real v7: AIX ~171 s atrasado).
MAX_CLOCK_SKEW_MS = 1000

#: stop_conditions MEDIDAS (há número medido) → saturação comprovada.
_STOP_CONDITIONS_MEDIDAS = ("host_cpu_pct", "swap_growth_mb", "p99_limit_ms",
                            "error_rate_pct")


def _latencias_medicao(result: ExperimentResult, env_id: str) -> list[list[float]]:
    """Latências de MEASUREMENT por run (uma lista por run) do ambiente."""
    por_run: list[list[float]] = []
    for run in result.runs:
        if run.environment_id != env_id or run.status != "COMPLETED":
            continue
        por_run.append([float(s.latency_ms) for s in run.samples])
    return [lst for lst in por_run if lst]


def _duracao_run_segundos(run: EnvironmentRunResult) -> float:
    """Duração real (s) da janela de MEASUREMENT de UMA run.

    ``max(finished_ns) - min(started_ns)`` das amostras daquela run — NUNCA
    o intervalo entre repetições diferentes. ``<= 0`` = duração inválida:
    a run é excluída do cálculo de vazão (e registrada em
    ``invalid_duration_runs``), nunca gera divisão por ~0 nem vazão
    artificial.
    """
    if not run.samples:
        return 0.0
    inicio = min(int(s.started_ns) for s in run.samples)
    fim = max(int(s.finished_ns) for s in run.samples)
    return (fim - inicio) / 1e9


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
    """Estatísticas por nível de concorrência (escada) do ambiente.

    Vazão do nível (metodologia FASE 1):
    ``operations_per_second`` = soma das operações das runs com duração
    válida ÷ soma das durações REAIS de MEASUREMENT de cada run
    (``_duracao_run_segundos``). Runs com duração inválida
    (``finished <= started``) são excluídas do numerador E do denominador e
    registradas em ``invalid_duration_runs`` — nunca inflam a vazão.

    Métricas de jornada (``journeys_count`` /
    ``completed_journeys_per_second``) só são emitidas quando TODAS as runs
    do nível carregam a contagem confiável ``completed_journeys``
    (registrada pelo executor); sem o dado, as chaves são omitidas — jornada
    completa nunca é inferida das amostras. ``planned_duration_s`` (soma do
    ``measurement_seconds`` planejado das runs) segue a mesma regra.

    ``tps`` é mantido como ALIAS LEGADO (depreciado) de
    ``operations_per_second`` para compatibilidade de leitura.
    """
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
        operacoes = 0
        duracao_s = 0.0
        invalidas = 0
        jornadas = 0
        jornadas_confiavel = True
        planejado_s = 0.0
        planejado_confiavel = True
        for run in runs:
            dur = _duracao_run_segundos(run)
            if getattr(run, "planned_duration_s", None) is None:
                planejado_confiavel = False
            else:
                planejado_s += float(run.planned_duration_s)
            if dur <= 0.0:
                # duração inválida: fora da vazão (e das jornadas/s, que
                # dividem pela mesma base), registrada — nunca infla
                invalidas += 1
                jornadas_confiavel = False
                continue
            operacoes += len(run.samples)
            duracao_s += dur
            if getattr(run, "completed_journeys", None) is None:
                jornadas_confiavel = False
            else:
                jornadas += int(run.completed_journeys)
        erros = sum(1 for run in runs for s in run.samples
                    if not s.success or s.timeout)
        total = sum(len(run.samples) for run in runs)
        ops_por_s = (operacoes / duracao_s) if duracao_s > 0 else 0.0
        stats = compute_stats(latencias)
        nivel = {
            "concurrency": conc,
            "runs_count": len(runs),
            "operations_count": operacoes,
            "observed_duration_s": duracao_s,
            "operations_per_second": ops_por_s,
            "invalid_duration_runs": invalidas,
            # ALIAS LEGADO (depreciado): mesmo valor corrigido de
            # operations_per_second — mantido para relatórios/UI antigos
            "tps": ops_por_s,
            "tps_deprecated": True,
            "p95_ms": stats.p95,
            "p99_ms": stats.p99,
            "error_pct": erros / total * 100.0 if total else 0.0,
        }
        if jornadas_confiavel:
            nivel["journeys_count"] = jornadas
            nivel["completed_journeys_per_second"] = (
                (jornadas / duracao_s) if duracao_s > 0 else 0.0)
        if planejado_confiavel:
            nivel["planned_duration_s"] = planejado_s
        saida.append(nivel)
    return saida


def _nivel_referencia(escada: list[dict], safe_operational_limit: int | None
                      ) -> dict | None:
    """Nível de referência da vazão do ambiente (para normalização/capacidade).

    UM nível explicitamente identificado — o limite operacional seguro
    (``degradation.safe_operational_limit``); fallback: o maior nível com
    vazão válida. Nunca um agregado de níveis de concorrência diferentes.
    """
    if not escada:
        return None
    por_conc = {int(n["concurrency"]): n for n in escada}
    if safe_operational_limit is not None:
        ref = por_conc.get(int(safe_operational_limit))
        if ref is not None and ref["operations_per_second"] > 0:
            return ref
    candidatos = [n for n in escada if n["operations_per_second"] > 0]
    return candidatos[-1] if candidatos else None


def _host_series_por_env(result: ExperimentResult, env_id: str) -> list[dict]:
    """Série REAL de amostras válidas de host do ambiente (todas as runs).

    Alimenta o gargalo dominante da escada — substitui a lista vazia
    hard-coded que fazia ``dominant_bottleneck`` sair sempre "unknown".
    """
    serie: list[dict] = []
    for run in result.runs:
        if run.environment_id != env_id:
            continue
        serie.extend(ler_amostras_host_validas(
            getattr(run, "host_samples_path", "") or ""))
    return serie


def _clock_skew_por_env(result: ExperimentResult, env_ids: list[str],
                        gate_ms: int) -> dict[str, dict]:
    """Clock skew orquestrador×host por ambiente (FASE 3).

    O offset é medido na coleta (script remoto registra ``clock_offset_ms``
    e desloca a janela — correção comprovável). ``measured=False`` com
    amostras de host válidas significa correção NÃO comprovável — a decisão
    vira INCONCLUSIVE (``build_decision``).
    """
    skew: dict[str, dict] = {}
    for env_id in env_ids:
        runs_env = [r for r in result.runs if r.environment_id == env_id]
        medidos = [abs(int(r.host_clock_offset_ms)) for r in runs_env
                   if getattr(r, "host_clock_offset_measured", False)
                   and getattr(r, "host_clock_offset_ms", None) is not None]
        medido = bool(medidos)
        max_abs = max(medidos) if medidos else 0
        skew[env_id] = {
            "measured": medido,
            "max_abs_offset_ms": max_abs,
            # correção comprovável: o offset foi medido e registrado na
            # coleta (a janela já é deslocada na origem)
            "corrected": medido,
            "within_gate": medido and max_abs <= gate_ms,
            "gate_ms": gate_ms,
        }
    return skew


def _classificar_parada(result: ExperimentResult) -> dict | None:
    """Classifica a parada da escada pela EVIDÊNCIA registrada (FASE 3).

    Uma falha de admissão de sessão NÃO é prova automática de saturação do
    servidor: a categoria sai do ``error_reason`` das runs ABORTED/FAILED
    (licença, login, launcher, recurso do orquestrador) ou da stop_condition
    medida. Sem parada → None.
    """
    stop = getattr(result, "stop_reason", None)
    if stop:
        condition = str(stop.get("condition", ""))
        if condition in _STOP_CONDITIONS_MEDIDAS:
            return {"category": "saturacao_comprovada",
                    "condition": condition,
                    "evidence": f"stop_condition medida: {condition}="
                                f"{stop.get('value')} (limite "
                                f"{stop.get('limit')})"}
        if condition == "session_admission_limit":
            evidencias = " ".join(
                (r.error_reason or "") for r in result.runs
                if r.status in ("ABORTED", "FAILED")).lower()
            for categoria, padroes in (
                    ("limite_licenca", ("user limit exceeded", "license",
                                        "licenç")),
                    ("falha_login", ("permission denied", "authentication")),
                    ("falha_launcher", ("not found",)),
                    ("limite_orquestrador", ("cannot allocate memory",
                                             "too many open files"))):
                if any(p in evidencias for p in padroes):
                    return {"category": categoria, "condition": condition,
                            "evidence": evidencias.strip()[:300]}
            # admissão sem evidência específica (ex.: âncora do menu não
            # apareceu — caso real v7 conc20): capacidade NÃO determinada,
            # jamais saturação comprovada
            return {"category": "capacidade_nao_determinada",
                    "condition": condition,
                    "evidence": evidencias.strip()[:300]}
        return {"category": "capacidade_nao_determinada",
                "condition": condition,
                "evidence": f"stop_condition:{condition}"}
    reason = str(getattr(result, "reason", "") or "")
    if reason.startswith("environment_unreachable"):
        return {"category": "ambiente_inacessivel", "condition": reason,
                "evidence": "colapso de transporte durante a escada"}
    return None


def _cobertura_funcional_por_env(result: ExperimentResult,
                                 env_ids: list[str]) -> dict[str, dict]:
    """Cobertura da verificação funcional por ambiente (FASE 4).

    ``checkpoints_executed``/``checkpoints_checked`` são registrados pelo
    executor (delta do ``checkpoint_log`` do adaptador na fase MEASUREMENT);
    ``None`` = não registrado (artefato antigo) — sem gate, mas explicitado.
    """
    cobertura: dict[str, dict] = {}
    for env_id in env_ids:
        runs_env = [r for r in result.runs
                    if r.environment_id == env_id and r.status == "COMPLETED"]
        executados = sum(int(r.checkpoints_executed) for r in runs_env
                         if getattr(r, "checkpoints_executed", None) is not None)
        checados = sum(int(r.checkpoints_checked) for r in runs_env
                       if getattr(r, "checkpoints_checked", None) is not None)
        registrado = any(getattr(r, "checkpoints_executed", None) is not None
                         for r in runs_env)
        excecoes = [exc for r in runs_env
                    for exc in (getattr(r, "checkpoint_exceptions", None) or [])]
        cobertura[env_id] = {
            "registrado": registrado,
            "executed": executados if registrado else None,
            "checked": checados if registrado else None,
            "coverage": (checados / executados
                         if registrado and executados > 0 else None),
            "exceptions": excecoes,
        }
    return cobertura


def build_comparison(result: ExperimentResult,
                     env_models: dict[str, EnvironmentModel] | None = None,
                     *, baseline_env: str | None = None,
                     target_env: str | None = None,
                     criteria: DegradationCriteria | None = None,
                     max_clock_skew_ms: int = MAX_CLOCK_SKEW_MS,
                     contract=None) -> dict:
    """Monta a comparação completa entre baseline e alvo (§15–§19).

    ``max_clock_skew_ms``: gate de clock skew orquestrador×host (default 1 s).
    ``contract``: contrato do experimento (``ExperimentContract``) — quando
    informado, os hashes de proveniência são validados
    (``provenance_problems``) e a decisão barra placeholders (FASE 4).
    """
    env_ids: list[str] = []
    for run in result.runs:
        if run.environment_id not in env_ids:
            env_ids.append(run.environment_id)
    baseline = baseline_env or (env_ids[0] if env_ids else "")
    target = target_env or (env_ids[1] if len(env_ids) > 1
                            else (env_ids[0] if env_ids else ""))

    stats_por_env: dict[str, dict] = {}
    for env_id in env_ids:
        pooled = aggregate_samples(_latencias_medicao(result, env_id))
        if pooled:
            stats = compute_stats(pooled)
            stats_por_env[env_id] = asdict(stats)

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

    # Cobertura por coletor (FASE 3): JSON parseável não é prova de coleta
    # válida — grupos essenciais ausentes forçam gargalo "unknown" e a
    # decisão vira INCONCLUSIVE (nunca gargalo inventado).
    cobertura = analisar_cobertura(list(result.runs))
    evidencia_gargalo = bottleneck_evidence(cobertura)

    recuperacao = getattr(result, "recovery", None) or {}

    degradacao_por_env = {}
    for env_id in env_ids:
        host_series = _host_series_por_env(result, env_id)
        rec_env = recuperacao.get(env_id) or {}
        relatorio = analyze_ladder(
            escadas[env_id], criteria or DegradationCriteria(), host_series,
            recovery_seconds=rec_env.get("recovery_seconds"))
        if not evidencia_gargalo.get(env_id, {}).get("ok", False):
            # cobertura insuficiente: o gargalo NÃO pode ser declarado
            relatorio = replace(relatorio, dominant_bottleneck="unknown")
        degradacao_por_env[env_id] = relatorio

    # Nível de referência da vazão por ambiente: UM nível explicitamente
    # identificado (limite operacional seguro da escada; fallback: maior
    # nível com vazão válida). A normalização e o campo legado tps_by_env
    # consomem SEMPRE essa vazão por nível — nunca um agregado
    # heterogêneo de níveis de concorrência diferentes.
    referencia_por_env: dict[str, dict] = {}
    tps_por_env: dict[str, float] = {}
    for env_id in env_ids:
        ref = _nivel_referencia(
            escadas[env_id],
            degradacao_por_env[env_id].safe_operational_limit)
        if ref is None:
            continue
        referencia_por_env[env_id] = {
            "concurrency": ref["concurrency"],
            "operations_per_second": ref["operations_per_second"],
            "basis": "safe_operational_limit",
        }
        tps_por_env[env_id] = ref["operations_per_second"]

    normalizacao = None
    if env_models:
        normalizacao = normalize(
            {env_id: {"tps": ref["operations_per_second"],
                      "throughput_level": ref["concurrency"],
                      "throughput_metric": "operations_per_second"}
             for env_id, ref in referencia_por_env.items()},
            env_models,
        )

    # Proveniência (FASE 4): com o contrato em mãos, os hashes são validados
    # na decisão — placeholder/ausente/igualdade não justificada barram.
    provenance_problems: list[dict] = []
    if contract is not None:
        from .contract import validate_provenance_hashes
        provenance_problems = validate_provenance_hashes(
            contract, exigir_presenca=True)

    return {
        "baseline_env": baseline,
        "target_env": target,
        "stats_by_env": stats_por_env,
        # LEGADO (depreciado): vazão do nível de referência, NÃO o agregado
        # heterogêneo antigo. Consumidores novos usam ``ladder_by_env`` /
        # ``throughput_reference``.
        "tps_by_env": tps_por_env,
        "tps_by_env_deprecated": True,
        "throughput_reference": referencia_por_env,
        "counts": {"baseline": cont_base, "target": cont_alvo},
        "functional_diffs": functional_diffs,
        "functional_evidence": functional_evidence,
        "functional_basis": functional_basis,
        "functional_coverage_by_env": _cobertura_funcional_por_env(
            result, env_ids),
        "ladder_by_env": escadas,
        "degradation_by_env": {
            env_id: asdict(rep) for env_id, rep in degradacao_por_env.items()
        },
        "collector_coverage": cobertura,
        "bottleneck_evidence": evidencia_gargalo,
        "clock_skew": _clock_skew_por_env(result, env_ids, max_clock_skew_ms),
        "stop_classification": _classificar_parada(result),
        "recovery_by_env": recuperacao,
        "provenance_problems": provenance_problems,
        "normalization": normalizacao,
    }


def build_capacity(result: ExperimentResult) -> dict:
    """Capacidade absoluta por ambiente (maior vazão e maior nível testados).

    A vazão vem de ``operations_per_second`` POR NÍVEL (soma de operações ÷
    soma das durações reais das runs daquele nível) — nunca de um agregado
    entre níveis. ``max_tps_observed`` é mantido como alias legado
    (depreciado) de ``max_operations_per_second_observed``.
    """
    capacidade: dict[str, dict] = {}
    env_ids: list[str] = []
    for run in result.runs:
        if run.environment_id not in env_ids:
            env_ids.append(run.environment_id)
    for env_id in env_ids:
        escada = _level_stats(result, env_id)
        maior_ops = max((n["operations_per_second"] for n in escada),
                        default=0.0)
        maior_nivel = max((n["concurrency"] for n in escada), default=0)
        capacidade[env_id] = {
            "max_operations_per_second_observed": maior_ops,
            # ALIAS LEGADO (depreciado) — mesmo valor corrigido
            "max_tps_observed": maior_ops,
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

    # Evidência de gargalo (FASE 3): cobertura insuficiente de coletores em
    # qualquer ambiente → gargalo não declarável → INCONCLUSIVE.
    evidencia_gargalo = comparison.get("bottleneck_evidence", {})
    envs_sem_evidencia = sorted(
        env for env, ev in evidencia_gargalo.items() if not ev.get("ok"))
    bottleneck_evidence_ok = not envs_sem_evidencia
    bottleneck_detail = ""
    if envs_sem_evidencia:
        partes = []
        for env in envs_sem_evidencia:
            faltantes = evidencia_gargalo[env].get("missing", [])
            partes.append(f"{env} sem {', '.join(faltantes) or 'cobertura'}")
        bottleneck_detail = (
            "evidência insuficiente para declarar o gargalo dominante: "
            + "; ".join(partes))

    # Clock skew (FASE 3): amostras de host válidas SEM offset medido →
    # correção de janela não comprovável → INCONCLUSIVE. Skew alto com
    # offset registrado → correção comprovável → veredito máximo WARN.
    cobertura = comparison.get("collector_coverage", {})
    skew_por_env = comparison.get("clock_skew", {})
    skew_nao_medido = sorted(
        env for env, skew in skew_por_env.items()
        if not skew.get("measured")
        and cobertura.get(env, {}).get("host", {}).get("amostras_validas", 0) > 0)
    clock_skew_ok = not skew_nao_medido
    clock_skew_detail = ""
    if skew_nao_medido:
        clock_skew_detail = (
            "clock skew não medido: amostras de host válidas sem offset de "
            f"relógio registrado ({', '.join(skew_nao_medido)}) — correção "
            "da janela temporal não comprovável")
    clock_skew_warnings: list[str] = []
    for env, skew in sorted(skew_por_env.items()):
        if skew.get("measured") and not skew.get("within_gate"):
            clock_skew_warnings.append(
                f"clock skew de {skew.get('max_abs_offset_ms')} ms em {env} "
                f"acima do gate ({skew.get('gate_ms')} ms) — correção de "
                "janela aplicada na coleta e comprovável (offset registrado)")

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
        bottleneck_evidence_ok=bottleneck_evidence_ok,
        bottleneck_detail=bottleneck_detail,
        clock_skew_ok=clock_skew_ok,
        clock_skew_detail=clock_skew_detail,
        clock_skew_warnings=clock_skew_warnings,
        stop_classification=comparison.get("stop_classification"),
        provenance_problems=(comparison.get("provenance_problems") or None),
        functional_coverage=(comparison.get("functional_coverage_by_env", {})
                             .get(target)),
        functional_evidence_count=evidencia.get(target, 0),
    )

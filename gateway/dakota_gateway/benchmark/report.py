"""Relatório e artefatos do experimento (contrato §24).

Gera, no diretório do experimento:

- ``report.md`` — relatório legível separando validação funcional, comparação
  de desempenho, capacidade absoluta, eficiência normalizada, degradação,
  saturação, recuperação, gargalos, limitações, nível de confiança e
  recomendação;
- ``report.json`` — o mesmo conteúdo em formato estruturado;
- ``aggregates/<env>.json`` — agregados por ambiente (nomes do experimento);
- ``aggregates/comparison.json`` e ``aggregates/capacity.json``;
- ``evidence-manifest.sha256`` — SHA-256 de TODOS os arquivos do experimento
  (gerado por último, sem se auto-incluir).
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .decision import Decision
from .models import ExperimentResult


def _resumo_runs(result: ExperimentResult) -> list[dict]:
    """Resumo por run (sem as amostras brutas, que ficam nos .jsonl)."""
    return [
        {
            "environment_id": r.environment_id,
            "iteration": r.iteration,
            "concurrency": r.concurrency,
            "status": r.status,
            "error_reason": r.error_reason,
            "measurement_samples": len(r.samples),
            "warmup_samples": len(r.warmup_samples),
            "cooldown_samples": len(r.cooldown_samples),
        }
        for r in result.runs
    ]


def _montar_report_dict(result: ExperimentResult, comparison: dict,
                        capacity: dict, decision: Decision) -> dict:
    """Estrutura única do relatório (usada por report.json e report.md)."""
    return {
        "functional_validation": {
            "ok": not comparison.get("functional_diffs"),
            "diffs": comparison.get("functional_diffs", []),
            "counts": comparison.get("counts", {}),
        },
        "performance_comparison": {
            "baseline_env": comparison.get("baseline_env", ""),
            "target_env": comparison.get("target_env", ""),
            "stats_by_env": comparison.get("stats_by_env", {}),
            "tps_by_env": comparison.get("tps_by_env", {}),
        },
        "absolute_capacity": capacity,
        "normalized_efficiency": comparison.get("normalization"),
        "degradation": comparison.get("degradation_by_env", {}),
        "saturation": {
            env: deg.get("degradation_point")
            for env, deg in comparison.get("degradation_by_env", {}).items()
        },
        "recovery": {
            env: deg.get("recovery_seconds")
            for env, deg in comparison.get("degradation_by_env", {}).items()
        },
        "bottlenecks": {
            env: deg.get("dominant_bottleneck", "unknown")
            for env, deg in comparison.get("degradation_by_env", {}).items()
        },
        "limitations": list(decision.reasons),
        "confidence": {
            "ci95_by_env": {
                env: {"ci95_low": s.get("ci95_low"), "ci95_high": s.get("ci95_high")}
                for env, s in comparison.get("stats_by_env", {}).items()
            },
        },
        "recommendation": decision.recommendation,
        "verdict": decision.verdict,
        "experiment": {
            "contract_sha256": result.contract_sha256,
            "status": result.status,
            "reason": result.reason,
            "runs": _resumo_runs(result),
        },
    }


def _fmt(valor, casas: int = 2) -> str:
    """Formata número ou devolve o bruto quando não é número."""
    if isinstance(valor, float):
        return f"{valor:.{casas}f}"
    return str(valor)


def _render_markdown(report: dict) -> str:
    """Renderiza o relatório em Markdown (seções do §24)."""
    perf = report["performance_comparison"]
    linhas = [
        "# Relatório de Benchmark — AIX vs Linux",
        "",
        f"**Veredito:** {report['verdict']}",
        f"**Status do experimento:** {report['experiment']['status']}",
    ]
    if report["experiment"].get("reason"):
        linhas.append(f"**Motivo:** {report['experiment']['reason']}")
    linhas.append("")

    linhas.append("## Validação funcional")
    fv = report["functional_validation"]
    linhas.append(f"- Equivalência funcional: {'OK' if fv['ok'] else 'DIVERGENTE'}")
    for diff in fv["diffs"][:20]:
        linhas.append(
            f"- Divergência: journey={diff.get('journey_id')} "
            f"step={diff.get('step_id')} ({diff.get('target_sig')})")
    linhas.append("")

    linhas.append("## Comparação de desempenho")
    linhas.append(f"- Baseline: {perf['baseline_env']} | Alvo: {perf['target_env']}")
    for env, stats in perf.get("stats_by_env", {}).items():
        linhas.append(
            f"- {env}: n={stats.get('n')} mean={_fmt(stats.get('mean'))}ms "
            f"p50={_fmt(stats.get('p50'))} p90={_fmt(stats.get('p90'))} "
            f"p95={_fmt(stats.get('p95'))} p99={_fmt(stats.get('p99'))} "
            f"max={_fmt(stats.get('max'))} cv={_fmt(stats.get('cv'), 4)}")
    for env, tps in perf.get("tps_by_env", {}).items():
        linhas.append(f"- TPS {env}: {_fmt(tps)}")
    linhas.append("")

    linhas.append("## Capacidade absoluta")
    linhas.append("```json")
    linhas.append(json.dumps(report["absolute_capacity"], indent=2,
                             ensure_ascii=False, default=str))
    linhas.append("```")
    linhas.append("")

    linhas.append("## Eficiência normalizada")
    norm = report.get("normalized_efficiency")
    if norm:
        linhas.append(f"- Status: {norm.get('status')}")
        for env, dados in norm.get("per_environment", {}).items():
            linhas.append(
                f"- {env}: tps/vCPU={_fmt(dados.get('tps_per_vcpu'))} "
                f"tps/core={_fmt(dados.get('tps_per_physical_core'))} "
                f"tps/entitled={_fmt(dados.get('tps_per_entitled_capacity'))} "
                f"tps/GB={_fmt(dados.get('tps_per_gb'))}")
        linhas.append("- Fórmulas:")
        for nome, formula in norm.get("formulas", {}).items():
            linhas.append(f"  - `{nome}` = {formula}")
    else:
        linhas.append("- Sem modelos de ambiente: normalização não calculada.")
    linhas.append("")

    linhas.append("## Degradação e saturação")
    for env, deg in report.get("degradation", {}).items():
        linhas.append(
            f"- {env}: ponto_de_degradação={deg.get('degradation_point')} "
            f"limite_seguro={deg.get('safe_operational_limit')} "
            f"máximo_observado={deg.get('maximum_observed_limit')}")
    linhas.append("")

    linhas.append("## Recuperação")
    for env, rec in report.get("recovery", {}).items():
        linhas.append(f"- {env}: {rec if rec is not None else 'não medido'}")
    linhas.append("")

    linhas.append("## Gargalos dominantes")
    for env, gargalo in report.get("bottlenecks", {}).items():
        linhas.append(f"- {env}: {gargalo}")
    linhas.append("")

    linhas.append("## Limitações")
    for razao in report.get("limitations", []):
        linhas.append(f"- {razao}")
    linhas.append("")

    linhas.append("## Nível de confiança")
    for env, ci in report.get("confidence", {}).get("ci95_by_env", {}).items():
        linhas.append(
            f"- {env}: IC95 = [{_fmt(ci.get('ci95_low'))}, "
            f"{_fmt(ci.get('ci95_high'))}] ms")
    linhas.append("")

    linhas.append("## Recomendação")
    linhas.append(report.get("recommendation") or "Sem recomendação (veredito não positivo).")
    linhas.append("")
    return "\n".join(linhas)


def _evidence_manifest(experiment_dir: Path) -> Path:
    """Gera evidence-manifest.sha256 (por último, sem se auto-incluir)."""
    alvo = experiment_dir / "evidence-manifest.sha256"
    linhas: list[str] = []
    for caminho in sorted(experiment_dir.rglob("*")):
        if not caminho.is_file() or caminho == alvo:
            continue
        digest = hashlib.sha256(caminho.read_bytes()).hexdigest()
        relativo = caminho.relative_to(experiment_dir).as_posix()
        linhas.append(f"{digest}  {relativo}")
    alvo.write_text("\n".join(linhas) + "\n", encoding="utf-8")
    return alvo


def write_experiment_artifacts(experiment_dir: Path, result: ExperimentResult,
                               comparison: dict, capacity: dict,
                               decision: Decision) -> None:
    """Grava report.md/report.json/aggregates/* + evidence-manifest (§24)."""
    experiment_dir = Path(experiment_dir)
    aggregates_dir = experiment_dir / "aggregates"
    aggregates_dir.mkdir(parents=True, exist_ok=True)

    report = _montar_report_dict(result, comparison, capacity, decision)

    (experiment_dir / "report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8")
    (experiment_dir / "report.md").write_text(
        _render_markdown(report), encoding="utf-8")

    # Agregados por ambiente — nomes vêm do experimento (não fixos).
    env_ids: list[str] = []
    for run in result.runs:
        if run.environment_id not in env_ids:
            env_ids.append(run.environment_id)
    for env_id in env_ids:
        dados = {
            "environment_id": env_id,
            "stats": comparison.get("stats_by_env", {}).get(env_id),
            "tps": comparison.get("tps_by_env", {}).get(env_id),
            "ladder": comparison.get("ladder_by_env", {}).get(env_id, []),
            "degradation": comparison.get("degradation_by_env", {}).get(env_id),
            "normalization": (comparison.get("normalization") or {})
            .get("per_environment", {}).get(env_id),
        }
        (aggregates_dir / f"{env_id}.json").write_text(
            json.dumps(dados, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8")

    (aggregates_dir / "comparison.json").write_text(
        json.dumps(comparison, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8")
    (aggregates_dir / "capacity.json").write_text(
        json.dumps(capacity, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8")

    _evidence_manifest(experiment_dir)


__all__ = ["write_experiment_artifacts"]

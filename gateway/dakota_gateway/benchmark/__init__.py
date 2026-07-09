"""Benchmark AIX vs Linux: orquestrador, coleta de metricas e comparacao."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class BenchmarkConfig:
    """Configuracao de benchmark."""
    benchmark_id: str = ""
    name: str = ""
    journey_id: str = ""
    dataset_name: str = ""

    # Ambientes a comparar
    environments: list[dict] = field(default_factory=list)
    # Cada ambiente: {"name": "aix", "host": "...", "user": "...", "command": "..."}

    concurrency: int = 5
    ramp_up_seconds: int = 3
    iterations: int = 3  # repeticoes para significancia estatistica
    seed: int = 0
    timeout_seconds: int = 300

    # Thresholds para PASS/WARN/FAIL
    tps_degradation_pct: float = 20.0  # WARN se TPS cair > 20%
    latency_increase_pct: float = 30.0
    error_rate_threshold_pct: float = 5.0
    screen_divergence_threshold: int = 0


@dataclass
class EnvironmentMetrics:
    """Metricas coletadas de um ambiente."""
    environment: str = ""
    host: str = ""

    # Throughput
    tps: float = 0.0
    total_ops: int = 0
    avg_latency_ms: float = 0.0
    p50_latency_ms: float = 0.0
    p95_latency_ms: float = 0.0
    p99_latency_ms: float = 0.0

    # Sistema
    cpu_user_pct: float = 0.0
    cpu_sys_pct: float = 0.0
    cpu_iowait_pct: float = 0.0
    memory_rss_mb: float = 0.0
    io_read_kbps: float = 0.0
    io_write_kbps: float = 0.0

    # Aplicacao
    lock_waits: int = 0
    deadlocks: int = 0
    validation_errors: int = 0
    timeouts: int = 0
    screen_divergences: int = 0
    total_errors: int = 0

    # Run
    duration_seconds: float = 0.0
    status: str = ""


@dataclass
class BenchmarkComparison:
    """Comparacao entre dois ambientes."""
    baseline_env: str = ""
    target_env: str = ""
    metrics: dict[str, dict] = field(default_factory=dict)
    verdict: str = ""  # PASS, WARN, FAIL
    recommendations: list[str] = field(default_factory=list)


@dataclass
class BenchmarkResult:
    """Resultado completo do benchmark."""
    benchmark_id: str = ""
    name: str = ""
    config: Optional[BenchmarkConfig] = None
    environment_metrics: list[EnvironmentMetrics] = field(default_factory=list)
    comparisons: list[BenchmarkComparison] = field(default_factory=list)
    duration_seconds: float = 0.0
    iterations_completed: int = 0
    summary: str = ""


class BenchmarkOrchestrator:
    """Orquestrador de benchmark multi-ambiente."""

    def __init__(self, db_path: str = ""):
        self.db_path = db_path

    def run(self, config: BenchmarkConfig) -> BenchmarkResult:
        """Executa benchmark completo."""
        start = time.time()
        result = BenchmarkResult(
            benchmark_id=config.benchmark_id,
            name=config.name,
            config=config,
        )

        all_env_metrics: dict[str, list[EnvironmentMetrics]] = {}

        for iteration in range(config.iterations):
            for env in config.environments:
                metrics = self._run_single(config, env, iteration)
                all_env_metrics.setdefault(env["name"], []).append(metrics)
            result.iterations_completed = iteration + 1

        # Agregar medias
        for env_name, metrics_list in all_env_metrics.items():
            aggregated = self._aggregate(metrics_list)
            result.environment_metrics.append(aggregated)

        # Comparar ambientes
        if len(result.environment_metrics) >= 2:
            baseline = result.environment_metrics[0]
            for target in result.environment_metrics[1:]:
                comparison = self._compare(baseline, target, config)
                result.comparisons.append(comparison)

        result.duration_seconds = round(time.time() - start, 1)
        result.summary = self._build_summary(result)

        return result

    def _run_single(
        self, config: BenchmarkConfig, env: dict, iteration: int
    ) -> EnvironmentMetrics:
        """Executa uma iteracao em um ambiente (simulacao/skeleton)."""
        metrics = EnvironmentMetrics(
            environment=env.get("name", ""),
            host=env.get("host", ""),
        )

        # Skeleton: no futuro, executa replay real e coleta metricas
        # Por enquanto, popula com valores placeholder para teste
        import random
        rng = random.Random(config.seed + iteration)

        metrics.tps = round(rng.uniform(8, 20), 1)
        metrics.total_ops = int(metrics.tps * rng.uniform(50, 100))
        metrics.avg_latency_ms = round(1000 / max(0.1, metrics.tps), 1)
        metrics.duration_seconds = round(metrics.total_ops / max(0.1, metrics.tps), 1)
        metrics.status = "completed"

        return metrics

    def _aggregate(self, metrics_list: list[EnvironmentMetrics]) -> EnvironmentMetrics:
        """Agrega medias de multiplas iteracoes."""
        if not metrics_list:
            return EnvironmentMetrics()

        first = metrics_list[0]
        n = len(metrics_list)

        return EnvironmentMetrics(
            environment=first.environment,
            host=first.host,
            tps=round(sum(m.tps for m in metrics_list) / n, 1),
            total_ops=sum(m.total_ops for m in metrics_list),
            avg_latency_ms=round(sum(m.avg_latency_ms for m in metrics_list) / n, 1),
            duration_seconds=round(sum(m.duration_seconds for m in metrics_list) / n, 1),
            total_errors=sum(m.total_errors for m in metrics_list),
            screen_divergences=sum(m.screen_divergences for m in metrics_list),
            status="completed",
        )

    def _compare(
        self,
        baseline: EnvironmentMetrics,
        target: EnvironmentMetrics,
        config: BenchmarkConfig,
    ) -> BenchmarkComparison:
        """Compara dois ambientes e emite veredito."""
        comp = BenchmarkComparison(
            baseline_env=baseline.environment,
            target_env=target.environment,
        )

        # Delta TPS
        tps_delta_pct = (
            round((target.tps - baseline.tps) / max(0.1, baseline.tps) * 100, 1)
        )
        # Delta latencia
        lat_delta_pct = (
            round((target.avg_latency_ms - baseline.avg_latency_ms) / max(0.1, baseline.avg_latency_ms) * 100, 1)
        )
        # Divergencias
        div_delta = target.screen_divergences - baseline.screen_divergences

        comp.metrics = {
            "tps": {
                "baseline": baseline.tps,
                "target": target.tps,
                "delta_pct": tps_delta_pct,
            },
            "avg_latency_ms": {
                "baseline": baseline.avg_latency_ms,
                "target": target.avg_latency_ms,
                "delta_pct": lat_delta_pct,
            },
            "screen_divergences": {
                "baseline": baseline.screen_divergences,
                "target": target.screen_divergences,
                "delta": div_delta,
            },
            "total_errors": {
                "baseline": baseline.total_errors,
                "target": target.total_errors,
            },
            "duration_seconds": {
                "baseline": baseline.duration_seconds,
                "target": target.duration_seconds,
            },
        }

        # Veredito
        issues = []
        if tps_delta_pct < -config.tps_degradation_pct:
            issues.append(f"TPS caiu {abs(tps_delta_pct)}% (limite: {config.tps_degradation_pct}%)")
        if lat_delta_pct > config.latency_increase_pct:
            issues.append(f"Latencia aumentou {lat_delta_pct}% (limite: {config.latency_increase_pct}%)")
        if div_delta > config.screen_divergence_threshold:
            issues.append(f"{div_delta} divergencias de tela novas")

        if not issues:
            comp.verdict = "PASS"
            comp.recommendations = ["Nenhuma degradacao detectada. Migracao aprovada."]
        elif len(issues) <= 1:
            comp.verdict = "WARN"
            comp.recommendations = issues + ["Revisar antes de aprovar migracao."]
        else:
            comp.verdict = "FAIL"
            comp.recommendations = issues + ["Migracao requer investigacao antes do deploy."]

        return comp

    def _build_summary(self, result: BenchmarkResult) -> str:
        """Constroi resumo textual."""
        lines = [f"Benchmark: {result.name}"]
        lines.append(f"Iteracoes: {result.iterations_completed}")

        for m in result.environment_metrics:
            lines.append(f"  {m.environment} ({m.host}): TPS={m.tps}, Lat={m.avg_latency_ms}ms, Erros={m.total_errors}")

        for c in result.comparisons:
            tps_delta = c.metrics["tps"]["delta_pct"]
            lines.append(f"  {c.baseline_env} vs {c.target_env}: TPS {tps_delta:+.1f}% → {c.verdict}")

        return "\n".join(lines)

    def run_and_report(self, config: BenchmarkConfig) -> dict:
        """Executa benchmark e retorna relatorio em dicionario."""
        result = self.run(config)
        return {
            "benchmark_id": result.benchmark_id,
            "name": result.name,
            "duration_seconds": result.duration_seconds,
            "iterations": result.iterations_completed,
            "environments": [
                {
                    "name": m.environment,
                    "host": m.host,
                    "tps": m.tps,
                    "avg_latency_ms": m.avg_latency_ms,
                    "duration_seconds": m.duration_seconds,
                    "errors": m.total_errors,
                    "divergences": m.screen_divergences,
                }
                for m in result.environment_metrics
            ],
            "comparisons": [
                {
                    "baseline": c.baseline_env,
                    "target": c.target_env,
                    "verdict": c.verdict,
                    "metrics": c.metrics,
                    "recommendations": c.recommendations,
                }
                for c in result.comparisons
            ],
            "summary": result.summary,
        }

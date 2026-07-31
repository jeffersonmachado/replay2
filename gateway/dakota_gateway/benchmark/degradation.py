"""Análise da escada de saturação (contrato §5.9/§18).

Detecta o ponto de degradação na escada de concorrência: o PRIMEIRO nível em
que (a) o TPS cresce menos que ``throughput_growth_min_pct`` sobre o nível
anterior (saturação), ou (b) o P95 cresce mais que ``p95_growth_max_pct``, ou
(c) a taxa de erro excede ``error_rate_max_pct``. Os critérios são
configuráveis (``DegradationCriteria``) e registrados no manifesto do
experimento.
"""
from __future__ import annotations

from dataclasses import dataclass

BOTTLENECKS = ("cpu", "memory", "disk_io", "network", "unknown")


@dataclass(frozen=True)
class DegradationCriteria:
    """Critérios configuráveis de degradação (§18) — vão ao manifesto."""

    throughput_growth_min_pct: float = 10.0
    concurrency_growth_pct: float = 50.0
    p95_growth_max_pct: float = 50.0
    error_rate_max_pct: float = 5.0


@dataclass(frozen=True)
class DegradationReport:
    """Resultado da análise da escada de carga."""

    degradation_point: int | None
    safe_operational_limit: int | None
    maximum_observed_limit: int | None
    dominant_bottleneck: str  # "cpu"|"memory"|"disk_io"|"network"|"unknown"
    recovery_seconds: float | None


def _nivel_degradado(anterior: dict, atual: dict, criterios: DegradationCriteria) -> bool:
    """True se ``atual`` degradou em relação ao nível ``anterior``."""
    conc_ant = float(anterior.get("concurrency", 0))
    conc_atu = float(atual.get("concurrency", 0))
    crescimento_conc = ((conc_atu - conc_ant) / conc_ant * 100.0) if conc_ant > 0 else 0.0

    tps_ant = float(anterior.get("tps", 0.0))
    tps_atu = float(atual.get("tps", 0.0))
    if tps_ant > 0 and crescimento_conc >= criterios.concurrency_growth_pct:
        # saturação: concorrência subiu e o TPS quase não acompanhou
        if (tps_atu - tps_ant) / tps_ant * 100.0 < criterios.throughput_growth_min_pct:
            return True

    p95_ant = float(anterior.get("p95_ms", 0.0))
    p95_atu = float(atual.get("p95_ms", 0.0))
    if p95_ant > 0:
        if (p95_atu - p95_ant) / p95_ant * 100.0 > criterios.p95_growth_max_pct:
            return True

    if float(atual.get("error_pct", 0.0)) > criterios.error_rate_max_pct:
        return True

    return False


def _gargalo_dominante(host_series: list[dict]) -> str:
    """Infere o gargalo dominante a partir das métricas de host da escada."""
    cpu_max = 0.0
    mem_max = 0.0
    disco_max = 0.0
    disco_busy_max = 0.0
    rede_max = 0.0
    for amostra in host_series or []:
        cpu_max = max(cpu_max, float(amostra.get("cpu_pct") or 0.0))
        mem_max = max(mem_max, float(amostra.get("mem_pct") or 0.0))
        disco_max = max(disco_max, float(amostra.get("disk_latency_ms") or 0.0))
        disco_busy_max = max(disco_busy_max, float(amostra.get("disk_busy_pct") or 0.0))
        rede_max = max(rede_max, float(amostra.get("net_util_pct") or 0.0))
    if cpu_max >= 90.0:
        return "cpu"
    if mem_max >= 90.0:
        return "memory"
    # disk_io por latência alta (>= 50 ms) ou por disco saturado (% tm_act
    # >= 90 — coletor AIX via iostat; ausente vira 0.0 e não dispara)
    if disco_max >= 50.0 or disco_busy_max >= 90.0:
        return "disk_io"
    if rede_max >= 90.0:
        return "network"
    return "unknown"


def analyze_ladder(level_stats: list[dict], criteria: DegradationCriteria,
                   host_series: list[dict]) -> DegradationReport:
    """Analisa a escada de carga (§5.9/§18).

    ``level_stats``: lista de dicts ``{"concurrency", "tps", "p95_ms",
    "p99_ms", "error_pct"}`` (ordenada por concorrência aqui dentro).
    ``host_series``: dicts com métricas de host por nível (só para o gargalo
    dominante). ``recovery_seconds`` fica ``None`` quando não há série de
    recuperação instrumentada (nunca é inventado).
    """
    if not level_stats:
        return DegradationReport(
            degradation_point=None,
            safe_operational_limit=None,
            maximum_observed_limit=None,
            dominant_bottleneck="unknown",
            recovery_seconds=None,
        )

    niveis = sorted(level_stats, key=lambda d: int(d["concurrency"]))
    maximo = int(niveis[-1]["concurrency"])
    degradation_point: int | None = None
    ultimo_saudavel = int(niveis[0]["concurrency"])

    anterior = niveis[0]
    for atual in niveis[1:]:
        if _nivel_degradado(anterior, atual, criteria):
            degradation_point = int(atual["concurrency"])
            break
        ultimo_saudavel = int(atual["concurrency"])
        anterior = atual

    if degradation_point is None:
        ultimo_saudavel = maximo

    return DegradationReport(
        degradation_point=degradation_point,
        safe_operational_limit=ultimo_saudavel,
        maximum_observed_limit=maximo,
        dominant_bottleneck=_gargalo_dominante(host_series),
        recovery_seconds=None,
    )

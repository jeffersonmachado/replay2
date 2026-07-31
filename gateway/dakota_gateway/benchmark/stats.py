"""Estatísticas do benchmark real (contrato §5.6/§15).

Convenções fixadas pelos testes imutáveis em ``tests/benchmark/``:

- ``percentile``: interpolação linear (método "linear" do numpy) sobre as
  amostras ORDENADAS — posição ``k = (n-1) * p/100``;
- ``compute_stats.stdev``: desvio-padrão POPULACIONAL (ddof=0), coerente com
  ``statistics.pstdev``;
- ``compute_stats.ci95_*``: intervalo normal ``mean ± 1.96 * stdev / sqrt(n)``;
- ``aggregate_samples``: pooling das amostras brutas (concatenação) — NUNCA
  média de médias quando as iterações têm tamanhos diferentes.
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from typing import Sequence


@dataclass(frozen=True)
class Stats:
    """Estatísticas descritivas de um conjunto de latências (ms)."""

    n: int
    mean: float
    p50: float
    p90: float
    p95: float
    p99: float
    max: float
    stdev: float
    cv: float
    ci95_low: float
    ci95_high: float


def percentile(sorted_samples: Sequence[float], p: float) -> float:
    """Percentil com interpolação linear (tipo numpy), ``p`` em [0, 100].

    ``sorted_samples`` deve estar ordenado; ``k = (n-1) * p/100`` e o
    resultado é ``s[floor(k)] + (k - floor(k)) * (s[ceil(k)] - s[floor(k)])``.
    """
    n = len(sorted_samples)
    if n == 0:
        raise ValueError("percentile exige ao menos uma amostra")
    if not 0.0 <= p <= 100.0:
        raise ValueError(f"percentil fora de [0, 100]: {p}")
    if n == 1:
        return float(sorted_samples[0])
    k = (n - 1) * (p / 100.0)
    low = math.floor(k)
    high = math.ceil(k)
    if low == high:
        return float(sorted_samples[low])
    frac = k - low
    return float(sorted_samples[low]) + frac * (
        float(sorted_samples[high]) - float(sorted_samples[low])
    )


def compute_stats(samples: Sequence[float]) -> Stats:
    """Estatísticas completas de ``samples`` (§5.6). ``n == 0`` → ValueError."""
    n = len(samples)
    if n == 0:
        raise ValueError("compute_stats exige ao menos uma amostra real")
    ordenadas = sorted(float(s) for s in samples)
    mean = statistics.fmean(ordenadas)
    stdev = statistics.pstdev(ordenadas)  # populacional (ddof=0)
    cv = (stdev / mean) if mean != 0.0 else 0.0
    margem = 1.96 * stdev / math.sqrt(n)
    return Stats(
        n=n,
        mean=mean,
        p50=percentile(ordenadas, 50),
        p90=percentile(ordenadas, 90),
        p95=percentile(ordenadas, 95),
        p99=percentile(ordenadas, 99),
        max=ordenadas[-1],
        stdev=stdev,
        cv=cv,
        ci95_low=mean - margem,
        ci95_high=mean + margem,
    )


def aggregate_samples(iterations: list[list[float]]) -> list[float]:
    """Pooling das amostras brutas de todas as iterações (§15).

    Concatena as amostras reais — nunca média de médias, que distorce o
    resultado quando as iterações têm tamanhos diferentes.
    """
    pooled: list[float] = []
    for amostras in iterations:
        pooled.extend(float(s) for s in amostras)
    return pooled

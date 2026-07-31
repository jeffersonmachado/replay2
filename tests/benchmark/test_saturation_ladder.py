"""§5.9/§18 — Escada de saturação (analyze_ladder).

Série controlada: níveis 1, 5, 10, 20, 40, 80; o TPS satura a partir de 20
(cresce < 10% de 20→40) e o P95 explode em 40 (+114% sobre o nível anterior).

Interpretações documentadas (o contrato não fixa o critério exato):

- ``degradation_point``: PRIMEIRO nível onde (a) o crescimento de TPS sobre o
  nível anterior fica abaixo de ``throughput_growth_min_pct`` (saturação), OU
  (b) o crescimento de P95 excede ``p95_growth_max_pct``, OU (c) a taxa de
  erro excede ``error_rate_max_pct``;
- ``safe_operational_limit``: maior nível testado ANTES do ponto de
  degradação (o último nível saudável);
- ``maximum_observed_limit``: maior nível efetivamente testado na escada;
- ``host_series``: dicts ``{"concurrency", "cpu_pct"}`` — usados só para o
  gargalo dominante; este teste não fixa o gargalo, apenas o valida no
  conjunto de valores do contrato.

Com os defaults (``DegradationCriteria()``), o nível 40 falha nos dois
critérios (TPS +2.5% < 10% e P95 +114% > 50%) → ponto de degradação 40.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "gateway"))

from dakota_gateway.benchmark.degradation import (  # noqa: E402
    DegradationCriteria,
    analyze_ladder,
)

NIVEIS = [1, 5, 10, 20, 40, 80]

# TPS satura em 20; P95 explode em 40.
LEVEL_STATS = [
    {"concurrency": 1, "tps": 10.0, "p95_ms": 50.0, "p99_ms": 80.0, "error_pct": 0.0},
    {"concurrency": 5, "tps": 50.0, "p95_ms": 55.0, "p99_ms": 90.0, "error_pct": 0.0},
    {"concurrency": 10, "tps": 100.0, "p95_ms": 60.0, "p99_ms": 100.0, "error_pct": 0.0},
    {"concurrency": 20, "tps": 200.0, "p95_ms": 70.0, "p99_ms": 120.0, "error_pct": 0.0},
    {"concurrency": 40, "tps": 205.0, "p95_ms": 150.0, "p99_ms": 300.0, "error_pct": 0.0},
    {"concurrency": 80, "tps": 206.0, "p95_ms": 400.0, "p99_ms": 900.0, "error_pct": 0.0},
]

HOST_SERIES = [
    {"concurrency": nivel, "cpu_pct": cpu}
    for nivel, cpu in zip(NIVEIS, [10.0, 35.0, 55.0, 80.0, 97.0, 99.0])
]

_GARGALOS_VALIDOS = {"cpu", "memory", "disk_io", "network", "unknown"}


class TestSaturationLadder(unittest.TestCase):
    """Ponto de degradação, limite seguro e limite máximo observado."""

    def test_criterios_default(self) -> None:
        relatorio = analyze_ladder(LEVEL_STATS, DegradationCriteria(), HOST_SERIES)
        self.assertEqual(40, relatorio.degradation_point)
        self.assertEqual(20, relatorio.safe_operational_limit)
        self.assertEqual(80, relatorio.maximum_observed_limit)
        self.assertIn(relatorio.dominant_bottleneck, _GARGALOS_VALIDOS)

    def test_criterios_configuraveis_mudam_o_ponto(self) -> None:
        # Com tolerância maior (TPS mínimo 1% e P95 máximo +200%), o nível 40
        # passa (TPS +2.5% >= 1%, P95 +114% <= 200%) e a degradação aparece
        # só em 80 (TPS +0.49% < 1%): safe=40, max=80.
        criterios = DegradationCriteria(
            throughput_growth_min_pct=1.0,
            concurrency_growth_pct=50.0,
            p95_growth_max_pct=200.0,
            error_rate_max_pct=5.0,
        )
        relatorio = analyze_ladder(LEVEL_STATS, criterios, HOST_SERIES)
        self.assertEqual(80, relatorio.degradation_point)
        self.assertEqual(40, relatorio.safe_operational_limit)
        self.assertEqual(80, relatorio.maximum_observed_limit)

    def test_taxa_de_erro_derruba_nivel(self) -> None:
        # Erros explodem já no nível 10 (6% > 5% default) → degradação em 10.
        stats_com_erro = [
            {"concurrency": 1, "tps": 10.0, "p95_ms": 50.0, "p99_ms": 80.0, "error_pct": 0.0},
            {"concurrency": 5, "tps": 50.0, "p95_ms": 55.0, "p99_ms": 90.0, "error_pct": 0.5},
            {"concurrency": 10, "tps": 100.0, "p95_ms": 60.0, "p99_ms": 100.0, "error_pct": 6.0},
            {"concurrency": 20, "tps": 200.0, "p95_ms": 70.0, "p99_ms": 120.0, "error_pct": 8.0},
        ]
        relatorio = analyze_ladder(stats_com_erro, DegradationCriteria(), HOST_SERIES)
        self.assertEqual(10, relatorio.degradation_point)
        self.assertEqual(5, relatorio.safe_operational_limit)
        self.assertEqual(20, relatorio.maximum_observed_limit)


if __name__ == "__main__":
    unittest.main()

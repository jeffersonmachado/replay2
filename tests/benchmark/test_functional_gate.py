"""§5.8/§16 — Porta funcional ANTES de desempenho.

Mesmo que o ambiente alvo (Linux) apresente estatísticas de latência MELHORES
que o baseline (AIX), uma única divergência funcional (``functional_ok=False``)
derruba o veredito para ``FAIL`` — e não pode haver recomendação de migração.

As ``Stats`` abaixo são construídas à mão: o alvo é estritamente melhor em
todos os percentis (latências menores). Se a porta funcional não fosse
avaliada primeiro, o veredito seria PASS — o teste garante que isso nunca
acontece.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "gateway"))

from dakota_gateway.benchmark.decision import decide  # noqa: E402
from dakota_gateway.benchmark.degradation import DegradationReport  # noqa: E402
from dakota_gateway.benchmark.stats import Stats  # noqa: E402


def _stats(mean: float, p50: float, p90: float, p95: float, p99: float,
           maximo: float, stdev: float) -> Stats:
    return Stats(
        n=1000,
        mean=mean,
        p50=p50, p90=p90, p95=p95, p99=p99,
        max=maximo,
        stdev=stdev,
        cv=stdev / mean,
        ci95_low=mean * 0.95,
        ci95_high=mean * 1.05,
    )


# Baseline AIX: mais lento. Alvo Linux: estritamente melhor em tudo.
STATS_AIX = _stats(100.0, 95.0, 150.0, 180.0, 250.0, 300.0, 40.0)
STATS_LINUX = _stats(60.0, 55.0, 90.0, 110.0, 150.0, 200.0, 20.0)


def _degradacao_ok() -> DegradationReport:
    return DegradationReport(
        degradation_point=None,
        safe_operational_limit=80,
        maximum_observed_limit=80,
        dominant_bottleneck="unknown",
        recovery_seconds=None,
    )


class TestFunctionalGate(unittest.TestCase):
    """Divergência funcional → FAIL mesmo com alvo mais rápido."""

    def test_divergencia_funcional_com_alvo_melhor_e_fail(self) -> None:
        decisao = decide(
            functional_ok=False,
            functional_diffs=[{
                "journey_id": "j-pedido",
                "step_id": "confirmar",
                "baseline_sig": "a" * 16,
                "target_sig": "b" * 16,
            }],
            stats_by_env={"aix": STATS_AIX, "linux": STATS_LINUX},
            samples_complete=True,
            collectors_ok=True,
            ci_acceptable=True,
            degradation=_degradacao_ok(),
            normalization_status="OK",
        )
        self.assertEqual("FAIL", decisao.verdict)
        # sem recomendação, ou certamente sem recomendar migração
        if decisao.recommendation is not None:
            self.assertNotIn("migr", decisao.recommendation.lower())
        self.assertNotEqual("PASS", decisao.verdict)
        self.assertNotEqual("WARN", decisao.verdict)
        # a divergência deve aparecer nas razões
        self.assertTrue(decisao.reasons, "decisão FAIL sem razões registradas")


if __name__ == "__main__":
    unittest.main()

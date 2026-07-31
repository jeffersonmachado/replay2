"""§5.3 — Sem amostras reais válidas, o veredito NUNCA é PASS.

Cenário: a execução não produziu amostras válidas de aplicação nem de host
(o adapter devolveu listas vazias — equivalente a ``samples_complete=False``
e ``stats_by_env`` vazio, pois ``compute_stats`` não existe para n=0).

Interpretação documentada (contrato §16/§20 em ``decision.decide``):
- "sem amostras válidas" é representado por ``samples_complete=False`` +
  ``stats_by_env={}`` (não há Stats sem amostras — n==0 é ValueError, §5.6);
- nesse caso ``decide`` deve retornar ``verdict == "INCONCLUSIVE"`` e
  ``recommendation is None`` — mesmo que TODO o resto esteja perfeito
  (funcional ok, coletores ok, CI aceitável, degradação ausente).
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "gateway"))

from dakota_gateway.benchmark.decision import decide  # noqa: E402
from dakota_gateway.benchmark.degradation import DegradationReport  # noqa: E402


def _degradacao_vazia() -> DegradationReport:
    return DegradationReport(
        degradation_point=None,
        safe_operational_limit=None,
        maximum_observed_limit=None,
        dominant_bottleneck="unknown",
        recovery_seconds=None,
    )


class TestNoPassWithoutRealMetrics(unittest.TestCase):
    """INCONCLUSIVE nunca vira PASS; sem amostras reais não há recomendação."""

    def test_sem_amostras_verdict_inconclusive(self) -> None:
        decisao = decide(
            functional_ok=True,
            functional_diffs=[],
            stats_by_env={},          # adapter devolveu listas vazias
            samples_complete=False,
            collectors_ok=True,
            ci_acceptable=True,
            degradation=_degradacao_vazia(),
            normalization_status="OK",
        )
        self.assertEqual("INCONCLUSIVE", decisao.verdict)
        self.assertIsNone(decisao.recommendation)
        self.assertNotEqual("PASS", decisao.verdict)

    def test_sem_amostras_e_coletores_falhos_tambem_inconclusive(self) -> None:
        decisao = decide(
            functional_ok=True,
            functional_diffs=[],
            stats_by_env={},
            samples_complete=False,
            collectors_ok=False,      # host_metrics também veio vazio
            ci_acceptable=False,
            degradation=_degradacao_vazia(),
            normalization_status="NORMALIZATION_INCONCLUSIVE",
        )
        self.assertEqual("INCONCLUSIVE", decisao.verdict)
        self.assertIsNone(decisao.recommendation)


if __name__ == "__main__":
    unittest.main()

"""FASE 3 — Clock skew entre ambientes (AIX × Linux): gate configurável.

Caso real (MIG24, experimento v7): o AIX estava ~171 s ATRASADO em relação
ao orquestrador e o Linux ~1,36 s adiantado. A janela de coleta já é
compensada na origem (o script remoto mede ``clock_offset_ms`` e desloca a
janela — correção comprovável, registrada no execution-result.json da run),
mas a comparação não verificava o tamanho do skew.

Regras fixadas por estes testes (gate padrão: 1 segundo):

- offset medido e |offset| <= gate → sem efeito;
- offset medido e |offset| > gate → a correção aplicada na coleta é
  comprovável (offset registrado) → veredito máximo WARN, com a evidência
  do skew e da correção na razão e no relatório;
- amostras de host VÁLIDAS sem offset medido → a correção NÃO é
  comprovável → INCONCLUSIVE (nunca comparação temporal sem prova de
  alinhamento de relógio);
- o gate é configurável (``max_clock_skew_ms`` em ``build_comparison``).

Estes testes DEVEM FALHAR antes da correção e PASSAR depois dela.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "gateway"))

from dakota_gateway.benchmark.comparison import (  # noqa: E402
    build_comparison,
    build_decision,
)
from dakota_gateway.benchmark.models import (  # noqa: E402
    EnvironmentRunResult,
    ExperimentResult,
    OperationSample,
)

BASELINE = "aix-power"
TARGET = "linux-x86"


def _amostra(env: str, idx: int) -> OperationSample:
    ns0 = 1_000_000_000 + idx * 20_000_000
    return OperationSample(
        experiment_id="exp-skew", environment_id=env, iteration=1,
        concurrency=1, virtual_user_id="vu-1", journey_id="j",
        step_id=f"ev-{idx}", phase="MEASUREMENT", started_ns=ns0,
        finished_ns=ns0 + 10_000_000, latency_ms=10.0,
        success=True, timeout=False, functional_divergence=False,
        error_code=None, screen_sig_checked=True,
        expected_screen_sig="sha256:x", observed_screen_sig="sha256:x")


def _host_completo(ts_ms: int) -> dict:
    return {"ts_ms": ts_ms, "cpu_pct": 12.0, "mem_pct": 30.0,
            "swap_pct": 0.5, "disk_latency_ms": 2.0, "iops": 40.0,
            "load1": 0.4, "net_rx_kbs": 120.0, "net_tx_kbs": 90.0}


def _montar(tmp: Path, *, offset_base: int, offset_alvo: int,
            medido: bool = True) -> ExperimentResult:
    for nome in ("host-base.jsonl", "host-alvo.jsonl"):
        (tmp / nome).write_text(
            "".join(json.dumps(_host_completo(1000 + i * 5000)) + "\n"
                    for i in range(4)), encoding="utf-8")
    runs = []
    for env, nome, offset in ((BASELINE, "host-base.jsonl", offset_base),
                              (TARGET, "host-alvo.jsonl", offset_alvo)):
        run = EnvironmentRunResult(
            environment_id=env, iteration=1, concurrency=1,
            status="COMPLETED",
            samples=[_amostra(env, i) for i in range(20)],
            host_samples_path=str(tmp / nome))
        run.host_clock_offset_ms = offset
        run.host_clock_offset_measured = medido
        runs.append(run)
    return ExperimentResult(
        contract_sha256="c" * 64, status="COMPLETED", runs=runs)


class TestClockSkewGate(unittest.TestCase):
    """Gate de skew (padrão 1 s) sobre o offset medido na coleta."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def test_skew_dentro_do_limite_nao_afeta_veredito(self) -> None:
        resultado = _montar(self.tmp, offset_base=300, offset_alvo=-400)
        comparison = build_comparison(
            resultado, baseline_env=BASELINE, target_env=TARGET)
        for env in (BASELINE, TARGET):
            skew = comparison["clock_skew"][env]
            self.assertTrue(skew["measured"])
            self.assertTrue(skew["within_gate"])
        decision = build_decision(resultado, comparison)
        self.assertEqual("PASS", decision.verdict)

    def test_skew_acima_do_limite_com_correcao_comprovavel_vira_warn(
            self) -> None:
        """Caso real v7 (AIX -171 s): offset medido e registrado → correção
        de janela comprovável → veredito máximo WARN, nunca PASS, com a
        evidência do skew na razão."""
        resultado = _montar(self.tmp, offset_base=-171_601,
                            offset_alvo=1_360)
        comparison = build_comparison(
            resultado, baseline_env=BASELINE, target_env=TARGET)
        skew = comparison["clock_skew"][BASELINE]
        self.assertTrue(skew["measured"])
        self.assertFalse(skew["within_gate"])
        self.assertTrue(skew["corrected"])
        self.assertEqual(171_601, skew["max_abs_offset_ms"])
        decision = build_decision(resultado, comparison)
        self.assertEqual("WARN", decision.verdict)
        razoes = " ".join(decision.reasons)
        self.assertIn("clock skew", razoes)
        self.assertIn(BASELINE, razoes)
        # a correção é explicitada — não é um WARN mudo
        self.assertIn("corre", razoes)

    def test_skew_nao_medido_com_amostras_validas_inconclusive(self) -> None:
        """Amostras de host válidas mas offset NÃO medido → correção não
        comprovável → INCONCLUSIVE."""
        resultado = _montar(self.tmp, offset_base=0, offset_alvo=0,
                            medido=False)
        comparison = build_comparison(
            resultado, baseline_env=BASELINE, target_env=TARGET)
        decision = build_decision(resultado, comparison)
        self.assertEqual("INCONCLUSIVE", decision.verdict)
        self.assertIsNone(decision.recommendation)
        self.assertIn("clock", " ".join(decision.reasons))

    def test_gate_configuravel(self) -> None:
        """Gate relaxado explicitamente (ex.: 300 s) → o mesmo skew de
        171 s não gera ressalva."""
        resultado = _montar(self.tmp, offset_base=-171_601,
                            offset_alvo=1_360)
        comparison = build_comparison(
            resultado, baseline_env=BASELINE, target_env=TARGET,
            max_clock_skew_ms=300_000)
        self.assertTrue(comparison["clock_skew"][BASELINE]["within_gate"])
        decision = build_decision(resultado, comparison)
        self.assertEqual("PASS", decision.verdict)


if __name__ == "__main__":
    unittest.main()

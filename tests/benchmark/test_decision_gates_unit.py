"""Portas de decisão endurecidas após o smoke real (cap13-smoke-v1).

O smoke real completo revelou 3 furos que estes testes fecham:

1. PASS com ZERO amostras de host em um ambiente: ``collectors_ok`` exigia
   apenas ``host_samples_path`` não-vazio — agora exige >=1 amostra VÁLIDA
   por ambiente (linha JSON que não seja o marcador de indisponibilidade);
2. divergência funcional sem evidência: os ``functional_diffs`` saíam com
   ``baseline_sig=""``/``target_sig="divergent"`` — agora carregam as
   assinaturas esperada/observadas registradas na amostra;
3. equivalência "comprovada" sem nenhuma comparação de tela: alvo com
   amostras mas nenhum ``screen_sig_checked`` → INCONCLUSIVE
   (``functional_evidence_missing``), nunca PASS.

Os ``ExperimentResult`` são fabricados diretamente (2 ambientes, runs
COMPLETED, arquivos de host samples em tmp) — sem executor e sem rede.
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


def _amostra(env: str, idx: int, *, divergence: bool = False,
             checked: bool = True, expected: str = "sha256:esperada",
             observed: str = "sha256:esperada") -> OperationSample:
    ns0 = 1_000_000_000 + idx * 20_000_000
    return OperationSample(
        experiment_id="exp-gates", environment_id=env, iteration=1,
        concurrency=1, virtual_user_id="vu-1", journey_id="j-pedido",
        step_id=f"ev-{idx}", phase="MEASUREMENT", started_ns=ns0,
        finished_ns=ns0 + 10_000_000, latency_ms=10.0,
        success=True, timeout=False, functional_divergence=divergence,
        error_code=None, screen_sig_checked=checked,
        expected_screen_sig=expected, observed_screen_sig=observed)


def _run(env: str, host_path: Path, amostras: list) -> EnvironmentRunResult:
    return EnvironmentRunResult(
        environment_id=env, iteration=1, concurrency=1, status="COMPLETED",
        samples=amostras, host_samples_path=str(host_path))


def _host_file(diretorio: Path, nome: str, linhas: list[dict]) -> Path:
    """Grava um host-samples.jsonl com as linhas informadas."""
    caminho = diretorio / nome
    caminho.write_text(
        "".join(json.dumps(l) + "\n" for l in linhas), encoding="utf-8")
    return caminho


class TestDecisionGates(unittest.TestCase):
    """As 4 portas exercitadas de ponta a ponta (comparison + decision)."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def _decide(self, result: ExperimentResult):
        comparison = build_comparison(
            result, baseline_env=BASELINE, target_env=TARGET)
        decision = build_decision(result, comparison)
        return comparison, decision

    def test_host_samples_vazias_num_ambiente_inconclusive(self) -> None:
        """Furo 1: path não-vazio sem amostra válida NÃO comprova coleta."""
        host_base = _host_file(self.tmp, "host-base.jsonl",
                               [{"ts_ms": 1, "cpu_pct": 12.5}])
        # alvo: apenas o marcador de indisponibilidade — zero amostras válidas
        host_alvo = _host_file(self.tmp, "host-alvo.jsonl",
                               [{"available": False, "reason": "ssh_failed"}])
        result = ExperimentResult(
            contract_sha256="c" * 64, status="COMPLETED", runs=[
                _run(BASELINE, host_base, [_amostra(BASELINE, i) for i in range(20)]),
                _run(TARGET, host_alvo, [_amostra(TARGET, i) for i in range(20)]),
            ])
        _, decision = self._decide(result)
        self.assertEqual("INCONCLUSIVE", decision.verdict)
        self.assertIsNone(decision.recommendation)
        razoes = " ".join(decision.reasons)
        self.assertIn("host_metrics", razoes)
        self.assertIn(TARGET, razoes)

    def test_divergencia_funcional_fail_com_assinaturas_de_evidencia(self) -> None:
        """Furo 2: o diff funcional carrega as sigs esperada/observada."""
        host_base = _host_file(self.tmp, "host-base.jsonl",
                               [{"ts_ms": 1, "cpu_pct": 12.5}])
        host_alvo = _host_file(self.tmp, "host-alvo.jsonl",
                               [{"ts_ms": 1, "cpu_pct": 30.0}])
        alvo = [_amostra(TARGET, i) for i in range(20)]
        alvo[7] = _amostra(TARGET, 7, divergence=True,
                           expected="sha256:esperada",
                           observed="sha256:observada-outra")
        result = ExperimentResult(
            contract_sha256="c" * 64, status="COMPLETED", runs=[
                _run(BASELINE, host_base, [_amostra(BASELINE, i) for i in range(20)]),
                _run(TARGET, host_alvo, alvo),
            ])
        comparison, decision = self._decide(result)
        self.assertEqual("FAIL", decision.verdict)
        self.assertIsNone(decision.recommendation)
        razoes = " ".join(decision.reasons)
        self.assertIn("j-pedido", razoes)
        self.assertIn("ev-7", razoes)
        # evidência auditável: assinaturas reais no diff, não strings mudas
        diff = comparison["functional_diffs"][0]
        self.assertEqual("sha256:esperada", diff["baseline_sig"])
        self.assertEqual("sha256:observada-outra", diff["target_sig"])

    def test_sem_evidencia_funcional_no_alvo_inconclusive(self) -> None:
        """Furo 3: amostras sem comparação de tela → equivalência não
        comprovada (INCONCLUSIVE), mesmo com todo o resto perfeito."""
        host_base = _host_file(self.tmp, "host-base.jsonl",
                               [{"ts_ms": 1, "cpu_pct": 12.5}])
        host_alvo = _host_file(self.tmp, "host-alvo.jsonl",
                               [{"ts_ms": 1, "cpu_pct": 30.0}])
        result = ExperimentResult(
            contract_sha256="c" * 64, status="COMPLETED", runs=[
                _run(BASELINE, host_base, [_amostra(BASELINE, i) for i in range(20)]),
                # alvo: NENHUMA amostra com screen_sig_checked
                _run(TARGET, host_alvo,
                     [_amostra(TARGET, i, checked=False, expected="",
                               observed="") for i in range(20)]),
            ])
        comparison, decision = self._decide(result)
        self.assertEqual(0, comparison["functional_evidence"][TARGET])
        self.assertGreater(comparison["functional_evidence"][BASELINE], 0)
        self.assertEqual("INCONCLUSIVE", decision.verdict)
        self.assertIsNone(decision.recommendation)
        self.assertIn("functional_evidence_missing", " ".join(decision.reasons))

    def test_caminho_feliz_pass(self) -> None:
        """Tudo comprovado: sigs checadas sem divergência, host válidas nos
        dois ambientes com TODOS os grupos essenciais cobertos, clock offset
        medido e dentro do gate, latências constantes (CI aceitável) → PASS.

        FASE 3: o "caminho feliz" exige cobertura completa de coletores
        (sem rede/memória/disco o gargalo não é declarável → INCONCLUSIVE)
        e offset de relógio medido (sem ele a correção da janela temporal
        não é comprovável → INCONCLUSIVE)."""
        completa = {"ts_ms": 1, "cpu_pct": 12.5, "mem_pct": 30.0,
                    "swap_pct": 0.5, "disk_latency_ms": 2.0, "iops": 40.0,
                    "disk_busy_pct": 5.0, "load1": 0.4,
                    "net_rx_kbs": 120.0, "net_tx_kbs": 90.0}
        host_base = _host_file(self.tmp, "host-base.jsonl", [dict(completa)])
        host_alvo = _host_file(self.tmp, "host-alvo.jsonl",
                               [dict(completa, cpu_pct=30.0)])
        run_base = _run(BASELINE, host_base,
                        [_amostra(BASELINE, i) for i in range(20)])
        run_alvo = _run(TARGET, host_alvo,
                        [_amostra(TARGET, i) for i in range(20)])
        for run in (run_base, run_alvo):
            run.host_clock_offset_ms = 0
            run.host_clock_offset_measured = True
        result = ExperimentResult(
            contract_sha256="c" * 64, status="COMPLETED",
            runs=[run_base, run_alvo])
        comparison, decision = self._decide(result)
        self.assertEqual(20, comparison["functional_evidence"][TARGET])
        self.assertEqual("PASS", decision.verdict)
        self.assertIsNotNone(decision.recommendation)


if __name__ == "__main__":
    unittest.main()

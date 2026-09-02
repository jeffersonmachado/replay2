"""FASE 4 — Cobertura da verificação funcional e wording honesto.

Problemas corrigidos (caso real v7):

1. a recomendação WARN dizia "Equivalência funcional comprovada" MESMO com
   ``functional_basis="per_env"`` (datasets divergentes) — contradição com o
   próprio relatório, que registra "paridade de dados NÃO comprovada";
2. cobertura da verificação funcional: checkpoints com assinatura esperada
   que NÃO foram checados (engine indisponível, sessão sem resposta) não
   podem passar despercebidos — cobertura < 100% sem exceções auditadas →
   INCONCLUSIVE; com exceções auditadas (``checkpoint_exceptions`` com
   ``reason``) → veredito máximo WARN;
3. evidência funcional ÚNICA (1 verificação de tela) não aprova equivalência
   → INCONCLUSIVE;
4. o relatório diferencia os estados funcionais (``divergente`` /
   ``equivalencia_comprovada`` / ``sem_divergencias_cobertura_parcial`` /
   ``paridade_nao_comprovada``) e NUNCA diz "OK"/"equivalente" fora de
   ``equivalencia_comprovada``.

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
from dakota_gateway.benchmark.report import (  # noqa: E402
    write_experiment_artifacts,
)

BASELINE = "aix-power"
TARGET = "linux-x86"


def _amostra(env: str, idx: int, *, basis: str = "shared",
             checked: bool = True) -> OperationSample:
    ns0 = 1_000_000_000 + idx * 20_000_000
    return OperationSample(
        experiment_id="exp-func", environment_id=env, iteration=1,
        concurrency=1, virtual_user_id="vu-1", journey_id="j-pedido",
        step_id=f"ev-{idx}", phase="MEASUREMENT", started_ns=ns0,
        finished_ns=ns0 + 10_000_000, latency_ms=10.0,
        success=True, timeout=False, functional_divergence=False,
        error_code=None, screen_sig_checked=checked,
        expected_screen_sig="sha256:x", observed_screen_sig="sha256:x",
        screen_check_basis=basis)


def _host_completo(ts_ms: int) -> dict:
    return {"ts_ms": ts_ms, "cpu_pct": 12.0, "mem_pct": 30.0,
            "swap_pct": 0.5, "disk_latency_ms": 2.0, "iops": 40.0,
            "disk_busy_pct": 5.0, "load1": 0.4,
            "net_rx_kbs": 120.0, "net_tx_kbs": 90.0}


def _resultado(tmp: Path, *, n_amostras: int = 20, basis: str = "shared",
               executados: int | None = 20, checados: int | None = 20,
               excecoes: list | None = None,
               divergente: bool = False) -> ExperimentResult:
    for nome in ("host-base.jsonl", "host-alvo.jsonl"):
        (tmp / nome).write_text(
            "".join(json.dumps(_host_completo(1000 + i * 5000)) + "\n"
                    for i in range(4)), encoding="utf-8")
    runs = []
    for env, nome in ((BASELINE, "host-base.jsonl"),
                      (TARGET, "host-alvo.jsonl")):
        amostras = [_amostra(env, i, basis=basis) for i in range(n_amostras)]
        if divergente and env == TARGET:
            amostras[3] = OperationSample(
                experiment_id="exp-func", environment_id=env, iteration=1,
                concurrency=1, virtual_user_id="vu-1", journey_id="j-pedido",
                step_id="ev-3", phase="MEASUREMENT",
                started_ns=1_000_000_000 + 3 * 20_000_000,
                finished_ns=1_000_000_000 + 3 * 20_000_000 + 10_000_000,
                latency_ms=10.0, success=True, timeout=False,
                functional_divergence=True, error_code=None,
                screen_sig_checked=True, expected_screen_sig="sha256:x",
                observed_screen_sig="sha256:y")
        run = EnvironmentRunResult(
            environment_id=env, iteration=1, concurrency=1,
            status="COMPLETED", samples=amostras,
            host_samples_path=str(tmp / nome))
        run.host_clock_offset_ms = 0
        run.host_clock_offset_measured = True
        run.checkpoints_executed = executados
        run.checkpoints_checked = checados
        run.checkpoint_exceptions = excecoes
        runs.append(run)
    return ExperimentResult(
        contract_sha256="c" * 64, status="COMPLETED", runs=runs)


class TestCoberturaFuncional(unittest.TestCase):
    """Cobertura 100% da verificação funcional é exigida no alvo."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def _decidir(self, resultado: ExperimentResult):
        comparison = build_comparison(
            resultado, baseline_env=BASELINE, target_env=TARGET)
        return comparison, build_decision(resultado, comparison)

    def test_cobertura_completa_nao_bloqueia(self) -> None:
        comparison, decision = self._decidir(_resultado(self.tmp))
        cov = comparison["functional_coverage_by_env"][TARGET]
        self.assertEqual(1.0, cov["coverage"])
        self.assertEqual("PASS", decision.verdict)

    def test_cobertura_parcial_sem_excecao_inconclusive(self) -> None:
        """15 de 20 checkpoints checados, SEM exceção auditada → INCONCLUSIVE."""
        comparison, decision = self._decidir(_resultado(
            self.tmp, executados=20, checados=15))
        self.assertAlmostEqual(
            0.75, comparison["functional_coverage_by_env"][TARGET]["coverage"])
        self.assertEqual("INCONCLUSIVE", decision.verdict)
        self.assertIsNone(decision.recommendation)
        self.assertIn("cobertura", " ".join(decision.reasons))

    def test_cobertura_parcial_com_excecoes_auditadas_warn_maximo(self) -> None:
        """Checkpoints não checados COM razão auditada (ex.: engine
        indisponível) → veredito máximo WARN, nunca PASS."""
        excecoes = [{"step_id": f"ev-{i}", "journey_id": "j-pedido",
                     "reason": "terminal_engine_unavailable"}
                    for i in range(5)]
        comparison, decision = self._decidir(_resultado(
            self.tmp, executados=20, checados=15, excecoes=excecoes))
        self.assertEqual("WARN", decision.verdict)
        razoes = " ".join(decision.reasons)
        self.assertIn("cobertura", razoes)
        self.assertIn("auditada", razoes)

    def test_cobertura_nao_registrada_nao_bloqueia(self) -> None:
        """Artefato antigo sem contadores (None) → sem gate de cobertura,
        mas o relatório explicita que não foi registrado."""
        comparison, decision = self._decidir(_resultado(
            self.tmp, executados=None, checados=None))
        cov = comparison["functional_coverage_by_env"][TARGET]
        self.assertFalse(cov["registrado"])
        self.assertIsNone(cov["coverage"])
        self.assertEqual("PASS", decision.verdict)

    def test_evidencia_unica_nao_aprova(self) -> None:
        """UMA verificação de tela no alvo não aprova equivalência."""
        comparison, decision = self._decidir(_resultado(self.tmp, n_amostras=1))
        self.assertEqual(1, comparison["functional_evidence"][TARGET])
        self.assertEqual("INCONCLUSIVE", decision.verdict)
        self.assertIn("evidência", " ".join(decision.reasons))


class TestWordingPerEnv(unittest.TestCase):
    """per_env NUNCA é "equivalente"/"comprovada" — nem na recomendação,
    nem no relatório (contradição real do v7)."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def test_recomendacao_per_env_nao_diz_comprovada(self) -> None:
        resultado = _resultado(self.tmp, basis="env")
        comparison = build_comparison(
            resultado, baseline_env=BASELINE, target_env=TARGET)
        self.assertEqual("per_env", comparison["functional_basis"])
        decision = build_decision(resultado, comparison)
        self.assertEqual("WARN", decision.verdict)
        recomendacao = decision.recommendation or ""
        self.assertNotIn("Equivalência funcional comprovada", recomendacao)
        self.assertIn("NÃO", recomendacao)

    def test_relatorio_diferencia_estados_funcionais(self) -> None:
        cenarios = {
            "equivalencia_comprovada": _resultado(self.tmp),
            "paridade_nao_comprovada": _resultado(self.tmp, basis="env"),
            "divergente": _resultado(self.tmp, divergente=True),
            "sem_divergencias_cobertura_parcial": _resultado(
                self.tmp, executados=20, checados=15,
                excecoes=[{"step_id": "ev-0", "journey_id": "j-pedido",
                           "reason": "terminal_engine_unavailable"}]),
        }
        for status_esperado, resultado in cenarios.items():
            comparison = build_comparison(
                resultado, baseline_env=BASELINE, target_env=TARGET)
            decision = build_decision(resultado, comparison)
            with tempfile.TemporaryDirectory() as tmp2:
                write_experiment_artifacts(
                    Path(tmp2), resultado, comparison, {}, decision)
                md = (Path(tmp2) / "report.md").read_text(encoding="utf-8")
                rep = json.loads(
                    (Path(tmp2) / "report.json").read_text(encoding="utf-8"))
            with self.subTest(status=status_esperado):
                self.assertEqual(status_esperado,
                                 rep["functional_validation"]["status"])
                # o md NUNCA diz "OK"/"equivalente" fora de comprovada
                if status_esperado != "equivalencia_comprovada":
                    self.assertNotIn("Equivalência funcional: OK", md)
                    self.assertNotIn("Equivalência funcional comprovada",
                                     md)
                else:
                    self.assertIn("COMPROVADA", md)


class TestCheckpointLogNoExecutor(unittest.TestCase):
    """A cobertura é REAL: o adaptador registra cada checkpoint (executado,
    checado, razão da não-checação) e o executor estampa os deltas da fase
    MEASUREMENT na run — que alimentam o gate da decisão."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def test_executor_estampa_cobertura_da_fase_measurement(self) -> None:
        from dakota_gateway.benchmark.contract import (
            StopConditions, ThinkTimeProfile, create_contract)
        from dakota_gateway.benchmark.executor import BenchmarkExecutor

        class _AdapterCheckpoints:
            """Fake: 2 checkpoints por passada MEASUREMENT — 1 checado,
            1 não checado com razão auditada."""

            def __init__(self, env_id: str) -> None:
                self.environment_id = env_id
                self.journey_incompletions: list = []
                self.checkpoint_log: list = []
                # offset de relógio medido na coleta (como o adaptador real)
                self.host_metrics_status = {"available": True, "attempts": 1,
                                            "clock_offset_ms": 0}

            def set_iteration_context(self, iteration: int,
                                      concurrency: int) -> None:
                pass

            def preflight(self) -> dict:
                return {"ok": True}

            def prepare_dataset(self, dataset_ref: dict) -> dict:
                return {"ok": True}

            def start_session(self, virtual_user_id: str) -> str:
                return f"h-{virtual_user_id}"

            def execute_journey(self, session_handle: str, journey: dict,
                                *, phase: str) -> list:
                self.checkpoint_log.append({
                    "phase": phase, "journey_id": "j", "step_id": "s1",
                    "checked": True, "reason": ""})
                self.checkpoint_log.append({
                    "phase": phase, "journey_id": "j", "step_id": "s2",
                    "checked": False,
                    "reason": "terminal_engine_unavailable"})
                ns0 = 1_000_000_000
                return [OperationSample(
                    experiment_id="exp-chk", environment_id=self.environment_id,
                    iteration=1, concurrency=1, virtual_user_id="vu-1",
                    journey_id="j", step_id=f"s{i}", phase=phase,
                    started_ns=ns0 + i * 10_000_000,
                    finished_ns=ns0 + (i + 1) * 10_000_000, latency_ms=10.0,
                    success=True, timeout=False, functional_divergence=False,
                    error_code=None, screen_sig_checked=True)
                    for i in (1, 2)]

            def stop_session(self, session_handle: str) -> None:
                pass

            def collect_application_metrics(self) -> dict:
                return {"available": True}

            def collect_host_metrics(self, from_ms: int, to_ms: int) -> list:
                return [dict(_host_completo(from_ms))]

            def collect_database_metrics(self) -> dict:
                return {"available": False,
                        "reason": "collector_not_supported"}

            def cleanup(self) -> None:
                pass

        contrato = create_contract(
            experiment_id="exp-chk",
            journey_set_sha256="a" * 64, dataset_sha256="b" * 64,
            application_version_sha256="c" * 64,
            seed=1, terminal_geometry="80x24", concurrency_levels=(1,),
            warmup_seconds=0, measurement_seconds=0, cooldown_seconds=0,
            iterations=1,
            think_time_profile=ThinkTimeProfile(type="none", sha256="d" * 64,
                                                params={}),
            stop_conditions=StopConditions(
                error_rate_pct=99.0, p99_limit_ms=999999.0,
                host_cpu_pct=99.0),
            environments=("env-a",))
        executor = BenchmarkExecutor(
            contrato, {"env-a": _AdapterCheckpoints("env-a")}, self.tmp,
            journeys=[{"journey_id": "j", "steps": [{"step_id": "s1"}]}])
        resultado = executor.run()
        run = resultado.runs[0]
        self.assertEqual(2, run.checkpoints_executed)
        self.assertEqual(1, run.checkpoints_checked)
        self.assertEqual(1, len(run.checkpoint_exceptions))
        self.assertEqual("terminal_engine_unavailable",
                         run.checkpoint_exceptions[0]["reason"])
        # cobertura parcial com exceção auditada → WARN máximo
        comparison = build_comparison(resultado)
        self.assertAlmostEqual(
            0.5,
            comparison["functional_coverage_by_env"]["env-a"]["coverage"])
        decision = build_decision(resultado, comparison)
        self.assertEqual("WARN", decision.verdict)


if __name__ == "__main__":
    unittest.main()

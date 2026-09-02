"""FASE 3 — Medição REAL de recuperação após a retirada da carga.

O ``DegradationReport.recovery_seconds`` era SEMPRE ``None`` (nunca
instrumentado) e o relatório dizia "não medido". Agora o contrato tem
``recovery_probe_seconds``: quando > 0, o executor

1. coleta a baseline de host ANTES da carga (janela pré-experimento);
2. ao fim da escada, aguarda a janela de sonda e coleta as amostras
   pós-carga REAIS (o sampler remoto continua gravando);
3. ``recovery_seconds`` = tempo entre o fim da carga e a primeira amostra
   de volta à faixa da baseline (CPU e load) — ou ``None`` +
   ``recovered=False`` se não recuperou dentro da janela.

Sem sonda (``recovery_probe_seconds=0``, default) ou sem baseline:
``recovery_seconds=None`` e o relatório diz "não medido" — nunca inventado.

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

from dakota_gateway.benchmark.contract import (  # noqa: E402
    StopConditions,
    ThinkTimeProfile,
    create_contract,
)
from dakota_gateway.benchmark.executor import BenchmarkExecutor  # noqa: E402
from dakota_gateway.benchmark.models import OperationSample  # noqa: E402


class _AdapterRecuperacao:
    """Adaptador fake com série de host ROTEIRIZADA por janela consultada.

    A lógica sob teste (medição de recuperação) é real: as amostras têm
    timestamps de verdade relativos à janela consultada. Ordem determinística
    das consultas do executor: baseline (antes da carga) → run → sonda de
    recuperação (pós-carga).
    """

    def __init__(self, env_id: str, *, recupera_em_s: float | None) -> None:
        self.environment_id = env_id
        self._recupera_em_s = recupera_em_s
        self._consultas = 0
        self.journey_incompletions: list = []

    # -- protocolo mínimo ---------------------------------------------------
    def set_iteration_context(self, iteration: int, concurrency: int) -> None:
        pass

    def preflight(self) -> dict:
        return {"ok": True, "checks": [{"name": "fake", "ok": True}]}

    def prepare_dataset(self, dataset_ref: dict) -> dict:
        return {"ok": True}

    def start_session(self, virtual_user_id: str) -> str:
        return f"h-{virtual_user_id}"

    def execute_journey(self, session_handle: str, journey: dict,
                        *, phase: str) -> list:
        ns0 = 1_000_000_000
        return [OperationSample(
            experiment_id="exp-rec", environment_id=self.environment_id,
            iteration=1, concurrency=1, virtual_user_id="vu-1",
            journey_id="j", step_id="s1", phase=phase, started_ns=ns0,
            finished_ns=ns0 + 10_000_000, latency_ms=10.0, success=True,
            timeout=False, functional_divergence=False, error_code=None,
            screen_sig_checked=True)]

    def stop_session(self, session_handle: str) -> None:
        pass

    def collect_application_metrics(self) -> dict:
        return {"available": True}

    def collect_host_metrics(self, from_ms: int, to_ms: int) -> list[dict]:
        """Baseline baixa; sob carga alta; pós-carga conforme o roteiro."""
        self._consultas += 1
        if self._consultas == 1:
            # baseline pré-carga: host ocioso
            return [{"ts_ms": from_ms, "cpu_pct": 5.0, "load1": 0.10}]
        if self._consultas == 2:
            # sob carga (janela da run): host carregado
            return [{"ts_ms": from_ms, "cpu_pct": 92.0, "load1": 6.0}]
        # sonda de recuperação: amostras a cada 0.5 s desde o fim da carga
        amostras = []
        for i in range(1, 9):
            ts = from_ms + i * 500
            t_s = i * 0.5
            if (self._recupera_em_s is not None
                    and t_s >= self._recupera_em_s):
                amostras.append({"ts_ms": ts, "cpu_pct": 6.0,
                                 "load1": 0.12})  # de volta à baseline
            else:
                amostras.append({"ts_ms": ts, "cpu_pct": 88.0,
                                 "load1": 5.5})   # ainda carregado
        return amostras

    def collect_database_metrics(self) -> dict:
        return {"available": False, "reason": "collector_not_supported"}

    def cleanup(self) -> None:
        pass


def _contrato(exp_id: str, probe_s: int):
    return create_contract(
        experiment_id=exp_id,
        journey_set_sha256="a" * 64,
        dataset_sha256="b" * 64,
        application_version_sha256="c" * 64,
        seed=1, terminal_geometry="80x24",
        concurrency_levels=(1,),
        warmup_seconds=0, measurement_seconds=0, cooldown_seconds=0,
        iterations=1,
        think_time_profile=ThinkTimeProfile(type="none", sha256="d" * 64,
                                            params={}),
        stop_conditions=StopConditions(
            error_rate_pct=99.0, p99_limit_ms=999999.0, host_cpu_pct=99.0),
        environments=("env-a",),
        recovery_probe_seconds=probe_s,
    )


class TestRecoveryProbe(unittest.TestCase):
    """Sonda de recuperação: medição real pós-carga × baseline."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def _executar(self, exp_id: str, probe_s: int, recupera_em_s):
        adapters = {"env-a": _AdapterRecuperacao("env-a",
                                                 recupera_em_s=recupera_em_s)}
        executor = BenchmarkExecutor(
            _contrato(exp_id, probe_s), adapters, Path(self._tmp.name),
            journeys=[{"journey_id": "j", "steps": [{"step_id": "s1"}]}])
        return executor.run()

    def test_recuperacao_medida_em_segundos_reais(self) -> None:
        resultado = self._executar("exp-rec-ok", probe_s=2, recupera_em_s=1.5)
        rec = resultado.recovery["env-a"]
        self.assertTrue(rec["recovered"])
        # primeira amostra na faixa da baseline: 1.5 s após o fim da carga
        self.assertAlmostEqual(1.5, rec["recovery_seconds"], places=1)
        # baseline e margem documentadas na evidência
        self.assertIn("baseline", rec)
        self.assertIn("cpu_pct", rec["baseline"])

    def test_sem_recuperacao_na_janela_recovered_false(self) -> None:
        resultado = self._executar("exp-rec-nunca", probe_s=2,
                                   recupera_em_s=None)
        rec = resultado.recovery["env-a"]
        self.assertFalse(rec["recovered"])
        self.assertIsNone(rec["recovery_seconds"])

    def test_sonda_desligada_por_padrao_nao_medido(self) -> None:
        resultado = self._executar("exp-rec-off", probe_s=0,
                                   recupera_em_s=1.0)
        self.assertEqual({}, resultado.recovery)

    def test_recuperacao_persistida_e_reconstruida(self) -> None:
        """A evidência vai ao execution-result.json e sobrevive ao rebuild
        (compare/report leem do disco — memória e disco não divergem)."""
        self._executar("exp-rec-persist", probe_s=2, recupera_em_s=1.5)
        dados = json.loads(
            (Path(self._tmp.name) / "exp-rec-persist"
             / "execution-result.json").read_text(encoding="utf-8"))
        self.assertIn("recovery", dados)
        self.assertTrue(dados["recovery"]["env-a"]["recovered"])
        self.assertAlmostEqual(
            1.5, dados["recovery"]["env-a"]["recovery_seconds"], places=1)

    def test_recovery_seconds_chega_ao_relatorio(self) -> None:
        from dakota_gateway.benchmark.comparison import (
            build_comparison, build_decision)
        from dakota_gateway.benchmark.report import write_experiment_artifacts

        resultado = self._executar("exp-rec-report", probe_s=2,
                                   recupera_em_s=1.5)
        comparison = build_comparison(resultado)
        decision = build_decision(resultado, comparison)
        self.assertAlmostEqual(
            1.5,
            comparison["degradation_by_env"]["env-a"]["recovery_seconds"],
            places=1)
        with tempfile.TemporaryDirectory() as tmp2:
            write_experiment_artifacts(
                Path(tmp2), resultado, comparison, {}, decision)
            md = (Path(tmp2) / "report.md").read_text(encoding="utf-8")
        self.assertNotIn("não medido", md)
        self.assertIn("1.5", md)


if __name__ == "__main__":
    unittest.main()

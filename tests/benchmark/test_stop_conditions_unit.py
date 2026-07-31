"""Testes unitários das stop_conditions da escada de carga (§11/§17).

Foco: saturação de CPU de host SUSTENTADA. §17 prescreve parar quando a "CPU
PERMANECER saturada" — uma amostra isolada acima do limite (pico transitório
ou carga externa de um host compartilhado) NÃO pode truncar a escada.

Caso real que motivou a regra: a execução oficial v1 (cap13-aix-linux-oficial-v1)
parou no nível conc5 por UMA amostra a 99% no AIX de produção (as demais ~28
amostras da run ficaram abaixo), truncando a escada [1, 5, 10, 20] no segundo
nível. A regra exige ``host_cpu_sustained_samples`` amostras CONSECUTIVAS
acima do limite na série temporal de UMA run.
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
from dakota_gateway.benchmark.models import (  # noqa: E402
    EnvironmentRunResult,
    OperationSample,
)


def _contrato(sustained: int = 3) -> "object":
    return create_contract(
        experiment_id="exp-stop-conditions",
        journey_set_sha256="a" * 64,
        dataset_sha256="b" * 64,
        application_version_sha256="c" * 64,
        seed=1,
        terminal_geometry="80x24",
        concurrency_levels=(1,),
        warmup_seconds=1,
        measurement_seconds=1,
        cooldown_seconds=1,
        iterations=1,
        think_time_profile=ThinkTimeProfile(type="none", sha256="d" * 64, params={}),
        stop_conditions=StopConditions(
            error_rate_pct=99.0, p99_limit_ms=999999.0,
            host_cpu_pct=95.0, host_cpu_sustained_samples=sustained),
        environments=("env-a", "env-b"),
    )


def _amostra_ok() -> OperationSample:
    return OperationSample(
        experiment_id="exp-stop-conditions", environment_id="env-a",
        iteration=1, concurrency=1, virtual_user_id="vu-1",
        journey_id="j", step_id="s", started_ns=0, finished_ns=1_000_000,
        latency_ms=1.0, success=True, timeout=False,
        functional_divergence=False, error_code=None, phase="MEASUREMENT")


class TestHostCpuSustained(unittest.TestCase):

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.executor = BenchmarkExecutor(
            _contrato(), adapters={}, artifacts_dir=Path(self._tmp.name))

    def _run_com_serie_cpu(self, env_id: str, cpus: list) -> EnvironmentRunResult:
        path = Path(self._tmp.name) / f"host-{env_id}.jsonl"
        with open(path, "w", encoding="utf-8") as fh:
            for i, cpu in enumerate(cpus):
                amostra = {"ts_ms": i * 1000}
                if cpu is not None:
                    amostra["cpu_pct"] = cpu
                fh.write(json.dumps(amostra) + "\n")
        return EnvironmentRunResult(
            environment_id=env_id, iteration=1, concurrency=1,
            status="COMPLETED", samples=[_amostra_ok()],
            host_samples_path=str(path))

    def test_pico_isolado_nao_para_escada(self) -> None:
        """Caso real v1: 1 amostra a 99% entre ~29 normais → sem stop."""
        runs = [self._run_com_serie_cpu("env-a", [10.0] * 14 + [99.0] + [10.0] * 14)]
        host = self.executor._collect_level_host_metrics(runs)
        self.assertIsNone(self.executor._stop_condition_hit(runs, host))

    def test_duas_consecutivas_abaixo_do_minimo_nao_para(self) -> None:
        runs = [self._run_com_serie_cpu("env-a", [96.0, 97.0, 10.0])]
        host = self.executor._collect_level_host_metrics(runs)
        self.assertIsNone(self.executor._stop_condition_hit(runs, host))

    def test_tres_consecutivas_declaram_saturacao(self) -> None:
        runs = [self._run_com_serie_cpu("env-a", [10.0, 96.0, 97.5, 99.0, 10.0])]
        host = self.executor._collect_level_host_metrics(runs)
        stop = self.executor._stop_condition_hit(runs, host)
        self.assertIsNotNone(stop)
        self.assertEqual("host_cpu_pct", stop["condition"])
        self.assertEqual(3, stop["sustained_samples"])
        self.assertEqual("env-a", stop["environment_id"])

    def test_intercaladas_nao_contam_como_sustentadas(self) -> None:
        runs = [self._run_com_serie_cpu("env-a", [96.0, 10.0, 97.0, 98.0, 10.0, 99.0])]
        host = self.executor._collect_level_host_metrics(runs)
        self.assertIsNone(self.executor._stop_condition_hit(runs, host))

    def test_amostra_sem_cpu_reseta_a_sequencia(self) -> None:
        """Marcador de indisponibilidade (sem cpu_pct) interrompe a contagem —
        'sem leitura' nunca conta como 'saturada'."""
        runs = [self._run_com_serie_cpu("env-a", [96.0, 97.0, None, 98.0, 99.0])]
        host = self.executor._collect_level_host_metrics(runs)
        self.assertIsNone(self.executor._stop_condition_hit(runs, host))

    def test_avaliacao_por_ambiente(self) -> None:
        """Só a série do ambiente saturado dispara; env são avaliadas por run,
        nunca concatenadas (2 do A + 1 do B não formam sequência)."""
        runs = [self._run_com_serie_cpu("env-a", [96.0, 97.0]),
                self._run_com_serie_cpu("env-b", [99.0])]
        host = self.executor._collect_level_host_metrics(runs)
        self.assertIsNone(self.executor._stop_condition_hit(runs, host))

    def test_limiar_configuravel_um_dispara_com_uma(self) -> None:
        executor = BenchmarkExecutor(
            _contrato(sustained=1), adapters={},
            artifacts_dir=Path(self._tmp.name))
        runs = [self._run_com_serie_cpu("env-a", [10.0, 99.0, 10.0])]
        host = executor._collect_level_host_metrics(runs)
        stop = executor._stop_condition_hit(runs, host)
        self.assertIsNotNone(stop)
        self.assertEqual(1, stop["sustained_samples"])


class TestStopConditionsContrato(unittest.TestCase):

    def test_manifest_inclui_sustained_samples(self) -> None:
        contrato = _contrato(sustained=4)
        manifest = contrato.to_manifest_dict()
        self.assertEqual(
            4, manifest["stop_conditions"]["host_cpu_sustained_samples"])

    def test_contrato_de_dict_preserva_sustained(self) -> None:
        from dakota_gateway.benchmark.contract import load_contract

        contrato = _contrato(sustained=5)
        with tempfile.TemporaryDirectory() as tmp:
            path = contrato.write_manifest(Path(tmp))
            restaurado = load_contract(path)
        self.assertEqual(5, restaurado.stop_conditions.host_cpu_sustained_samples)


if __name__ == "__main__":
    unittest.main()

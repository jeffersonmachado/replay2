"""Regressão — tails forenses de VUs não vazam entre runs do benchmark.

Bug confirmado: ``SSHReplayAdapter._tails_by_vu`` acumula os tails de saída
por usuário virtual e ``set_iteration_context`` (chamado pelo executor no
início de CADA run, por ambiente) NÃO limpava o dict. Resultado: os logs
forenses da run N (``runs/<run>/logs/session-<vu>.log``) incluíam VUs
encerrados em runs anteriores — uma run de concorrência 1 executada depois
de uma de concorrência 10 gravava ``session-vu-2.log`` … ``session-vu-10.log``
com conteúdo de sessões que NÃO pertencem a ela (evidência contaminada).

Estes testes DEVEM FALHAR antes da correção e PASSAR depois dela.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "gateway"))

from dakota_gateway.benchmark.adapters import SSHReplayAdapter  # noqa: E402
from dakota_gateway.benchmark.contract import (  # noqa: E402
    StopConditions,
    ThinkTimeProfile,
    create_contract,
)
from dakota_gateway.benchmark.environments import (  # noqa: E402
    CpuModel,
    EnvironmentModel,
)
from dakota_gateway.benchmark.executor import BenchmarkExecutor  # noqa: E402


def _contrato(exp_id: str, niveis: tuple[int, ...]):
    return create_contract(
        experiment_id=exp_id,
        journey_set_sha256="a" * 64,
        dataset_sha256="b" * 64,
        application_version_sha256="c" * 64,
        seed=1, terminal_geometry="80x24",
        concurrency_levels=niveis,
        warmup_seconds=0, measurement_seconds=0, cooldown_seconds=0,
        iterations=1,
        think_time_profile=ThinkTimeProfile(type="none", sha256="d" * 64,
                                            params={}),
        stop_conditions=StopConditions(
            error_rate_pct=99.0, p99_limit_ms=999999.0, host_cpu_pct=99.0),
        environments=("env-a",),
    )


def _modelo() -> EnvironmentModel:
    return EnvironmentModel(
        environment_id="env-a", platform="Linux", architecture="x86_64",
        host="192.0.2.10", port=22,
        user_secret_ref="ssh-key:ferblo@192.0.2.10",
        application_endpoint="ssh://ferblo@192.0.2.10:22",
        cpu=CpuModel(model="Xeon", virtual_processors=8,
                     physical_processors=8),
        memory_mb=8192,
    )


class _StdinFake:
    """stdin de processo fake: aceita write/flush/close."""

    def __init__(self) -> None:
        self.escrito = bytearray()

    def write(self, dados: bytes) -> int:
        self.escrito.extend(dados)
        return len(dados)

    def flush(self) -> None:
        pass

    def close(self) -> None:
        pass


class _ProcFake:
    """Processo ssh PTY fake: escreve um marcador único na saída e cala.

    O ``_drain_until_stable`` do adaptador lê o marcador e, após
    ``stable_ms`` sem saída, considera a tela estável — cronometragem real,
    sem rede.
    """

    def __init__(self, argv, marcador: bytes, **kwargs) -> None:
        self.argv = argv
        self.stdin = _StdinFake()
        r, w = os.pipe()
        self.stdout = os.fdopen(r, "rb", buffering=0)
        os.write(w, marcador)
        # escritor fecha a ponta para o EOF vir naturalmente no terminate
        self._w = w
        self.returncode: int | None = None
        self._lock = threading.Lock()

    def terminate(self) -> None:
        self.returncode = 0
        try:
            os.close(self._w)
        except OSError:
            pass

    def kill(self) -> None:
        self.terminate()

    def wait(self, timeout: float | None = None) -> int:
        self.returncode = 0
        return 0


class _FabricaProcs:
    """popen_factory com marcador único por sessão (MARCA-<n>)."""

    def __init__(self) -> None:
        self.seq = 0

    def __call__(self, argv, **kwargs) -> _ProcFake:
        self.seq += 1
        return _ProcFake(argv, f"MARCA-{self.seq}\n".encode("utf-8"),
                         **kwargs)


def _ssh_runner_fake(argv, entrada, timeout):
    """ssh one-shot fake: host_metrics responde a sentinela com 0 amostras."""

    class _Res:
        returncode = 0
        stdout = '{"host_metrics_query": "done", "rows": 0, "clock_offset_ms": 0}\n'
        stderr = ""

    return _Res()


class TestTailsNaoVazamEntreRuns(unittest.TestCase):
    """Isolamento dos tails por run (nível × iteração × ambiente)."""

    def test_session_tails_nao_contem_vus_de_runs_anteriores(self) -> None:
        """Unidade: run conc2 (vu-1, vu-2) → run conc1 (vu-1): os tails da
        segunda run NÃO podem ter vu-2 nem conteúdo da primeira."""
        fabrica = _FabricaProcs()
        adapter = SSHReplayAdapter(
            _modelo(), _contrato("exp-tails-unit", (1,)),
            popen_factory=fabrica, ssh_runner=_ssh_runner_fake,
            stable_ms=30)
        jornada = {"journey_id": "j",
                   "steps": [{"step_id": "s1", "key_text": "x"}]}

        # run 1: concorrência 2 — dois VUs
        adapter.set_iteration_context(1, 2)
        for vu in ("vu-1", "vu-2"):
            handle = adapter.start_session(vu)
            adapter.execute_journey(handle, jornada, phase="MEASUREMENT")
            adapter.stop_session(handle)
        tails_run1 = adapter.session_tails()
        self.assertIn("vu-2", tails_run1)  # sanidade: existiu na run 1

        # run 2: concorrência 1 — só vu-1
        adapter.set_iteration_context(1, 1)
        handle = adapter.start_session("vu-1")
        adapter.execute_journey(handle, jornada, phase="MEASUREMENT")
        adapter.stop_session(handle)
        tails_run2 = adapter.session_tails()

        self.assertNotIn("vu-2", tails_run2)
        self.assertIn("vu-1", tails_run2)
        # conteúdo da run 1 não pode vazar para o tail da run 2
        self.assertNotIn(b"MARCA-1", tails_run2["vu-1"])
        self.assertNotIn(b"MARCA-2", tails_run2["vu-1"])

    def test_logs_da_run_nao_contem_vus_de_runs_anteriores(self) -> None:
        """Executor: logs/ da run de concorrência 1 (depois da de
        concorrência 2) não trazem session-vu-2.log nem marcadores
        das sessões da run anterior."""
        fabrica = _FabricaProcs()
        adapter = SSHReplayAdapter(
            _modelo(), _contrato("exp-tails-exec", (2, 1)),
            popen_factory=fabrica, ssh_runner=_ssh_runner_fake,
            stable_ms=30)
        with tempfile.TemporaryDirectory() as tmp:
            executor = BenchmarkExecutor(
                _contrato("exp-tails-exec", (2, 1)), {"env-a": adapter},
                Path(tmp),
                journeys=[{"journey_id": "j",
                           "steps": [{"step_id": "s1", "key_text": "x"}]}])
            resultado = executor.run()
            self.assertEqual("COMPLETED", resultado.status)
            logs_conc1 = (Path(tmp) / "exp-tails-exec" / "runs"
                          / "env-a-iter1-conc1" / "logs")
            arquivos = sorted(p.name for p in logs_conc1.glob("*.log"))

        # sem vazamento: só vu-1 participou da run de concorrência 1
        self.assertEqual(["session-vu-1.log"], arquivos)


if __name__ == "__main__":
    unittest.main()

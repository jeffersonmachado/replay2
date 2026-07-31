"""Regressão: circuit breaker contra tempestade de erros de sessão (§9).

Cenário real que motivou este teste: a jornada da captura 13 termina com o
operador saindo do ERP e dando LOGOUT no shell (``exit\\r``) — a sessão SSH
morre no meio da jornada. Sem proteção, o executor continuava executando
jornadas contra a sessão morta e gerou ~1,77 MILHÃO de amostras
``session_io_error`` instantâneas (~933MB) em uma única fase.

Proteções verificadas aqui:

1. adapter: sessão morta/fechada ABORTA a jornada corrente — os steps
   restantes não são executados e NÃO viram amostras (ficam em
   ``journey_incompletions`` para auditoria);
2. executor: cada passada da jornada abre sessão NOVA (ciclo completo do
   §9) — morte natural da sessão (``session_closed``) não falha a run;
3. executor: N passadas consecutivas com morte inesperada de sessão
   (``session_io_error``) → run FAILED ``session_unstable``, com backoff;
4. executor: ``start_session`` falhando → run FAILED sem amostras sintéticas;
5. executor: cap de amostras de erro por fase → run FAILED
   ``error_sample_cap_exceeded``;
6. o application-samples.jsonl fica LIMITADO (ordem de dezenas/centenas de
   linhas) mesmo com a sessão morrendo em loop — nunca mais explode.
"""
from __future__ import annotations

import base64
import json
import os
import select
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
from dakota_gateway.benchmark.executor import (  # noqa: E402
    MAX_CONSECUTIVE_FAILED_PASSES,
    BenchmarkExecutor,
)
from dakota_gateway.benchmark.models import OperationSample  # noqa: E402


def _contrato(experiment_id: str = "exp-circuit", fase_s: int = 5) -> "object":
    return create_contract(
        experiment_id=experiment_id,
        journey_set_sha256="a" * 64,
        dataset_sha256="b" * 64,
        application_version_sha256="c" * 64,
        seed=1,
        terminal_geometry="80x24",
        concurrency_levels=(1,),
        warmup_seconds=fase_s,
        measurement_seconds=fase_s,
        cooldown_seconds=fase_s,
        iterations=1,
        think_time_profile=ThinkTimeProfile(type="none", sha256="d" * 64, params={}),
        stop_conditions=StopConditions(error_rate_pct=99.0, p99_limit_ms=999999.0),
        environments=("env-x",),
    )


def _modelo() -> EnvironmentModel:
    return EnvironmentModel(
        environment_id="env-x",
        platform="Linux",
        architecture="x86_64",
        host="192.0.2.10",
        user_secret_ref="ssh-key:ferblo@192.0.2.10",
        application_endpoint="ssh://ferblo@192.0.2.10:22",
        cpu=CpuModel(virtual_processors=2, physical_processors=2),
        memory_mb=4096,
    )


def _jornada(n_steps: int, journey_id: str = "j-circuit") -> dict:
    return {
        "journey_id": journey_id,
        "steps": [
            {"step_id": f"ev-{i}",
             "key_b64": base64.b64encode(f"OP 0\n".encode()).decode()}
            for i in range(n_steps)
        ],
    }


def _runner_ok(argv, input_text, timeout):
    class _R:
        returncode = 0
        stdout = ""
        stderr = ""
    return _R()


# ── Fakes de PTY para os testes do adapter ──────────────────────────────────


class _FakeProcFechaAposN:
    """PTY fake que responde N linhas e depois FECHA o stdout (EOF real).

    Simula o logout do shell no meio da jornada: o lado remoto encerra a
    sessão e o próximo read do adaptador recebe EOF.
    """

    def __init__(self, argv, stdin=None, stdout=None, stderr=None,
                 fecha_apos: int = 3):
        self._fecha_apos = fecha_apos
        self._stdin_r, stdin_w = os.pipe()
        stdout_r, self._stdout_w = os.pipe()
        self.stdin = os.fdopen(stdin_w, "wb", buffering=0)
        self.stdout = os.fdopen(stdout_r, "rb", buffering=0)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        buf = b""
        respondidas = 0
        while not self._stop.is_set():
            prontos, _, _ = select.select([self._stdin_r], [], [], 0.05)
            if not prontos:
                continue
            try:
                chunk = os.read(self._stdin_r, 65536)
            except OSError:
                break
            if not chunk:
                break
            buf += chunk
            while b"\n" in buf:
                linha, buf = buf.split(b"\n", 1)
                respondidas += 1
                if respondidas > self._fecha_apos:
                    # logout: fecha o stdout → o adaptador verá EOF
                    try:
                        os.close(self._stdout_w)
                    except OSError:
                        pass
                    return
                try:
                    os.write(self._stdout_w, linha + b"\r\nRESP\r\n")
                except OSError:
                    return

    def terminate(self) -> None:
        self._stop.set()

    def kill(self) -> None:
        self._stop.set()

    def wait(self, timeout=None) -> int:
        self._stop.set()
        self._thread.join(timeout=timeout or 1.0)
        try:
            os.close(self._stdout_w)
        except OSError:
            pass
        return 0


class _StdinQuebrado:
    """stdin fake que levanta BrokenPipeError na N-ésima escrita."""

    def __init__(self, falha_na: int):
        self._falha_na = falha_na
        self.escritas = 0

    def write(self, dados) -> int:
        self.escritas += 1
        if self.escritas >= self._falha_na:
            raise BrokenPipeError("sessão morta (fake)")
        return len(dados)

    def flush(self) -> None:
        pass

    def close(self) -> None:
        pass


class _FakeProcStdinQuebrado:
    """PTY fake cuja escrita no stdin falha na N-ésima vez (sessão morta)."""

    def __init__(self, argv, stdin=None, stdout=None, stderr=None,
                 falha_na: int = 3):
        stdout_r, self._stdout_w = os.pipe()
        self.stdin = _StdinQuebrado(falha_na)
        self.stdout = os.fdopen(stdout_r, "rb", buffering=0)

    def terminate(self) -> None:
        pass

    def kill(self) -> None:
        pass

    def wait(self, timeout=None) -> int:
        try:
            os.close(self._stdout_w)
        except OSError:
            pass
        return 0


class TestAdapterAbortaJornadaNaMorteDaSessao(unittest.TestCase):
    """Adapter: jornada aborta no primeiro erro de sessão (sem amostras dos
    steps restantes) e registra a incompletude para auditoria."""

    def test_eof_natural_marca_session_closed_e_aborta(self) -> None:
        adapter = SSHReplayAdapter(
            _modelo(), _contrato(),
            ssh_runner=_runner_ok,
            popen_factory=lambda argv, **kw: _FakeProcFechaAposN(argv, fecha_apos=3, **kw),
            stable_ms=30, step_timeout_s=5.0)
        self.addCleanup(adapter.cleanup)
        sessao = adapter.start_session("vu-1")
        amostras = adapter.execute_journey(sessao, _jornada(10), phase="MEASUREMENT")
        adapter.stop_session(sessao)

        # 3 respostas ok + 1 amostra session_closed; steps 5..10 NÃO executados
        self.assertEqual(4, len(amostras))
        self.assertTrue(all(s.success for s in amostras[:3]))
        self.assertEqual("session_closed", amostras[3].error_code)
        self.assertFalse(amostras[3].success)

        self.assertEqual(1, len(adapter.journey_incompletions))
        inc = adapter.journey_incompletions[0]
        self.assertEqual("session_closed", inc["reason"])
        self.assertEqual(4, inc["executed_steps"])
        self.assertEqual(6, inc["skipped_steps"])
        self.assertEqual(10, inc["total_steps"])

    def test_write_em_sessao_morta_marca_io_error_e_aborta(self) -> None:
        adapter = SSHReplayAdapter(
            _modelo(), _contrato(),
            ssh_runner=_runner_ok,
            popen_factory=lambda argv, **kw: _FakeProcStdinQuebrado(argv, falha_na=3, **kw),
            stable_ms=20, step_timeout_s=5.0)
        self.addCleanup(adapter.cleanup)
        sessao = adapter.start_session("vu-1")
        amostras = adapter.execute_journey(sessao, _jornada(10), phase="MEASUREMENT")
        adapter.stop_session(sessao)

        # 2 ok + 1 session_io_error; steps 4..10 NÃO executados
        self.assertEqual(3, len(amostras))
        self.assertEqual("session_io_error", amostras[2].error_code)
        inc = adapter.journey_incompletions[0]
        self.assertEqual("session_io_error", inc["reason"])
        self.assertEqual(7, inc["skipped_steps"])

    def test_tail_da_sessao_preservado_para_forense(self) -> None:
        adapter = SSHReplayAdapter(
            _modelo(), _contrato(),
            ssh_runner=_runner_ok,
            popen_factory=lambda argv, **kw: _FakeProcFechaAposN(argv, fecha_apos=3, **kw),
            stable_ms=30, step_timeout_s=5.0)
        self.addCleanup(adapter.cleanup)
        sessao = adapter.start_session("vu-1")
        adapter.execute_journey(sessao, _jornada(10), phase="MEASUREMENT")
        adapter.stop_session(sessao)
        tails = adapter.session_tails()
        self.assertIn("vu-1", tails)
        self.assertIn(b"RESP", tails["vu-1"])


# ── Fakes de adapter (nível de protocolo) para os testes do executor ────────


def _amostra(exp: str, env: str, phase: str, step: str, *,
             success: bool = True, error_code: str | None = None) -> OperationSample:
    t0 = time.monotonic_ns()
    return OperationSample(
        experiment_id=exp, environment_id=env, iteration=1, concurrency=1,
        virtual_user_id="vu-1", journey_id="j-circuit", step_id=step,
        phase=phase, started_ns=t0, finished_ns=t0 + 1_000_000,
        latency_ms=1.0, success=success, timeout=False,
        functional_divergence=False, error_code=error_code)


class _AdapterFakeBase:
    """Base do protocolo EnvironmentExecutionAdapter para os fakes."""

    def __init__(self) -> None:
        self.sessions_opened = 0

    def preflight(self) -> dict:
        return {"ok": True, "checks": []}

    def prepare_dataset(self, dataset_ref: dict) -> dict:
        return {"ok": True}

    def start_session(self, virtual_user_id: str) -> str:
        self.sessions_opened += 1
        return f"{virtual_user_id}#{self.sessions_opened}"

    def stop_session(self, session_handle: str) -> None:
        pass

    def collect_application_metrics(self) -> dict:
        return {"ok": True, "samples": []}

    def collect_host_metrics(self, from_ms: int, to_ms: int) -> list[dict]:
        return []

    def collect_database_metrics(self) -> dict:
        return {"available": False, "reason": "collector_not_supported"}

    def cleanup(self) -> None:
        pass


class _AdapterSessaoMorreSempre(_AdapterFakeBase):
    """Toda passada: 1 amostra ok e a sessão morre (session_io_error)."""

    def execute_journey(self, session_handle, journey, *, phase):
        return [
            _amostra("exp-circuit", "env-x", phase, "s1"),
            _amostra("exp-circuit", "env-x", phase, "s2",
                     success=False, error_code="session_io_error"),
        ]


class _AdapterStartSessionFalha(_AdapterFakeBase):
    """start_session sempre falha (servidor recusando conexão)."""

    def start_session(self, virtual_user_id: str) -> str:
        raise ConnectionError("ssh: Connection refused (fake)")


class _AdapterMuitosErros(_AdapterFakeBase):
    """Passadas grandes de erros NÃO-sessão (para o cap por fase)."""

    def execute_journey(self, session_handle, journey, *, phase):
        return [
            _amostra("exp-circuit", "env-x", phase, f"s{i}",
                     success=False, error_code="unexpected_response")
            for i in range(300)
        ]


class _AdapterFimNatural(_AdapterFakeBase):
    """Toda passada termina com session_closed (logout natural do §9)."""

    def execute_journey(self, session_handle, journey, *, phase):
        time.sleep(0.02)  # passada com duração realista (não busy-loop)
        return [
            _amostra("exp-circuit", "env-x", phase, "s1"),
            _amostra("exp-circuit", "env-x", phase, "s2"),
            _amostra("exp-circuit", "env-x", phase, "s3",
                     success=False, error_code="session_closed"),
        ]


class TestExecutorCircuitBreaker(unittest.TestCase):
    """Executor: sessão por passada, disjuntor e caps (sem explosão)."""

    def _executa(self, adapter, fase_s: int = 5):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        executor = BenchmarkExecutor(
            _contrato(fase_s=fase_s), {"env-x": adapter}, Path(tmp.name),
            journeys=[_jornada(5)])
        resultado = executor.run()
        return resultado, Path(tmp.name)

    def test_sessao_morrendo_sempre_run_failed_limitado(self) -> None:
        adapter = _AdapterSessaoMorreSempre()
        resultado, tmp = self._executa(adapter)
        self.assertEqual("FAILED", resultado.status)
        self.assertEqual("session_unstable", resultado.reason)
        run = resultado.runs[0]
        self.assertEqual("FAILED", run.status)
        # disjuntor: 3 passadas falhas → 3 sessões abertas (reconnect por
        # passada), 2 amostras cada — NUNCA milhões
        self.assertEqual(MAX_CONSECUTIVE_FAILED_PASSES, adapter.sessions_opened)
        total = (len(run.samples) + len(run.warmup_samples)
                 + len(run.cooldown_samples))
        self.assertLessEqual(total, 10)

        # o arquivo de amostras fica limitado (antes: 1,77M linhas / 933MB)
        jsonl = next(tmp.rglob("application-samples.jsonl"))
        linhas = jsonl.read_text(encoding="utf-8").splitlines()
        self.assertLessEqual(len(linhas), 10,
                             f"application-samples.jsonl explodiu: {len(linhas)}")

    def test_start_session_falhando_run_failed_sem_amostras(self) -> None:
        adapter = _AdapterStartSessionFalha()
        resultado, _ = self._executa(adapter)
        self.assertEqual("FAILED", resultado.status)
        self.assertIn("start_session_failed", resultado.reason)
        run = resultado.runs[0]
        self.assertEqual("FAILED", run.status)
        total = (len(run.samples) + len(run.warmup_samples)
                 + len(run.cooldown_samples))
        self.assertEqual(0, total, "não pode gerar amostra sintética")

    def test_cap_de_erros_por_fase(self) -> None:
        adapter = _AdapterMuitosErros()
        resultado, _ = self._executa(adapter)
        self.assertEqual("FAILED", resultado.status)
        self.assertEqual("error_sample_cap_exceeded", resultado.reason)
        run = resultado.runs[0]
        total = (len(run.samples) + len(run.warmup_samples)
                 + len(run.cooldown_samples))
        # cap em 1000: a fase aborta logo após cruzar o limite (≤ 1 passada extra)
        self.assertLessEqual(total, 1300)

    def test_fim_natural_nao_falha_e_reabre_sessao_por_passada(self) -> None:
        adapter = _AdapterFimNatural()
        resultado, _ = self._executa(adapter, fase_s=1)
        self.assertEqual("COMPLETED", resultado.status)
        run = resultado.runs[0]
        self.assertEqual("COMPLETED", run.status)
        # várias passadas → várias sessões abertas (ciclo completo do §9)
        self.assertGreater(adapter.sessions_opened, 3)
        # passadas limitadas pela duração da fase (20ms/passada + overhead):
        # ordem de centenas de amostras no máximo, nunca milhões
        total = (len(run.samples) + len(run.warmup_samples)
                 + len(run.cooldown_samples))
        self.assertGreater(total, 0)
        self.assertLess(total, 5000)


if __name__ == "__main__":
    unittest.main()

"""Adaptador controlado de ambiente para os testes do benchmark real (P1).

Implementa o protocolo ``EnvironmentExecutionAdapter`` (contrato §8) executando
operações REAIS contra um processo filho local: um servidor "echo" Python que
lê linhas de stdin no formato ``OP <step_id> <delay_ms> [payload]``, dorme
``delay_ms`` milissegundos e responde ``OK <step_id> [payload]`` em stdout.

NENHUMA latência é inventada: cada operação é cronometrada com
``time.monotonic_ns`` em torno do round-trip real pelo pipe do subprocesso.
O atraso injetado é real (``time.sleep`` dentro do processo filho), portanto a
latência medida deve ser >= atraso injetado (mais o custo real do round-trip).

Nota de desacoplamento: este módulo NÃO importa ``dakota_gateway.benchmark``
(o pacote sob teste ainda não existe na P1). ``ControlledSample`` espelha,
campo a campo, o ``OperationSample`` do contrato §9 (duck typing); quando a
implementação P2 existir, o executor que consumir o adaptador só precisa dos
atributos documentados. Isso permite que os testes que exercitam apenas este
suporte PASSEM já na P1, provando que o oráculo de medição é real.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Optional

# Servidor echo executado como processo filho. Protocolo de linhas:
#   in : "OP <step_id> <delay_ms> [payload]"  |  "STOP"
#   out: "OK <step_id> [payload]"
_ECHO_SERVER_SRC = (
    "import sys, time\n"
    "for line in sys.stdin:\n"
    "    parts = line.split()\n"
    "    if not parts:\n"
    "        continue\n"
    "    if parts[0] == 'STOP':\n"
    "        break\n"
    "    delay_ms = float(parts[2])\n"
    "    if delay_ms > 0:\n"
    "        time.sleep(delay_ms / 1000.0)\n"
    "    resp = 'OK ' + parts[1]\n"
    "    if len(parts) > 3:\n"
    "        resp += ' ' + ' '.join(parts[3:])\n"
    "    sys.stdout.write(resp + '\\n')\n"
    "    sys.stdout.flush()\n"
)


@dataclass
class ControlledSample:
    """Espelho de ``OperationSample`` (contrato §9) — mesmos campos/ordem."""

    experiment_id: str
    environment_id: str
    iteration: int
    concurrency: int
    virtual_user_id: str
    journey_id: str
    step_id: str
    phase: str  # "WARMUP" | "MEASUREMENT" | "COOLDOWN"
    started_ns: int
    finished_ns: int
    latency_ms: float
    success: bool
    timeout: bool
    functional_divergence: bool
    error_code: Optional[str]

    def to_jsonl(self) -> str:
        """Serializa como uma linha JSON (formato de application-samples.jsonl)."""
        import json

        return json.dumps(self.__dict__, sort_keys=True)


class ControlledAdapter:
    """``EnvironmentExecutionAdapter`` que mede round-trips reais via pipes.

    Formato de jornada aceito (interpretação P1, documentada nos testes)::

        {"journey_id": "j1",
         "steps": [{"step_id": "s1", "delay_ms": 40.0, "payload": "abc"}, ...]}

    - ``delay_ms``: atraso REAL injetado no processo filho antes da resposta;
    - ``payload`` (opcional): ecoado pelo filho; se o eco divergir, a amostra
      é marcada com ``functional_divergence=True``.
    """

    def __init__(self, environment_id: str = "controlled", *,
                 experiment_id: str = "exp-controlled",
                 iteration: int = 1, concurrency: int = 1) -> None:
        self.environment_id = environment_id
        self.experiment_id = experiment_id
        self.iteration = iteration
        self.concurrency = concurrency
        self._sessions: dict[str, dict] = {}
        self._session_seq = 0
        self._samples: list[ControlledSample] = []

    # -- ciclo de vida -------------------------------------------------

    def preflight(self) -> dict:
        """Preflight REAL: sobe um filho de probe e faz um round-trip."""
        checks = []
        ok = False
        detail = ""
        proc = None
        try:
            proc = self._spawn()
            self._roundtrip(proc, "preflight", 0.0)
            ok = True
        except OSError as exc:  # pragma: no cover - ambiente quebrado
            detail = f"falha ao iniciar processo echo: {exc}"
        finally:
            if proc is not None:
                self._shutdown(proc)
        checks.append({"name": "echo_process_roundtrip", "ok": ok, "detail": detail})
        return {"ok": ok, "checks": checks}

    def prepare_dataset(self, dataset_ref: dict) -> dict:
        """Sem dataset real no ambiente controlado; registra e confirma."""
        return {"ok": True, "dataset_ref": dict(dataset_ref)}

    def start_session(self, virtual_user_id: str) -> str:
        """Abre uma sessão real: um processo filho dedicado ao usuário virtual."""
        self._session_seq += 1
        handle = f"{virtual_user_id}#{self._session_seq}"
        self._sessions[handle] = {
            "proc": self._spawn(),
            "virtual_user_id": virtual_user_id,
        }
        return handle

    def execute_journey(self, session_handle: str, journey: dict,
                        *, phase: str) -> list[ControlledSample]:
        """Executa os passos da jornada medindo latência real por operação."""
        if phase not in ("WARMUP", "MEASUREMENT", "COOLDOWN"):
            raise ValueError(f"fase inválida: {phase!r}")
        session = self._sessions[session_handle]
        proc = session["proc"]
        journey_id = str(journey.get("journey_id", "journey"))
        produced: list[ControlledSample] = []
        for step in journey.get("steps", []):
            step_id = str(step.get("step_id", "step"))
            delay_ms = float(step.get("delay_ms", 0.0))
            payload = step.get("payload")
            response, started_ns, finished_ns = self._roundtrip(
                proc, step_id, delay_ms, payload
            )
            latency_ms = (finished_ns - started_ns) / 1_000_000.0
            parts = response.split()
            success = len(parts) >= 2 and parts[0] == "OK" and parts[1] == step_id
            divergence = False
            if success and payload is not None:
                divergence = len(parts) < 3 or parts[2] != str(payload)
            sample = ControlledSample(
                experiment_id=self.experiment_id,
                environment_id=self.environment_id,
                iteration=self.iteration,
                concurrency=self.concurrency,
                virtual_user_id=session["virtual_user_id"],
                journey_id=journey_id,
                step_id=step_id,
                phase=phase,
                started_ns=started_ns,
                finished_ns=finished_ns,
                latency_ms=latency_ms,
                success=success,
                timeout=False,
                functional_divergence=divergence,
                error_code=None if success else "unexpected_response",
            )
            self._samples.append(sample)
            produced.append(sample)
        return produced

    def stop_session(self, session_handle: str) -> None:
        """Encerra a sessão (pede STOP ao filho e aguarda o término real)."""
        session = self._sessions.pop(session_handle, None)
        if session is not None:
            self._shutdown(session["proc"])

    def cleanup(self) -> None:
        """Encerra todas as sessões ainda abertas."""
        for handle in list(self._sessions):
            self.stop_session(handle)

    # -- coletores ------------------------------------------------------

    def collect_application_metrics(self) -> dict:
        """Devolve as amostras reais acumuladas, separadas por fase."""
        return {
            "ok": True,
            "samples": list(self._samples),
            "measurement_samples": self.measurement_samples,
            "warmup_samples": self.warmup_samples,
            "cooldown_samples": self.cooldown_samples,
        }

    def collect_host_metrics(self, from_ms: int, to_ms: int) -> list[dict]:
        """Amostra REAL do host de teste (load average via os.getloadavg)."""
        load1, load5, load15 = os.getloadavg()
        return [{
            "ts_ms": int(time.time() * 1000),
            "from_ms": from_ms,
            "to_ms": to_ms,
            "host_id": "localhost",
            "platform": sys.platform,
            "load1": load1,
            "load5": load5,
            "load15": load15,
        }]

    def collect_database_metrics(self) -> dict:
        """Sem banco no ambiente controlado (contrato §14)."""
        return {"available": False, "reason": "collector_not_supported"}

    # -- amostras por fase ----------------------------------------------

    @property
    def samples(self) -> list[ControlledSample]:
        return list(self._samples)

    @property
    def measurement_samples(self) -> list[ControlledSample]:
        return [s for s in self._samples if s.phase == "MEASUREMENT"]

    @property
    def warmup_samples(self) -> list[ControlledSample]:
        return [s for s in self._samples if s.phase == "WARMUP"]

    @property
    def cooldown_samples(self) -> list[ControlledSample]:
        return [s for s in self._samples if s.phase == "COOLDOWN"]

    # -- internos ---------------------------------------------------------

    @staticmethod
    def _spawn() -> subprocess.Popen:
        return subprocess.Popen(
            [sys.executable, "-u", "-c", _ECHO_SERVER_SRC],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
            bufsize=1,
        )

    @staticmethod
    def _roundtrip(proc: subprocess.Popen, step_id: str, delay_ms: float,
                   payload: Optional[str] = None) -> tuple[str, int, int]:
        """Um round-trip real cronometrado com ``time.monotonic_ns``."""
        line = f"OP {step_id} {delay_ms}"
        if payload is not None:
            line += f" {payload}"
        assert proc.stdin is not None and proc.stdout is not None
        started_ns = time.monotonic_ns()
        proc.stdin.write(line + "\n")
        proc.stdin.flush()
        response = proc.stdout.readline().strip()
        finished_ns = time.monotonic_ns()
        return response, started_ns, finished_ns

    @staticmethod
    def _shutdown(proc: subprocess.Popen) -> None:
        try:
            if proc.stdin is not None:
                proc.stdin.write("STOP\n")
                proc.stdin.flush()
            proc.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):  # pragma: no cover
            proc.kill()
            proc.wait(timeout=5)

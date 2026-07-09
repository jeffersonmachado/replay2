"""Executor remoto: conecta via SSH e executa jornadas contra sistema real."""
from __future__ import annotations

import json
import os
import time
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from .journey import JourneyDefinition, JourneyDataset, JourneyStep
from .journey_builder import JourneyBuilder
from .journey_verifier import JourneyVerifier, JourneyVerificationResult
from .error_detector import ErrorDetector


@dataclass
class RemoteSessionResult:
    """Resultado de uma sessão remota real."""
    session_index: int = 0
    status: str = "pending"
    screens_captured: list[str] = field(default_factory=list)
    errors_detected: list[dict] = field(default_factory=list)
    verification: Optional[JourneyVerificationResult] = None
    duration_ms: int = 0
    raw_output: str = ""


@dataclass
class RemoteExecutionResult:
    """Resultado de execução remota completa."""
    journey_id: str = ""
    total_sessions: int = 0
    completed: int = 0
    failed: int = 0
    session_results: list[RemoteSessionResult] = field(default_factory=list)
    aggregate_verification: Optional[dict] = None
    duration_ms: int = 0


class RemoteExecutor:
    """Executa jornadas contra sistema real via SSH.

    Modos:
    - dry_run: simula sem SSH (default)
    - real: conecta SSH de verdade
    """

    def __init__(self, mode: str = "dry_run", db_path: str = ""):
        self.mode = mode
        self.db_path = db_path
        self.detector = ErrorDetector()

    def execute_journey(
        self,
        journey: JourneyDefinition,
        jds: JourneyDataset,
        session_count: int,
        target_host: str = "",
        target_user: str = "",
        target_command: str = "",
        target_port: int = 0,
        on_progress: Optional[Callable] = None,
    ) -> RemoteExecutionResult:
        """Executa jornada completa contra sistema real."""
        result = RemoteExecutionResult(
            journey_id=journey.journey_id,
            total_sessions=session_count,
        )
        start_ms = int(time.time() * 1000)
        semaphore = threading.Semaphore(5)  # max 5 sessões SSH simultâneas
        threads: list[threading.Thread] = []
        lock = threading.Lock()

        builder = JourneyBuilder()
        verifier = JourneyVerifier()

        def run_session(sess_idx: int):
            semaphore.acquire()
            try:
                sess_result = RemoteSessionResult(session_index=sess_idx, status="running")
                sess_start = int(time.time() * 1000)

                try:
                    inputs = builder.generate_replay_script(journey, jds, session_index=sess_idx)

                    if self.mode == "real" and target_host:
                        screens = self._execute_ssh_session(
                            target_host, target_user, target_command, target_port, inputs
                        )
                    else:
                        # dry_run: simular telas
                        from .stress_runner import SyntheticStressRunner
                        screens = SyntheticStressRunner._simulate_screens(journey, jds, sess_idx)

                    sess_result.screens_captured = [s.get("screen_text", "") for s in screens]
                    verification = verifier.verify_session(journey, sess_idx, screens)
                    sess_result.verification = verification

                    # Detectar erros nas telas capturadas
                    for screen_text in sess_result.screens_captured:
                        errors = self.detector.detect(screen_text, {
                            "session_index": sess_idx,
                            "journey_id": journey.journey_id,
                        })
                        sess_result.errors_detected.extend([
                            {"type": e.error_type, "severity": e.severity, "message": e.line_text}
                            for e in errors
                        ])

                    sess_result.status = "success" if verification.passed else "failed"

                except Exception as exc:
                    sess_result.status = "error"
                    sess_result.errors_detected.append({
                        "type": "technical_error", "severity": "high", "message": str(exc),
                    })

                sess_result.duration_ms = int(time.time() * 1000) - sess_start

                with lock:
                    result.session_results.append(sess_result)
                    if sess_result.status == "success":
                        result.completed += 1
                    else:
                        result.failed += 1
                    if on_progress:
                        on_progress(result.completed + result.failed, session_count)

            finally:
                semaphore.release()

        for sess_idx in range(session_count):
            t = threading.Thread(target=run_session, args=(sess_idx,), daemon=True)
            threads.append(t)
            t.start()
            time.sleep(0.05)  # micro ramp-up

        for t in threads:
            t.join(timeout=300)

        result.duration_ms = int(time.time() * 1000) - start_ms

        # Análise agregada
        all_vr = [sr.verification for sr in result.session_results if sr.verification]
        if all_vr:
            result.aggregate_verification = verifier.analyze_errors(all_vr)

        return result

    def _execute_ssh_session(
        self,
        host: str,
        user: str,
        command: str,
        port: int,
        inputs: str,
    ) -> list[dict]:
        """Executa sessão SSH real e captura telas."""
        import subprocess
        import pty
        import selectors

        dest = f"{user}@{host}" if user else host
        argv = ["ssh", "-tt", "-o", "StrictHostKeyChecking=no", "-o", "BatchMode=yes"]
        if port:
            argv += ["-p", str(port)]
        argv.append(dest)
        if command:
            argv += ["--", command]

        master_fd, slave_fd = pty.openpty()
        proc = subprocess.Popen(
            argv, stdin=slave_fd, stdout=slave_fd, stderr=slave_fd,
            preexec_fn=os.setsid, close_fds=True,
        )
        os.close(slave_fd)

        screens: list[dict] = []
        screen_buf = b""
        input_lines = inputs.split("\n")

        sel = selectors.DefaultSelector()
        sel.register(master_fd, selectors.EVENT_READ)

        input_idx = 0
        timeout_ms = 30000
        start = int(time.time() * 1000)

        try:
            while input_idx < len(input_lines) or screen_buf:
                elapsed = int(time.time() * 1000) - start
                if elapsed > timeout_ms:
                    break

                events = sel.select(timeout=0.5)
                for key, _ in events:
                    data = os.read(master_fd, 8192)
                    if data:
                        screen_buf += data

                # Enviar próximo input quando tela estabilizar
                if input_idx < len(input_lines):
                    # Aguardar um pouco para a tela estabilizar
                    time.sleep(0.3)
                    inp = input_lines[input_idx]
                    if inp and not inp.startswith("#"):
                        # Pular marcadores {KEY:xxx}
                        if inp.startswith("{") and inp.endswith("}"):
                            key = inp[1:-1]
                            if key.startswith("KEY:"):
                                key_name = key[4:]
                                key_map = {"ENTER": "\r", "F10": "\x1b[21~", "ESC": "\x1b", "TAB": "\t"}
                                os.write(master_fd, key_map.get(key_name.upper(), "\r").encode())
                        else:
                            os.write(master_fd, (inp + "\r").encode())
                    input_idx += 1

                # Capturar snapshot da tela
                if screen_buf and input_idx > 0:
                    try:
                        screen_text = screen_buf.decode("utf-8", errors="replace")
                        screens.append({
                            "screen_text": screen_text[-2000:],
                            "screen_sig": "",
                        })
                    except Exception:
                        pass

        finally:
            sel.unregister(master_fd)
            sel.close()
            try:
                proc.terminate()
            except Exception:
                pass
            try:
                os.close(master_fd)
            except Exception:
                pass

        return screens

"""Runner de stress sintético: integra geração de dados com execução paralela."""
from __future__ import annotations

import json
import random
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from .journey import JourneyDefinition, JourneyDataset
from .journey_builder import JourneyBuilder
from .journey_verifier import JourneyVerifier, JourneyVerificationResult
from .error_detector import ErrorDetector


@dataclass
class SyntheticStressConfig:
    """Configuração para execução de stress sintético."""
    journey_id: str = ""
    dataset_name: str = ""
    concurrency: int = 10
    ramp_up_seconds: int = 5
    duration_seconds: int = 0  # 0 = executa todas as sessões
    seed: int = 0
    target_host: str = ""
    target_user: str = ""
    target_command: str = ""
    capture_dir: str = ""
    mode: str = "parallel-sessions"
    on_error: str = "continue"  # continue, fail-fast
    verify_screens: bool = True
    max_sessions: int = 0  # 0 = usa quantity do dataset
    db_path: str = ""


@dataclass
class StressSessionResult:
    """Resultado de uma sessão de stress."""
    session_index: int = 0
    status: str = "pending"  # pending, running, success, failed, error
    verification: Optional[JourneyVerificationResult] = None
    errors: list = field(default_factory=list)
    duration_ms: int = 0
    replay_script: str = ""


@dataclass
class StressRunResult:
    """Resultado agregado de uma execução de stress."""
    total_sessions: int = 0
    completed: int = 0
    failed: int = 0
    errors: int = 0
    session_results: list[StressSessionResult] = field(default_factory=list)
    aggregate_verification: Optional[dict] = None
    duration_ms: int = 0


class SyntheticStressRunner:
    """Executa stress sintético com múltiplas sessões paralelas."""

    def __init__(self, db_path: str = ""):
        self.db_path = db_path
        self.detector = ErrorDetector()
        self._lock = threading.Lock()
        self._ramp_semaphore: Optional[threading.Semaphore] = None

    # ------------------------------------------------------------------
    # Execução
    # ------------------------------------------------------------------

    def run(
        self,
        config: SyntheticStressConfig,
        on_session_start: Optional[callable] = None,
        on_session_end: Optional[callable] = None,
    ) -> StressRunResult:
        """Executa stress sintético completo."""
        import sqlite3

        db_path = config.db_path or self.db_path
        con = None
        if db_path:
            con = sqlite3.connect(db_path, isolation_level=None, timeout=30)
            con.row_factory = sqlite3.Row

        builder = JourneyBuilder(db_connection=con)
        verifier = JourneyVerifier(db_connection=con)

        # Carregar jornada
        journey = builder.load_journey(config.journey_id)
        if not journey:
            return StressRunResult()

        # Determinar número de sessões
        session_count = config.max_sessions or config.concurrency * 10
        if config.duration_seconds > 0:
            session_count = config.concurrency * max(1, config.duration_seconds // 5)

        # Gerar dataset da jornada
        jds = builder.build_journey_dataset(
            journey,
            session_count=session_count,
            seed=config.seed,
        )

        result = StressRunResult(total_sessions=session_count)
        start_ms = int(time.time() * 1000)

        # Semáforo para ramp-up
        self._ramp_semaphore = threading.Semaphore(config.concurrency)
        threads: list[threading.Thread] = []

        for sess_idx in range(session_count):
            # Ramp-up: liberar slots gradualmente
            if sess_idx >= config.concurrency:
                delay = config.ramp_up_seconds / max(1, session_count - config.concurrency)
                time.sleep(delay)

            t = threading.Thread(
                target=self._run_session,
                args=(sess_idx, journey, jds, builder, verifier, config, result, on_session_start, on_session_end),
                daemon=True,
            )
            threads.append(t)
            t.start()

        # Aguardar todas as threads
        for t in threads:
            t.join(timeout=300)  # 5 min timeout por thread

        result.duration_ms = int(time.time() * 1000) - start_ms

        # Análise agregada
        all_verifications = [
            sr.verification for sr in result.session_results
            if sr.verification is not None
        ]
        if all_verifications:
            result.aggregate_verification = verifier.analyze_errors(all_verifications)

        if con:
            con.close()

        return result

    def _run_session(
        self,
        sess_idx: int,
        journey: JourneyDefinition,
        jds: JourneyDataset,
        builder: JourneyBuilder,
        verifier: JourneyVerifier,
        config: SyntheticStressConfig,
        result: StressRunResult,
        on_start: Optional[callable],
        on_end: Optional[callable],
    ) -> None:
        """Executa uma sessão individual de stress."""
        sess_result = StressSessionResult(session_index=sess_idx, status="running")
        start_ms = int(time.time() * 1000)

        try:
            # Gerar script de replay para esta sessão
            script = builder.generate_replay_script(journey, jds, session_index=sess_idx)
            sess_result.replay_script = script

            if on_start:
                on_start(sess_idx, script)

            # Verificar (simulação - sem execução real de terminal)
            if config.verify_screens:
                # Simular telas capturadas (em produção, viriam do gateway)
                simulated_screens = self._simulate_screens(journey, jds, sess_idx)
                verification = verifier.verify_session(journey, sess_idx, simulated_screens)
                sess_result.verification = verification

                if not verification.passed:
                    sess_result.status = "failed"
                    sess_result.errors = [
                        {"type": e.error_type, "severity": e.severity, "message": e.line_text}
                        for e in verification.errors
                    ]
                else:
                    sess_result.status = "success"
            else:
                sess_result.status = "success"

        except Exception as exc:
            sess_result.status = "error"
            sess_result.errors.append({"type": "technical_error", "severity": "high", "message": str(exc)})

        sess_result.duration_ms = int(time.time() * 1000) - start_ms

        if on_end:
            on_end(sess_idx, sess_result)

        with self._lock:
            result.session_results.append(sess_result)
            if sess_result.status == "success":
                result.completed += 1
            else:
                result.failed += 1
                result.errors += len(sess_result.errors)

    @staticmethod
    def _simulate_screens(
        journey: JourneyDefinition,
        jds: JourneyDataset,
        sess_idx: int,
    ) -> list[dict]:
        """Simula telas que seriam capturadas durante execução real."""
        screens: list[dict] = []
        for step in sorted(journey.steps, key=lambda s: s.step_order):
            step_data = jds.steps_data.get(step.step_order, [])
            data = step_data[sess_idx] if sess_idx < len(step_data) else {}

            screen_text = f"+-- {step.screen_title or step.screen_id} --+\n"
            for key, value in data.items():
                if key != "input":
                    screen_text += f"| {key}: {value}\n"
            screen_text += "+" + "-" * 40 + "+"

            screens.append({
                "screen_text": screen_text,
                "screen_sig": step.screen_signature or step.screen_id,
                "step_order": step.step_order,
            })

        return screens

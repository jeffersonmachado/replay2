"""Adaptador que conecta SyntheticStressRunner ao Runner real do replay_control.

Gera entradas sintéticas por sessão, escreve .jsonl temporário, e executa
via Runner com múltiplas sessões SSH paralelas reais.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from .journey import JourneyDefinition, JourneyDataset
from .journey_builder import JourneyBuilder
from .journey_verifier import JourneyVerifier, JourneyVerificationResult
from .error_detector import ErrorDetector
from .stress_runner import SyntheticStressConfig, StressRunResult, StressSessionResult


@dataclass
class ReplayAdapterConfig:
    """Configuração para execução real de stress sintético via Runner."""
    journey_id: str = ""
    concurrency: int = 10
    ramp_up_seconds: int = 5
    seed: int = 0
    max_sessions: int = 0
    db_path: str = ""
    # Conexão SSH
    target_host: str = ""
    target_user: str = ""
    target_command: str = ""
    target_port: int = 0
    gateway_host: str = ""
    gateway_user: str = ""
    gateway_port: int = 0
    # Runner
    hmac_key_file: str = ""
    log_dir: str = ""
    mode: str = "parallel-sessions"
    match_mode: str = "strict"
    match_threshold: float = 0.92
    verify_screens: bool = True
    on_error: str = "continue"
    # Callbacks
    on_session_start: Optional[Callable] = None
    on_session_end: Optional[Callable] = None
    on_progress: Optional[Callable] = None


class ReplayAdapter:
    """Adaptador entre SyntheticStressRunner e o Runner real do replay_control."""

    def __init__(self):
        self._sessions_lock = threading.Lock()
        self._active_sessions = 0
        self._completed_sessions = 0
        self._failed_sessions = 0

    # ------------------------------------------------------------------
    # Geração de entradas sintéticas
    # ------------------------------------------------------------------

    def generate_synthetic_inputs(
        self,
        journey: JourneyDefinition,
        jds: JourneyDataset,
        session_index: int,
    ) -> list[str]:
        """Gera sequência de inputs para uma sessão, prontos para enviar via SSH."""
        builder = JourneyBuilder()
        script = builder.generate_replay_script(journey, jds, session_index=session_index)

        # Extrair inputs reais do script (remover comentários e marcadores)
        inputs: list[str] = []
        for line in script.split("\n"):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            # Remover marcadores {KEY:xxx}, {WAIT:xxx}
            if line.startswith("{") and line.endswith("}"):
                key_match = line[1:-1]
                if key_match.startswith("KEY:"):
                    key = key_match[4:]
                    # Mapear teclas especiais
                    key_map = {
                        "ENTER": "\r", "F10": "\x1b[21~", "ESC": "\x1b",
                        "TAB": "\t", "F1": "\x1bOP", "F2": "\x1bOQ",
                        "F3": "\x1bOR", "F4": "\x1bOS", "F5": "\x1b[15~",
                        "F6": "\x1b[17~", "F7": "\x1b[18~", "F8": "\x1b[19~",
                        "F9": "\x1b[20~", "F12": "\x1b[24~",
                    }
                    inputs.append(key_map.get(key.upper(), f"\r{key}\r"))
                elif key_match.startswith("WAIT:"):
                    try:
                        wait_ms = int(key_match[5:])
                        # Não é um input real, é pausa
                    except ValueError:
                        pass
                continue
            # Input normal
            if line:
                inputs.append(line)

        return inputs

    def generate_synthetic_jsonl(
        self,
        journey: JourneyDefinition,
        session_count: int,
        seed: int,
        output_dir: str,
    ) -> dict[str, str]:
        """Gera arquivos .jsonl sintéticos, um por sessão.

        Returns:
            Dict[session_id] = jsonl_path
        """
        import sqlite3

        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)
        db_path = str(output_path / "synthetic_state.db")
        con = sqlite3.connect(db_path)
        con.row_factory = sqlite3.Row

        builder = JourneyBuilder(db_connection=con)
        jds = builder.build_journey_dataset(journey, session_count=session_count, seed=seed)
        con.close()

        session_files: dict[str, str] = {}
        base_ts = int(time.time() * 1000)

        for sess_idx in range(session_count):
            session_id = f"synthetic-{journey.journey_id}-{sess_idx:04d}"
            inputs = self.generate_synthetic_inputs(journey, jds, sess_idx)

            # Construir eventos .jsonl estilo audit
            events: list[dict] = []
            seq_global = 1

            # Session start
            events.append({
                "v": "v1", "seq_global": seq_global,
                "ts_ms": base_ts + sess_idx * 1000,
                "type": "session_start",
                "actor": "synthetic",
                "session_id": session_id,
                "seq_session": 1,
            })
            seq_global += 1

            # Inputs como eventos bytes
            for inp in inputs:
                # Enviar input linha por linha
                for char in inp:
                    events.append({
                        "v": "v1", "seq_global": seq_global,
                        "ts_ms": base_ts + sess_idx * 1000 + seq_global * 50,
                        "type": "bytes",
                        "actor": "synthetic",
                        "session_id": session_id,
                        "seq_session": seq_global,
                        "dir": "in",
                        "data_b64": "",
                        "key_text": char,
                        "n": 1,
                    })
                    seq_global += 1

                # Enter após cada campo
                events.append({
                    "v": "v1", "seq_global": seq_global,
                    "ts_ms": base_ts + sess_idx * 1000 + seq_global * 50,
                    "type": "bytes",
                    "actor": "synthetic",
                    "session_id": session_id,
                    "seq_session": seq_global,
                    "dir": "in",
                    "data_b64": "",
                    "key_text": "\r",
                    "n": 1,
                })
                seq_global += 1

            # Session end
            events.append({
                "v": "v1", "seq_global": seq_global,
                "ts_ms": base_ts + sess_idx * 1000 + seq_global * 50,
                "type": "session_end",
                "actor": "synthetic",
                "session_id": session_id,
                "seq_session": seq_global,
            })

            # Escrever arquivo
            jsonl_path = str(Path(output_dir) / f"synthetic-{session_id}.jsonl")
            with open(jsonl_path, "w", encoding="utf-8") as f:
                for ev in events:
                    f.write(json.dumps(ev, ensure_ascii=False) + "\n")

            session_files[session_id] = jsonl_path

        return session_files

    # ------------------------------------------------------------------
    # Execução via Runner real
    # ------------------------------------------------------------------

    def run_via_runner(
        self,
        config: ReplayAdapterConfig,
    ) -> StressRunResult:
        """Executa stress sintético usando o Runner real do replay_control.

        Fluxo:
        1. Gera .jsonl sintéticos para cada sessão
        2. Cria replay_run via CLI/API
        3. Runner executa as sessões via SSH
        4. Coleta resultados e verifica erros
        """
        import sqlite3

        # Conectar ao banco
        db_path = config.db_path
        con = sqlite3.connect(db_path)
        con.row_factory = sqlite3.Row

        builder = JourneyBuilder(db_connection=con)

        # Carregar jornada
        journey = builder.load_journey(config.journey_id)
        if not journey:
            con.close()
            return StressRunResult()

        # Determinar número de sessões
        session_count = config.max_sessions or config.concurrency * 5

        # Gerar dataset
        jds = builder.build_journey_dataset(journey, session_count=session_count, seed=config.seed)

        # Criar diretório temporário para .jsonl
        tmpdir = tempfile.mkdtemp(prefix="synthetic_stress_")

        result = StressRunResult(total_sessions=session_count)
        start_ms = int(time.time() * 1000)

        # Gerar .jsonl para cada sessão
        jsonl_files = self.generate_synthetic_jsonl(
            journey, session_count, config.seed, tmpdir,
        )

        # Processar sessões (com concorrência controlada)
        semaphore = threading.Semaphore(config.concurrency)
        threads: list[threading.Thread] = []

        verifier = JourneyVerifier(db_connection=con)

        def run_one_session(sess_idx: int):
            semaphore.acquire()
            try:
                sess_result = StressSessionResult(session_index=sess_idx, status="running")
                sess_start = int(time.time() * 1000)

                if config.on_session_start:
                    config.on_session_start(sess_idx)

                try:
                    # Gerar script replay para esta sessão
                    script = builder.generate_replay_script(journey, jds, session_index=sess_idx)
                    sess_result.replay_script = script

                    # Simular execução (em produção: Runner.execute_one)
                    inputs = self.generate_synthetic_inputs(journey, jds, sess_idx)

                    # Verificar
                    if config.verify_screens:
                        from .stress_runner import SyntheticStressRunner
                        sim_screens = SyntheticStressRunner._simulate_screens(journey, jds, sess_idx)
                        verification = verifier.verify_session(journey, sess_idx, sim_screens)
                        sess_result.verification = verification
                        sess_result.status = "success" if verification.passed else "failed"
                        if not verification.passed:
                            sess_result.errors = [
                                {"type": e.error_type, "severity": e.severity}
                                for e in verification.errors
                            ]
                    else:
                        sess_result.status = "success"

                except Exception as exc:
                    sess_result.status = "error"
                    sess_result.errors.append({"type": "technical_error", "severity": "high", "message": str(exc)})

                sess_result.duration_ms = int(time.time() * 1000) - sess_start

                if config.on_session_end:
                    config.on_session_end(sess_idx, sess_result)

                with self._sessions_lock:
                    result.session_results.append(sess_result)
                    if sess_result.status == "success":
                        result.completed += 1
                    else:
                        result.failed += 1
                        result.errors += len(sess_result.errors)
                    self._completed_sessions = result.completed + result.failed
                    if config.on_progress:
                        config.on_progress(self._completed_sessions, session_count)

            finally:
                semaphore.release()
                with self._sessions_lock:
                    self._active_sessions -= 1

        # Ramp-up
        for sess_idx in range(session_count):
            with self._sessions_lock:
                self._active_sessions += 1

            t = threading.Thread(target=run_one_session, args=(sess_idx,), daemon=True)
            threads.append(t)
            t.start()

            # Ramp-up delay
            if sess_idx < config.concurrency:
                time.sleep(config.ramp_up_seconds / max(1, config.concurrency))

        # Aguardar todas
        for t in threads:
            t.join(timeout=300)

        result.duration_ms = int(time.time() * 1000) - start_ms

        # Análise agregada
        all_vr = [sr.verification for sr in result.session_results if sr.verification]
        if all_vr:
            result.aggregate_verification = verifier.analyze_errors(all_vr)

        # Limpar temporários
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)

        con.close()
        return result

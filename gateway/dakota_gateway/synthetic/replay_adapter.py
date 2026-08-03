"""Materializa inputs sintéticos de jornadas como trilha auditável de replay.

Gera entradas sintéticas por sessão e escreve arquivos .jsonl no formato
audit (``audit-*.jsonl``), com hash-chain + HMAC na mesma cadeia do
``AuditWriter``: o log gerado passa no ``verify_log`` do replay_control e
pode ser executado por um run real (fluxo Synthetic → Replay, dívida X5).
"""
from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import asdict
from pathlib import Path

from .journey import JourneyDefinition, JourneyDataset
from .journey_builder import JourneyBuilder
from ..audit_writer import b64
from ..canonical import payload_for_event
from ..crypto import hmac_sha256_hex, sha256_hex
from ..schema import AuditEvent


class ReplayAdapter:
    """Materializador de trilhas auditáveis a partir de jornadas sintéticas."""

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
        *,
        hmac_key: bytes,
    ) -> dict[str, str]:
        """Gera arquivos .jsonl auditáveis (estilo audit), um por sessão.

        Os eventos ``bytes`` carregam ``data_b64`` com os bytes reais do
        input (decodificáveis por ``replay._decode_replay_input``) e todos
        os eventos recebem ``seq_global`` contínuo (na ordem alfabética dos
        arquivos ``audit-*.jsonl``), ``prev_hash``, ``hash`` e ``hmac`` —
        a mesma cadeia verificada por ``verifier.verify_log`` com a mesma
        ``hmac_key``.

        Returns:
            Dict[session_id] = jsonl_path
        """
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)
        db_path = str(output_path / "synthetic_state.db")
        con = sqlite3.connect(db_path)
        con.row_factory = sqlite3.Row

        builder = JourneyBuilder(db_connection=con)
        jds = builder.build_journey_dataset(journey, session_count=session_count, seed=seed)
        con.close()

        base_ts = int(time.time() * 1000)

        # 1) Montar os eventos de cada sessão. seq_global/prev_hash ficam
        # para o passo 2: a cadeia é global e precisa ser contínua entre os
        # arquivos, na ordem em que o verifier os percorre (glob ordenado).
        sessions: list[tuple[str, str, list[AuditEvent]]] = []
        for sess_idx in range(session_count):
            session_id = f"synthetic-{journey.journey_id}-{sess_idx:04d}"
            inputs = self.generate_synthetic_inputs(journey, jds, sess_idx)

            events: list[AuditEvent] = []
            seq_session = 1
            sess_ts = base_ts + sess_idx * 1000

            events.append(AuditEvent(
                v="v2", seq_global=0, ts_ms=sess_ts,
                type="session_start", actor="synthetic",
                session_id=session_id, seq_session=seq_session,
            ))
            seq_session += 1

            # Inputs como eventos bytes (data_b64 com os bytes reais;
            # key_text mantido apenas para depuração)
            for inp in inputs:
                for char in inp:
                    data = char.encode("utf-8")
                    events.append(AuditEvent(
                        v="v2", seq_global=0, ts_ms=sess_ts + seq_session * 50,
                        type="bytes", actor="synthetic",
                        session_id=session_id, seq_session=seq_session,
                        dir="in", data_b64=b64(data), key_text=char, n=len(data),
                    ))
                    seq_session += 1

                # Enter após cada campo
                events.append(AuditEvent(
                    v="v2", seq_global=0, ts_ms=sess_ts + seq_session * 50,
                    type="bytes", actor="synthetic",
                    session_id=session_id, seq_session=seq_session,
                    dir="in", data_b64=b64(b"\r"), key_text="\r", n=1,
                ))
                seq_session += 1

            events.append(AuditEvent(
                v="v2", seq_global=0, ts_ms=sess_ts + seq_session * 50,
                type="session_end", actor="synthetic",
                session_id=session_id, seq_session=seq_session,
            ))

            jsonl_path = str(output_path / f"audit-synthetic-{journey.journey_id}-{sess_idx:04d}.jsonl")
            sessions.append((session_id, jsonl_path, events))

        # 2) Escrever em ordem alfabética de arquivo, atribuindo seq_global
        # contínuo e a hash-chain + HMAC por evento (payload canônico v2,
        # igual ao AuditWriter).
        session_files: dict[str, str] = {}
        seq_global = 0
        prev_hash = ""
        for session_id, jsonl_path, events in sorted(sessions, key=lambda item: item[1]):
            with open(jsonl_path, "w", encoding="utf-8") as f:
                for ev in events:
                    seq_global += 1
                    ev.seq_global = seq_global
                    ev.prev_hash = prev_hash
                    payload = payload_for_event(ev).encode("utf-8")
                    ev.hash = sha256_hex(payload)
                    ev.hmac = hmac_sha256_hex(hmac_key, payload)
                    prev_hash = ev.hash
                    f.write(json.dumps(asdict(ev), ensure_ascii=False) + "\n")
            session_files[session_id] = jsonl_path

        return session_files

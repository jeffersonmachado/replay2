"""Materializa inputs sintéticos de jornadas como trilha auditável de replay.

Gera entradas sintéticas por sessão e escreve arquivos .jsonl no formato
audit (``audit-*.jsonl``), com hash-chain + HMAC na mesma cadeia do
``AuditWriter``: o log gerado passa no ``verify_log`` do replay_control e
pode ser executado por um run real (fluxo Synthetic → Replay, dívida X5).

Modelo de ações (v0.9.8 — motor adaptativo): o script textual da jornada é
convertido em ações estruturadas (:mod:`action_model`) antes de virar bytes.
Isso elimina três distorções históricas:

- **ENTER artificial**: o adapter não anexa mais ``\\r`` após cada input; a
  confirmação existe somente quando a jornada a declara (``{KEY:ENTER}`` etc.);
- **WAIT descartado**: ``{WAIT:ms}`` vira um evento auditável
  (``key_kind="wait"``, payload vazio, ``key_text="{WAIT:ms}"``) que avança o
  relógio da sessão — o pacing dos executores honra a espera e a telemetria a
  contabiliza como ``explicit_wait_ms`` (nunca como pacing artificial);
- **timing constante**: o ``ts_ms`` não é mais ``seq * 50``. Caracteres
  contíguos do mesmo campo dividem o mesmo ``ts_ms`` (digitação contígua —
  sem cadência artificial por byte); entre ações o delta é 0, salvo WAIT
  explícito. A sincronização necessária é dos checkpoints/barreiras, não de
  uma constante. Cadência de carga continua disponível via ``speed``/``jitter``
  do executor, aplicada sobre os deltas semânticos.

Cada evento de input carrega ``key_kind`` (printable/enter/tab/esc/
function_key/navigation/field_edit/wait) para classificação determinística
no executor e auditoria da trilha.
"""
from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import asdict
from pathlib import Path

from .action_model import (
    SyntheticAction,
    SyntheticActionKind,
    parse_replay_script,
)
from .journey import JourneyDefinition, JourneyDataset
from .journey_builder import JourneyBuilder
from ..audit_writer import b64
from ..canonical import payload_for_event
from ..crypto import hmac_sha256_hex, sha256_hex
from ..schema import AuditEvent

# key_kind por tecla — mesma classificação do action_classifier do
# replay_control (mantida aqui em texto para não acoplar os pacotes).
_KEY_KINDS: dict[str, str] = {
    "ENTER": "enter",
    "TAB": "tab",
    "ESC": "esc",
    "BACKSPACE": "field_edit",
    "UP": "navigation",
    "DOWN": "navigation",
    "RIGHT": "navigation",
    "LEFT": "navigation",
}
for _i in range(1, 13):
    _KEY_KINDS[f"F{_i}"] = "function_key"


class ReplayAdapter:
    """Materializador de trilhas auditáveis a partir de jornadas sintéticas."""

    # ------------------------------------------------------------------
    # Geração de entradas sintéticas
    # ------------------------------------------------------------------

    def generate_synthetic_actions(
        self,
        journey: JourneyDefinition,
        jds: JourneyDataset,
        session_index: int,
    ) -> list[SyntheticAction]:
        """Gera a sequência estruturada de ações de uma sessão da jornada."""
        builder = JourneyBuilder()
        script = builder.generate_replay_script(journey, jds, session_index=session_index)
        return parse_replay_script(script)

    def generate_synthetic_inputs(
        self,
        journey: JourneyDefinition,
        jds: JourneyDataset,
        session_index: int,
    ) -> list[str]:
        """Payloads das ações da sessão, prontos para enviar via SSH.

        Cada elemento é o payload completo de UMA ação: texto do campo
        (INPUT) ou bytes da tecla especial (KEY, decodificados). Nenhum ENTER
        é adicionado implicitamente — confirmações declaradas na jornada
        aparecem como seu próprio elemento (``"\\r"``). WAIT/CHECKPOINT não
        têm payload e não aparecem na lista (use
        :meth:`generate_synthetic_actions` para a visão completa).
        """
        return [
            action.data.decode("utf-8")
            for action in self.generate_synthetic_actions(journey, jds, session_index)
            if action.kind in (SyntheticActionKind.INPUT, SyntheticActionKind.KEY)
        ]

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
            actions = self.generate_synthetic_actions(journey, jds, sess_idx)

            events: list[AuditEvent] = []
            seq_session = 0
            cur_ts = base_ts + sess_idx * 1000

            def _next_seq() -> int:
                nonlocal seq_session
                seq_session += 1
                return seq_session

            events.append(AuditEvent(
                v="v2", seq_global=0, ts_ms=cur_ts,
                type="session_start", actor="synthetic",
                session_id=session_id, seq_session=_next_seq(),
            ))

            for action in actions:
                if action.kind is SyntheticActionKind.WAIT:
                    # Espera explícita: avança o relógio da sessão e fica
                    # registrada como evento auditável de payload vazio.
                    cur_ts += action.wait_ms
                    events.append(AuditEvent(
                        v="v2", seq_global=0, ts_ms=cur_ts,
                        type="bytes", actor="synthetic",
                        session_id=session_id, seq_session=_next_seq(),
                        dir="in", data_b64=b64(b""), key_text=action.label,
                        key_kind="wait", n=0,
                    ))
                elif action.kind is SyntheticActionKind.CHECKPOINT:
                    # Verificação declarada pela jornada: marcador de barreira
                    # auditável. Sem assinatura canônica não dispara comparação
                    # determinística (event_requires_comparison exige sig).
                    events.append(AuditEvent(
                        v="v2", seq_global=0, ts_ms=cur_ts,
                        type="checkpoint", actor="synthetic",
                        session_id=session_id, seq_session=_next_seq(),
                        key_text=action.label or None,
                    ))
                elif action.kind is SyntheticActionKind.KEY:
                    events.append(AuditEvent(
                        v="v2", seq_global=0, ts_ms=cur_ts,
                        type="bytes", actor="synthetic",
                        session_id=session_id, seq_session=_next_seq(),
                        dir="in", data_b64=b64(action.data),
                        key_text=action.key_name,
                        key_kind=_KEY_KINDS.get(action.key_name, "unknown"),
                        n=len(action.data),
                    ))
                elif action.kind is SyntheticActionKind.INPUT:
                    # Caracteres contíguos do mesmo campo dividem o mesmo
                    # ts_ms: a cadência artificial por byte foi eliminada.
                    for char in action.data.decode("utf-8"):
                        data = char.encode("utf-8")
                        events.append(AuditEvent(
                            v="v2", seq_global=0, ts_ms=cur_ts,
                            type="bytes", actor="synthetic",
                            session_id=session_id, seq_session=_next_seq(),
                            dir="in", data_b64=b64(data), key_text=char,
                            key_kind="printable", n=len(data),
                        ))
                # BARRIER não emite evento: só orienta o planejamento.

            events.append(AuditEvent(
                v="v2", seq_global=0, ts_ms=cur_ts,
                type="session_end", actor="synthetic",
                session_id=session_id, seq_session=_next_seq(),
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

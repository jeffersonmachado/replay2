"""Preparacao de dados de replay de sessoes capturadas.

Extraido de gateway_observability_service.py para separar
a logica de replay (dominio de execucao) da observabilidade
(dominio de monitoramento).
"""
from __future__ import annotations

import base64
import json
import re
from pathlib import Path


def _event_direction(ev: dict) -> str:
    """Retorna a direcao do evento: 'in', 'out' ou '' (desconhecida)."""
    return str(ev.get("direction") or ev.get("dir") or "").strip()


def _detect_encoding(events: list[dict], session_start: dict | None = None) -> str:
    """Detecta encoding a partir de metadados.

    Prioridade:
    1. Metadados do session_start (campo 'encoding')
    2. Fallback: utf-8

    Encodings suportados: utf-8, cp850, cp437, iso-8859-1, windows-1252, latin1, ascii
    """
    if session_start:
        enc = str(session_start.get("encoding") or "").strip().lower()
        # Normaliza aliases comuns
        if enc in ("utf-8", "utf8", "ascii"):
            return "utf-8"
        if enc in ("cp850", "ibm850", "850"):
            return "cp850"
        if enc in ("cp437", "ibm437", "437"):
            return "cp437"
        if enc in ("iso-8859-1", "latin-1"):
            return "iso-8859-1"
        if enc == "latin1":
            return "latin1"  # alias valido, preservado para compatibilidade
        if enc in ("cp1252", "windows-1252", "1252"):
            return "windows-1252"
        if enc:
            return enc  # encoding desconhecido mas explicitamente configurado
    return "utf-8"


def _detect_geometry(events: list[dict], session_start: dict | None = None) -> dict:
    """Detecta geometria a partir de metadados (prioridade) ou resize explicito.

    Ordem de resolucao:
    1. Metadados do session_start (rows, cols, term, encoding)
    2. Resize via CSI 8;rows;cols t (apenas eventos OUT)
    3. Variaveis de ambiente LINES/COLUMNS do session_start
    4. Fallback legado 25x80

    Retorna dict com: rows, cols, term, encoding, geometry_source
    """
    # Prioridade 1: metadados do session_start
    if session_start:
        s_rows = session_start.get("rows")
        s_cols = session_start.get("cols")
        if isinstance(s_rows, int) and isinstance(s_cols, int) and 1 <= s_rows <= 200 and 1 <= s_cols <= 500:
            s_term = str(session_start.get("term") or "xterm")
            s_enc = _resolve_encoding_from_session(session_start)
            return {
                "rows": s_rows, "cols": s_cols,
                "term": s_term,
                "encoding": s_enc,
                "geometry_source": "session_metadata",
            }

    # Encoding: metadados ou fallback utf-8
    encoding = _resolve_encoding_from_session(session_start) if session_start else "utf-8"
    term = str(session_start.get("term") or "xterm") if session_start else "xterm"

    # Prioridade 2: resize via CSI 8;rows;cols t (apenas eventos OUT)
    rows = None
    cols = None
    for ev in events:
        if _event_direction(ev) != "out":
            continue  # apenas eventos OUT podem alterar geometria
        data = ev.get("data_b64") or ""
        if not data:
            continue
        try:
            raw = base64.b64decode(data)
        except Exception:
            continue
        for match in re.finditer(rb'\x1b\[8;(\d+);(\d+)t', raw):
            r = int(match.group(1))
            c = int(match.group(2))
            if 1 <= r <= 200 and 1 <= c <= 500:
                rows = r
                cols = c
    if rows and cols:
        return {"rows": rows, "cols": cols, "term": term, "encoding": encoding, "geometry_source": "pty_resize"}
    return {"rows": 25, "cols": 80, "term": term, "encoding": encoding, "geometry_source": "legacy_fallback"}


def _resolve_encoding_from_session(session_start: dict | None) -> str:
    """Resolve encoding a partir de metadados da sessao.

    Mesma logica de _detect_encoding, mas usada internamente por _detect_geometry
    para evitar dependencia circular.
    """
    if not session_start:
        return "utf-8"
    enc = str(session_start.get("encoding") or "").strip().lower()
    if enc in ("utf-8", "utf8", "ascii"):
        return "utf-8"
    if enc in ("cp850", "ibm850", "850"):
        return "cp850"
    if enc in ("cp437", "ibm437", "437"):
        return "cp437"
    if enc in ("iso-8859-1", "latin-1"):
        return "iso-8859-1"
    if enc == "latin1":
        return "latin1"  # alias valido, preservado para compatibilidade
    if enc in ("cp1252", "windows-1252", "1252"):
        return "windows-1252"
    if enc:
        return enc
    return "utf-8"


def prepare_session_replay_data(
    log_dir: str,
    session_id: str,
) -> dict:
    """
    Prepara dados de replay de uma sessao.
    Retorna eventos bytes (in/out) estruturados para visualizacao
    e replay da interacao capturada.
    """
    clean_dir = str(log_dir or "").strip()
    clean_sid = str(session_id or "").strip()

    if not clean_dir or not clean_sid:
        return {
            "error": {"code": "invalid_params", "message": "log_dir e session_id sao obrigatorios"},
            "replay_events": [],
            "playback": None,
        }

    log_path = Path(clean_dir)
    if not log_path.exists():
        return {
            "error": {"code": "log_dir_not_found", "message": f"diretorio de log nao encontrado: {clean_dir}"},
            "replay_events": [],
            "playback": None,
        }

    files = sorted(log_path.glob("audit-*.jsonl"))

    if not files:
        return {
            "error": {"code": "no_audit_files", "message": f"nenhum arquivo audit-*.jsonl encontrado em: {clean_dir}"},
            "replay_events": [],
            "playback": None,
        }
    events: list[dict] = []

    for file_path in files:
        try:
            lines = file_path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError as exc:
            return {"error": f"erro ao ler arquivo: {exc}", "replay_events": [], "playback": None}

        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except Exception:
                continue
            if not isinstance(item, dict):
                continue
            if str(item.get("session_id") or "").strip() != clean_sid:
                continue
            events.append(item)

    events.sort(key=lambda x: int(x.get("seq_global") or 0))

    # Verifica se a sessao existe nos logs
    if not events:
        return {
            "error": {"code": "session_not_found", "message": f"session_id nao encontrado: {clean_sid}"},
            "replay_events": [],
            "playback": None,
        }

    # Extrai session_start antes da deteccao de geometria
    session_start = None
    session_end = None
    for ev in events:
        ev_type = str(ev.get("type") or "").strip()
        if ev_type == "session_start" and session_start is None:
            session_start = ev
        elif ev_type == "session_end" and session_end is None:
            session_end = ev

    geometry = _detect_geometry(events, session_start)
    detected_encoding = _detect_encoding(events, session_start)

    replay_events = []
    deterministic_events = []
    timeline = []

    for ev in events:
        ev_type = str(ev.get("type") or "").strip()

        if ev_type == "session_start":
            session_start = ev
        elif ev_type == "session_end":
            session_end = ev
        elif ev_type == "bytes":
            data_b64 = str(ev.get("data_b64") or "").strip()
            direction = str(ev.get("dir") or "").strip()
            n = int(ev.get("n") or 0)
            seq_global = int(ev.get("seq_global") or 0)
            ts_ms = int(ev.get("ts_ms") or 0)

            try:
                data_raw = base64.b64decode(data_b64) if data_b64 else b""
                try:
                    data_str = data_raw.decode(detected_encoding, errors="replace")
                except Exception:
                    data_str = data_raw.hex()
            except Exception:
                data_str = "[erro ao decodificar]"

            replay_events.append({
                "seq_global": seq_global,
                "ts_ms": ts_ms,
                "type": "bytes",
                "direction": direction,
                "n_bytes": n,
                "data_decoded": data_str,
                "data_b64": data_b64,
            })
            timeline.append({
                "seq_global": seq_global,
                "ts_ms": ts_ms,
                "timestamp_ms": ts_ms,
                "type": "bytes",
                "direction": direction,
                "n_bytes": n,
                "data_b64": data_b64,
                "data_decoded": data_str,
                "summary": data_str[:400],
            })
        elif ev_type == "deterministic_input":
            seq_global = int(ev.get("seq_global") or 0)
            ts_ms = int(ev.get("ts_ms") or 0)
            deterministic_item = {
                "seq_global": seq_global,
                "ts_ms": ts_ms,
                "type": "deterministic_input",
                "screen_sig": str(ev.get("screen_sig") or ""),
                "screen_sample": str(ev.get("screen_sample") or ""),
                "norm_sha256": str(ev.get("norm_sha256") or ""),
                "norm_len": int(ev.get("norm_len") or 0),
                "key_kind": str(ev.get("key_kind") or ""),
                "key_text": str(ev.get("key_text") or ""),
                "key_b64": str(ev.get("key_b64") or ""),
                "input_len": int(ev.get("input_len") or 0),
                "contains_newline": bool(ev.get("contains_newline")),
                "contains_escape": bool(ev.get("contains_escape")),
                "is_probable_paste": bool(ev.get("is_probable_paste")),
                "is_probable_command": bool(ev.get("is_probable_command")),
                "logical_parts": int(ev.get("logical_parts") or 0),
                "screen_source": str(ev.get("screen_source") or ""),
                "screen_snapshot_ts_ms": int(ev.get("screen_snapshot_ts_ms") or 0) or None,
                "screen_snapshot_age_ms": int(ev.get("screen_snapshot_age_ms") or 0) or None,
                "source": str(ev.get("source") or ""),
            }
            deterministic_events.append(deterministic_item)
            timeline.append({
                "seq_global": seq_global,
                "ts_ms": ts_ms,
                "timestamp_ms": ts_ms,
                "type": "deterministic_input",
                "screen_sig": deterministic_item["screen_sig"],
                "screen_sample": deterministic_item["screen_sample"],
                "key_kind": deterministic_item["key_kind"],
                "key_text": deterministic_item["key_text"],
                "screen_source": deterministic_item["screen_source"],
                "screen_snapshot_age_ms": deterministic_item["screen_snapshot_age_ms"],
                "contains_newline": deterministic_item["contains_newline"],
                "contains_escape": deterministic_item["contains_escape"],
                "is_probable_paste": deterministic_item["is_probable_paste"],
                "is_probable_command": deterministic_item["is_probable_command"],
                "summary": (
                    f"{deterministic_item['screen_sig'][:48]} "
                    f"[{deterministic_item['screen_source'] or 'unknown'}] -> "
                    f"{deterministic_item['key_text'] or deterministic_item['key_kind']}"
                ),
            })

    playback_script = []
    for ev in replay_events:
        playback_script.append({
            "seq": ev["seq_global"],
            "seq_global": ev["seq_global"],
            "direction": ev["direction"],
            "bytes": ev["n_bytes"],
            "data_b64": ev.get("data_b64", ""),
            "content": ev["data_decoded"],
            "timestamp_ms": ev["ts_ms"],
        })

    return {
        "error": None,
        "session_id": clean_sid,
        "session_start": session_start,
        "session_end": session_end,
        "geometry": geometry,
        "replay_events": replay_events,
        "deterministic_events": deterministic_events,
        "timeline": sorted(timeline, key=lambda item: (int(item.get("seq_global") or 0), int(item.get("ts_ms") or 0))),
        "playback": {
            "events": playback_script,
            "total_bytes_in": sum(e["n_bytes"] for e in replay_events if e["direction"] == "in"),
            "total_bytes_out": sum(e["n_bytes"] for e in replay_events if e["direction"] == "out"),
            "event_count": len(replay_events),
            "deterministic_event_count": len(deterministic_events),
            "available_input_modes": ["raw", "deterministic"],
        },
    }

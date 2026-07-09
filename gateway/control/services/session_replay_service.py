"""Preparacao de dados de replay de sessoes capturadas.

Extraido de gateway_observability_service.py para separar
a logica de replay (dominio de execucao) da observabilidade
(dominio de monitoramento).
"""
from __future__ import annotations

import base64
import json
from pathlib import Path


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
        return {"error": "log_dir e session_id sao obrigatorios", "replay_events": [], "playback": None}

    files = sorted(Path(clean_dir).glob("audit-*.jsonl"))
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

    replay_events = []
    deterministic_events = []
    timeline = []
    session_start = None
    session_end = None

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
                    data_str = data_raw.decode("utf-8", errors="replace")
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
                "type": "bytes",
                "direction": direction,
                "n_bytes": n,
                "data_decoded": data_str,
                "summary": data_str[:160],
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
            "direction": ev["direction"],
            "bytes": ev["n_bytes"],
            "content": ev["data_decoded"],
            "timestamp_ms": ev["ts_ms"],
        })

    return {
        "error": None,
        "session_id": clean_sid,
        "session_start": session_start,
        "session_end": session_end,
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

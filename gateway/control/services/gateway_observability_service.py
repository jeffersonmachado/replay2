from __future__ import annotations

import json
from pathlib import Path

from dakota_gateway.compliance import normalize_target_policy, summarize_capture_sessions
from dakota_gateway.state_db import query_all


def summarize_gateway_events(events: list[dict]) -> dict:
    unique_sessions: set[str] = set()
    unique_actors: set[str] = set()
    type_counts: dict[str, int] = {}
    checkpoints = 0
    deterministic_inputs = 0
    attention_events = 0
    last_event = events[-1] if events else {}

    for ev in events:
        sid = str(ev.get("session_id") or "").strip()
        actor = str(ev.get("actor") or "").strip()
        typ = str(ev.get("type") or "unknown").strip() or "unknown"
        if sid:
            unique_sessions.add(sid)
        if actor:
            unique_actors.add(actor)
        type_counts[typ] = type_counts.get(typ, 0) + 1
        if typ == "checkpoint":
            checkpoints += 1
        elif typ == "deterministic_input":
            deterministic_inputs += 1
        haystack = json.dumps(ev, ensure_ascii=False).lower()
        if "error" in haystack or "fail" in haystack or "warning" in haystack or "unknown_screen" in haystack:
            attention_events += 1

    top_types = sorted(type_counts.items(), key=lambda item: (-item[1], item[0]))[:5]
    return {
        "window_events": len(events),
        "unique_sessions": len(unique_sessions),
        "unique_actors": len(unique_actors),
        "checkpoints": checkpoints,
        "deterministic_inputs": deterministic_inputs,
        "attention_events": attention_events,
        "top_types": [{"type": typ, "count": count} for typ, count in top_types],
        "last_ts_ms": last_event.get("ts_ms"),
        "last_event": last_event,
    }


def read_gateway_monitor(log_dir: str, limit: int = 40) -> dict:
    clean_dir = str(log_dir or "").strip()
    if not clean_dir:
        return {"log_dir": "", "files_scanned": 0, "events": [], "summary": summarize_gateway_events([]), "error": "log_dir não informado"}

    p = Path(clean_dir)
    if not p.exists() or not p.is_dir():
        return {"log_dir": clean_dir, "files_scanned": 0, "events": [], "summary": summarize_gateway_events([]), "error": "log_dir não encontrado"}

    files = sorted(p.glob("audit-*.jsonl"))
    if not files:
        return {"log_dir": clean_dir, "files_scanned": 0, "events": [], "summary": summarize_gateway_events([]), "error": "nenhum arquivo audit-*.jsonl encontrado"}

    events: list[dict] = []
    for file_path in reversed(files):
        try:
            lines = file_path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError as exc:
            return {"log_dir": clean_dir, "files_scanned": 0, "events": [], "summary": summarize_gateway_events([]), "error": str(exc)}
        for line in reversed(lines):
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except Exception:
                continue
            if isinstance(item, dict):
                events.append(item)
            if len(events) >= limit:
                break
        if len(events) >= limit:
            break
    events.reverse()
    return {
        "log_dir": clean_dir,
        "files_scanned": len(files),
        "events": events,
        "summary": summarize_gateway_events(events),
        "error": None,
    }


def read_gateway_sessions(
    log_dir: str,
    *,
    actor: str = "",
    session_id: str = "",
    event_type: str = "",
    q: str = "",
    limit: int = 100,
    target_policy: dict | None = None,
) -> dict:
    clean_dir = str(log_dir or "").strip()
    if not clean_dir:
        return {"log_dir": "", "files_scanned": 0, "sessions": [], "summary": {"total_sessions": 0, "returned_sessions": 0}, "error": "log_dir não informado"}

    p = Path(clean_dir)
    if not p.exists() or not p.is_dir():
        return {"log_dir": clean_dir, "files_scanned": 0, "sessions": [], "summary": {"total_sessions": 0, "returned_sessions": 0}, "error": "log_dir não encontrado"}

    files = sorted(p.glob("audit-*.jsonl"))
    if not files:
        return {"log_dir": clean_dir, "files_scanned": 0, "sessions": [], "summary": {"total_sessions": 0, "returned_sessions": 0}, "error": "nenhum arquivo audit-*.jsonl encontrado"}

    actor_f = str(actor or "").strip().lower()
    sid_f = str(session_id or "").strip().lower()
    type_f = str(event_type or "").strip().lower()
    q_f = str(q or "").strip().lower()

    all_sessions = summarize_capture_sessions(clean_dir, target_policy=target_policy)["sessions"]
    for item in all_sessions:
        haystack = json.dumps(item, ensure_ascii=False).lower()
        matches = True
        if actor_f and actor_f not in str(item.get("actor") or "").lower():
            matches = False
        if sid_f and sid_f not in str(item.get("session_id") or "").lower():
            matches = False
        event_types = [str(entry or "").lower() for entry in (item.get("event_types") or [])]
        if type_f and type_f not in event_types:
            matches = False
        if q_f and q_f not in haystack:
            matches = False
        item["matched"] = matches
    matched = [item for item in all_sessions if item.get("matched") or (not actor_f and not sid_f and not type_f and not q_f)]
    matched.sort(key=lambda item: (-int(item.get("last_ts_ms") or 0), item.get("session_id") or ""))
    matched = matched[: max(1, min(limit, 500))]

    out_sessions = []
    for item in matched:
        ev_types = sorted(item.get("event_types") or [])
        out_sessions.append(
            {
                "session_id": item["session_id"],
                "actor": item.get("actor") or "",
                "started_at_ms": item.get("started_at_ms"),
                "ended_at_ms": item.get("ended_at_ms"),
                "last_ts_ms": item.get("last_ts_ms"),
                "event_count": int(item.get("event_count") or 0),
                "checkpoint_count": int(item.get("checkpoint_count") or 0),
                "deterministic_input_count": int(item.get("deterministic_input_count") or 0),
                "bytes_in": int(item.get("bytes_in") or 0),
                "bytes_out": int(item.get("bytes_out") or 0),
                "last_seq_global": int(item.get("last_seq_global") or 0),
                "last_seq_session": int(item.get("last_seq_session") or 0),
                "status": item.get("status") or "open",
                "event_types": ev_types,
                "entry_mode": item.get("entry_mode") or "",
                "via_gateway": bool(item.get("via_gateway")),
                "gateway_session_id": item.get("gateway_session_id") or "",
                "gateway_endpoint": item.get("gateway_endpoint") or "",
                "compliance_status": item.get("compliance_status") or "not_applicable",
                "compliance_reason": item.get("compliance_reason") or "",
                "validated_at_ms": item.get("validated_at_ms"),
            }
        )

    return {
        "log_dir": clean_dir,
        "files_scanned": len(files),
        "sessions": out_sessions,
        "summary": {
            "total_sessions": len(all_sessions),
            "returned_sessions": len(out_sessions),
            "filters": {
                "actor": actor,
                "session_id": session_id,
                "event_type": event_type,
                "q": q,
            },
            "policy": normalize_target_policy(target_policy),
        },
        "error": None,
    }


def read_gateway_session_detail(
    log_dir: str,
    session_id: str,
    *,
    limit: int = 200,
    seq_global_from: int = 0,
    seq_global_to: int = 0,
    ts_from: int = 0,
    ts_to: int = 0,
    con=None,
) -> dict:
    clean_dir = str(log_dir or "").strip()
    clean_sid = str(session_id or "").strip()
    if not clean_dir:
        return {"log_dir": "", "session": None, "events": [], "failures": [], "error": "log_dir não informado"}
    if not clean_sid:
        return {"log_dir": clean_dir, "session": None, "events": [], "failures": [], "error": "session_id não informado"}

    sessions_payload = read_gateway_sessions(clean_dir, session_id=clean_sid, limit=1)
    if sessions_payload.get("error"):
        return {"log_dir": clean_dir, "session": None, "events": [], "failures": [], "error": sessions_payload["error"]}

    target_session = None
    for item in sessions_payload.get("sessions") or []:
        if str(item.get("session_id") or "") == clean_sid:
            target_session = item
            break
    if not target_session:
        return {"log_dir": clean_dir, "session": None, "events": [], "failures": [], "error": "sessão não encontrada"}

    files = sorted(Path(clean_dir).glob("audit-*.jsonl"))
    events: list[dict] = []
    for file_path in files:
        try:
            lines = file_path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError as exc:
            return {"log_dir": clean_dir, "session": target_session, "events": [], "failures": [], "error": str(exc)}
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
    events.sort(key=lambda item: (int(item.get("seq_global") or 0), int(item.get("seq_session") or 0), int(item.get("ts_ms") or 0)))
    filtered_events = []
    for item in events:
        seq_global = int(item.get("seq_global") or 0)
        ts_ms = int(item.get("ts_ms") or 0)
        if seq_global_from and seq_global < int(seq_global_from):
            continue
        if seq_global_to and seq_global > int(seq_global_to):
            continue
        if ts_from and ts_ms < int(ts_from):
            continue
        if ts_to and ts_ms > int(ts_to):
            continue
        filtered_events.append(item)
    events = filtered_events[: max(1, min(limit, 500))]

    failures = []
    if con is not None:
        try:
            rows = query_all(
                con,
                """
                SELECT id, run_id, ts_ms, session_id, seq_global, seq_session, flow_name,
                       event_type, failure_type, severity, expected_value, observed_value,
                       message, evidence_json
                FROM replay_failures
                WHERE session_id=?
                ORDER BY id DESC
                LIMIT 100
                """,
                (clean_sid,),
            )
            for row in rows:
                item = dict(row)
                try:
                    item["evidence"] = json.loads(item.pop("evidence_json") or "{}")
                except Exception:
                    item["evidence"] = {"raw": item.pop("evidence_json")}
                failures.append(item)
        except Exception:
            failures = []

    filtered_failures = []
    for item in failures:
        seq_global = int(item.get("seq_global") or 0)
        ts_ms = int(item.get("ts_ms") or 0)
        if seq_global_from and seq_global < int(seq_global_from):
            continue
        if seq_global_to and seq_global > int(seq_global_to):
            continue
        if ts_from and ts_ms < int(ts_from):
            continue
        if ts_to and ts_ms > int(ts_to):
            continue
        filtered_failures.append(item)
    failures = filtered_failures

    failure_keys = {(int(item.get("seq_global") or 0), int(item.get("seq_session") or 0)) for item in failures}
    failure_by_seq_global: dict[int, list[dict]] = {}
    for item in failures:
        seq_global = int(item.get("seq_global") or 0)
        failure_by_seq_global.setdefault(seq_global, []).append(item)

    annotated_events = []
    checkpoints = 0
    attention_events = 0
    for item in events:
        seq_global = int(item.get("seq_global") or 0)
        seq_session = int(item.get("seq_session") or 0)
        typ = str(item.get("type") or "unknown")
        kind = "neutral"
        if typ == "checkpoint":
            kind = "checkpoint"
            checkpoints += 1
        elif typ == "session_start":
            kind = "session_start"
        elif typ == "session_end":
            kind = "session_end"
        elif typ == "unknown_screen":
            kind = "warning"
            attention_events += 1
        elif typ == "bytes":
            kind = "io"
        elif typ == "deterministic_input":
            kind = "deterministic_input"
        if (seq_global, seq_session) in failure_keys or failure_by_seq_global.get(seq_global):
            kind = "failure"
            attention_events += 1

        annotated = dict(item)
        annotated["event_key"] = f"g{seq_global or 0}-s{seq_session or 0}"
        annotated["event_kind"] = kind
        annotated["linked_failures"] = failure_by_seq_global.get(seq_global, [])
        if typ == "deterministic_input":
            annotated["timeline_label"] = (
                f"{str(item.get('screen_sig') or '-')[:48]} -> {str(item.get('key_text') or item.get('key_kind') or '-')}"
            )
            annotated["screen_context"] = {
                "screen_sig": str(item.get("screen_sig") or ""),
                "screen_sample": str(item.get("screen_sample") or ""),
                "screen_source": str(item.get("screen_source") or ""),
                "screen_snapshot_ts_ms": item.get("screen_snapshot_ts_ms"),
                "screen_snapshot_age_ms": item.get("screen_snapshot_age_ms"),
            }
        annotated_events.append(annotated)

    failure_groups: dict[str, dict] = {}
    for item in failures:
        group_key = "|".join(
            [
                str(item.get("failure_type") or ""),
                str(item.get("severity") or ""),
                str(item.get("expected_value") or ""),
                str(item.get("observed_value") or ""),
            ]
        )
        group = failure_groups.setdefault(
            group_key,
            {
                "signature": group_key,
                "failure_type": item.get("failure_type") or "",
                "severity": item.get("severity") or "",
                "expected_value": item.get("expected_value") or "",
                "observed_value": item.get("observed_value") or "",
                "count": 0,
                "seq_globals": [],
                "messages": [],
            },
        )
        group["count"] += 1
        seq_global = int(item.get("seq_global") or 0)
        if seq_global and seq_global not in group["seq_globals"]:
            group["seq_globals"].append(seq_global)
        msg = str(item.get("message") or "")
        if msg and msg not in group["messages"]:
            group["messages"].append(msg)
    grouped_failures = sorted(failure_groups.values(), key=lambda item: (-int(item.get("count") or 0), item.get("failure_type") or ""))
    for item in grouped_failures:
        item["seq_globals"] = sorted(item.get("seq_globals") or [])

    return {
        "log_dir": clean_dir,
        "session": target_session,
        "events": annotated_events,
        "failures": failures,
        "failure_groups": grouped_failures,
        "summary": {
            "event_count": len(annotated_events),
            "failure_count": len(failures),
            "checkpoint_count": checkpoints,
            "attention_events": attention_events,
            "filters": {
                "seq_global_from": int(seq_global_from or 0),
                "seq_global_to": int(seq_global_to or 0),
                "ts_from": int(ts_from or 0),
                "ts_to": int(ts_to or 0),
                "limit": int(limit or 0),
            },
        },
        "error": None,
    }


def prepare_session_replay_data(
    log_dir: str,
    session_id: str,
) -> dict:
    """
    Prepara dados de replay de uma sessão.
    Retorna eventos bytes (in/out) estruturados para permissionário visualizar
    e fazer replay da interação capturada.
    """
    import base64
    
    clean_dir = str(log_dir or "").strip()
    clean_sid = str(session_id or "").strip()
    
    if not clean_dir or not clean_sid:
        return {"error": "log_dir e session_id são obrigatórios", "replay_events": [], "playback": None}
    
    # Ler todos os eventos da sessão
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
    
    # Organizar por seq_global
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
            # Decodificar bytes
            data_b64 = str(ev.get("data_b64") or "").strip()
            direction = str(ev.get("dir") or "").strip()  # "in" ou "out"
            n = int(ev.get("n") or 0)
            seq_global = int(ev.get("seq_global") or 0)
            ts_ms = int(ev.get("ts_ms") or 0)
            
            try:
                data_raw = base64.b64decode(data_b64) if data_b64 else b""
                # Tentar decodificar como UTF-8, caso contrário usar hex
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
                "direction": direction,  # "in" (input) ou "out" (output)
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
    
    # Preparar estrutura de playback
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

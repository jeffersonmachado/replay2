from __future__ import annotations

import json

from dakota_gateway.state_db import query_all, query_one
from control.services.gateway_state_service import (
    activate_gateway as _activate_gateway,
    deactivate_gateway as _deactivate_gateway,
    get_gateway_state as _get_gateway_state,
)
from control.services.capture_service import start_capture as _start_capture
from control.services.capture_service import get_capture as _get_capture


def _write_json(handler, status_code: int, payload: dict) -> None:
    handler.send_response(status_code)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.end_headers()
    handler.wfile.write(json.dumps(payload, ensure_ascii=False).encode("utf-8"))


def _resolve_target_policy(handler, target_env_id: int, *, query_one_fn):
    if int(target_env_id or 0) <= 0:
        return None
    con = handler._db()
    try:
        row = query_one_fn(con, "SELECT * FROM target_environments WHERE id=?", (int(target_env_id),))
        return dict(row) if row else None
    finally:
        handler._db_release(con)


def handle_gateway_get_route(
    handler,
    parsed_path,
    *,
    parse_qs_fn,
    query_one_fn,
    read_gateway_monitor_fn,
    read_gateway_sessions_fn,
    read_gateway_session_detail_fn,
) -> bool:
    path = parsed_path.path

    if path == "/api/gateway/state":
        user = handler._require()
        if not user:
            return True
        con = handler._db()
        try:
            state = _get_gateway_state(con)
        finally:
            handler._db_release(con)
        _write_json(handler, 200, state)
        return True

    if path == "/api/gateway/monitor":
        user = handler._require()
        if not user:
            return True
        qs = parse_qs_fn(parsed_path.query or "")
        log_dir = str((qs.get("log_dir") or [""])[0])
        limit = int((qs.get("limit") or ["40"])[0] or 40)
        limit = max(1, min(limit, 200))
        _write_json(handler, 200, read_gateway_monitor_fn(log_dir, limit=limit))
        return True

    if path == "/api/gateway/sessions":
        user = handler._require()
        if not user:
            return True
        qs = parse_qs_fn(parsed_path.query or "")
        target_env_id = int((qs.get("target_env_id") or ["0"])[0] or 0)
        target_policy = _resolve_target_policy(handler, target_env_id, query_one_fn=query_one_fn)
        payload = read_gateway_sessions_fn(
            str((qs.get("log_dir") or [""])[0]),
            actor=str((qs.get("actor") or [""])[0]),
            session_id=str((qs.get("session_id") or [""])[0]),
            event_type=str((qs.get("event_type") or [""])[0]),
            q=str((qs.get("q") or [""])[0]),
            limit=max(1, min(int((qs.get("limit") or ["60"])[0] or 60), 500)),
            target_policy=target_policy,
        )
        _write_json(handler, 200, payload)
        return True

    if path.startswith("/api/gateway/sessions/") and path.endswith("/compliance"):
        user = handler._require()
        if not user:
            return True
        parts = path.split("/")
        if len(parts) < 6:
            handler.send_response(404)
            handler.end_headers()
            return True
        session_id = parts[4]
        qs = parse_qs_fn(parsed_path.query or "")
        target_env_id = int((qs.get("target_env_id") or ["0"])[0] or 0)
        target_policy = _resolve_target_policy(handler, target_env_id, query_one_fn=query_one_fn)
        payload = read_gateway_sessions_fn(
            str((qs.get("log_dir") or [""])[0]),
            session_id=session_id,
            limit=1,
            target_policy=target_policy,
        )
        session_payload = (payload.get("sessions") or [None])[0]
        if not session_payload:
            handler.send_response(404)
            handler.end_headers()
            return True
        _write_json(
            handler,
            200,
            {
                "session_id": session_id,
                "compliance": {
                    "entry_mode": session_payload.get("entry_mode") or "",
                    "via_gateway": bool(session_payload.get("via_gateway")),
                    "gateway_session_id": session_payload.get("gateway_session_id") or "",
                    "gateway_endpoint": session_payload.get("gateway_endpoint") or "",
                    "compliance_status": session_payload.get("compliance_status") or "not_applicable",
                    "compliance_reason": session_payload.get("compliance_reason") or "",
                    "validated_at_ms": session_payload.get("validated_at_ms"),
                },
            },
        )
        return True

    if path.startswith("/api/gateway/sessions/"):
        user = handler._require()
        if not user:
            return True
        session_id = path.split("/")[-1] if path.split("/") else ""
        qs = parse_qs_fn(parsed_path.query or "")
        con = handler._db()
        try:
            payload = read_gateway_session_detail_fn(
                str((qs.get("log_dir") or [""])[0]),
                session_id,
                limit=max(1, min(int((qs.get("limit") or ["200"])[0] or 200), 500)),
                seq_global_from=int((qs.get("seq_global_from") or ["0"])[0] or 0),
                seq_global_to=int((qs.get("seq_global_to") or ["0"])[0] or 0),
                ts_from=int((qs.get("ts_from") or ["0"])[0] or 0),
                ts_to=int((qs.get("ts_to") or ["0"])[0] or 0),
                con=con,
            )
        finally:
            handler._db_release(con)
        _write_json(handler, 200, payload)
        return True

    return False


def handle_gateway_post_route(
    handler,
    parsed_path,
    body: dict,
    *,
    now_ms_fn,
    capture_log_dir: str,
    start_port22_capture_fn=None,
    stop_port22_capture_fn=None,
    start_runtime_capture_fn=None,
    stop_runtime_capture_fn=None,
) -> bool:
    path = parsed_path.path

    if path == "/api/gateway/activate":
        user = handler._require(roles={"admin", "operator"})
        if not user:
            return True
        connection_profile_id = body.get("connection_profile_id") or None
        operational_user_id = body.get("operational_user_id") or None
        if connection_profile_id:
            connection_profile_id = int(connection_profile_id)
        if operational_user_id:
            operational_user_id = int(operational_user_id)
        con = handler._db()
        try:
            state = _activate_gateway(
                con,
                user_id=user["id"],
                username=user["username"],
                connection_profile_id=connection_profile_id,
                operational_user_id=operational_user_id,
                now_ms_fn=now_ms_fn,
            )
            # Captura passa a iniciar junto com a ativação do gateway.
            active_capture = query_one(
                con,
                "SELECT id FROM capture_sessions WHERE status='active' ORDER BY id DESC LIMIT 1",
                (),
            )
            if active_capture:
                auto_capture = _get_capture(con, int(active_capture["id"]))
                state["auto_capture"] = auto_capture if auto_capture else {"id": int(active_capture["id"])}
            else:
                capture = _start_capture(
                    con,
                    user_id=int(user["id"]),
                    username=str(user["username"]),
                    log_dir_base=capture_log_dir,
                    connection_profile_id=connection_profile_id,
                    operational_user_id=operational_user_id,
                    notes="captura iniciada automaticamente na ativação do gateway",
                    now_ms_fn=now_ms_fn,
                )
                state["auto_capture"] = capture
            if callable(start_port22_capture_fn):
                state["port22_capture"] = start_port22_capture_fn(state.get("auto_capture") or {})
            if callable(start_runtime_capture_fn):
                state["runtime_capture"] = start_runtime_capture_fn(state.get("auto_capture") or {}, body or {})
        except Exception as exc:
            _write_json(handler, 400, {"error": str(exc)})
            return True
        finally:
            handler._db_release(con)
        _write_json(handler, 200, state)
        return True

    if path == "/api/gateway/deactivate":
        user = handler._require(roles={"admin", "operator"})
        if not user:
            return True
        con = handler._db()
        try:
            # Check for active captures before deactivating
            active_capture = query_one(
                con,
                "SELECT id FROM capture_sessions WHERE status='active' LIMIT 1",
            )
            force = bool(body.get("force", False))
            if active_capture and not force:
                _write_json(
                    handler,
                    409,
                    {
                        "error": "Há uma captura ativa. Use force=true para desativar mesmo assim.",
                        "active_capture_id": int(active_capture["id"]),
                    },
                )
                return True

            interrupted_capture_ids = []
            if force:
                active_rows = query_all(
                    con,
                    "SELECT id FROM capture_sessions WHERE status='active' ORDER BY id",
                )
                interrupted_capture_ids = [int(row["id"]) for row in active_rows]
                if interrupted_capture_ids:
                    ts = int(now_ms_fn())
                    con.execute(
                        """
                        UPDATE capture_sessions
                        SET status='interrupted', ended_at_ms=?
                        WHERE status='active'
                        """,
                        (ts,),
                    )

            state = _deactivate_gateway(
                con,
                username=user["username"],
                now_ms_fn=now_ms_fn,
            )
            if callable(stop_port22_capture_fn):
                state["port22_capture"] = stop_port22_capture_fn()
            if callable(stop_runtime_capture_fn):
                state["runtime_capture"] = stop_runtime_capture_fn()
            if interrupted_capture_ids:
                state["interrupted_capture_ids"] = interrupted_capture_ids
        except Exception as exc:
            _write_json(handler, 400, {"error": str(exc)})
            return True
        finally:
            handler._db_release(con)
        _write_json(handler, 200, state)
        return True

    return False

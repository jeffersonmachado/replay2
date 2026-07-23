from __future__ import annotations

import json
from pathlib import Path

from dakota_gateway.state_db import query_all, query_one
from control.services.gateway_state_service import (
    activate_gateway as _activate_gateway,
    deactivate_gateway as _deactivate_gateway,
    get_gateway_state as _get_gateway_state,
)
from control.services.capture_service import start_capture as _start_capture
from control.services.capture_service import get_capture as _get_capture
from control.routes.route_helpers import parse_int, public_error_message, write_json


def _validated_log_dir(handler, raw: str) -> str | None:
    """Valida que log_dir da query string resolve sob o capture_log_dir configurado.

    Retorna o caminho validado ("" se não informado) ou None se fora da raiz
    permitida. Sem capture_log_dir configurado no servidor (ex.: testes
    unitários), mantém o comportamento anterior.
    """
    clean = str(raw or "").strip()
    if not clean:
        return ""
    base = str(getattr(handler.server, "capture_log_dir", "") or "").strip()
    if not base:
        return clean
    try:
        resolved = Path(clean).resolve()
        resolved.relative_to(Path(base).resolve())
    except Exception:
        return None
    return str(resolved)

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
        write_json(handler, 200, state)
        return True

    if path == "/api/gateway/system-users":
        user = handler._require(roles={"admin", "operator"})
        if not user:
            return True
        import pwd, grp, os as _os
        users_list = []
        groups_list = []
        try:
            for pw in pwd.getpwall():
                if pw.pw_uid >= 100 and pw.pw_shell and pw.pw_shell not in ("/bin/false", "/usr/bin/false", "/sbin/nologin"):
                    users_list.append({"name": pw.pw_name, "uid": pw.pw_uid})
        except Exception:
            pass
        try:
            for g in grp.getgrall():
                if g.gr_gid >= 100:
                    groups_list.append({"name": g.gr_name, "gid": g.gr_gid})
        except Exception:
            pass
        write_json(handler, 200, {"users": users_list, "groups": groups_list})
        return True

    if path == "/api/gateway/monitor":
        user = handler._require()
        if not user:
            return True
        qs = parse_qs_fn(parsed_path.query or "")
        log_dir = _validated_log_dir(handler, (qs.get("log_dir") or [""])[0])
        if log_dir is None:
            write_json(handler, 400, {"error": "log_dir fora do diretorio de capturas configurado"})
            return True
        limit = parse_int((qs.get("limit") or ["40"])[0] or 40, 40, min_value=1, max_value=200)
        write_json(handler, 200, read_gateway_monitor_fn(log_dir, limit=limit))
        return True

    if path == "/api/gateway/sessions":
        user = handler._require()
        if not user:
            return True
        qs = parse_qs_fn(parsed_path.query or "")
        log_dir = _validated_log_dir(handler, (qs.get("log_dir") or [""])[0])
        if log_dir is None:
            write_json(handler, 400, {"error": "log_dir fora do diretorio de capturas configurado"})
            return True
        target_env_id = parse_int((qs.get("target_env_id") or ["0"])[0] or 0, 0, min_value=0)
        target_policy = _resolve_target_policy(handler, target_env_id, query_one_fn=query_one_fn)
        payload = read_gateway_sessions_fn(
            log_dir,
            actor=str((qs.get("actor") or [""])[0]),
            logname=str((qs.get("logname") or [""])[0]),
            session_id=str((qs.get("session_id") or [""])[0]),
            event_type=str((qs.get("event_type") or [""])[0]),
            q=str((qs.get("q") or [""])[0]),
            uid=str((qs.get("uid") or [""])[0]),
            gid=str((qs.get("gid") or [""])[0]),
            ts_from=parse_int((qs.get("ts_from") or ["0"])[0] or 0, 0, min_value=0),
            ts_to=parse_int((qs.get("ts_to") or ["0"])[0] or 0, 0, min_value=0),
            limit=parse_int((qs.get("limit") or ["60"])[0] or 60, 60, min_value=1, max_value=500),
            target_policy=target_policy,
        )
        write_json(handler, 200, payload)
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
        log_dir = _validated_log_dir(handler, (qs.get("log_dir") or [""])[0])
        if log_dir is None:
            write_json(handler, 400, {"error": "log_dir fora do diretorio de capturas configurado"})
            return True
        target_env_id = parse_int((qs.get("target_env_id") or ["0"])[0] or 0, 0, min_value=0)
        target_policy = _resolve_target_policy(handler, target_env_id, query_one_fn=query_one_fn)
        payload = read_gateway_sessions_fn(
            log_dir,
            session_id=session_id,
            limit=1,
            target_policy=target_policy,
        )
        session_payload = (payload.get("sessions") or [None])[0]
        if not session_payload:
            handler.send_response(404)
            handler.end_headers()
            return True
        write_json(
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
        log_dir = _validated_log_dir(handler, (qs.get("log_dir") or [""])[0])
        if log_dir is None:
            write_json(handler, 400, {"error": "log_dir fora do diretorio de capturas configurado"})
            return True
        con = handler._db()
        try:
            payload = read_gateway_session_detail_fn(
                log_dir,
                session_id,
                limit=parse_int((qs.get("limit") or ["200"])[0] or 200, 200, min_value=1, max_value=500),
                seq_global_from=parse_int((qs.get("seq_global_from") or ["0"])[0] or 0, 0, min_value=0),
                seq_global_to=parse_int((qs.get("seq_global_to") or ["0"])[0] or 0, 0, min_value=0),
                ts_from=parse_int((qs.get("ts_from") or ["0"])[0] or 0, 0, min_value=0),
                ts_to=parse_int((qs.get("ts_to") or ["0"])[0] or 0, 0, min_value=0),
                con=con,
            )
        finally:
            handler._db_release(con)
        write_json(handler, 200, payload)
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
            connection_profile_id = parse_int(connection_profile_id, 0) or None
        if operational_user_id:
            operational_user_id = parse_int(operational_user_id, 0) or None
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
            write_json(handler, 400, {"error": public_error_message(exc, fallback="falha ao ativar o gateway")})
            return True
        finally:
            handler._db_release(con)
        write_json(handler, 200, state)
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
                write_json(
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
            write_json(handler, 400, {"error": public_error_message(exc, fallback="falha ao desativar o gateway")})
            return True
        finally:
            handler._db_release(con)
        write_json(handler, 200, state)
        return True

    if path == "/api/gateway/capture-scope":
        user = handler._require(roles={"admin", "operator"})
        if not user:
            return True
        users = str(body.get("users", "*")).strip() or "*"
        groups = str(body.get("groups", "*")).strip() or "*"
        import json as _json
        scope = _json.dumps({"users": users, "groups": groups})
        con = handler._db()
        try:
            con.execute(
                "UPDATE gateway_state SET capture_scope_json=?, updated_at_ms=? WHERE id=1",
                (scope, now_ms_fn()),
            )
            con.commit()
        finally:
            handler._db_release(con)
        write_json(handler, 200, {"ok": True, "capture_scope": {"users": users, "groups": groups}})
        return True

    return False

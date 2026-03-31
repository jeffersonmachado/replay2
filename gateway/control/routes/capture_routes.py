"""
Rotas de captura — UI-first.
POST /api/captures/start   — inicia captura (requer gateway ativo)
POST /api/captures/{id}/stop — encerra captura
GET  /api/captures         — lista capturas
GET  /api/captures/{id}    — detalhe da captura
GET  /api/captures/{id}/events — eventos da sessão via JSONL
GET  /api/captures/{id}/replay — dados de replay/view de uma sessão
GET  /api/captures/{id}/sessions — lista sessões dentro de uma captura
"""
from __future__ import annotations

import json

from control.services.capture_service import (
    get_capture as _get_capture,
    list_captures as _list_captures,
    start_capture as _start_capture,
    stop_capture as _stop_capture,
)
from control.services.gateway_observability_service import (
    read_gateway_sessions as _read_gateway_sessions,
    prepare_session_replay_data as _prepare_session_replay_data,
)


def _write_json(handler, status_code: int, payload) -> None:
    handler.send_response(status_code)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.end_headers()
    handler.wfile.write(json.dumps(payload, ensure_ascii=False).encode("utf-8"))


def handle_capture_get_route(
    handler,
    parsed_path,
    *,
    parse_qs_fn,
    read_gateway_monitor_fn,
) -> bool:
    path = parsed_path.path

    if path == "/api/captures":
        user = handler._require()
        if not user:
            return True
        qs = parse_qs_fn(parsed_path.query or "")
        limit = max(1, min(int((qs.get("limit") or ["60"])[0] or 60), 500))
        con = handler._db()
        try:
            items = _list_captures(con, limit=limit)
        finally:
            handler._db_release(con)
        _write_json(handler, 200, {"captures": items, "total": len(items)})
        return True

    # GET /api/captures/{id}/events — lê todos os eventos do log_dir da sessão
    if path.startswith("/api/captures/") and path.endswith("/events"):
        user = handler._require()
        if not user:
            return True
        parts = path.split("/")
        if len(parts) < 4:
            handler.send_response(404)
            handler.end_headers()
            return True
        try:
            capture_id = int(parts[3])
        except (ValueError, IndexError):
            handler.send_response(404)
            handler.end_headers()
            return True
        con = handler._db()
        try:
            capture = _get_capture(con, capture_id)
        finally:
            handler._db_release(con)
        if not capture:
            handler.send_response(404)
            handler.end_headers()
            return True
        qs = parse_qs_fn(parsed_path.query or "")
        log_dir = capture.get("log_dir") or ""
        limit = max(1, min(int((qs.get("limit") or ["300"])[0] or 300), 1000))
        payload = read_gateway_monitor_fn(log_dir, limit=limit)
        _write_json(handler, 200, {**payload, "capture_id": capture_id})
        return True

    # GET /api/captures/{id}/sessions — lista sessões dentro de uma captura
    if path.startswith("/api/captures/") and path.endswith("/sessions"):
        user = handler._require()
        if not user:
            return True
        parts = path.split("/")
        if len(parts) < 4:
            handler.send_response(404)
            handler.end_headers()
            return True
        try:
            capture_id = int(parts[3])
        except (ValueError, IndexError):
            handler.send_response(404)
            handler.end_headers()
            return True
        con = handler._db()
        try:
            capture = _get_capture(con, capture_id)
        finally:
            handler._db_release(con)
        if not capture:
            handler.send_response(404)
            handler.end_headers()
            return True
        qs = parse_qs_fn(parsed_path.query or "")
        log_dir = capture.get("log_dir") or ""
        limit = max(1, min(int((qs.get("limit") or ["100"])[0] or 100), 500))
        sessions_payload = _read_gateway_sessions(log_dir, limit=limit)
        _write_json(handler, 200, {**sessions_payload, "capture_id": capture_id})
        return True

    # GET /api/captures/{id}/replay?session_id=... — dados para view/replay de uma sessão
    if path.startswith("/api/captures/") and path.endswith("/replay"):
        user = handler._require()
        if not user:
            return True
        parts = path.split("/")
        if len(parts) < 4:
            handler.send_response(404)
            handler.end_headers()
            return True
        try:
            capture_id = int(parts[3])
        except (ValueError, IndexError):
            handler.send_response(404)
            handler.end_headers()
            return True
        con = handler._db()
        try:
            capture = _get_capture(con, capture_id)
        finally:
            handler._db_release(con)
        if not capture:
            handler.send_response(404)
            handler.end_headers()
            return True
        qs = parse_qs_fn(parsed_path.query or "")
        session_id = str((qs.get("session_id") or [""])[0] or "").strip()
        if not session_id:
            _write_json(handler, 400, {"error": "session_id obrigatório"})
            return True
        log_dir = capture.get("log_dir") or ""
        replay_data = _prepare_session_replay_data(log_dir, session_id)
        _write_json(handler, 200, {**replay_data, "capture_id": capture_id, "log_dir": log_dir, "capture": capture})
        return True

    # GET /api/captures/{id}
    if path.startswith("/api/captures/"):
        user = handler._require()
        if not user:
            return True
        # Garantir que não seja rota estática tipo /api/captures/start
        parts = path.split("/")
        if len(parts) < 4 or parts[3] in ("start",):
            return False
        try:
            capture_id = int(parts[3])
        except (ValueError, IndexError):
            return False
        con = handler._db()
        try:
            capture = _get_capture(con, capture_id)
        finally:
            handler._db_release(con)
        if not capture:
            handler.send_response(404)
            handler.end_headers()
            return True
        _write_json(handler, 200, capture)
        return True

    return False


def handle_capture_post_route(
    handler,
    parsed_path,
    body: dict,
    *,
    now_ms_fn,
    log_dir_base: str,
) -> bool:
    path = parsed_path.path

    if path == "/api/captures/start":
        user = handler._require(roles={"admin", "operator"})
        if not user:
            return True
        connection_profile_id = body.get("connection_profile_id") or None
        if connection_profile_id:
            connection_profile_id = int(connection_profile_id)
        operational_user_id = body.get("operational_user_id") or None
        if operational_user_id:
            operational_user_id = int(operational_user_id)
        target_env_id = body.get("target_env_id") or None
        if target_env_id:
            target_env_id = int(target_env_id)
        connection_profile_name = str(body.get("connection_profile_name") or "").strip() or None
        notes = str(body.get("notes") or "").strip()
        con = handler._db()
        try:
            capture = _start_capture(
                con,
                user_id=user["id"],
                username=user["username"],
                log_dir_base=log_dir_base,
                connection_profile_id=connection_profile_id,
                connection_profile_name=connection_profile_name,
                operational_user_id=operational_user_id,
                target_env_id=target_env_id,
                notes=notes or None,
                now_ms_fn=now_ms_fn,
            )
        except ValueError as exc:
            _write_json(handler, 409, {"error": str(exc)})
            return True
        except Exception as exc:
            _write_json(handler, 500, {"error": str(exc)})
            return True
        finally:
            handler._db_release(con)
        _write_json(handler, 200, capture)
        return True

    # POST /api/captures/{id}/stop
    if path.startswith("/api/captures/") and path.endswith("/stop"):
        user = handler._require(roles={"admin", "operator"})
        if not user:
            return True
        parts = path.split("/")
        try:
            capture_id = int(parts[3])
        except (ValueError, IndexError):
            handler.send_response(404)
            handler.end_headers()
            return True
        con = handler._db()
        try:
            capture = _stop_capture(
                con,
                capture_id,
                username=user["username"],
                now_ms_fn=now_ms_fn,
            )
        except ValueError as exc:
            _write_json(handler, 409, {"error": str(exc)})
            return True
        except Exception as exc:
            _write_json(handler, 500, {"error": str(exc)})
            return True
        finally:
            handler._db_release(con)
        _write_json(handler, 200, capture)
        return True

    return False

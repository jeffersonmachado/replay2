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

from control.routes.route_helpers import parse_int, public_error_message, write_json
from control.services.capture_service import (
    get_capture as _get_capture,
    list_captures as _list_captures,
    start_capture as _start_capture,
    stop_capture as _stop_capture,
)
from control.services.capture_synthesis_service import synthesize_capture as _synthesize_capture
from control.services.gateway_observability_service import (
    read_gateway_sessions as _read_gateway_sessions,
)
from control.services.session_replay_service import (
    prepare_session_replay_data as _prepare_session_replay_data,
)


def _replay_status_code(replay_data: dict) -> int:
    error = replay_data.get("error")
    if isinstance(error, dict):
        code = str(error.get("code") or "")
        if code in {"log_dir_not_found", "no_audit_files", "session_not_found"}:
            return 404
        if code == "invalid_params":
            return 400
        return 500
    if error:
        return 500

    playback = replay_data.get("playback") or {}
    for items in (
        replay_data.get("events") or [],
        replay_data.get("timeline_items") or [],
        playback.get("events") or [],
    ):
        for container in items if isinstance(items, list) else []:
            warning = container.get("integrity_warning") if isinstance(container, dict) else None
            if warning and warning.get("integrity_error") == "invalid_base64":
                return 422
    return 200


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
        limit = parse_int((qs.get("limit") or ["20"])[0] or 20, 20, min_value=1, max_value=500)
        offset = parse_int((qs.get("offset") or ["0"])[0] or 0, 0, min_value=0)
        search = (qs.get("search") or [""])[0] or ""
        created_by = (qs.get("created_by") or [""])[0] or ""
        ts_from = parse_int((qs.get("ts_from") or ["0"])[0] or 0, 0, min_value=0)
        ts_to = parse_int((qs.get("ts_to") or ["0"])[0] or 0, 0, min_value=0)
        status = (qs.get("status") or [""])[0] or ""
        con = handler._db()
        try:
            items, total = _list_captures(con, limit=limit, offset=offset, search=search,
                                          created_by=created_by, ts_from=ts_from, ts_to=ts_to, status=status)
        finally:
            handler._db_release(con)
        write_json(handler, 200, {"captures": items, "total": total, "limit": limit, "offset": offset})
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
        limit = parse_int((qs.get("limit") or ["300"])[0] or 300, 300, min_value=1, max_value=1000)
        payload = read_gateway_monitor_fn(log_dir, limit=limit)
        write_json(handler, 200, {**payload, "capture_id": capture_id})
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
        limit = parse_int((qs.get("limit") or ["100"])[0] or 100, 100, min_value=1, max_value=500)
        sessions_payload = _read_gateway_sessions(log_dir, limit=limit)
        write_json(handler, 200, {**sessions_payload, "capture_id": capture_id})
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
            write_json(handler, 400, {"error": "session_id obrigatório"})
            return True
        log_dir = capture.get("log_dir") or ""
        replay_data = _prepare_session_replay_data(log_dir, session_id)
        status = _replay_status_code(replay_data)
        write_json(handler, status, {**replay_data, "capture_id": capture_id, "log_dir": log_dir, "capture": capture})
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
            # _get_capture já computa session_count/event_count do disco
            # (com cache) para capturas ativas — sem contagem duplicada aqui.
            capture = _get_capture(con, capture_id)
        finally:
            handler._db_release(con)
        if not capture:
            handler.send_response(404)
            handler.end_headers()
            return True
        write_json(handler, 200, capture)
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
            connection_profile_id = parse_int(connection_profile_id, 0) or None
        operational_user_id = body.get("operational_user_id") or None
        if operational_user_id:
            operational_user_id = parse_int(operational_user_id, 0) or None
        target_env_id = body.get("target_env_id") or None
        if target_env_id:
            target_env_id = parse_int(target_env_id, 0) or None
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
            write_json(handler, 409, {"error": str(exc)})
            return True
        except Exception as exc:
            write_json(handler, 500, {"error": public_error_message(exc, fallback="falha ao iniciar captura")})
            return True
        finally:
            handler._db_release(con)
        write_json(handler, 200, capture)
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
            write_json(handler, 409, {"error": str(exc)})
            return True
        except Exception as exc:
            write_json(handler, 500, {"error": public_error_message(exc, fallback="falha ao encerrar captura")})
            return True
        finally:
            handler._db_release(con)
        write_json(handler, 200, capture)
        return True

    # POST /api/captures/{id}/synthesize
    if path.startswith("/api/captures/") and path.endswith("/synthesize"):
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

        source_dir = str(body.get("source_dir") or "").strip()
        if not source_dir:
            write_json(handler, 400, {"error": "source_dir obrigatório"})
            return True

        con = handler._db()
        try:
            payload = _synthesize_capture(
                con,
                capture_id,
                source_dir=source_dir,
                samples=parse_int(body.get("samples", 10), 10, min_value=1),
                seed=body.get("seed"),
                name=str(body.get("name") or "").strip(),
                out_dir=str(body.get("out_dir") or "").strip(),
                include_validation=bool(body.get("validate", True)),
                include_stress=bool(body.get("stress", False)),
                concurrency=parse_int(body.get("concurrency", 5), 5, min_value=1),
            )
        except ValueError as exc:
            message = str(exc)
            status = 404 if "não encontrada" in message else 400
            write_json(handler, status, {"ok": False, "error": message})
            return True
        except Exception as exc:
            write_json(handler, 500, {"ok": False, "error": str(exc)})
            return True
        finally:
            handler._db_release(con)
        write_json(handler, 200, payload)
        return True

    return False


def handle_capture_delete_route(handler, parsed_path) -> bool:
    """DELETE /api/captures/{id} — exclui captura (apenas se nao ativa)."""
    path = parsed_path.path
    if not path.startswith("/api/captures/") or path.count("/") != 3:
        return False
    user = handler._require(roles={"admin", "operator"})
    if not user:
        return True
    try:
        capture_id = int(path.split("/")[3])
    except (ValueError, IndexError):
        handler.send_response(404)
        handler.end_headers()
        return True
    con = handler._db()
    try:
        row = con.execute(
            "SELECT id, status FROM capture_sessions WHERE id=?", (capture_id,)
        ).fetchone()
        if not row:
            handler.send_response(404)
            handler.end_headers()
            return True
        if row["status"] == "active":
            write_json(handler, 409, {"ok": False, "error": "captura ativa deve ser parada antes de excluir"})
            return True
        con.execute("DELETE FROM capture_sessions WHERE id=?", (capture_id,))
        write_json(handler, 200, {"ok": True, "deleted_capture_id": capture_id})
    finally:
        handler._db_release(con)
    return True

from __future__ import annotations

import json
import time


def _write_json(handler, status_code: int, payload: dict) -> None:
    handler.send_response(status_code)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.end_headers()
    handler.wfile.write(json.dumps(payload, ensure_ascii=False).encode("utf-8"))


def handle_admin_get_route(handler, parsed_path, *, gateway_service_status, query_all_fn) -> bool:
    if parsed_path.path == "/api/gateway/status":
        user = handler._require()
        if not user:
            return True
        # Status legado do serviço sshd + estado lógico do gateway (fonte única)
        service_status = gateway_service_status()
        con = handler._db()
        try:
            from control.services.gateway_state_service import build_operational_policy, get_gateway_state
            logical_state = get_gateway_state(con)
        except Exception:
            logical_state = {}
            build_operational_policy = None
        finally:
            handler._db_release(con)
        if build_operational_policy is not None:
            policy = build_operational_policy(
                logical_active=bool(logical_state.get("active")),
                service_running=bool(service_status.get("running")),
            )
        else:
            policy = {
                "desired_mode": "gateway_active" if bool(logical_state.get("active")) else "gateway_inactive",
                "desired_ssh_route": None,
                "effective_ssh_route": None,
                "capture_available": bool(logical_state.get("active")),
                "policy_ok": False,
                "reason": "falha ao avaliar política",
            }
        merged = {
            **service_status,
            "logical_active": bool(logical_state.get("active")),
            "activated_by_username": logical_state.get("activated_by_username"),
            "activated_at_ms": logical_state.get("activated_at_ms"),
            "environment": logical_state.get("environment") or {},
            "policy": policy,
            "policy_ok": bool(policy.get("policy_ok")),
            "capture_available": bool(policy.get("capture_available")),
            "ssh_desired_route": policy.get("desired_ssh_route"),
            "ssh_effective_route": policy.get("effective_ssh_route"),
        }
        _write_json(handler, 200, merged)
        return True

    if parsed_path.path == "/api/users":
        user = handler._require(roles={"admin"})
        if not user:
            return True
        con = handler._db()
        try:
            rows = query_all_fn(con, "SELECT id,username,role,created_at_ms FROM users ORDER BY id", ())
            users = [dict(row) for row in rows]
        finally:
            handler._db_release(con)
        _write_json(handler, 200, {"users": users})
        return True

    return False


def handle_admin_post_route(
    handler,
    parsed_path,
    body: dict,
    *,
    auth_module,
    query_one_fn,
    now_ms_fn,
    gateway_toggle_fn,
) -> bool:
    if parsed_path.path == "/api/login":
        username = str(body.get("username") or "")
        password = str(body.get("password") or "")
        con = handler._db()
        try:
            row = query_one_fn(con, "SELECT id,username,role,password_hash FROM users WHERE username=?", (username,))
            if not row or not auth_module.verify_password(password, row["password_hash"]):
                handler.send_response(401)
                handler.end_headers()
                return True
            token = auth_module.new_session_token()
            token_hash = auth_module.sha256_hex(token.encode("utf-8"))
            exp = int(time.time() * 1000) + 12 * 3600 * 1000
            con.execute(
                "INSERT INTO sessions(user_id, token_hash, created_at_ms, expires_at_ms) VALUES(?,?,?,?)",
                (int(row["id"]), token_hash, now_ms_fn(), exp),
            )
            cookie = auth_module.sign_cookie(handler.server.cookie_secret, username, token, exp)
            handler.send_response(200)
            handler._set_cookie("dakota_session", cookie)
            handler.end_headers()
            return True
        finally:
            handler._db_release(con)

    if parsed_path.path == "/api/logout":
        handler._auth()
        handler.send_response(200)
        handler._clear_cookie("dakota_session")
        handler.end_headers()
        return True

    if parsed_path.path == "/api/gateway/toggle":
        user = handler._require(roles={"admin", "operator"})
        if not user:
            return True
        enabled = bool(body.get("enabled"))
        status = gateway_toggle_fn(enabled)
        code = 200 if not status.get("error") and status.get("running") == enabled else 500
        _write_json(handler, code, status)
        return True

    if parsed_path.path == "/api/users":
        user = handler._require(roles={"admin"})
        if not user:
            return True
        username = str(body.get("username") or "")
        password = str(body.get("password") or "")
        role = str(body.get("role") or "")
        if role not in ("admin", "operator", "viewer"):
            handler.send_response(400)
            handler.end_headers()
            return True
        ph = auth_module.pbkdf2_hash_password(password)
        con = handler._db()
        try:
            con.execute(
                "INSERT INTO users(username,password_hash,role,created_at_ms) VALUES(?,?,?,?)",
                (username, ph, role, now_ms_fn()),
            )
        finally:
            handler._db_release(con)
        handler.send_response(200)
        handler.end_headers()
        return True

    return False

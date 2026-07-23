from __future__ import annotations

import re
import sqlite3
import threading
import time

from control.routes.route_helpers import write_json
from control.services.gateway_state_service import (
    build_full_gateway_status as _build_full_gateway_status,
)

# Username: charset seguro para o cookie (sem '|', que quebraria sign_cookie).
_USERNAME_RE = re.compile(r"^[A-Za-z0-9_.@-]{1,64}$")

# ── Rate limiting simples de login (A5) ──
# Após MAX falhas consecutivas por (IP, username) dentro da janela, responde 429.
_LOGIN_MAX_FAILURES = 5
_LOGIN_WINDOW_SECONDS = 600
_LOGIN_LOCKOUT_SECONDS = 60
_login_failures: dict[tuple[str, str], list[float]] = {}
_login_lock = threading.Lock()


def _client_ip(handler) -> str:
    try:
        return str(handler.client_address[0])
    except Exception:
        return "unknown"


def _login_throttled(handler, username: str) -> bool:
    """Verifica se o par (IP, username) está temporariamente bloqueado."""
    now = time.time()
    key = (_client_ip(handler), username)
    with _login_lock:
        failures = [ts for ts in _login_failures.get(key, []) if now - ts < _LOGIN_WINDOW_SECONDS]
        _login_failures[key] = failures
        if len(failures) >= _LOGIN_MAX_FAILURES and now - failures[-1] < _LOGIN_LOCKOUT_SECONDS:
            return True
    return False


def _register_login_failure(handler, username: str) -> None:
    key = (_client_ip(handler), username)
    with _login_lock:
        _login_failures.setdefault(key, []).append(time.time())


def _clear_login_failures(handler, username: str) -> None:
    key = (_client_ip(handler), username)
    with _login_lock:
        _login_failures.pop(key, None)


def handle_admin_get_route(handler, parsed_path, *, gateway_service_status, query_all_fn) -> bool:
    if parsed_path.path == "/api/gateway/status":
        user = handler._require()
        if not user:
            return True
        # Status completo do gateway — payload unificado com /ws/gateway-status
        service_status = gateway_service_status()
        con = handler._db()
        try:
            merged = _build_full_gateway_status(con, service_status)
        finally:
            handler._db_release(con)
        merged["capture_log_dir"] = getattr(handler.server, "capture_log_dir", "")
        write_json(handler, 200, merged)
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
        write_json(handler, 200, {"users": users})
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
        if not username or not password:
            write_json(handler, 400, {"error": "username e password obrigatorios"})
            return True
        if _login_throttled(handler, username):
            write_json(handler, 429, {"error": "muitas tentativas de login; aguarde e tente novamente"})
            return True
        con = handler._db()
        try:
            row = query_one_fn(con, "SELECT id,username,role,password_hash FROM users WHERE username=?", (username,))
            if not row or not auth_module.verify_password(password, row["password_hash"]):
                _register_login_failure(handler, username)
                write_json(handler, 401, {"error": "credenciais invalidas"})
                return True
            _clear_login_failures(handler, username)
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
        # Invalida a sessão no banco (token_hash) e varre sessões expiradas.
        cookie_value = handler._get_cookie("dakota_session") if hasattr(handler, "_get_cookie") else None
        if cookie_value:
            parsed = auth_module.verify_cookie(handler.server.cookie_secret, cookie_value)
            if parsed:
                _username, token, _exp = parsed
                token_hash = auth_module.sha256_hex(token.encode("utf-8"))
                con = handler._db()
                try:
                    con.execute("DELETE FROM sessions WHERE token_hash=?", (token_hash,))
                    con.execute("DELETE FROM sessions WHERE expires_at_ms < ?", (int(time.time() * 1000),))
                except sqlite3.OperationalError:
                    pass  # tabela ainda não criada — nada a invalidar
                finally:
                    handler._db_release(con)
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
        write_json(handler, code, status)
        return True

    if parsed_path.path == "/api/users":
        user = handler._require(roles={"admin"})
        if not user:
            return True
        username = str(body.get("username") or "")
        password = str(body.get("password") or "")
        role = str(body.get("role") or "")
        if role not in ("admin", "operator", "viewer"):
            write_json(handler, 400, {"error": "role invalido"})
            return True
        if not _USERNAME_RE.match(username):
            write_json(handler, 400, {"error": "username invalido: use 1-64 caracteres de letras, numeros, '_', '.', '@' ou '-'"})
            return True
        ph = auth_module.pbkdf2_hash_password(password)
        con = handler._db()
        try:
            try:
                con.execute(
                    "INSERT INTO users(username,password_hash,role,created_at_ms) VALUES(?,?,?,?)",
                    (username, ph, role, now_ms_fn()),
                )
            except sqlite3.IntegrityError:
                write_json(handler, 409, {"error": "username ja cadastrado"})
                return True
        finally:
            handler._db_release(con)
        write_json(handler, 200, {"ok": True, "username": username, "role": role})
        return True

    return False

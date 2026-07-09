from __future__ import annotations

import sqlite3
import time
from http import HTTPStatus
from http.cookies import SimpleCookie

from dakota_gateway import auth
from dakota_gateway.state_db import init_db, query_one


def set_cookie(handler, name: str, value: str, max_age: int = 3600 * 12) -> None:
    cookie = SimpleCookie()
    cookie[name] = value
    cookie[name]["path"] = "/"
    cookie[name]["max-age"] = str(max_age)
    cookie[name]["httponly"] = True
    cookie[name]["samesite"] = "Lax"
    # Secure em produção ou quando HTTPS detectado
    if _is_production() or _is_https(handler):
        cookie[name]["secure"] = True
    handler.send_header("Set-Cookie", cookie.output(header="").strip())


def _is_https(handler) -> bool:
    """Detecta se a conexão é HTTPS (direta ou via proxy)."""
    forwarded = handler.headers.get("X-Forwarded-Proto") or ""
    if forwarded.lower() == "https":
        return True
    return False


def _is_production() -> bool:
    """Verifica se o modo produção está ativo."""
    import os
    return os.environ.get("DAKOTA_ENV", "").strip().lower() == "production"


def clear_cookie(handler, name: str) -> None:
    cookie = SimpleCookie()
    cookie[name] = ""
    cookie[name]["path"] = "/"
    cookie[name]["max-age"] = "0"
    handler.send_header("Set-Cookie", cookie.output(header="").strip())


def get_cookie(handler, name: str) -> str | None:
    raw = handler.headers.get("Cookie") or ""
    cookie = SimpleCookie()
    cookie.load(raw)
    if name not in cookie:
        return None
    return cookie[name].value


def authenticate_request(handler):
    cookie_value = get_cookie(handler, "dakota_session")
    if not cookie_value:
        return None
    parsed = auth.verify_cookie(handler.server.cookie_secret, cookie_value)
    if not parsed:
        return None

    username, token, _exp = parsed
    token_hash = auth.sha256_hex(token.encode("utf-8"))
    con = handler._db()
    try:
        try:
            row = query_one(
                con,
                "SELECT u.id,u.username,u.role,s.expires_at_ms "
                "FROM users u JOIN sessions s ON s.user_id=u.id "
                "WHERE u.username=? AND s.token_hash=? "
                "ORDER BY s.id DESC LIMIT 1",
                (username, token_hash),
            )
        except sqlite3.OperationalError as exc:
            if "no such table" in str(exc).lower():
                init_db(con)
                return None
            raise
        if not row:
            return None
        if int(row["expires_at_ms"]) < int(time.time() * 1000):
            return None
        return {"id": int(row["id"]), "username": row["username"], "role": row["role"]}
    finally:
        handler._db_release(con)


def require_user(handler, roles: set[str] | None = None):
    user = authenticate_request(handler)
    if not user:
        handler.send_response(HTTPStatus.UNAUTHORIZED)
        handler.end_headers()
        return None
    if roles and user["role"] not in roles:
        handler.send_response(HTTPStatus.FORBIDDEN)
        handler.end_headers()
        return None
    return user


def require_page_user(handler, roles: set[str] | None = None):
    user = authenticate_request(handler)
    if not user:
        handler.send_response(302)
        handler.send_header("Location", "/login")
        handler.end_headers()
        return None
    if roles and user["role"] not in roles:
        handler.send_response(HTTPStatus.FORBIDDEN)
        handler.end_headers()
        return None
    return user

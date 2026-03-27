from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import time
from dataclasses import dataclass


def pbkdf2_hash_password(password: str, salt_b64: str | None = None) -> str:
    """
    Returns: pbkdf2_sha256$<iters>$<salt_b64>$<dk_b64>
    """
    iters = 200_000
    if salt_b64 is None:
        salt = os.urandom(16)
        salt_b64 = base64.b64encode(salt).decode("ascii")
    else:
        salt = base64.b64decode(salt_b64.encode("ascii"))
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iters, dklen=32)
    dk_b64 = base64.b64encode(dk).decode("ascii")
    return f"pbkdf2_sha256${iters}${salt_b64}${dk_b64}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iters_s, salt_b64, dk_b64 = stored.split("$", 3)
        if algo != "pbkdf2_sha256":
            return False
        iters = int(iters_s)
        salt = base64.b64decode(salt_b64.encode("ascii"))
        want = base64.b64decode(dk_b64.encode("ascii"))
        got = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iters, dklen=len(want))
        return hmac.compare_digest(got, want)
    except Exception:
        return False


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def new_session_token() -> str:
    return secrets.token_urlsafe(32)


def sign_cookie(secret: bytes, username: str, token: str, expires_at_ms: int) -> str:
    """
    Cookie value: base64url(username|token|exp|sig)
    """
    payload = f"{username}|{token}|{expires_at_ms}".encode("utf-8")
    sig = hmac.new(secret, payload, hashlib.sha256).hexdigest()
    raw = f"{username}|{token}|{expires_at_ms}|{sig}".encode("utf-8")
    # Strip base64 padding so the cookie value stays token-safe and avoids quoting
    # in Set-Cookie headers, which makes non-browser HTTP clients easier to use.
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def verify_cookie(secret: bytes, cookie_val: str) -> tuple[str, str, int] | None:
    try:
        padded = cookie_val + ("=" * (-len(cookie_val) % 4))
        raw = base64.urlsafe_b64decode(padded.encode("ascii"))
        parts = raw.decode("utf-8").split("|")
        if len(parts) != 4:
            return None
        username, token, exp_s, sig = parts
        exp = int(exp_s)
        payload = f"{username}|{token}|{exp}".encode("utf-8")
        want = hmac.new(secret, payload, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(want, sig):
            return None
        if int(time.time() * 1000) > exp:
            return None
        return username, token, exp
    except Exception:
        return None


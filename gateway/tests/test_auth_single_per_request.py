"""Regressão: cada requisição deve ser autenticada UMA única vez.

Antes da correção, o guard central (_api_auth_guard) autenticava e os
endpoints reautenticavam via _require()/_auth() — parse de cookie +
verify_cookie + query no banco repetidos por requisição. A correção guarda
o usuário autenticado no contexto do handler e o reutiliza; o cache é
invalidado a cada nova requisição (keep-alive reusa o handler).

O teste conta chamadas a dakota_gateway.auth.verify_cookie (ponto único de
validação do cookie de sessão).
"""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.request import HTTPCookieProcessor, Request, build_opener

ROOT = Path(__file__).resolve().parents[2]
GATEWAY_DIR = ROOT / "gateway"
if str(GATEWAY_DIR) not in sys.path:
    sys.path.insert(0, str(GATEWAY_DIR))

import http.cookiejar

import dakota_gateway.auth as auth
from dakota_gateway.state_db import connect, init_db, now_ms

CONTROL_SERVER_PATH = GATEWAY_DIR / "control" / "server.py"
SPEC = importlib.util.spec_from_file_location("control_server_auth1", CONTROL_SERVER_PATH)
CONTROL = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(CONTROL)

import control.auth_support as auth_support


class AuthSinglePerRequestTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tmpdir.name) / "test.db")
        con = connect(self.db_path)
        init_db(con)
        con.execute(
            "INSERT INTO users(username,password_hash,role,created_at_ms) VALUES(?,?,'admin',?)",
            ("admin", auth.pbkdf2_hash_password("admin123"), now_ms()),
        )
        con.close()
        self.server = CONTROL.ControlServer(
            ("127.0.0.1", 0),
            CONTROL.Handler,
            db_path=self.db_path,
            cookie_secret=b"test_cookie_secret_32_bytes___",
            hmac_key=b"test_hmac_key_32_bytes__________",
        )
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        time.sleep(0.2)
        self.opener = build_opener(HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self._request("POST", "/api/login", {"username": "admin", "password": "admin123"})

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmpdir.cleanup()

    def _request(self, method: str, path: str, data: dict | None = None):
        url = f"http://127.0.0.1:{self.port}{path}"
        body = None if data is None else json.dumps(data).encode("utf-8")
        req = Request(url, data=body, headers={"Content-Type": "application/json"}, method=method)
        with self.opener.open(req, timeout=5) as resp:
            return resp.status, resp.read().decode("utf-8")

    def test_api_request_authenticates_exactly_once(self):
        calls = []
        original = auth.verify_cookie

        def counting(secret, value):
            calls.append(1)
            return original(secret, value)

        with patch.object(auth, "verify_cookie", counting):
            status, _body = self._request("GET", "/api/me")
        self.assertEqual(status, 200)
        self.assertEqual(
            len(calls), 1,
            f"verify_cookie chamado {len(calls)}x na mesma requisição (guard + endpoint)",
        )

    def test_auth_cache_reused_within_request_and_reset_between_requests(self):
        """Cache por requisição: 2ª chamada no mesmo handler não revalida;
        reset_auth_cache (nova requisição no mesmo handler, keep-alive) revalida."""
        con = connect(self.db_path)
        try:
            row = con.execute("SELECT id FROM users WHERE username='admin'").fetchone()
            token = auth.new_session_token()
            con.execute(
                "INSERT INTO sessions(user_id,token_hash,created_at_ms,expires_at_ms) VALUES(?,?,?,?)",
                (int(row["id"]), auth.sha256_hex(token.encode("utf-8")), now_ms(), now_ms() + 60000),
            )
        finally:
            con.close()
        cookie_value = auth.sign_cookie(self.server.cookie_secret, "admin", token, now_ms() + 60000)

        pool = self.server.db_pool
        handler = SimpleNamespace(
            headers={"Cookie": f"dakota_session={cookie_value}"},
            server=SimpleNamespace(cookie_secret=self.server.cookie_secret),
            _db=pool.acquire,
            _db_release=pool.release,
        )

        calls = []
        original = auth.verify_cookie

        def counting(secret, value):
            calls.append(1)
            return original(secret, value)

        with patch.object(auth, "verify_cookie", counting):
            user1 = auth_support.authenticate_request(handler)
            user2 = auth_support.authenticate_request(handler)
            self.assertEqual(len(calls), 1, "cache do handler não reutilizado")
            self.assertIsNotNone(user1)
            self.assertEqual(user1, user2)
            auth_support.reset_auth_cache(handler)
            user3 = auth_support.authenticate_request(handler)
            self.assertEqual(len(calls), 2, "reset do cache (nova requisição) não revalidou")
            self.assertEqual(user1, user3)


if __name__ == "__main__":
    unittest.main()

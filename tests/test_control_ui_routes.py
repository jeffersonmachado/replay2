#!/usr/bin/env python3
from __future__ import annotations

import http.cookiejar
import importlib.util
import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from urllib.request import Request, build_opener, HTTPCookieProcessor

ROOT = Path(__file__).resolve().parents[1]
GATEWAY_DIR = ROOT / "gateway"
sys.path.insert(0, str(GATEWAY_DIR))

import dakota_gateway.auth as auth
from dakota_gateway.state_db import connect, init_db, now_ms

CONTROL_SERVER_PATH = GATEWAY_DIR / "control" / "server.py"
SPEC = importlib.util.spec_from_file_location("control_server", CONTROL_SERVER_PATH)
CONTROL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CONTROL)


class ControlUiRoutesTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tmpdir.name) / "test.db")
        self.cookie_secret = b"test_cookie_secret_32_bytes___"
        self.hmac_key = b"test_hmac_key_32_bytes__________"
        con = connect(self.db_path)
        init_db(con)
        ph = auth.pbkdf2_hash_password("admin123")
        con.execute(
            "INSERT INTO users(username,password_hash,role,created_at_ms) VALUES(?,?,'admin',?)",
            ("admin", ph, now_ms()),
        )
        con.close()

        try:
            self.server = CONTROL.ControlServer(
                ("127.0.0.1", 0),
                CONTROL.Handler,
                db_path=self.db_path,
                cookie_secret=self.cookie_secret,
                hmac_key=self.hmac_key,
            )
        except PermissionError as exc:
            raise unittest.SkipTest(f"sandbox sem permissao para abrir socket local: {exc}") from exc
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        time.sleep(0.2)
        self.opener = build_opener(HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self._request("POST", "/api/login", {"username": "admin", "password": "admin123"})

    def tearDown(self):
        if hasattr(self, "server"):
            self.server.shutdown()
            self.server.server_close()
        self.tmpdir.cleanup()

    def _request(self, method: str, path: str, data: dict | None = None) -> tuple[int, str, dict]:
        url = f"http://127.0.0.1:{self.port}{path}"
        body = None if data is None else json.dumps(data).encode("utf-8")
        req = Request(url, data=body, headers={"Content-Type": "application/json"}, method=method)
        with self.opener.open(req, timeout=5) as resp:
            payload = resp.read().decode("utf-8")
            return resp.status, payload, dict(resp.headers)

    def test_html_pages_render_navigation_shell(self):
        for path in ("/", "/runs", "/runs/new", "/gateway", "/catalog", "/observability", "/admin"):
            with self.subTest(path=path):
                status, body, _headers = self._request("GET", path)
                self.assertEqual(status, 200)
                self.assertIn("Dashboard", body)
                self.assertIn("Execuções", body)
                self.assertIn("Gateway", body)
                self.assertIn("Catálogo", body)
                self.assertIn("Observabilidade", body)
                self.assertIn("Administração", body)

        status, body, _headers = self._request("GET", "/login")
        self.assertEqual(status, 200)
        self.assertIn("loginForm", body)
        self.assertIn("/assets/js/pages/login.js", body)

    def test_run_detail_route_and_js_assets_are_available(self):
        status, body, _headers = self._request("GET", "/runs/123")
        self.assertEqual(status, 200)
        self.assertIn("Detalhe da Run", body)
        self.assertIn("/assets/js/pages/run_detail.js", body)

        for path in (
            "/assets/js/core/api.js",
            "/assets/js/core/navigation.js",
            "/assets/js/pages/dashboard.js",
            "/assets/js/pages/login.js",
            "/assets/js/pages/runs.js",
            "/assets/js/pages/gateway.js",
        ):
            with self.subTest(path=path):
                status, body, headers = self._request("GET", path)
                self.assertEqual(status, 200)
                self.assertIn("application/javascript", headers["Content-Type"])
                self.assertTrue(len(body) > 20)


if __name__ == "__main__":
    unittest.main(verbosity=2)

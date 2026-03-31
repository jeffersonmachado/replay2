from __future__ import annotations

import http.cookiejar
import importlib.util
import json
import sys
import tempfile
import threading
import time
import unittest
from urllib.error import HTTPError
from pathlib import Path
from urllib.request import HTTPCookieProcessor, Request, build_opener

ROOT = Path(__file__).resolve().parents[2]
GATEWAY_DIR = ROOT / "gateway"
if str(GATEWAY_DIR) not in sys.path:
    sys.path.insert(0, str(GATEWAY_DIR))

import dakota_gateway.auth as auth
from dakota_gateway.state_db import connect, init_db, now_ms

CONTROL_SERVER_PATH = GATEWAY_DIR / "control" / "server.py"
SPEC = importlib.util.spec_from_file_location("control_server", CONTROL_SERVER_PATH)
CONTROL = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(CONTROL)


class UiRoutesTests(unittest.TestCase):
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

        self.server = CONTROL.ControlServer(
            ("127.0.0.1", 0),
            CONTROL.Handler,
            db_path=self.db_path,
            cookie_secret=self.cookie_secret,
            hmac_key=self.hmac_key,
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
            payload = resp.read().decode("utf-8")
            return resp.status, payload, dict(resp.headers)

    def _request_any(self, method: str, path: str, data: dict | None = None):
        url = f"http://127.0.0.1:{self.port}{path}"
        body = None if data is None else json.dumps(data).encode("utf-8")
        req = Request(url, data=body, headers={"Content-Type": "application/json"}, method=method)
        try:
            with self.opener.open(req, timeout=5) as resp:
                payload = resp.read().decode("utf-8")
                return resp.status, payload, dict(resp.headers)
        except HTTPError as exc:
            payload = exc.read().decode("utf-8")
            return exc.code, payload, dict(exc.headers)

    def test_authenticated_routes_return_sidebar_and_title(self):
        expected_titles = {
            "/": "Painel Operacional",
            "/runs": "Execucoes",
            "/gateway": "Gateway",
            "/captures": "Capturas",
            "/captures/new": "Capturas",
            "/catalog": "Catalogo",
            "/observability": "Observabilidade",
            "/admin": "Administracao",
        }
        for path, title in expected_titles.items():
            with self.subTest(path=path):
                status, body, _headers = self._request("GET", path)
                self.assertEqual(status, 200)
                self.assertIn("r2ctl-sidebar", body)
                self.assertIn("Dashboard", body)
                self.assertIn(title, body)

    def test_ui_assets_load(self):
        for path in (
            "/assets/js/core/navigation.js",
            "/assets/js/pages/dashboard.js",
            "/assets/js/pages/gateway.js",
        ):
            with self.subTest(path=path):
                status, body, headers = self._request("GET", path)
                self.assertEqual(status, 200)
                self.assertIn("application/javascript", headers.get("Content-Type", ""))
                self.assertGreater(len(body), 20)

    def test_gateway_state_and_capture_lifecycle_via_ui_endpoints(self):
        status, body, _headers = self._request("GET", "/api/gateway/state")
        self.assertEqual(status, 200)
        state = json.loads(body)
        self.assertFalse(bool(state.get("active")))
        self.assertIn("policy", state)
        self.assertEqual(state["policy"].get("desired_ssh_route"), "direct_port_22")
        self.assertFalse(bool(state["policy"].get("capture_available")))

        status, body, _headers = self._request("POST", "/api/gateway/activate", {})
        self.assertEqual(status, 200)
        state = json.loads(body)
        self.assertTrue(bool(state.get("active")))
        self.assertEqual(state["policy"].get("desired_ssh_route"), "gateway_proxy")
        self.assertTrue(bool(state["policy"].get("capture_available")))
        auto_capture = state.get("auto_capture") or {}
        self.assertEqual(auto_capture.get("status"), "active")
        self.assertTrue(int(auto_capture.get("id", 0)) > 0)

        status, body, _headers = self._request_any("POST", "/api/captures/start", {"notes": "teste-ui"})
        self.assertEqual(status, 409)
        capture_id = int(auto_capture["id"])
        status, body, _headers = self._request("GET", f"/api/captures/{capture_id}/events")
        self.assertEqual(status, 200)
        events_payload = json.loads(body)
        self.assertEqual(int(events_payload.get("capture_id", 0)), capture_id)
        self.assertIn("events", events_payload)

        status, body, _headers = self._request("POST", f"/api/captures/{capture_id}/stop", {})
        self.assertEqual(status, 200)
        stopped = json.loads(body)
        self.assertEqual(stopped.get("status"), "finished")

    def test_deactivate_gateway_blocks_when_capture_active_without_force(self):
        status, body, _headers = self._request("POST", "/api/gateway/activate", {})
        self.assertEqual(status, 200)
        capture = (json.loads(body) or {}).get("auto_capture") or {}
        capture_id = int(capture["id"])

        status, body, _headers = self._request_any("POST", "/api/gateway/deactivate", {})
        self.assertEqual(status, 409)
        err = json.loads(body)
        self.assertEqual(int(err.get("active_capture_id", 0)), capture_id)

        status, body, _headers = self._request("POST", "/api/gateway/deactivate", {"force": True})
        self.assertEqual(status, 200)
        state = json.loads(body)
        self.assertFalse(bool(state.get("active")))
        interrupted = state.get("interrupted_capture_ids") or []
        self.assertIn(capture_id, interrupted)

        status, body, _headers = self._request("GET", f"/api/captures/{capture_id}")
        self.assertEqual(status, 200)
        stopped_capture = json.loads(body)
        self.assertEqual(stopped_capture.get("status"), "interrupted")


if __name__ == "__main__":
    unittest.main()

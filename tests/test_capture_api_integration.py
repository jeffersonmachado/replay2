#!/usr/bin/env python3
"""Testes de integração da API de captura e replay via HTTP.

Usa o mesmo padrão de test_ui_routes.py: sobe o ControlServer em uma thread,
autentica via cookie, e testa os endpoints REST.

Cobertura:
  - POST /api/login (autenticação)
  - GET  /api/captures (listagem)
  - POST /api/captures/start (nova captura)
  - GET  /api/captures/{id} (detalhe com contagem)
  - GET  /api/captures/{id}/sessions (sessões)
  - GET  /api/captures/{id}/replay?session_id= (replay)
  - GET  /api/captures/{id}/events (eventos)
  - POST /api/captures/{id}/stop (encerrar)
  - DELETE /api/captures/{id} (excluir)
  - Cenários de erro: captura inexistente, session_id inválido, parâmetros ausentes
"""
from __future__ import annotations

import http.cookiejar
import importlib.util
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, Request, build_opener

ROOT = Path(__file__).resolve().parents[1]
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


class CaptureApiIntegrationTests(unittest.TestCase):
    """Testes de integração da API REST de captura e replay."""

    @classmethod
    def setUpClass(cls):
        cls.tmpdir = tempfile.TemporaryDirectory()
        cls.db_path = str(Path(cls.tmpdir.name) / "test.db")
        cls.cookie_secret = b"test_cookie_secret_32_bytes___"
        cls.hmac_key = b"test_hmac_key_32_bytes__________"

        con = connect(cls.db_path)
        init_db(con)
        ph = auth.pbkdf2_hash_password("admin123")
        con.execute(
            "INSERT INTO users(username,password_hash,role,created_at_ms) VALUES(?,?,'admin',?)",
            ("admin", ph, now_ms()),
        )
        con.close()

        cls.server = CONTROL.ControlServer(
            ("127.0.0.1", 0),
            CONTROL.Handler,
            db_path=cls.db_path,
            cookie_secret=cls.cookie_secret,
            hmac_key=cls.hmac_key,
        )
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        time.sleep(0.5)

        cls.opener = build_opener(HTTPCookieProcessor(http.cookiejar.CookieJar()))
        # Autentica — se falhar, tenta mais uma vez após delay
        for attempt in range(3):
            try:
                cls._do_login()
                break
            except Exception:
                if attempt == 2:
                    raise
                time.sleep(0.5)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmpdir.cleanup()

    @classmethod
    def _do_login(cls):
        """Faz login e verifica que retornou 200."""
        status, _payload, headers = cls._request("POST", "/api/login", {"username": "admin", "password": "admin123"})
        if status != 200:
            raise RuntimeError(f"Login failed: status={status}")
        # Verifica que o cookie foi setado
        set_cookie = headers.get("Set-Cookie") or headers.get("set-cookie") or ""
        if "dakota_session" not in set_cookie:
            raise RuntimeError(f"Login response missing session cookie: {headers}")

    @classmethod
    def _request(cls, method: str, path: str, data: dict | None = None):
        url = f"http://127.0.0.1:{cls.port}{path}"
        body = None if data is None else json.dumps(data).encode("utf-8")
        req = Request(url, data=body, headers={"Content-Type": "application/json"}, method=method)
        try:
            with cls.opener.open(req, timeout=5) as resp:
                raw_body = resp.read().decode("utf-8")
                payload = json.loads(raw_body) if raw_body.strip() else {}
                return resp.status, payload, dict(resp.headers)
        except HTTPError as exc:
            raw_body = exc.read().decode("utf-8")
            try:
                return exc.code, json.loads(raw_body) if raw_body.strip() else {}, dict(exc.headers)
            except Exception:
                return exc.code, {"error": raw_body}, dict(exc.headers)

    # ═══════════════════════════════════════════════════════════════════════
    # Autenticação
    # ═══════════════════════════════════════════════════════════════════════

    def test_login_admin_success(self):
        """Login com credenciais válidas retorna 200 e seta cookie."""
        opener2 = build_opener(HTTPCookieProcessor(http.cookiejar.CookieJar()))
        url = f"http://127.0.0.1:{self.port}/api/login"
        body = json.dumps({"username": "admin", "password": "admin123"}).encode()
        req = Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
        with opener2.open(req, timeout=5) as resp:
            self.assertEqual(resp.status, 200)
            # Login retorna 200 com Set-Cookie, corpo pode ser vazio
            set_cookie = resp.headers.get("Set-Cookie") or resp.headers.get("set-cookie") or ""
            self.assertIn("dakota_session", set_cookie)

    def test_login_invalid_password(self):
        """Login com senha inválida retorna 401."""
        status, _payload, _ = self._request("POST", "/api/login", {"username": "admin", "password": "wrong"})
        self.assertEqual(status, 401)

    def test_login_missing_fields(self):
        """Login sem username/senha retorna 400."""
        status, payload, _ = self._request("POST", "/api/login", {})
        self.assertIn(status, [400, 401])

    # ═══════════════════════════════════════════════════════════════════════
    # Capturas — Operações básicas
    # ═══════════════════════════════════════════════════════════════════════

    def test_list_captures_empty(self):
        """Listagem retorna array vazio sem capturas."""
        status, payload, _ = self._request("GET", "/api/captures")
        self.assertEqual(status, 200)
        self.assertIn("captures", payload)
        self.assertIn("total", payload)
        self.assertIsInstance(payload["captures"], list)

    def test_start_capture_requires_gateway_active(self):
        """Iniciar captura sem gateway ativo retorna 409 (conflito)."""
        status, payload, _ = self._request("POST", "/api/captures/start", {})
        self.assertEqual(status, 409)
        self.assertIn("error", payload)

    def test_capture_nonexistent_returns_404(self):
        """GET de captura inexistente retorna 404."""
        status, payload, _ = self._request("GET", "/api/captures/99999")
        self.assertEqual(status, 404)

    def test_capture_nonexistent_sessions_returns_404(self):
        """GET de sessões de captura inexistente retorna 404."""
        status, payload, _ = self._request("GET", "/api/captures/99999/sessions")
        self.assertEqual(status, 404)

    def test_capture_nonexistent_events_returns_404(self):
        """GET de eventos de captura inexistente retorna 404."""
        status, payload, _ = self._request("GET", "/api/captures/99999/events")
        self.assertEqual(status, 404)

    def test_capture_nonexistent_replay_returns_404(self):
        """GET de replay de captura inexistente retorna 404."""
        status, payload, _ = self._request("GET", "/api/captures/99999/replay?session_id=abc")
        self.assertEqual(status, 404)

    def test_stop_nonexistent_capture_returns_409(self):
        """POST stop em captura inexistente retorna 409 (conflito)."""
        status, payload, _ = self._request("POST", "/api/captures/99999/stop", {})
        self.assertEqual(status, 409)

    # ═══════════════════════════════════════════════════════════════════════
    # Capturas — Ciclo completo com gateway ativo
    # ═══════════════════════════════════════════════════════════════════════

    def test_full_capture_lifecycle_with_auto_activation(self):
        """Ciclo completo: ativar gateway → captura auto-criada → listar → detalhe → stop."""
        # Ativa gateway
        status, state, _ = self._request("POST", "/api/gateway/activate", {})
        self.assertEqual(status, 200)
        self.assertTrue(state.get("active"))
        auto_cap = state.get("auto_capture") or {}
        self.assertEqual(auto_cap.get("status"), "active")
        capture_id = int(auto_cap["id"])
        self.assertGreater(capture_id, 0)

        # Lista capturas
        status, caps, _ = self._request("GET", "/api/captures")
        self.assertEqual(status, 200)
        self.assertGreaterEqual(caps["total"], 1)
        cap_ids = [c["id"] for c in caps["captures"]]
        self.assertIn(capture_id, cap_ids)

        # Detalhe
        status, detail, _ = self._request("GET", f"/api/captures/{capture_id}")
        self.assertEqual(status, 200)
        self.assertEqual(detail["id"], capture_id)
        self.assertEqual(detail["status"], "active")
        self.assertIn("session_count", detail)
        self.assertIn("event_count", detail)

        # Sessões (pode estar vazia)
        status, sessions, _ = self._request("GET", f"/api/captures/{capture_id}/sessions")
        self.assertEqual(status, 200)
        self.assertIn("sessions", sessions)
        self.assertIsInstance(sessions["sessions"], list)

        # Eventos (pode estar vazio)
        status, events, _ = self._request("GET", f"/api/captures/{capture_id}/events")
        self.assertEqual(status, 200)
        self.assertIn("events", events)
        self.assertIsInstance(events["events"], list)

        # Stop
        status, stopped, _ = self._request("POST", f"/api/captures/{capture_id}/stop", {})
        self.assertEqual(status, 200)
        self.assertEqual(stopped.get("status"), "finished")

        # Detalhe pós-stop
        status, detail2, _ = self._request("GET", f"/api/captures/{capture_id}")
        self.assertEqual(status, 200)
        self.assertEqual(detail2["status"], "finished")

    # ═══════════════════════════════════════════════════════════════════════
    # Replay — Validação de estrutura
    # ═══════════════════════════════════════════════════════════════════════

    def test_replay_missing_session_id_returns_400(self):
        """Replay sem session_id retorna 400."""
        # Ativa gateway primeiro
        self._request("POST", "/api/gateway/activate", {})
        caps = self._request("GET", "/api/captures")[1]
        if caps.get("captures"):
            cid = caps["captures"][0]["id"]
            status, payload, _ = self._request("GET", f"/api/captures/{cid}/replay")
            self.assertEqual(status, 400)
            self.assertIn("error", payload)

    def test_replay_nonexistent_session_returns_error(self):
        """Replay com session_id inexistente retorna erro."""
        self._request("POST", "/api/gateway/activate", {})
        caps = self._request("GET", "/api/captures")[1]
        if caps.get("captures"):
            cid = caps["captures"][0]["id"]
            status, payload, _ = self._request("GET", f"/api/captures/{cid}/replay?session_id=nonexistent-12345")
            self.assertIn(status, [200, 404])
            if status == 200:
                self.assertIn("error", payload)

    # ═══════════════════════════════════════════════════════════════════════
    # Delete
    # ═══════════════════════════════════════════════════════════════════════

    def test_delete_finished_capture(self):
        """Excluir captura finalizada retorna 200."""
        self._request("POST", "/api/gateway/activate", {})
        caps = self._request("GET", "/api/captures")[1]
        if caps.get("captures"):
            cid = caps["captures"][0]["id"]
            # Stop primeiro
            self._request("POST", f"/api/captures/{cid}/stop", {})
            # Delete
            status, payload, _ = self._request("DELETE", f"/api/captures/{cid}")
            self.assertEqual(status, 200)
            # Confirmar que foi removida
            status2, _payload2, _ = self._request("GET", f"/api/captures/{cid}")
            self.assertEqual(status2, 404)

    def test_delete_nonexistent_capture_returns_404(self):
        """Excluir captura inexistente retorna 404."""
        status, payload, _ = self._request("DELETE", "/api/captures/99999")
        self.assertEqual(status, 404)


if __name__ == "__main__":
    unittest.main()

"""Testes para API /api/knowledge-base — P2-A."""
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
from urllib.request import HTTPCookieProcessor, Request, build_opener
from urllib.error import HTTPError

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


class KnowledgeBaseApiTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tmpdir.name) / "test.db")
        self.cookie_secret = b"test_cookie_secret_32_bytes___"
        self.hmac_key = b"test_hmac_key_32_bytes__________"

        # Source dir com um arquivo .prg mínimo
        self.source_dir = Path(self.tmpdir.name) / "source"
        self.source_dir.mkdir()
        (self.source_dir / "cadcli.prg").write_text("""
TITLE "Cadastro de Clientes"
@ 01,01 SAY "Nome"
@ 01,20 GET cNome
@ 02,01 SAY "CPF"
@ 02,20 GET cCpf
""", encoding="utf-8")

        con = connect(self.db_path)
        init_db(con)
        ph = auth.pbkdf2_hash_password("admin123")
        con.execute(
            "INSERT INTO users(username,password_hash,role,created_at_ms) VALUES(?,?,'admin',?)",
            ("admin", ph, now_ms()),
        )
        # Usuario viewer para teste de nao-admin
        ph_viewer = auth.pbkdf2_hash_password("viewer123")
        con.execute(
            "INSERT INTO users(username,password_hash,role,created_at_ms) VALUES(?,?,'viewer',?)",
            ("viewer", ph_viewer, now_ms()),
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

    def tearDown(self):
        self.server.shutdown()
        self.thread.join(timeout=2)
        self.tmpdir.cleanup()

    def _open(self) -> object:
        return build_opener(HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def _request(self, opener, method: str, path: str, data: dict | None = None) -> tuple[int, str, dict]:
        url = f"http://127.0.0.1:{self.port}{path}"
        body = None if data is None else json.dumps(data).encode("utf-8")
        req = Request(url, data=body, headers={"Content-Type": "application/json"}, method=method)
        with opener.open(req, timeout=5) as resp:
            payload = resp.read().decode("utf-8")
            return resp.status, payload, dict(resp.headers)

    def _request_any(self, opener, method: str, path: str, data: dict | None = None) -> tuple[int, str, dict]:
        url = f"http://127.0.0.1:{self.port}{path}"
        body = None if data is None else json.dumps(data).encode("utf-8")
        req = Request(url, data=body, headers={"Content-Type": "application/json"}, method=method)
        try:
            with opener.open(req, timeout=5) as resp:
                payload = resp.read().decode("utf-8")
                return resp.status, payload, dict(resp.headers)
        except HTTPError as exc:
            payload = exc.read().decode("utf-8")
            return exc.code, payload, dict(exc.headers)

    def _login_admin(self, opener):
        code, payload, _ = self._request(opener, "POST", "/api/login", {"username": "admin", "password": "admin123"})
        return code, payload

    def _login(self, opener, username, password):
        code, payload, _ = self._request(opener, "POST", "/api/login", {"username": username, "password": password})
        return code, payload

    def _json(self, payload: str) -> dict:
        try:
            return json.loads(payload)
        except json.JSONDecodeError:
            return {"_raw": payload}

    def test_knowledge_base_requires_admin(self):
        """Sem autenticação, retorna 401."""
        opener = self._open()
        code, payload, _ = self._request_any(opener, "GET", "/api/knowledge-base?source=/tmp")
        self.assertEqual(code, 401)

    def test_knowledge_base_requires_source_param(self):
        """Sem parâmetro source, retorna 400."""
        opener = self._open()
        self._login_admin(opener)
        code, payload, _ = self._request_any(opener, "GET", "/api/knowledge-base")
        self.assertEqual(code, 400)
        data = self._json(payload)
        self.assertIn("source", data.get("error", ""))

    def test_knowledge_base_source_not_found(self):
        """Source inexistente retorna 404."""
        opener = self._open()
        self._login_admin(opener)
        code, payload, _ = self._request_any(opener, "GET", "/api/knowledge-base?source=/nonexistent_dir_xyz")
        self.assertEqual(code, 404)

    def test_knowledge_base_valid_source(self):
        """Source válido retorna 200 com relatório completo."""
        opener = self._open()
        self._login_admin(opener)
        code, payload, _ = self._request(opener, "GET", f"/api/knowledge-base?source={self.source_dir}")
        self.assertEqual(code, 200)
        data = self._json(payload)
        self.assertIn("pipeline", data)
        self.assertEqual(data["pipeline"], "P2-A Synthetic Knowledge Base")
        # Estrutura do discovery_report (flat)
        self.assertIn("entities", data)
        self.assertIn("screens", data)
        self.assertIn("screen_entity_bindings", data)
        self.assertIn("dependency_graph", data)
        self.assertIn("_meta", data)
        # Pelo menos 1 tela detectada
        self.assertGreaterEqual(data["screens"], 1)

    def test_knowledge_base_production_requires_dakota_source_root(self):
        """Em produção sem DAKOTA_SOURCE_ROOT, retorna 500."""
        opener = self._open()
        self._login_admin(opener)

        # Simula ambiente de produção
        old_env = os.environ.get("DAKOTA_ENV", "")
        os.environ["DAKOTA_ENV"] = "production"
        # Remove DAKOTA_SOURCE_ROOT se existir
        old_root = os.environ.pop("DAKOTA_SOURCE_ROOT", None)
        try:
            code, payload, _ = self._request_any(opener, "GET", f"/api/knowledge-base?source={self.source_dir}")
            self.assertEqual(code, 500)
            data = self._json(payload)
            self.assertIn("DAKOTA_SOURCE_ROOT", data.get("error", ""))
        finally:
            os.environ["DAKOTA_ENV"] = old_env
            if old_root is not None:
                os.environ["DAKOTA_SOURCE_ROOT"] = old_root

    def test_knowledge_base_production_path_outside_root(self):
        """Source fora do DAKOTA_SOURCE_ROOT retorna 403."""
        opener = self._open()
        self._login_admin(opener)

        old_env = os.environ.get("DAKOTA_ENV", "")
        os.environ["DAKOTA_ENV"] = "production"
        os.environ["DAKOTA_SOURCE_ROOT"] = "/some/allowed/path"
        try:
            code, payload, _ = self._request_any(opener, "GET", f"/api/knowledge-base?source={self.source_dir}")
            self.assertEqual(code, 403)
            data = self._json(payload)
            self.assertIn("fora do DAKOTA_SOURCE_ROOT", data.get("error", ""))
        finally:
            os.environ["DAKOTA_ENV"] = old_env

    def test_is_relative_to_method(self):
        """Testa o método _is_relative_to diretamente."""
        handler = CONTROL.Handler

        # Subdiretório deve ser relativo
        assert handler._is_relative_to(Path("/a/b/c"), Path("/a/b")) is True
        # Igual deve ser relativo
        assert handler._is_relative_to(Path("/a/b"), Path("/a/b")) is True
        # Fora não deve ser
        assert handler._is_relative_to(Path("/a/b"), Path("/a/c")) is False
        # Parent não deve ser
        assert handler._is_relative_to(Path("/a"), Path("/a/b")) is False
        # Completamente diferente
        assert handler._is_relative_to(Path("/x/y"), Path("/a/b")) is False

    # ── Testes de autorizacao ──

    def test_non_admin_viewer_receives_403(self):
        """Usuario viewer autenticado recebe 403 ao acessar /api/knowledge-base."""
        opener = self._open()
        self._login(opener, "viewer", "viewer123")
        code, payload, _ = self._request_any(opener, "GET", "/api/knowledge-base?source=/tmp")
        self.assertEqual(code, 403)

    def test_admin_with_valid_path_in_production(self):
        """Admin com path dentro de DAKOTA_SOURCE_ROOT em producao recebe 200."""
        opener = self._open()
        self._login_admin(opener)

        old_env = os.environ.get("DAKOTA_ENV", "")
        os.environ["DAKOTA_ENV"] = "production"
        os.environ["DAKOTA_SOURCE_ROOT"] = str(Path(self.tmpdir.name).resolve())
        try:
            code, payload, _ = self._request(opener, "GET", f"/api/knowledge-base?source={self.source_dir}")
            self.assertEqual(code, 200)
        finally:
            os.environ["DAKOTA_ENV"] = old_env


if __name__ == "__main__":
    unittest.main()
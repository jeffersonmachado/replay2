"""Testes do rate limiting por IP (X2).

Cobre a janela fixa do `RateLimiter` (limite, reset, isolamento por chave,
retry_after, poda), a configuração por env e a integração HTTP real: servidor
com limite baixo responde 429 com Retry-After após estourar o teto, enquanto
/api/login passa pelo limiter genérico (throttle próprio) e /health não é
limitado.
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

from control.rate_limit import RateLimiter, from_env


class RateLimiterUnitTests(unittest.TestCase):
    def test_permite_ate_o_teto_e_depois_nega(self):
        limiter = RateLimiter(max_requests=3, window_seconds=60)
        base = 1000.0
        self.assertTrue(limiter.allow("ip1", now=base))
        self.assertTrue(limiter.allow("ip1", now=base + 1))
        self.assertTrue(limiter.allow("ip1", now=base + 2))
        self.assertFalse(limiter.allow("ip1", now=base + 3))

    def test_janela_reseta(self):
        limiter = RateLimiter(max_requests=2, window_seconds=10)
        base = 1000.0
        self.assertTrue(limiter.allow("ip1", now=base))
        self.assertTrue(limiter.allow("ip1", now=base + 1))
        self.assertFalse(limiter.allow("ip1", now=base + 2))
        # nova janela: volta a permitir
        self.assertTrue(limiter.allow("ip1", now=base + 11))

    def test_isolamento_por_chave(self):
        limiter = RateLimiter(max_requests=1, window_seconds=60)
        base = 1000.0
        self.assertTrue(limiter.allow("ip1", now=base))
        self.assertFalse(limiter.allow("ip1", now=base))
        self.assertTrue(limiter.allow("ip2", now=base))

    def test_retry_after_positivo_e_decresce_na_janela(self):
        limiter = RateLimiter(max_requests=1, window_seconds=60)
        base = 1000.0
        limiter.allow("ip1", now=base)
        self.assertGreaterEqual(limiter.retry_after("ip1", now=base + 10), 50)
        self.assertGreaterEqual(limiter.retry_after("ip1", now=base + 59), 1)
        self.assertEqual(limiter.retry_after("sem-registro", now=base), 1)

    def test_poda_de_chaves_vencidas(self):
        limiter = RateLimiter(max_requests=1, window_seconds=5)
        base = 1000.0
        for i in range(5):
            limiter.allow(f"ip{i}", now=base)
        limiter.allow("ip-novo", now=base + 100)
        limiter._purge_locked(base + 100)
        self.assertEqual(list(limiter._hits.keys()), ["ip-novo"])

    def test_from_env_default_custom_e_desabilitado(self):
        self.assertIsNone(from_env({"DAKOTA_RATE_LIMIT": "0"}))
        self.assertEqual(from_env({}).max_requests, 600)
        self.assertEqual(
            from_env({"DAKOTA_RATE_LIMIT_RPM": "42"}).max_requests, 42)


class RateLimitIntegrationTests(unittest.TestCase):
    """Servidor real com limite baixo: 429 + Retry-After após o teto."""

    @classmethod
    def setUpClass(cls):
        cls._old_rpm = os.environ.get("DAKOTA_RATE_LIMIT_RPM")
        os.environ["DAKOTA_RATE_LIMIT_RPM"] = "5"

    @classmethod
    def tearDownClass(cls):
        if cls._old_rpm is None:
            os.environ.pop("DAKOTA_RATE_LIMIT_RPM", None)
        else:
            os.environ["DAKOTA_RATE_LIMIT_RPM"] = cls._old_rpm

    def setUp(self):
        spec = importlib.util.spec_from_file_location(
            "control_server", GATEWAY_DIR / "control" / "server.py")
        control = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(control)

        self.tmpdir = tempfile.TemporaryDirectory()
        db_path = str(Path(self.tmpdir.name) / "rl.db")
        self.server = control.ControlServer(
            ("127.0.0.1", 0),
            control.Handler,
            db_path=db_path,
            cookie_secret=b"test_cookie_secret_32_bytes___",
            hmac_key=b"test_hmac_key_32_bytes__________",
        )
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.opener = build_opener(HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmpdir.cleanup()

    def _status(self, path: str) -> tuple[int, str]:
        req = Request(f"http://127.0.0.1:{self.port}{path}")
        try:
            with self.opener.open(req, timeout=10) as resp:
                return resp.status, resp.read().decode("utf-8")
        except HTTPError as exc:
            return exc.code, exc.read().decode("utf-8")

    def test_api_estoura_limite_e_retorna_429_com_retry_after(self):
        # /api/runs exige auth → 401; após 5 requisições o limiter responde 429
        codigos = [self._status("/api/runs")[0] for _ in range(5)]
        self.assertEqual(codigos, [401] * 5)
        req = Request(f"http://127.0.0.1:{self.port}/api/runs")
        try:
            with self.opener.open(req, timeout=10) as resp:
                self.fail(f"esperava 429, veio {resp.status}")
        except HTTPError as exc:
            self.assertEqual(exc.code, 429)
            self.assertIn("Retry-After", exc.headers)
            self.assertGreaterEqual(int(exc.headers["Retry-After"]), 1)
            self.assertIn("limite", exc.read().decode("utf-8"))

    def test_health_nao_e_limitado(self):
        codigos = [self._status("/health")[0] for _ in range(8)]
        self.assertEqual(codigos, [200] * 8)

    def test_login_nao_passa_pelo_limiter_generico(self):
        # Servidor dedicado com limite genérico de 3 rpm: a 4ª requisição a
        # /api/login deve continuar 401 (credencial inválida) — se o login
        # passasse pelo limiter genérico, seria 429. O throttle próprio do
        # login só bloqueia após 5 falhas.
        os.environ["DAKOTA_RATE_LIMIT_RPM"] = "3"
        try:
            self.tearDown()
            self.setUp()
        finally:
            os.environ["DAKOTA_RATE_LIMIT_RPM"] = "5"
        for i in range(4):
            req = Request(
                f"http://127.0.0.1:{self.port}/api/login",
                data=json.dumps({"username": "x", "password": "y"}).encode(),
                headers={"Content-Type": "application/json"}, method="POST")
            try:
                with self.opener.open(req, timeout=10) as resp:
                    codigo = resp.status
            except HTTPError as exc:
                codigo = exc.code
            self.assertEqual(codigo, 401, f"tentativa {i + 1}: esperava 401 (sem limiter genérico no login)")


if __name__ == "__main__":
    unittest.main()

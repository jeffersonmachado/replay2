"""Regressão: /ready deve liberar a conexão do pool mesmo quando a query falha.

Antes da correção, o handler de /ready fazia acquire → execute → release em
sequência, sem finally: se o execute levantasse, a conexão ficava presa no
pool (sem release), esgotando os slots a cada requisição com erro.
"""

from __future__ import annotations

import importlib.util
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, build_opener

ROOT = Path(__file__).resolve().parents[2]
GATEWAY_DIR = ROOT / "gateway"
if str(GATEWAY_DIR) not in sys.path:
    sys.path.insert(0, str(GATEWAY_DIR))

from dakota_gateway.state_db import connect, init_db

CONTROL_SERVER_PATH = GATEWAY_DIR / "control" / "server.py"
SPEC = importlib.util.spec_from_file_location("control_server_ready", CONTROL_SERVER_PATH)
CONTROL = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(CONTROL)


class _BrokenConnection:
    def execute(self, *_args, **_kwargs):
        raise RuntimeError("db indisponivel (simulado)")


class ReadyDbReleaseTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        db_path = str(Path(self.tmpdir.name) / "test.db")
        con = connect(db_path)
        init_db(con)
        con.close()
        self.server = CONTROL.ControlServer(
            ("127.0.0.1", 0),
            CONTROL.Handler,
            db_path=db_path,
            cookie_secret=b"test_cookie_secret_32_bytes___",
            hmac_key=b"test_hmac_key_32_bytes__________",
        )
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        time.sleep(0.2)
        self.opener = build_opener()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmpdir.cleanup()

    def _get_ready(self) -> int:
        req = Request(f"http://127.0.0.1:{self.port}/ready")
        try:
            with self.opener.open(req, timeout=5) as resp:
                resp.read()
                return resp.status
        except HTTPError as exc:
            exc.read()
            return exc.code

    def test_ready_releases_connection_on_success(self):
        self.assertEqual(self._get_ready(), 200)
        self.assertEqual(
            len(self.server.db_pool._in_use), 0,
            "conexão do pool ficou presa após /ready com sucesso",
        )

    def test_ready_releases_connection_on_query_failure(self):
        releases = []
        original_release = CONTROL.Handler._db_release

        def counting_release(handler_self, con):
            releases.append(con)
            # con é quebrada (fake): não devolver ao pool real
            return None

        with patch.object(CONTROL.Handler, "_db", lambda _self: _BrokenConnection()), \
             patch.object(CONTROL.Handler, "_db_release", counting_release):
            status = self._get_ready()
        self.assertEqual(status, 503)
        self.assertEqual(
            len(releases), 1,
            "conexão NÃO foi liberada quando a query do /ready falhou (sem finally)",
        )


if __name__ == "__main__":
    unittest.main()

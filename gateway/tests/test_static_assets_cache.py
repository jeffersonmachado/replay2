"""Regressão: assets estáticos com cache em memória, ETag, Cache-Control e gzip.

Antes da correção, cada GET /assets/* relia o arquivo do disco
(mermaid.min.js tem ~3,3 MB) e respondia sem ETag e com Cache-Control:
no-cache. A correção mantém cache em memória invalidado por mtime/size,
responde ETag (304 em If-None-Match), Cache-Control com max-age e corpo
gzip quando o cliente aceita (Accept-Encoding: gzip).
"""

from __future__ import annotations

import gzip
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
SPEC = importlib.util.spec_from_file_location("control_server_assets", CONTROL_SERVER_PATH)
CONTROL = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(CONTROL)

from control.routes import ui_routes

MERMAID = "/assets/mermaid.min.js"


class StaticAssetsCacheTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        db_path = str(Path(self.tmpdir.name) / "test.db")
        con = connect(db_path)
        init_db(con)
        con.close()
        ui_routes._reset_asset_cache()
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

    def _get(self, path: str, headers: dict | None = None):
        url = f"http://127.0.0.1:{self.port}{path}"
        req = Request(url, headers=headers or {})
        try:
            with self.opener.open(req, timeout=10) as resp:
                return resp.status, resp.read(), dict(resp.headers)
        except HTTPError as exc:
            return exc.code, exc.read(), dict(exc.headers)

    def test_second_request_does_not_reread_from_disk(self):
        original_read_bytes = Path.read_bytes
        reads: list[str] = []

        def counting(self_path):
            if "mermaid.min.js" in str(self_path):
                reads.append(str(self_path))
            return original_read_bytes(self_path)

        with patch.object(Path, "read_bytes", counting):
            status1, body1, _h1 = self._get(MERMAID)
            status2, body2, _h2 = self._get(MERMAID)
        self.assertEqual(status1, 200)
        self.assertEqual(status2, 200)
        self.assertEqual(body1, body2)
        self.assertGreater(len(body1), 1_000_000)
        self.assertEqual(
            len(reads), 1,
            f"mermaid.min.js relido do disco {len(reads)}x (esperado: 1 leitura + cache)",
        )

    def test_etag_cache_control_and_304(self):
        ui_routes._reset_asset_cache()
        status, _body, headers = self._get(MERMAID)
        self.assertEqual(status, 200)
        etag = headers.get("ETag")
        self.assertTrue(etag, "resposta sem ETag")
        cache_control = headers.get("Cache-Control", "")
        self.assertIn("max-age", cache_control)

        status2, body2, headers2 = self._get(MERMAID, {"If-None-Match": etag})
        self.assertEqual(status2, 304, "If-None-Match com ETag válido deveria responder 304")
        self.assertEqual(body2, b"")
        self.assertEqual(headers2.get("ETag"), etag)

    def test_gzip_when_accepted(self):
        ui_routes._reset_asset_cache()
        status, body, headers = self._get(MERMAID, {"Accept-Encoding": "gzip"})
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("Content-Encoding"), "gzip")
        self.assertIn("Accept-Encoding", headers.get("Vary", ""))
        expected = (ui_routes.STATIC_ROOT / "mermaid.min.js").read_bytes()
        self.assertEqual(gzip.decompress(body), expected)
        self.assertLess(len(body), len(expected) // 2, "gzip deveria reduzir bem o mermaid")

        # Sem Accept-Encoding gzip: corpo cru, sem Content-Encoding.
        status2, body2, headers2 = self._get(MERMAID)
        self.assertEqual(status2, 200)
        self.assertNotEqual(headers2.get("Content-Encoding"), "gzip")
        self.assertEqual(body2, expected)


if __name__ == "__main__":
    unittest.main()

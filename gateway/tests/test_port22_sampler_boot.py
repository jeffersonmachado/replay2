"""Regressão: o sampler da porta 22 deve ligar no boot do control plane.

Na ativação via API/UI quem liga o sampler é a rota /api/gateway/activate;
no boot (gateway_auto_activate ou captura retomada) ele ficava desligado até
a próxima ativação manual. O ControlServer.__init__ agora liga o sampler
sempre que existe captura ativa ao subir.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from dakota_gateway import auth
from dakota_gateway.state_db import connect, init_db, now_ms

import control.server as CONTROL


class Port22SamplerBootTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tmpdir.name) / "test.db")
        con = connect(self.db_path)
        init_db(con)
        ph = auth.pbkdf2_hash_password("admin123")
        con.execute(
            "INSERT INTO users(username,password_hash,role,created_at_ms) VALUES(?,?,'admin',?)",
            ("admin", ph, now_ms()),
        )
        con.close()

    def tearDown(self):
        self.tmpdir.cleanup()

    def _boot(self, *, auto_activate: bool) -> CONTROL.ControlServer:
        return CONTROL.ControlServer(
            ("127.0.0.1", 0),
            CONTROL.Handler,
            db_path=self.db_path,
            cookie_secret=b"test_cookie_secret_32_bytes___",
            hmac_key=b"test_hmac_key_32_bytes__________",
            gateway_auto_activate=auto_activate,
        )

    def _sampler_files(self) -> list[Path]:
        captures_dir = Path(self.tmpdir.name) / "captures"
        return sorted(captures_dir.rglob("supervision/audit-*.jsonl"))

    def test_boot_com_auto_activate_liga_sampler(self):
        server = self._boot(auto_activate=True)
        try:
            # captura foi criada no boot e o sampler está rodando para ela
            self.assertIsNotNone(server.port22_sampler._capture)
            files = self._sampler_files()
            self.assertEqual(len(files), 1)
            self.assertIn("session_start", files[0].read_text())
        finally:
            server.port22_sampler.stop()
            server.server_close()

    def test_boot_sem_auto_activate_nao_liga_sampler(self):
        server = self._boot(auto_activate=False)
        try:
            self.assertIsNone(server.port22_sampler._capture)
            self.assertEqual(self._sampler_files(), [])
        finally:
            server.port22_sampler.stop()
            server.server_close()


if __name__ == "__main__":
    unittest.main()

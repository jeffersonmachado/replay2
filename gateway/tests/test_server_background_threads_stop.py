"""Regressão: server_close() deve parar as threads de fundo do ControlServer.

Cada instância de ControlServer liga HostMetricsSampler (ativo por default),
CacheJanitor e, havendo captura ativa, o sampler da porta 22. Sem parada no
server_close, suítes que sobem dezenas de servidores (gateway/tests inteira)
acumulam threads residuais contendendo no GIL e gravando em DBs já apagados
pelo tearDown — causa do flake de timeout (socket 5s em requisição loopback)
visto na suíte gateway/tests da árvore extraída do release 0.8.7.

O teste DEVE FALHAR antes da correção e PASSAR depois dela.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from dakota_gateway import auth
from dakota_gateway.state_db import connect, init_db, now_ms

import control.server as CONTROL


class ServerBackgroundThreadsStopTests(unittest.TestCase):
    def test_server_close_stops_background_threads(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "test.db")
            con = connect(db_path)
            init_db(con)
            con.execute(
                "INSERT INTO users(username,password_hash,role,created_at_ms) VALUES(?,?,'admin',?)",
                ("admin", auth.pbkdf2_hash_password("admin123"), now_ms()),
            )
            con.close()

            server = CONTROL.ControlServer(
                ("127.0.0.1", 0),
                CONTROL.Handler,
                db_path=db_path,
                cookie_secret=b"test_cookie_secret_32_bytes___",
                hmac_key=b"test_hmac_key_32_bytes__________",
                capture_log_dir=str(Path(tmp) / "captures"),
            )
            sampler = server.host_metrics_sampler
            janitor = server.replay_cache_janitor
            self.assertIsNotNone(sampler, "host_metrics_sampler deveria estar ativo por default")
            self.assertTrue(sampler._thread is not None and sampler._thread.is_alive())

            server.server_close()

            self.assertIsNone(sampler._thread, "host_metrics_sampler segue vivo após server_close")
            if janitor is not None:
                self.assertFalse(
                    janitor._thread is not None and janitor._thread.is_alive(),
                    "cache janitor segue vivo após server_close",
                )
            self.assertIsNone(
                server.port22_sampler._thread,
                "sampler da porta 22 segue vivo após server_close",
            )

    def test_server_close_idempotent(self):
        """Fechar duas vezes (ou fechar com componentes já parados) não falha."""
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "test.db")
            con = connect(db_path)
            init_db(con)
            con.close()

            server = CONTROL.ControlServer(
                ("127.0.0.1", 0),
                CONTROL.Handler,
                db_path=db_path,
                cookie_secret=b"test_cookie_secret_32_bytes___",
                hmac_key=b"test_hmac_key_32_bytes__________",
                capture_log_dir=str(Path(tmp) / "captures"),
            )
            server.host_metrics_sampler.stop()
            server.port22_sampler.stop()
            server.server_close()
            server.server_close()


if __name__ == "__main__":
    unittest.main()

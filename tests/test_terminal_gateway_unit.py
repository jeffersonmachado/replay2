#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GATEWAY_DIR = ROOT / "gateway"
sys.path.insert(0, str(GATEWAY_DIR))

from dakota_gateway.gateway import GatewayConfig, TerminalGateway


class TerminalGatewayUnitTests(unittest.TestCase):
    def test_ssh_argv_defaults_to_interactive_password_mode(self):
        cfg = GatewayConfig(
            log_dir="/tmp/audit",
            hmac_key=b"x" * 32,
            source_host="localhost",
            source_user="teste",
        )
        gw = TerminalGateway(cfg)
        self.assertEqual(gw._ssh_argv(), ["ssh", "-tt", "-o", "BatchMode=no", "teste@localhost"])
        gw.writer.close()

    def test_ssh_argv_accepts_batch_mode_yes(self):
        cfg = GatewayConfig(
            log_dir="/tmp/audit",
            hmac_key=b"x" * 32,
            source_host="localhost",
            source_user="teste",
            ssh_batch_mode="yes",
        )
        gw = TerminalGateway(cfg)
        self.assertEqual(gw._ssh_argv(), ["ssh", "-tt", "-o", "BatchMode=yes", "teste@localhost"])
        gw.writer.close()

    def test_session_argv_uses_c_not_lc_for_local_command(self):
        """Garante que _session_argv usa -c em vez de -lc (compativel com AIX)."""
        cfg = GatewayConfig(
            log_dir="/tmp/audit",
            hmac_key=b"x" * 32,
            source_command="echo oi",
        )
        gw = TerminalGateway(cfg)
        argv = gw._session_argv()
        self.assertEqual(argv, ["/bin/sh", "-c", "echo oi"])
        self.assertNotIn("-lc", argv)
        gw.writer.close()

    def test_session_argv_uses_login_shell_when_no_command(self):
        """Garante que shell interativo usa -l sem comando."""
        cfg = GatewayConfig(
            log_dir="/tmp/audit",
            hmac_key=b"x" * 32,
        )
        gw = TerminalGateway(cfg)
        argv = gw._session_argv()
        # Deve terminar com -l (login shell), shell pode variar por SO
        self.assertEqual(argv[-1], "-l")
        self.assertGreater(len(argv), 1)
        gw.writer.close()

    def test_run_batch_pipe_exists(self):
        """Garante que o metodo _run_batch_pipe existe (batch mode sem PTY)."""
        self.assertTrue(hasattr(TerminalGateway, '_run_batch_pipe'))
        self.assertTrue(callable(TerminalGateway._run_batch_pipe))

    def test_run_has_batch_fast_path(self):
        """Garante que run() tem o fast path para batch mode."""
        import inspect
        src = inspect.getsource(TerminalGateway.run)
        self.assertIn('_run_batch_pipe', src)
        self.assertIn('batch_mode == "yes"', src)


if __name__ == "__main__":
    unittest.main(verbosity=2)

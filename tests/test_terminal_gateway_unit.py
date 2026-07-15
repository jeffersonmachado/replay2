#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
GATEWAY_DIR = ROOT / "gateway"
sys.path.insert(0, str(GATEWAY_DIR))

from dakota_gateway.gateway import GatewayConfig, TerminalGateway
from dakota_gateway.cli_commands.runtime import _resolve_terminal_options
from dakota_gateway.terminal_config import TerminalGeometry, normalize_encoding


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

    def test_runtime_terminal_options_cli_has_priority(self):
        ns = SimpleNamespace(rows=30, cols=132, term="xterm-256color", encoding="cp850")
        with patch("dakota_gateway.cli_commands.runtime.geometry_from_tty") as tty_mock:
            tty_mock.return_value = (TerminalGeometry(40, 120), "tty")
            opts = _resolve_terminal_options(ns, session_metadata={"rows": 25, "cols": 80})
        self.assertEqual(opts["rows"], 30)
        self.assertEqual(opts["cols"], 132)
        self.assertEqual(opts["term"], "xterm-256color")
        self.assertEqual(opts["encoding"], "cp850")
        self.assertEqual(opts["geometry_source"], "explicit")

    def test_runtime_terminal_options_partial_rows_uses_resolved_cols(self):
        ns = SimpleNamespace(rows=40, cols=None, term="", encoding="")
        opts = _resolve_terminal_options(ns, session_metadata={"rows": 25, "cols": 100, "term": "xterm", "encoding": "utf-8"})
        self.assertEqual(opts["rows"], 40)
        self.assertEqual(opts["cols"], 100)
        self.assertEqual(opts["geometry_source"], "explicit")

    def test_runtime_terminal_options_partial_cols_uses_resolved_rows(self):
        ns = SimpleNamespace(rows=None, cols=132, term="", encoding="")
        with patch("dakota_gateway.cli_commands.runtime.geometry_from_tty") as tty_mock:
            tty_mock.return_value = (TerminalGeometry(33, 101), "tty")
            opts = _resolve_terminal_options(ns)
        self.assertEqual(opts["rows"], 33)
        self.assertEqual(opts["cols"], 132)
        self.assertEqual(opts["geometry_source"], "explicit")

    def test_runtime_terminal_options_tty_before_environment(self):
        ns = SimpleNamespace(rows=None, cols=None, term="", encoding="")
        with patch("dakota_gateway.cli_commands.runtime.geometry_from_tty") as tty_mock, \
             patch("dakota_gateway.cli_commands.runtime.geometry_from_environment") as env_mock:
            tty_mock.return_value = (TerminalGeometry(33, 101), "tty")
            env_mock.return_value = (TerminalGeometry(44, 122), "environment")
            opts = _resolve_terminal_options(ns)
        self.assertEqual(opts["rows"], 33)
        self.assertEqual(opts["cols"], 101)
        self.assertEqual(opts["geometry_source"], "tty")

    def test_unknown_encoding_uses_single_backend_fallback(self):
        self.assertEqual(normalize_encoding("x-unknown-codepage"), "utf-8")
        self.assertEqual(normalize_encoding("cp1252"), "windows-1252")
        self.assertEqual(normalize_encoding("latin1"), "latin1")

    def test_runtime_terminal_options_unknown_encoding_falls_back_to_utf8(self):
        ns = SimpleNamespace(rows=25, cols=80, term="xterm", encoding="x-unknown-codepage")
        opts = _resolve_terminal_options(ns)
        self.assertEqual(opts["encoding"], "utf-8")


if __name__ == "__main__":
    unittest.main(verbosity=2)

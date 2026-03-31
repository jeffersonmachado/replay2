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

    def test_session_argv_uses_local_command_when_source_host_missing(self):
        cfg = GatewayConfig(
            log_dir="/tmp/audit",
            hmac_key=b"x" * 32,
            source_command="echo oi",
        )
        gw = TerminalGateway(cfg)
        self.assertEqual(gw._session_argv(), ["/bin/sh", "-lc", "echo oi"])
        gw.writer.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)

#!/usr/bin/env python3
from __future__ import annotations

import ast
import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GATEWAY_DIR = ROOT / "gateway"
sys.path.insert(0, str(GATEWAY_DIR))

from dakota_gateway.cli import main
from dakota_gateway.state_db import connect, init_db


class CliCatalogUnitTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tmpdir.name) / "cli.db")
        con = connect(self.db_path)
        init_db(con)
        con.close()

    def tearDown(self):
        self.tmpdir.cleanup()

    def _run_cli(self, argv: list[str]) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = main(argv)
        return code, stdout.getvalue().strip(), stderr.getvalue().strip()

    def test_targets_add_and_list_keep_gateway_policy_fields(self):
        code, stdout, stderr = self._run_cli(
            [
                "targets",
                "--db",
                self.db_path,
                "add",
                "--name",
                "Recital 24 HML",
                "--host",
                "recital24.example",
                "--gateway-required",
                "--direct-ssh-policy",
                "gateway_only",
                "--capture-start-mode",
                "login_required",
                "--capture-compliance-mode",
                "strict",
                "--gateway-host",
                "gw.example",
                "--gateway-user",
                "bastion",
                "--gateway-port",
                "2200",
            ]
        )
        self.assertEqual(code, 0)
        self.assertTrue(stdout.isdigit(), stdout)
        self.assertEqual(stderr, "")

        code, stdout, stderr = self._run_cli(["targets", "--db", self.db_path, "list"])
        self.assertEqual(code, 0)
        self.assertEqual(stderr, "")
        row = ast.literal_eval(stdout)
        self.assertEqual(row["host"], "recital24.example")
        self.assertEqual(row["direct_ssh_policy"], "gateway_only")
        self.assertEqual(row["capture_start_mode"], "login_required")
        self.assertEqual(row["capture_compliance_mode"], "strict")
        self.assertEqual(row["gateway_required"], 1)

    def test_profiles_add_and_list_preserve_transport_defaults(self):
        code, stdout, stderr = self._run_cli(
            [
                "profiles",
                "--db",
                self.db_path,
                "add",
                "--name",
                "SSH Batch",
                "--transport",
                "ssh",
                "--username",
                "replay",
                "--port",
                "2200",
                "--credential-ref",
                "env:RECITAL24_SSH_KEY",
            ]
        )
        self.assertEqual(code, 0)
        self.assertTrue(stdout.isdigit(), stdout)
        self.assertEqual(stderr, "")

        code, stdout, stderr = self._run_cli(["profiles", "--db", self.db_path, "list"])
        self.assertEqual(code, 0)
        self.assertEqual(stderr, "")
        row = ast.literal_eval(stdout)
        self.assertEqual(row["name"], "SSH Batch")
        self.assertEqual(row["transport"], "ssh")
        self.assertEqual(row["username"], "replay")
        self.assertEqual(row["port"], 2200)


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GATEWAY_DIR = ROOT / "gateway"
sys.path.insert(0, str(GATEWAY_DIR))

from dakota_gateway.compliance import evaluate_run_compliance, summarize_capture_sessions
from dakota_gateway.replay import ReplayConfig, _TargetSession


class GatewayComplianceUnitTests(unittest.TestCase):
    def _write_log(self, tmpdir: str, entries: list[dict]) -> str:
        log_dir = Path(tmpdir) / "audit"
        log_dir.mkdir(parents=True, exist_ok=True)
        (log_dir / "audit-20260330.part001.jsonl").write_text(
            "\n".join(json.dumps(item, ensure_ascii=False) for item in entries),
            encoding="utf-8",
        )
        return str(log_dir)

    def test_gateway_required_login_required_marks_direct_session_non_compliant(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            log_dir = self._write_log(
                tmpdir,
                [
                    {
                        "ts_ms": 1000,
                        "type": "session_start",
                        "session_id": "sess-1",
                        "actor": "alice",
                        "seq_global": 1,
                        "seq_session": 1,
                        "entry_mode": "direct_ssh",
                        "via_gateway": False,
                        "source_command": "legacy-shell",
                    },
                    {
                        "ts_ms": 1100,
                        "type": "session_end",
                        "session_id": "sess-1",
                        "actor": "alice",
                        "seq_global": 2,
                        "seq_session": 2,
                    },
                ],
            )
            summary = summarize_capture_sessions(
                log_dir,
                target_policy={
                    "gateway_required": True,
                    "direct_ssh_policy": "gateway_only",
                    "capture_start_mode": "login_required",
                    "capture_compliance_mode": "strict",
                },
            )

        self.assertEqual(summary["summary"]["compliance_status"], "rejected")
        self.assertEqual(summary["sessions"][0]["compliance_status"], "non_compliant")
        self.assertIn("gateway", summary["sessions"][0]["compliance_reason"])

    def test_run_compliance_rejects_gateway_only_target_on_direct_replay_transport(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            log_dir = self._write_log(
                tmpdir,
                [
                    {
                        "ts_ms": 1000,
                        "type": "session_start",
                        "session_id": "sess-1",
                        "actor": "alice",
                        "seq_global": 1,
                        "seq_session": 1,
                        "entry_mode": "gateway_ssh",
                        "via_gateway": True,
                        "gateway_session_id": "sess-1",
                        "gateway_endpoint": "gw.example",
                    },
                    {
                        "ts_ms": 1100,
                        "type": "bytes",
                        "session_id": "sess-1",
                        "actor": "alice",
                        "seq_global": 2,
                        "seq_session": 2,
                        "dir": "out",
                        "n": 6,
                        "data_b64": "bG9naW46",
                    },
                    {
                        "ts_ms": 1200,
                        "type": "session_end",
                        "session_id": "sess-1",
                        "actor": "alice",
                        "seq_global": 3,
                        "seq_session": 3,
                    },
                ],
            )
            compliance = evaluate_run_compliance(
                log_dir,
                target_policy={
                    "gateway_required": True,
                    "direct_ssh_policy": "gateway_only",
                    "capture_start_mode": "login_required",
                    "capture_compliance_mode": "strict",
                },
                resolved_target={"target_host": "legacy.example"},
                resolved_params={"transport": "ssh"},
            )

        self.assertEqual(compliance["entry_mode"], "gateway_ssh")
        self.assertEqual(compliance["compliance_status"], "rejected")
        self.assertIn("replay usa transporte direto ssh", compliance["compliance_reason"])

    def test_run_compliance_accepts_proxyjump_gateway_route(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            log_dir = self._write_log(
                tmpdir,
                [
                    {
                        "ts_ms": 1000,
                        "type": "session_start",
                        "session_id": "sess-1",
                        "actor": "alice",
                        "seq_global": 1,
                        "seq_session": 1,
                        "entry_mode": "gateway_ssh",
                        "via_gateway": True,
                        "gateway_session_id": "sess-1",
                        "gateway_endpoint": "gw.example",
                    },
                    {
                        "ts_ms": 1100,
                        "type": "bytes",
                        "session_id": "sess-1",
                        "actor": "alice",
                        "seq_global": 2,
                        "seq_session": 2,
                        "dir": "out",
                        "n": 6,
                        "data_b64": "bG9naW46",
                    },
                    {
                        "ts_ms": 1200,
                        "type": "session_end",
                        "session_id": "sess-1",
                        "actor": "alice",
                        "seq_global": 3,
                        "seq_session": 3,
                    },
                ],
            )
            compliance = evaluate_run_compliance(
                log_dir,
                target_policy={
                    "gateway_required": True,
                    "direct_ssh_policy": "gateway_only",
                    "capture_start_mode": "login_required",
                    "capture_compliance_mode": "strict",
                },
                resolved_target={"target_host": "legacy.example"},
                resolved_params={"transport": "ssh", "gateway_host": "gw.example", "gateway_route_mode": "proxyjump"},
            )

        self.assertEqual(compliance["compliance_status"], "compliant")
        self.assertTrue(compliance["gateway_route"])

    def test_run_compliance_warns_when_policy_is_warn(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            log_dir = self._write_log(
                tmpdir,
                [
                    {
                        "ts_ms": 1000,
                        "type": "session_start",
                        "session_id": "sess-1",
                        "actor": "alice",
                        "seq_global": 1,
                        "seq_session": 1,
                        "entry_mode": "direct_ssh",
                        "via_gateway": False,
                    },
                    {
                        "ts_ms": 1100,
                        "type": "session_end",
                        "session_id": "sess-1",
                        "actor": "alice",
                        "seq_global": 2,
                        "seq_session": 2,
                    },
                ],
            )
            compliance = evaluate_run_compliance(
                log_dir,
                target_policy={
                    "gateway_required": True,
                    "direct_ssh_policy": "unrestricted",
                    "capture_start_mode": "session_start_required",
                    "capture_compliance_mode": "warn",
                },
                resolved_target={"target_host": "legacy.example"},
                resolved_params={"transport": "ssh"},
            )

        self.assertEqual(compliance["compliance_status"], "warning")
        self.assertIn("alerta de origem", compliance["compliance_reason"])

    def test_replay_target_session_uses_proxyjump_when_gateway_route_is_configured(self):
        fake = _TargetSession.__new__(_TargetSession)
        fake.cfg = ReplayConfig(
            log_dir="/tmp/audit",
            target_host="dest.example",
            target_user="replay",
            transport="ssh",
            target_port=2222,
            gateway_host="gw.example",
            gateway_user="bastion",
            gateway_port=2200,
        )
        fake.target_user_override = None

        argv = fake._ssh_argv()

        self.assertEqual(
            argv,
            ["ssh", "-tt", "-o", "BatchMode=yes", "-J", "bastion@gw.example:2200", "-p", "2222", "replay@dest.example"],
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)

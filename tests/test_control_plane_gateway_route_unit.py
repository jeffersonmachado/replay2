#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GATEWAY_DIR = ROOT / "gateway"
sys.path.insert(0, str(GATEWAY_DIR))

from dakota_gateway.compliance import derive_gateway_route_from_capture
from dakota_gateway.state_db import connect, init_db, now_ms

CONTROL_SERVER_PATH = GATEWAY_DIR / "control" / "server.py"
SPEC = importlib.util.spec_from_file_location("control_server", CONTROL_SERVER_PATH)
CONTROL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CONTROL)


class ControlPlaneGatewayRouteUnitTests(unittest.TestCase):
    def _write_log(self, root: Path, entries: list[dict]) -> str:
        log_dir = root / "audit"
        log_dir.mkdir(parents=True, exist_ok=True)
        (log_dir / "audit-20260330.part001.jsonl").write_text(
            "\n".join(json.dumps(item, ensure_ascii=False) for item in entries),
            encoding="utf-8",
        )
        return str(log_dir)

    def test_derive_gateway_route_from_capture_uses_gateway_endpoint(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            log_dir = self._write_log(
                Path(tmpdir),
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
                        "gateway_endpoint": "gw.capture.example",
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

            route = derive_gateway_route_from_capture(
                log_dir,
                target_policy={"gateway_required": True, "capture_compliance_mode": "strict"},
            )

        self.assertEqual(route["gateway_host"], "gw.capture.example")
        self.assertEqual(route["gateway_route_mode"], "proxyjump")

    def test_resolve_run_target_request_carries_gateway_route_from_target_metadata(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            con = connect(str(db_path))
            init_db(con)
            now = now_ms()
            target_id = con.execute(
                """
                INSERT INTO target_environments(
                    env_id, name, host, port, platform, transport_hint,
                    gateway_required, direct_ssh_policy, capture_start_mode,
                    capture_compliance_mode, allow_admin_direct_access,
                    description, metadata_json, created_at_ms, updated_at_ms
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    "prd",
                    "PRD",
                    "legacy.example",
                    22,
                    "linux",
                    "ssh",
                    1,
                    "gateway_only",
                    "login_required",
                    "strict",
                    0,
                    None,
                    json.dumps({"gateway_host": "gw.target.example", "gateway_user": "bastion", "gateway_port": 2200}),
                    now,
                    now,
                ),
            ).lastrowid
            profile_id = con.execute(
                """
                INSERT INTO connection_profiles(
                    profile_id, name, transport, username, port, command, credential_ref, auth_mode, options_json, created_at_ms, updated_at_ms
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    "ssh-prof",
                    "SSH",
                    "ssh",
                    "replay",
                    2222,
                    "legacy-shell",
                    None,
                    "external",
                    "{}",
                    now,
                    now,
                ),
            ).lastrowid

            resolved_target, params = CONTROL._resolve_run_target_request(
                con,
                {"target_env_id": target_id, "connection_profile_id": profile_id},
            )
            con.close()

        self.assertEqual(resolved_target["target_host"], "legacy.example")
        self.assertEqual(params["gateway_host"], "gw.target.example")
        self.assertEqual(params["gateway_user"], "bastion")
        self.assertEqual(params["gateway_port"], 2200)
        self.assertEqual(params["gateway_route_mode"], "proxyjump")
        self.assertEqual(params["target_port"], 2222)


if __name__ == "__main__":
    unittest.main(verbosity=2)

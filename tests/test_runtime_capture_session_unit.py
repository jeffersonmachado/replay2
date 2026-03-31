#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import mock_open, patch

ROOT = Path(__file__).resolve().parents[1]
GATEWAY_DIR = ROOT / "gateway"
sys.path.insert(0, str(GATEWAY_DIR))

from dakota_gateway.cli_commands.runtime import _resolve_capture_log_dir, _resolve_capture_session
from dakota_gateway import auth
from dakota_gateway.state_db import connect, init_db, now_ms
from control.services.capture_service import ensure_active_capture_for_gateway

CONTROL_SERVER_PATH = GATEWAY_DIR / "control" / "server.py"
SPEC = importlib.util.spec_from_file_location("control_server", CONTROL_SERVER_PATH)
CONTROL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CONTROL)


class RuntimeCaptureSessionUnitTests(unittest.TestCase):
    def test_resolve_active_capture_log_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "test.db")
            con = connect(db_path)
            init_db(con)
            con.execute(
                "INSERT INTO users(username,password_hash,role,created_at_ms) VALUES(?,?,?,?)",
                ("admin", auth.pbkdf2_hash_password("admin123"), "admin", now_ms()),
            )
            con.execute(
                "INSERT INTO capture_sessions(session_uuid,status,created_by,created_by_username,started_at_ms,environment_json,connection_profile_id,connection_profile_name,operational_user_id,gateway_state_snapshot_json,log_dir,target_env_id,notes) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    "sess-1",
                    "active",
                    1,
                    "admin",
                    now_ms(),
                    "{}",
                    None,
                    None,
                    None,
                    "{}",
                    str(Path(tmp) / "captures" / "sess-1"),
                    None,
                    "",
                ),
            )
            con.close()

            log_dir = _resolve_capture_log_dir(db_path)
            self.assertTrue(log_dir.endswith("captures/sess-1"))
            self.assertTrue(Path(log_dir).exists())

    def test_resolve_capture_session_returns_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "test.db")
            con = connect(db_path)
            init_db(con)
            con.execute(
                "INSERT INTO users(username,password_hash,role,created_at_ms) VALUES(?,?,?,?)",
                ("admin", auth.pbkdf2_hash_password("admin123"), "admin", now_ms()),
            )
            con.execute(
                "INSERT INTO capture_sessions(session_uuid,status,created_by,created_by_username,started_at_ms,environment_json,connection_profile_id,connection_profile_name,operational_user_id,gateway_state_snapshot_json,log_dir,target_env_id,notes) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    "sess-42",
                    "active",
                    1,
                    "admin",
                    now_ms(),
                    "{}",
                    None,
                    None,
                    None,
                    "{}",
                    str(Path(tmp) / "captures" / "sess-42"),
                    None,
                    "",
                ),
            )
            capture_id = int(con.execute("SELECT id FROM capture_sessions WHERE session_uuid='sess-42'").fetchone()["id"])
            con.close()

            capture = _resolve_capture_session(db_path, capture_id)
            self.assertEqual(capture["id"], capture_id)
            self.assertEqual(capture["session_uuid"], "sess-42")
            self.assertTrue(capture["log_dir"].endswith("captures/sess-42"))

    def test_ensure_active_capture_for_gateway_creates_capture_when_gateway_is_active(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "test.db")
            log_dir_base = str(Path(tmp) / "captures")
            con = connect(db_path)
            init_db(con)
            user_id = con.execute(
                "INSERT INTO users(username,password_hash,role,created_at_ms) VALUES(?,?,?,?)",
                ("admin", auth.pbkdf2_hash_password("admin123"), "admin", now_ms()),
            ).lastrowid
            ts = now_ms()
            con.execute(
                """
                UPDATE gateway_state
                SET active=1,
                    activated_at_ms=?,
                    activated_by_id=?,
                    activated_by_username=?,
                    environment_json='{}',
                    connection_profile_id=NULL,
                    operational_user_id=NULL,
                    capture_enabled=1,
                    updated_at_ms=?
                WHERE id=1
                """,
                (ts, int(user_id), "admin", ts),
            )

            capture = ensure_active_capture_for_gateway(
                con,
                log_dir_base=log_dir_base,
                now_ms_fn=now_ms,
            )
            active_count = int(
                con.execute("SELECT COUNT(*) AS n FROM capture_sessions WHERE status='active'").fetchone()["n"]
            )
            con.close()

        self.assertIsNotNone(capture)
        self.assertEqual(active_count, 1)
        self.assertEqual(capture["status"], "active")
        self.assertIn("captura retomada automaticamente", capture["notes"])

    def test_control_server_startup_reconciles_active_gateway_with_new_capture(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "test.db")
            capture_log_dir = str(Path(tmp) / "captures")
            con = connect(db_path)
            init_db(con)
            user_id = con.execute(
                "INSERT INTO users(username,password_hash,role,created_at_ms) VALUES(?,?,?,?)",
                ("admin", auth.pbkdf2_hash_password("admin123"), "admin", now_ms()),
            ).lastrowid
            ts = now_ms()
            con.execute(
                """
                UPDATE gateway_state
                SET active=1,
                    activated_at_ms=?,
                    activated_by_id=?,
                    activated_by_username=?,
                    environment_json='{}',
                    connection_profile_id=NULL,
                    operational_user_id=NULL,
                    capture_enabled=1,
                    updated_at_ms=?
                WHERE id=1
                """,
                (ts, int(user_id), "admin", ts),
            )
            con.execute(
                """
                INSERT INTO capture_sessions(
                    session_uuid,status,created_by,created_by_username,started_at_ms,
                    environment_json,connection_profile_id,connection_profile_name,
                    operational_user_id,gateway_state_snapshot_json,log_dir,target_env_id,notes
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    "stale-sess",
                    "active",
                    int(user_id),
                    "admin",
                    now_ms(),
                    "{}",
                    None,
                    None,
                    None,
                    "{}",
                    str(Path(capture_log_dir) / "stale-sess"),
                    None,
                    "stale capture",
                ),
            )
            con.close()

            try:
                server = CONTROL.ControlServer(
                    ("127.0.0.1", 0),
                    CONTROL.Handler,
                    db_path=db_path,
                    cookie_secret=b"test_cookie_secret_32_bytes___",
                    hmac_key=b"test_hmac_key_32_bytes__________",
                    capture_log_dir=capture_log_dir,
                )
            except PermissionError as exc:
                raise unittest.SkipTest(f"sandbox sem permissao para abrir socket local: {exc}") from exc
            server.port22_sampler.stop()
            server.runtime_capture.stop()
            server.server_close()

            con = connect(db_path)
            rows = con.execute(
                "SELECT id, session_uuid, status, notes FROM capture_sessions ORDER BY id"
            ).fetchall()
            con.close()

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["status"], "interrupted")
        self.assertEqual(rows[1]["status"], "active")
        self.assertIn("captura retomada automaticamente", rows[1]["notes"])

    def test_port22_sampler_ignores_malformed_ss_output(self):
        sampler = CONTROL._Port22CaptureSampler()
        with patch.object(
            CONTROL,
            "_run_cmd",
            return_value=(0, "socket: Operation not permitted\nRecv-Q Send-Q Local Address:Port Peer Address:PortProcess\n"),
        ):
            current = sampler._sample_established_ssh()
        self.assertEqual(current, set())

    def test_port22_sampler_falls_back_to_proc_net_tcp(self):
        sampler = CONTROL._Port22CaptureSampler()
        proc_tcp = (
            "  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode\n"
            "   0: 0100007F:0016 0100007F:CFE6 01 00000000:00000000 00:00000000 00000000  1000        0 0 1 0000000000000000 20 4 0 10 -1\n"
        )
        with patch.object(CONTROL, "_run_cmd", return_value=(1, "Cannot open netlink socket: Operation not permitted")):
            with patch.object(CONTROL.os.path, "exists", return_value=True):
                with patch("builtins.open", mock_open(read_data=proc_tcp)):
                    current = sampler._sample_established_ssh()
        self.assertEqual(current, {("127.0.0.1:22", "127.0.0.1:53222")})


if __name__ == "__main__":
    unittest.main(verbosity=2)

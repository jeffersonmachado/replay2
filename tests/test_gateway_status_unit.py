#!/usr/bin/env python3
"""Unit tests for gateway service detection/toggle helpers."""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

GATEWAY_DIR = Path(__file__).resolve().parents[1] / "gateway"
sys.path.insert(0, str(GATEWAY_DIR))

import dakota_gateway.auth as auth
from dakota_gateway import replay_control as replay_control_mod
from dakota_gateway.replay_control import add_run_failure, build_failure_record, create_run
from dakota_gateway.state_db import connect, init_db, now_ms
from control.services.gateway_observability_service import prepare_session_replay_data

CONTROL_SERVER_PATH = GATEWAY_DIR / "control" / "server.py"
SPEC = importlib.util.spec_from_file_location("control_server", CONTROL_SERVER_PATH)
CONTROL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CONTROL)


class GatewayStatusUnitTests(unittest.TestCase):
    def test_replay_window_filters_events_by_seq_session_and_checkpoint(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            log_file = Path(tmpdir) / "audit-20260329.part000.jsonl"
            entries = [
                {"ts_ms": 1000, "type": "session_start", "session_id": "s1", "seq_global": 1, "seq_session": 1},
                {"ts_ms": 1100, "type": "bytes", "session_id": "s1", "seq_global": 2, "seq_session": 2, "dir": "in", "data_b64": "QQ=="},
                {"ts_ms": 1200, "type": "checkpoint", "session_id": "s1", "seq_global": 3, "seq_session": 3, "sig": "SIG:MENU"},
                {"ts_ms": 1300, "type": "bytes", "session_id": "s1", "seq_global": 4, "seq_session": 4, "dir": "in", "data_b64": "Qg=="},
                {"ts_ms": 1400, "type": "checkpoint", "session_id": "s2", "seq_global": 5, "seq_session": 1, "sig": "SIG:OUTRO"},
                {"ts_ms": 1500, "type": "bytes", "session_id": "s2", "seq_global": 6, "seq_session": 2, "dir": "in", "data_b64": "Qw=="},
            ]
            log_file.write_text("\n".join(json.dumps(item) for item in entries), encoding="utf-8")

            selected = list(
                replay_control_mod._selected_events(
                    tmpdir,
                    {
                        "replay_from_seq_global": 3,
                        "replay_to_seq_global": 4,
                        "replay_session_id": "s1",
                    },
                )
            )
            from_checkpoint = list(
                replay_control_mod._selected_events(
                    tmpdir,
                    {
                        "replay_session_id": "s1",
                        "replay_from_checkpoint_sig": "SIG:MENU",
                    },
                )
            )
            selected_end = replay_control_mod.compute_seq_end(
                tmpdir,
                {
                    "replay_session_id": "s1",
                    "replay_from_checkpoint_sig": "SIG:MENU",
                },
            )

        self.assertEqual([item["seq_global"] for item in selected], [3, 4])
        self.assertEqual([item["seq_global"] for item in from_checkpoint], [3, 4])
        self.assertEqual(selected_end, 4)

    def test_retry_run_preserves_partial_reprocessing_params(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "replay.db"
            con = connect(str(db_path))
            init_db(con)
            ph = auth.pbkdf2_hash_password("admin123")
            con.execute(
                "INSERT INTO users(username,password_hash,role,created_at_ms) VALUES(?,?,'admin',?)",
                ("admin", ph, now_ms()),
            )
            user = con.execute("SELECT id FROM users WHERE username='admin'").fetchone()
            run_id = create_run(con, int(user["id"]), tmpdir, "legacy.example", "recital", "", "strict-global")
            con.execute(
                "UPDATE replay_runs SET params_json=? WHERE id=?",
                (
                    json.dumps(
                        {
                            "environment": "recital24-hml",
                            "replay_from_seq_global": 10,
                            "replay_to_seq_global": 40,
                            "replay_session_id": "sess-10",
                        }
                    ),
                    run_id,
                ),
            )
            con.execute("UPDATE replay_runs SET status='failed' WHERE id=?", (run_id,))
            retry_id = CONTROL.retry_run(con, run_id, int(user["id"]))
            retry_row = con.execute("SELECT parent_run_id, params_json FROM replay_runs WHERE id=?", (retry_id,)).fetchone()
            con.close()

        retry_params = json.loads(retry_row["params_json"])
        self.assertEqual(int(retry_row["parent_run_id"]), run_id)
        self.assertEqual(retry_params["replay_from_seq_global"], 10)
        self.assertEqual(retry_params["replay_to_seq_global"], 40)
        self.assertEqual(retry_params["replay_session_id"], "sess-10")

    def test_create_reprocess_run_from_failure_derives_partial_scope(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "replay.db"
            con = connect(str(db_path))
            init_db(con)
            ph = auth.pbkdf2_hash_password("admin123")
            con.execute(
                "INSERT INTO users(username,password_hash,role,created_at_ms) VALUES(?,?,'admin',?)",
                ("admin", ph, now_ms()),
            )
            user = con.execute("SELECT id FROM users WHERE username='admin'").fetchone()
            run_id = create_run(con, int(user["id"]), tmpdir, "legacy.example", "recital", "", "strict-global")
            con.execute(
                "UPDATE replay_runs SET params_json=?, status='failed' WHERE id=?",
                (json.dumps({"environment": "recital24-hml"}), run_id),
            )
            failure_id = add_run_failure(
                con,
                run_id,
                build_failure_record(
                    session_id="sess-77",
                    seq_global=88,
                    seq_session=9,
                    flow_name="pagamento",
                    event_type="checkpoint",
                    failure_type="checkpoint_mismatch",
                    severity="high",
                    expected_value="SIG:PAGAMENTO",
                    observed_value="SIG:ERRO",
                    message="checkpoint falhou",
                    evidence={},
                ),
            )
            run_a = CONTROL._create_reprocess_run_from_failure(con, run_id, failure_id, "from-failure", int(user["id"]))
            con.execute("UPDATE replay_runs SET status='failed' WHERE id=?", (run_a,))
            run_b = CONTROL._create_reprocess_run_from_failure(con, run_id, failure_id, "session-from-failure", int(user["id"]))
            con.execute("UPDATE replay_runs SET status='failed' WHERE id=?", (run_b,))
            run_c = CONTROL._create_reprocess_run_from_failure(con, run_id, failure_id, "session-from-checkpoint", int(user["id"]))
            row_a = con.execute("SELECT params_json, parent_run_id FROM replay_runs WHERE id=?", (run_a,)).fetchone()
            row_b = con.execute("SELECT params_json FROM replay_runs WHERE id=?", (run_b,)).fetchone()
            row_c = con.execute("SELECT params_json FROM replay_runs WHERE id=?", (run_c,)).fetchone()
            con.close()

        params_a = json.loads(row_a["params_json"])
        params_b = json.loads(row_b["params_json"])
        params_c = json.loads(row_c["params_json"])
        self.assertEqual(int(row_a["parent_run_id"]), run_id)
        self.assertEqual(params_a["replay_from_seq_global"], 88)
        self.assertNotIn("replay_session_id", params_a)
        self.assertEqual(params_b["replay_from_seq_global"], 88)
        self.assertEqual(params_b["replay_session_id"], "sess-77")
        self.assertEqual(params_c["replay_session_id"], "sess-77")
        self.assertEqual(params_c["replay_from_checkpoint_sig"], "SIG:PAGAMENTO")
        self.assertNotIn("replay_from_seq_global", params_c)

    def test_run_family_and_reprocess_trace_show_origin_and_outcome(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "replay.db"
            con = connect(str(db_path))
            init_db(con)
            ph = auth.pbkdf2_hash_password("admin123")
            con.execute(
                "INSERT INTO users(username,password_hash,role,created_at_ms) VALUES(?,?,'admin',?)",
                ("admin", ph, now_ms()),
            )
            user = con.execute("SELECT id FROM users WHERE username='admin'").fetchone()
            base_run = create_run(con, int(user["id"]), tmpdir, "legacy.example", "recital", "", "strict-global")
            con.execute("UPDATE replay_runs SET status='failed' WHERE id=?", (base_run,))
            failure_id = add_run_failure(
                con,
                base_run,
                build_failure_record(
                    session_id="sess-99",
                    seq_global=55,
                    seq_session=5,
                    flow_name="menu",
                    event_type="checkpoint",
                    failure_type="checkpoint_mismatch",
                    severity="high",
                    expected_value="SIG:MENU",
                    observed_value="SIG:ERRO",
                    message="falha base",
                    evidence={},
                ),
            )
            child_run = CONTROL._create_reprocess_run_from_failure(con, base_run, failure_id, "session-from-checkpoint", int(user["id"]))
            con.execute("UPDATE replay_runs SET status='success' WHERE id=?", (child_run,))
            family = CONTROL._build_run_family(con, child_run)
            trace = CONTROL._build_reprocess_trace(con, child_run)
            con.close()

        self.assertEqual(family["root_run_id"], base_run)
        self.assertEqual([item["id"] for item in family["members"]], [base_run, child_run])
        self.assertEqual(trace["source_run_id"], base_run)
        self.assertEqual(trace["failure_id"], failure_id)
        self.assertEqual(trace["scope"], "session-from-checkpoint")
        self.assertEqual(trace["outcome"], "resolved")
        self.assertEqual(trace["source_failure"]["session_id"], "sess-99")

    def test_reprocess_analytics_groups_by_flow_and_repeated_signature(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "replay.db"
            con = connect(str(db_path))
            init_db(con)
            ph = auth.pbkdf2_hash_password("admin123")
            con.execute(
                "INSERT INTO users(username,password_hash,role,created_at_ms) VALUES(?,?,'admin',?)",
                ("admin", ph, now_ms()),
            )
            user = con.execute("SELECT id FROM users WHERE username='admin'").fetchone()

            base_a = create_run(con, int(user["id"]), tmpdir, "legacy.example", "recital", "", "strict-global")
            con.execute("UPDATE replay_runs SET status='failed' WHERE id=?", (base_a,))
            con.execute("UPDATE replay_runs SET params_json=? WHERE id=?", (json.dumps({"environment": "recital24-hml"}), base_a))
            failure_a = add_run_failure(
                con,
                base_a,
                build_failure_record(
                    session_id="sess-a",
                    seq_global=10,
                    seq_session=1,
                    flow_name="pagamento",
                    event_type="checkpoint",
                    failure_type="checkpoint_mismatch",
                    severity="high",
                    expected_value="SIG:PAG",
                    observed_value="SIG:ERR",
                    message="falha pagamento",
                    evidence={},
                ),
            )
            child_a = CONTROL._create_reprocess_run_from_failure(con, base_a, failure_a, "from-failure", int(user["id"]))
            con.execute("UPDATE replay_runs SET status='success' WHERE id=?", (child_a,))
            con.execute("UPDATE replay_runs SET params_json=? WHERE id=?", (json.dumps({"environment": "recital24-hml"}), child_a))

            base_b = create_run(con, int(user["id"]), tmpdir + "-b", "legacy.example", "recital", "", "strict-global")
            con.execute("UPDATE replay_runs SET status='failed' WHERE id=?", (base_b,))
            con.execute("UPDATE replay_runs SET params_json=? WHERE id=?", (json.dumps({"environment": "recital24-prd"}), base_b))
            failure_b = add_run_failure(
                con,
                base_b,
                build_failure_record(
                    session_id="sess-b",
                    seq_global=20,
                    seq_session=2,
                    flow_name="pagamento",
                    event_type="checkpoint",
                    failure_type="checkpoint_mismatch",
                    severity="high",
                    expected_value="SIG:PAG",
                    observed_value="SIG:ERR",
                    message="falha pagamento repetida",
                    evidence={},
                ),
            )
            child_b = CONTROL._create_reprocess_run_from_failure(con, base_b, failure_b, "from-failure", int(user["id"]))
            con.execute("UPDATE replay_runs SET params_json=? WHERE id=?", (json.dumps({"environment": "recital24-prd"}), child_b))
            add_run_failure(
                con,
                child_b,
                build_failure_record(
                    session_id="sess-b",
                    seq_global=20,
                    seq_session=2,
                    flow_name="pagamento",
                    event_type="checkpoint",
                    failure_type="checkpoint_mismatch",
                    severity="high",
                    expected_value="SIG:PAG",
                    observed_value="SIG:ERR",
                    message="falha repetiu",
                    evidence={},
                ),
            )
            con.execute("UPDATE replay_runs SET status='failed' WHERE id=?", (child_b,))

            base_c = create_run(con, int(user["id"]), tmpdir + "-c", "legacy.example", "recital", "", "strict-global")
            con.execute("UPDATE replay_runs SET status='failed' WHERE id=?", (base_c,))
            con.execute("UPDATE replay_runs SET params_json=? WHERE id=?", (json.dumps({"environment": "recital24-prd"}), base_c))
            failure_c = add_run_failure(
                con,
                base_c,
                build_failure_record(
                    session_id="sess-c",
                    seq_global=30,
                    seq_session=3,
                    flow_name="cadastro",
                    event_type="checkpoint",
                    failure_type="checkpoint_mismatch",
                    severity="high",
                    expected_value="SIG:CAD",
                    observed_value="SIG:ERR",
                    message="falha cadastro",
                    evidence={},
                ),
            )
            child_c = CONTROL._create_reprocess_run_from_failure(con, base_c, failure_c, "from-failure", int(user["id"]))
            con.execute("UPDATE replay_runs SET params_json=? WHERE id=?", (json.dumps({"environment": "recital24-prd"}), child_c))
            con.execute("UPDATE replay_runs SET status='queued' WHERE id=?", (child_c,))

            analytics = CONTROL._build_reprocess_analytics(con)
            con.close()

        self.assertEqual(analytics["summary"]["total_attempts"], 3)
        self.assertEqual(analytics["summary"]["resolved"], 1)
        self.assertEqual(analytics["summary"]["repeated"], 1)
        self.assertEqual(analytics["summary"]["pending_or_reincident"], 2)
        self.assertEqual(analytics["by_flow"][0]["flow_name"], "pagamento")
        self.assertEqual(analytics["by_flow"][0]["attempts"], 2)
        self.assertEqual(analytics["by_flow"][0]["resolved"], 1)
        self.assertEqual(analytics["by_flow"][0]["repeated"], 1)
        self.assertEqual(analytics["by_environment"][0]["environment"], "recital24-prd")
        self.assertEqual(analytics["automation_candidates"][0]["failure_type"], "checkpoint_mismatch")
        self.assertGreater(analytics["automation_candidates"][0]["automation_candidate_score"], 0)
        self.assertEqual(analytics["pending_queue"][0]["current_status"], "queued")
        self.assertEqual(analytics["repeated_signatures"][0]["failure_type"], "checkpoint_mismatch")
        self.assertEqual(analytics["repeated_signatures"][0]["repeated"], 1)

    def test_linux_status_marks_gateway_unavailable_when_no_service_exists(self):
        responses = [
            (4, "Unit sshd.service could not be found."),
            (4, "Unit ssh.service could not be found."),
            (4, "Unit sshd.socket could not be found."),
            (4, "Unit ssh.socket could not be found."),
        ]

        with patch.object(CONTROL.platform, "system", return_value="Linux"), \
             patch.object(CONTROL.shutil, "which", return_value="/bin/systemctl"), \
             patch.object(CONTROL, "_run_cmd", side_effect=responses):
            state = CONTROL._gateway_service_status()

        self.assertFalse(state["running"])
        self.assertFalse(state["available"])
        self.assertEqual(state["service"], "unavailable")
        self.assertIn("não encontrado", state["error"])

    def test_toggle_returns_error_when_gateway_service_is_unavailable(self):
        unavailable_state = {
            "platform": "linux",
            "service": "unavailable",
            "socket": "unavailable",
            "running": False,
            "available": False,
            "error": "serviço ssh/sshd não encontrado neste host",
        }

        with patch.object(CONTROL, "_gateway_service_status", return_value=unavailable_state), \
             patch.object(CONTROL, "_run_cmd") as run_cmd:
            state = CONTROL._gateway_toggle(False)

        run_cmd.assert_not_called()
        self.assertFalse(state["available"])
        self.assertIn("não encontrado", state["error"])

    def test_linux_status_considers_socket_active_as_gateway_on(self):
        responses = [
            (0, "sshd.service loaded"),
            (0, "ssh.socket loaded"),
            (3, "inactive"),
            (0, "active"),
        ]

        with patch.object(CONTROL.platform, "system", return_value="Linux"), \
             patch.object(CONTROL.shutil, "which", return_value="/bin/systemctl"), \
             patch.object(CONTROL, "_run_cmd", side_effect=responses):
            state = CONTROL._gateway_service_status()

        self.assertTrue(state["available"])
        self.assertTrue(state["running"])
        self.assertFalse(state["service_running"])
        self.assertTrue(state["socket_running"])
        self.assertIsNone(state["error"])

    def test_toggle_uses_sudo_and_stops_socket_and_service(self):
        initial_state = {
            "platform": "linux",
            "service": "sshd",
            "socket": "ssh.socket",
            "running": True,
            "available": True,
            "error": None,
        }
        final_state = {
            "platform": "linux",
            "service": "sshd",
            "socket": "ssh.socket",
            "running": False,
            "available": True,
            "error": None,
        }

        with patch.object(CONTROL, "_gateway_service_status", side_effect=[initial_state, final_state]), \
             patch.object(CONTROL, "_run_cmd", return_value=(0, "")) as run_cmd:
            state = CONTROL._gateway_toggle(False)

        run_cmd.assert_called_once_with(["sudo", "-n", "systemctl", "stop", "ssh.socket", "sshd"])
        self.assertFalse(state["running"])

    def test_read_gateway_monitor_summarizes_recent_events(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            log_file = Path(tmpdir) / "audit-20260329.part000.jsonl"
            entries = [
                {"ts_ms": 1000, "type": "in_bytes", "actor": "alice", "session_id": "s1", "dir": "in", "n": 12},
                {"ts_ms": 2000, "type": "checkpoint", "actor": "alice", "session_id": "s1"},
                {"ts_ms": 3000, "type": "unknown_screen", "actor": "bob", "session_id": "s2", "message": "warning"},
            ]
            log_file.write_text("\n".join(json.dumps(item) for item in entries), encoding="utf-8")

            payload = CONTROL._read_gateway_monitor(tmpdir, limit=40)

        self.assertEqual(payload["log_dir"], tmpdir)
        self.assertEqual(len(payload["events"]), 3)
        self.assertEqual(payload["summary"]["window_events"], 3)
        self.assertEqual(payload["summary"]["unique_sessions"], 2)
        self.assertEqual(payload["summary"]["checkpoints"], 1)
        self.assertEqual(payload["summary"]["attention_events"], 1)
        self.assertEqual(payload["summary"]["last_event"]["type"], "unknown_screen")

    def test_read_gateway_sessions_filters_by_metadata(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            log_file = Path(tmpdir) / "audit-20260329.part000.jsonl"
            entries = [
                {"ts_ms": 1000, "type": "session_start", "actor": "alice", "session_id": "s1", "seq_global": 1, "seq_session": 1},
                {"ts_ms": 1100, "type": "bytes", "actor": "alice", "session_id": "s1", "dir": "in", "n": 12, "seq_global": 2, "seq_session": 2},
                {"ts_ms": 1200, "type": "checkpoint", "actor": "alice", "session_id": "s1", "seq_global": 3, "seq_session": 3},
                {"ts_ms": 2000, "type": "session_start", "actor": "bob", "session_id": "s2", "seq_global": 4, "seq_session": 1},
                {"ts_ms": 2100, "type": "unknown_screen", "actor": "bob", "session_id": "s2", "message": "warning", "seq_global": 5, "seq_session": 2},
                {"ts_ms": 2200, "type": "session_end", "actor": "bob", "session_id": "s2", "seq_global": 6, "seq_session": 3},
            ]
            log_file.write_text("\n".join(json.dumps(item) for item in entries), encoding="utf-8")

            payload = CONTROL._read_gateway_sessions(tmpdir, actor="bob", event_type="unknown_screen", limit=10)

        self.assertEqual(payload["log_dir"], tmpdir)
        self.assertEqual(payload["summary"]["total_sessions"], 2)
        self.assertEqual(payload["summary"]["returned_sessions"], 1)
        self.assertEqual(payload["sessions"][0]["session_id"], "s2")
        self.assertEqual(payload["sessions"][0]["actor"], "bob")
        self.assertEqual(payload["sessions"][0]["status"], "closed")
        self.assertIn("unknown_screen", payload["sessions"][0]["event_types"])

    def test_read_gateway_session_detail_returns_timeline_and_related_failures(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            log_file = Path(tmpdir) / "audit-20260329.part000.jsonl"
            entries = [
                {"ts_ms": 1000, "type": "session_start", "actor": "alice", "session_id": "s1", "seq_global": 1, "seq_session": 1},
                {"ts_ms": 1100, "type": "bytes", "actor": "alice", "session_id": "s1", "dir": "in", "n": 12, "seq_global": 2, "seq_session": 2},
                {"ts_ms": 1200, "type": "checkpoint", "actor": "alice", "session_id": "s1", "seq_global": 3, "seq_session": 3},
                {"ts_ms": 1300, "type": "session_end", "actor": "alice", "session_id": "s1", "seq_global": 4, "seq_session": 4},
            ]
            log_file.write_text("\n".join(json.dumps(item) for item in entries), encoding="utf-8")

            db_path = Path(tmpdir) / "replay.db"
            con = connect(str(db_path))
            init_db(con)
            ph = auth.pbkdf2_hash_password("admin123")
            con.execute(
                "INSERT INTO users(username,password_hash,role,created_at_ms) VALUES(?,?,'admin',?)",
                ("admin", ph, now_ms()),
            )
            user = con.execute("SELECT id FROM users WHERE username='admin'").fetchone()
            scenario_id = CONTROL._save_operational_scenario(
                con,
                payload={
                    "name": "Stress cliente critico",
                    "description": "cenario com SLA apertado",
                    "scenario_type": "stress",
                    "owner_name": "Ops HML",
                    "owner_contact": "hml@example.com",
                    "sla_max_failure_rate_pct": 10,
                    "sla_max_criticality_score": 20,
                    "log_dir": tmpdir,
                    "target_host": "legacy.example",
                    "target_user": "recital",
                    "target_command": "",
                    "mode": "strict-global",
                    "params": {"environment": "recital24-hml"},
                },
                created_by=int(user["id"]),
            )
            run_id = create_run(
                con,
                created_by=int(user["id"]),
                log_dir=tmpdir,
                target_host="legacy.example",
                target_user="recital",
                target_command="",
                mode="strict-global",
            )
            add_run_failure(
                con,
                run_id,
                build_failure_record(
                    session_id="s1",
                    seq_global=3,
                    seq_session=3,
                    event_type="checkpoint",
                    failure_type="checkpoint_mismatch",
                    severity="high",
                    expected_value="SIG:MENU",
                    observed_value="SIG:ERRO",
                    message="checkpoint mismatch session=s1",
                    evidence={"screen_state": "MENU"},
                ),
            )

            payload = CONTROL._read_gateway_session_detail(tmpdir, "s1", limit=20, con=con)
            con.close()

        self.assertIsNone(payload["error"])
        self.assertEqual(payload["session"]["session_id"], "s1")
        self.assertEqual(len(payload["events"]), 4)
        self.assertEqual(payload["events"][0]["type"], "session_start")
        self.assertEqual(payload["events"][-1]["type"], "session_end")
        self.assertEqual(payload["events"][0]["event_kind"], "session_start")
        self.assertEqual(payload["events"][2]["event_kind"], "failure")
        self.assertEqual(payload["events"][2]["linked_failures"][0]["failure_type"], "checkpoint_mismatch")
        self.assertEqual(len(payload["failures"]), 1)
        self.assertEqual(payload["failures"][0]["run_id"], run_id)
        self.assertEqual(payload["failures"][0]["failure_type"], "checkpoint_mismatch")
        self.assertEqual(payload["failure_groups"][0]["count"], 1)

    def test_prepare_session_replay_data_includes_deterministic_timeline(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            log_file = Path(tmpdir) / "audit-20260329.part000.jsonl"
            entries = [
                {"ts_ms": 1000, "type": "session_start", "actor": "alice", "session_id": "s1", "seq_global": 1, "seq_session": 1},
                {
                    "ts_ms": 1100,
                    "type": "deterministic_input",
                    "actor": "alice",
                    "session_id": "s1",
                    "seq_global": 2,
                    "seq_session": 2,
                    "screen_sig": "SIG:MENU",
                    "screen_sample": "MENU PRINCIPAL",
                    "norm_sha256": "a" * 64,
                    "norm_len": 32,
                    "key_text": "1",
                    "key_kind": "printable",
                    "key_b64": "MQ==",
                    "input_len": 1,
                    "screen_source": "stable",
                    "screen_snapshot_ts_ms": 1005,
                    "screen_snapshot_age_ms": 95,
                    "source": "gateway_record",
                },
                {"ts_ms": 1200, "type": "bytes", "actor": "alice", "session_id": "s1", "dir": "in", "n": 1, "data_b64": "MQ==", "seq_global": 3, "seq_session": 3},
                {"ts_ms": 1300, "type": "bytes", "actor": "alice", "session_id": "s1", "dir": "out", "n": 4, "data_b64": "T0sNCg==", "seq_global": 4, "seq_session": 4},
            ]
            log_file.write_text("\n".join(json.dumps(item) for item in entries), encoding="utf-8")

            replay_data = prepare_session_replay_data(tmpdir, "s1")
            sessions_data = CONTROL._read_gateway_sessions(tmpdir, session_id="s1", limit=1)

        self.assertIsNone(replay_data["error"])
        self.assertEqual(replay_data["playback"]["deterministic_event_count"], 1)
        self.assertEqual(replay_data["deterministic_events"][0]["screen_sig"], "SIG:MENU")
        self.assertEqual(replay_data["deterministic_events"][0]["screen_source"], "stable")
        self.assertEqual(replay_data["timeline"][0]["type"], "deterministic_input")
        self.assertEqual(replay_data["timeline"][0]["key_text"], "1")
        self.assertEqual(replay_data["timeline"][0]["screen_source"], "stable")
        self.assertEqual(sessions_data["sessions"][0]["deterministic_input_count"], 1)

    def test_read_gateway_session_detail_filters_by_seq_global_and_groups_repeated_failures(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            log_file = Path(tmpdir) / "audit-20260329.part000.jsonl"
            entries = [
                {"ts_ms": 1000, "type": "session_start", "actor": "alice", "session_id": "s1", "seq_global": 1, "seq_session": 1},
                {"ts_ms": 1100, "type": "checkpoint", "actor": "alice", "session_id": "s1", "seq_global": 2, "seq_session": 2},
                {"ts_ms": 1200, "type": "checkpoint", "actor": "alice", "session_id": "s1", "seq_global": 3, "seq_session": 3},
                {"ts_ms": 1300, "type": "session_end", "actor": "alice", "session_id": "s1", "seq_global": 4, "seq_session": 4},
            ]
            log_file.write_text("\n".join(json.dumps(item) for item in entries), encoding="utf-8")

            db_path = Path(tmpdir) / "replay.db"
            con = connect(str(db_path))
            init_db(con)
            ph = auth.pbkdf2_hash_password("admin123")
            con.execute(
                "INSERT INTO users(username,password_hash,role,created_at_ms) VALUES(?,?,'admin',?)",
                ("admin", ph, now_ms()),
            )
            user = con.execute("SELECT id FROM users WHERE username='admin'").fetchone()
            scenario_id = CONTROL._save_operational_scenario(
                con,
                payload={
                    "name": "Stress cliente critico",
                    "description": "cenario com SLA apertado",
                    "scenario_type": "stress",
                    "owner_name": "Ops HML",
                    "owner_contact": "hml@example.com",
                    "sla_max_failure_rate_pct": 10,
                    "sla_max_criticality_score": 20,
                    "log_dir": tmpdir,
                    "target_host": "legacy.example",
                    "target_user": "recital",
                    "target_command": "",
                    "mode": "strict-global",
                    "params": {"environment": "recital24-hml"},
                },
                created_by=int(user["id"]),
            )
            run_id = create_run(
                con,
                created_by=int(user["id"]),
                log_dir=tmpdir,
                target_host="legacy.example",
                target_user="recital",
                target_command="",
                mode="strict-global",
            )
            add_run_failure(
                con,
                run_id,
                build_failure_record(
                    session_id="s1",
                    seq_global=2,
                    seq_session=2,
                    event_type="checkpoint",
                    failure_type="checkpoint_mismatch",
                    severity="high",
                    expected_value="SIG:MENU",
                    observed_value="SIG:ERRO",
                    message="checkpoint mismatch session=s1 seq2",
                    evidence={"screen_state": "MENU"},
                ),
            )
            add_run_failure(
                con,
                run_id,
                build_failure_record(
                    session_id="s1",
                    seq_global=3,
                    seq_session=3,
                    event_type="checkpoint",
                    failure_type="checkpoint_mismatch",
                    severity="high",
                    expected_value="SIG:MENU",
                    observed_value="SIG:ERRO",
                    message="checkpoint mismatch session=s1 seq3",
                    evidence={"screen_state": "MENU"},
                ),
            )

            payload = CONTROL._read_gateway_session_detail(tmpdir, "s1", limit=20, seq_global_from=2, seq_global_to=3, con=con)
            con.close()

        self.assertIsNone(payload["error"])
        self.assertEqual([event["seq_global"] for event in payload["events"]], [2, 3])
        self.assertEqual(len(payload["failures"]), 2)
        self.assertEqual(payload["failure_groups"][0]["count"], 2)
        self.assertEqual(payload["failure_groups"][0]["seq_globals"], [2, 3])
        self.assertEqual(payload["summary"]["filters"]["seq_global_from"], 2)
        self.assertEqual(payload["summary"]["filters"]["seq_global_to"], 3)

    def test_build_observability_overview_combines_gateway_and_ops(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            log_file = Path(tmpdir) / "audit-20260329.part000.jsonl"
            entries = [
                {"ts_ms": 1000, "type": "session_start", "actor": "alice", "session_id": "s1", "seq_global": 1, "seq_session": 1},
                {"ts_ms": 1100, "type": "checkpoint", "actor": "alice", "session_id": "s1", "seq_global": 2, "seq_session": 2},
                {"ts_ms": 1200, "type": "unknown_screen", "actor": "alice", "session_id": "s1", "seq_global": 3, "seq_session": 3},
            ]
            log_file.write_text("\n".join(json.dumps(item) for item in entries), encoding="utf-8")

            db_path = Path(tmpdir) / "replay.db"
            con = connect(str(db_path))
            init_db(con)
            ph = auth.pbkdf2_hash_password("admin123")
            con.execute(
                "INSERT INTO users(username,password_hash,role,created_at_ms) VALUES(?,?,'admin',?)",
                ("admin", ph, now_ms()),
            )
            user = con.execute("SELECT id FROM users WHERE username='admin'").fetchone()
            scenario_id = CONTROL._save_operational_scenario(
                con,
                payload={
                    "name": "Stress cliente critico",
                    "description": "cenario com SLA apertado",
                    "scenario_type": "stress",
                    "owner_name": "Ops HML",
                    "owner_contact": "hml@example.com",
                    "sla_max_failure_rate_pct": 10,
                    "sla_max_criticality_score": 20,
                    "log_dir": tmpdir,
                    "target_host": "legacy.example",
                    "target_user": "recital",
                    "target_command": "",
                    "mode": "strict-global",
                    "params": {"environment": "recital24-hml"},
                },
                created_by=int(user["id"]),
            )
            run_id = create_run(
                con,
                created_by=int(user["id"]),
                log_dir=tmpdir,
                target_host="legacy.example",
                target_user="recital",
                target_command="",
                mode="strict-global",
            )
            add_run_failure(
                con,
                run_id,
                build_failure_record(
                    session_id="s1",
                    seq_global=3,
                    seq_session=3,
                    flow_name="consulta_cliente",
                    event_type="unknown_screen",
                    failure_type="checkpoint_mismatch",
                    severity="high",
                    expected_value="SIG:MENU",
                    observed_value="SIG:ERRO",
                    message="checkpoint mismatch session=s1",
                    evidence={"screen_state": "MENU"},
                ),
            )
            con.execute("UPDATE replay_runs SET status='failed' WHERE id=?", (run_id,))
            previous_run_id = create_run(
                con,
                created_by=int(user["id"]),
                log_dir=tmpdir,
                target_host="legacy.example",
                target_user="recital",
                target_command="",
                mode="strict-global",
            )
            con.execute(
                "UPDATE replay_runs SET params_json=? WHERE id IN (?, ?)",
                (
                    json.dumps({"environment": "recital24-hml", "scenario_id": int(scenario_id), "scenario_name": "Stress cliente critico", "scenario_type": "stress"}),
                    previous_run_id,
                    run_id,
                ),
            )
            con.execute("UPDATE replay_runs SET status='success' WHERE id=?", (previous_run_id,))
            con.execute("UPDATE replay_runs SET parent_run_id=? WHERE id=?", (previous_run_id, run_id))

            payload = CONTROL._build_observability_overview(con, log_dir="", limit=20, user_id=int(user["id"]))
            con.close()

        self.assertIsNone(payload["error"])
        self.assertEqual(payload["summary"]["log_dir_source"], "recent_run")
        self.assertEqual(payload["gateway"]["log_dir"], tmpdir)
        self.assertEqual(payload["gateway"]["summary"]["window_events"], 3)
        self.assertEqual(payload["gateway"]["summary"]["attention_events"], 1)
        self.assertTrue(payload["ops"]["enabled"])
        self.assertEqual(payload["ops"]["summary"]["total_runs"], 2)
        self.assertEqual(payload["ops"]["summary"]["total_failures"], 1)
        self.assertEqual(payload["ops"]["run_status"][0]["status"], "failed")
        self.assertEqual(payload["ops"]["recent_failures"][0]["session_id"], "s1")
        compared_run = next(item for item in payload["ops"]["recent_runs"] if item["id"] == run_id)
        self.assertEqual(compared_run["environment"], "recital24-hml")
        self.assertIn("comparison_summary", compared_run)
        self.assertTrue(compared_run["comparison_summary"]["regression"])
        self.assertEqual(compared_run["flow_summary"][0]["flow_name"], "consulta_cliente")
        self.assertEqual(payload["ops"]["recent_regressions"][0]["run_id"], run_id)
        self.assertEqual(payload["ops"]["recent_regressions"][0]["environment"], "recital24-hml")
        self.assertEqual(payload["ops"]["summary"]["sla_breaches"], 1)
        self.assertEqual(payload["ops"]["sla_breaches"][0]["name"], "Stress cliente critico")
        self.assertEqual(payload["ops"]["sla_breaches"][0]["sla_summary"]["status"], "breached")

    def test_build_run_comparison_detects_new_and_recurring_failures_against_parent(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "replay.db"
            con = connect(str(db_path))
            init_db(con)
            ph = auth.pbkdf2_hash_password("admin123")
            con.execute(
                "INSERT INTO users(username,password_hash,role,created_at_ms) VALUES(?,?,'admin',?)",
                ("admin", ph, now_ms()),
            )
            user = con.execute("SELECT id FROM users WHERE username='admin'").fetchone()

            base_run_id = create_run(
                con,
                created_by=int(user["id"]),
                log_dir=tmpdir,
                target_host="legacy.example",
                target_user="recital",
                target_command="",
                mode="strict-global",
            )
            con.execute(
                "UPDATE replay_runs SET params_json=? WHERE id=?",
                (json.dumps({"environment": "recital24-prd"}), base_run_id),
            )
            add_run_failure(
                con,
                base_run_id,
                build_failure_record(
                    session_id="s1",
                    seq_global=2,
                    seq_session=2,
                    flow_name="fluxo_menu",
                    event_type="checkpoint",
                    failure_type="checkpoint_mismatch",
                    severity="high",
                    expected_value="SIG:MENU",
                    observed_value="SIG:ERRO",
                    message="baseline recurring",
                    evidence={"screen_state": "MENU"},
                ),
            )
            con.execute("UPDATE replay_runs SET status='failed' WHERE id=?", (base_run_id,))

            current_run_id = create_run(
                con,
                created_by=int(user["id"]),
                log_dir=tmpdir,
                target_host="legacy.example",
                target_user="recital",
                target_command="",
                mode="strict-global",
                parent_run_id=base_run_id,
            )
            con.execute(
                "UPDATE replay_runs SET params_json=? WHERE id=?",
                (json.dumps({"environment": "recital24-prd"}), current_run_id),
            )
            add_run_failure(
                con,
                current_run_id,
                build_failure_record(
                    session_id="s1",
                    seq_global=2,
                    seq_session=2,
                    flow_name="fluxo_menu",
                    event_type="checkpoint",
                    failure_type="checkpoint_mismatch",
                    severity="high",
                    expected_value="SIG:MENU",
                    observed_value="SIG:ERRO",
                    message="current recurring",
                    evidence={"screen_state": "MENU"},
                ),
            )
            add_run_failure(
                con,
                current_run_id,
                build_failure_record(
                    session_id="s2",
                    seq_global=5,
                    seq_session=4,
                    flow_name="fluxo_pagamento",
                    event_type="runtime",
                    failure_type="technical_error",
                    severity="critical",
                    expected_value="screen:checkout",
                    observed_value="connection-reset",
                    message="new hard failure",
                    evidence={"exception": "connection reset"},
                ),
            )
            con.execute("UPDATE replay_runs SET status='failed' WHERE id=?", (current_run_id,))

            payload = CONTROL._build_run_comparison(con, current_run_id)
            con.close()

        self.assertIsNotNone(payload)
        self.assertEqual(payload["baseline_mode"], "parent")
        self.assertEqual(payload["environment"], "recital24-prd")
        self.assertEqual(payload["baseline_run"]["id"], base_run_id)
        self.assertEqual(payload["baseline_run"]["environment"], "recital24-prd")
        self.assertTrue(payload["summary"]["regression"])
        self.assertEqual(payload["summary"]["current_failure_count"], 2)
        self.assertEqual(payload["summary"]["baseline_failure_count"], 1)
        self.assertEqual(payload["summary"]["new_failure_groups"], 1)
        self.assertEqual(payload["summary"]["recurring_failure_groups"], 1)
        self.assertEqual(payload["new_failures"][0]["failure_type"], "technical_error")
        self.assertEqual(payload["recurring_failures"][0]["failure_type"], "checkpoint_mismatch")
        self.assertEqual(payload["recurring_failures"][0]["baseline_count"], 1)
        self.assertEqual(payload["flow_summary"][0]["flow_name"], "fluxo_pagamento")
        self.assertEqual(payload["flow_summary"][0]["delta_count"], 1)

    def test_run_report_and_exports_include_environment_flow_and_severity(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "replay.db"
            con = connect(str(db_path))
            init_db(con)
            ph = auth.pbkdf2_hash_password("admin123")
            con.execute(
                "INSERT INTO users(username,password_hash,role,created_at_ms) VALUES(?,?,'admin',?)",
                ("admin", ph, now_ms()),
            )
            user = con.execute("SELECT id FROM users WHERE username='admin'").fetchone()
            run_id = create_run(
                con,
                created_by=int(user["id"]),
                log_dir=tmpdir,
                target_host="legacy.example",
                target_user="recital",
                target_command="",
                mode="strict-global",
            )
            con.execute(
                "UPDATE replay_runs SET params_json=?, status='failed' WHERE id=?",
                (json.dumps({"environment": "recital24-qa"}), run_id),
            )
            add_run_failure(
                con,
                run_id,
                build_failure_record(
                    session_id="s1",
                    seq_global=9,
                    seq_session=3,
                    flow_name="fluxo_consulta",
                    event_type="checkpoint",
                    failure_type="checkpoint_mismatch",
                    severity="high",
                    expected_value="SIG:MENU",
                    observed_value="SIG:ERRO",
                    message="consulta mismatch",
                    evidence={"screen_state": "MENU"},
                ),
            )
            report = CONTROL._build_run_report(con, run_id)
            markdown = CONTROL._report_to_markdown(report)
            csv_text = CONTROL._report_to_csv(report)
            con.close()

        self.assertEqual(report["environment"], "recital24-qa")
        self.assertEqual(report["summary"]["flow_count_with_failures"], 1)
        self.assertEqual(report["summary"]["by_flow"]["fluxo_consulta"], 1)
        self.assertEqual(report["flows"][0]["flow_name"], "fluxo_consulta")
        self.assertEqual(report["flows"][0]["severities"]["high"], 1)
        self.assertIn("environment: recital24-qa", markdown)
        self.assertIn("## Flows", markdown)
        self.assertIn("fluxo_consulta", markdown)
        self.assertIn('"recital24-qa"', csv_text)
        self.assertIn('"fluxo_consulta"', csv_text)

    def test_build_runs_trend_report_groups_by_environment_and_flow(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "replay.db"
            con = connect(str(db_path))
            init_db(con)
            ph = auth.pbkdf2_hash_password("admin123")
            con.execute(
                "INSERT INTO users(username,password_hash,role,created_at_ms) VALUES(?,?,'admin',?)",
                ("admin", ph, now_ms()),
            )
            user = con.execute("SELECT id FROM users WHERE username='admin'").fetchone()

            base_ts = now_ms()
            run1 = create_run(con, int(user["id"]), tmpdir, "legacy.example", "recital", "", "strict-global")
            con.execute("UPDATE replay_runs SET params_json=?, status='success' WHERE id=?", (json.dumps({"environment": "hml"}), run1))
            con.execute("UPDATE replay_runs SET created_at_ms=? WHERE id=?", (base_ts - 3000, run1))

            run2 = create_run(con, int(user["id"]), tmpdir, "legacy.example", "recital", "", "strict-global", parent_run_id=run1)
            con.execute("UPDATE replay_runs SET params_json=?, status='failed' WHERE id=?", (json.dumps({"environment": "hml"}), run2))
            con.execute("UPDATE replay_runs SET created_at_ms=? WHERE id=?", (base_ts - 1000, run2))
            add_run_failure(
                con, run2,
                build_failure_record(
                    session_id="s1", seq_global=2, seq_session=2, flow_name="fluxo_login",
                    event_type="checkpoint", failure_type="checkpoint_mismatch", severity="high",
                    expected_value="SIG:A", observed_value="SIG:B", message="login mismatch", evidence={}
                ),
            )

            run3 = create_run(con, int(user["id"]), tmpdir + "-2", "legacy2.example", "recital", "", "strict-global")
            con.execute("UPDATE replay_runs SET params_json=?, status='failed' WHERE id=?", (json.dumps({"environment": "prd"}), run3))
            con.execute("UPDATE replay_runs SET created_at_ms=? WHERE id=?", (base_ts + 1000, run3))
            add_run_failure(
                con, run3,
                build_failure_record(
                    session_id="s2", seq_global=5, seq_session=3, flow_name="fluxo_pagamento",
                    event_type="runtime", failure_type="technical_error", severity="critical",
                    expected_value="checkout", observed_value="timeout", message="payment timeout", evidence={}
                ),
            )

            payload = CONTROL._build_runs_trend_report(con, run_limit=10)
            con.close()

        self.assertEqual(payload["summary"]["run_count"], 3)
        self.assertEqual(payload["summary"]["environment_count"], 2)
        self.assertEqual(payload["summary"]["flow_count"], 2)
        self.assertEqual(payload["summary"]["regression_runs"], 1)
        self.assertEqual(payload["environments"][0]["environment"], "hml")
        self.assertEqual(payload["environments"][0]["regressions"], 1)
        self.assertEqual(payload["flows"][0]["flow_name"], "fluxo_login")
        self.assertEqual(payload["flows"][0]["regressions"], 1)

        with tempfile.TemporaryDirectory() as tmpdir2:
            db_path = Path(tmpdir2) / "replay.db"
            con = connect(str(db_path))
            init_db(con)
            ph = auth.pbkdf2_hash_password("admin123")
            con.execute(
                "INSERT INTO users(username,password_hash,role,created_at_ms) VALUES(?,?,'admin',?)",
                ("admin", ph, now_ms()),
            )
            user = con.execute("SELECT id FROM users WHERE username='admin'").fetchone()
            ts0 = now_ms()
            ra = create_run(con, int(user["id"]), tmpdir2, "hml.example", "recital", "", "strict-global")
            con.execute("UPDATE replay_runs SET params_json=?, status='failed', created_at_ms=? WHERE id=?", (json.dumps({"environment": "hml"}), ts0 - 5000, ra))
            add_run_failure(
                con, ra,
                build_failure_record(
                    session_id="sa", seq_global=1, seq_session=1, flow_name="fluxo_hml",
                    event_type="checkpoint", failure_type="checkpoint_mismatch", severity="high",
                    expected_value="A", observed_value="B", message="hml fail", evidence={}
                ),
            )
            rb = create_run(con, int(user["id"]), tmpdir2, "prd.example", "recital", "", "strict-global")
            con.execute("UPDATE replay_runs SET params_json=?, status='failed', created_at_ms=? WHERE id=?", (json.dumps({"environment": "prd"}), ts0 + 5000, rb))
            add_run_failure(
                con, rb,
                build_failure_record(
                    session_id="sb", seq_global=2, seq_session=1, flow_name="fluxo_prd",
                    event_type="runtime", failure_type="technical_error", severity="critical",
                    expected_value="X", observed_value="Y", message="prd fail", evidence={}
                ),
            )
            filtered = CONTROL._build_runs_trend_report(
                con,
                run_limit=10,
                environment="prd",
                created_from_ms=ts0,
                created_to_ms=ts0 + 10000,
            )
            con.close()

        self.assertEqual(filtered["summary"]["run_count"], 1)
        self.assertEqual(filtered["summary"]["environment_count"], 1)
        self.assertEqual(filtered["environments"][0]["environment"], "prd")
        self.assertEqual(filtered["flows"][0]["flow_name"], "fluxo_prd")
        self.assertEqual(filtered["summary"]["filters"]["environment"], "prd")

    def test_observability_scenarios_can_be_saved_listed_and_deleted(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "replay.db"
            con = connect(str(db_path))
            init_db(con)
            ph = auth.pbkdf2_hash_password("admin123")
            con.execute(
                "INSERT INTO users(username,password_hash,role,created_at_ms) VALUES(?,?,'admin',?)",
                ("admin", ph, now_ms()),
            )
            con.execute(
                "INSERT INTO users(username,password_hash,role,created_at_ms) VALUES(?,?,'operator',?)",
                ("alice", ph, now_ms()),
            )
            user = con.execute("SELECT id FROM users WHERE username='admin'").fetchone()
            alice = con.execute("SELECT id FROM users WHERE username='alice'").fetchone()

            scenario_id = CONTROL._save_analytics_scenario(
                con,
                name="HML ultima semana",
                scope="observability",
                visibility="shared",
                tags="hml,pagamento",
                filters={
                    "environment": "hml",
                    "created_from_ms": 1000,
                    "created_to_ms": 2000,
                    "run_limit": 25,
                    "log_dir": "/tmp/gateway-hml",
                },
                created_by=int(user["id"]),
            )
            updated_id = CONTROL._save_analytics_scenario(
                con,
                name="HML ultima semana",
                scope="observability",
                visibility="shared",
                tags=["hml", "time-a"],
                filters={
                    "environment": "hml",
                    "created_from_ms": 1500,
                    "created_to_ms": 2500,
                    "run_limit": 30,
                    "log_dir": "/tmp/gateway-hml",
                },
                created_by=int(user["id"]),
            )
            CONTROL._save_analytics_scenario(
                con,
                name="PRD privado",
                scope="observability",
                visibility="private",
                tags="prd",
                filters={"environment": "prd", "run_limit": 10},
                created_by=int(alice["id"]),
            )
            scenarios = CONTROL._list_analytics_scenarios(con, scope="observability", user_id=int(user["id"]))
            alice_view = CONTROL._list_analytics_scenarios(con, scope="observability", user_id=int(alice["id"]))
            fav_set = CONTROL._set_analytics_scenario_favorite(con, scenario_id, int(alice["id"]), True)
            alice_filtered = CONTROL._list_analytics_scenarios(
                con,
                scope="observability",
                user_id=int(alice["id"]),
                visibility="shared",
                tag="time-a",
            )
            deleted = CONTROL._delete_analytics_scenario(con, scenario_id, scope="observability")
            scenarios_after = CONTROL._list_analytics_scenarios(con, scope="observability", user_id=int(user["id"]))
            con.close()

        self.assertEqual(updated_id, scenario_id)
        self.assertEqual(len(scenarios), 1)
        self.assertEqual(len(alice_view), 2)
        self.assertEqual(scenarios[0]["name"], "HML ultima semana")
        self.assertEqual(scenarios[0]["filters"]["created_from_ms"], 1500)
        self.assertEqual(scenarios[0]["filters"]["run_limit"], 30)
        self.assertEqual(scenarios[0]["visibility"], "shared")
        self.assertEqual(scenarios[0]["tags"], ["hml", "time-a"])
        self.assertEqual(scenarios[0]["created_by_username"], "admin")
        self.assertTrue(fav_set)
        self.assertEqual(len(alice_filtered), 1)
        self.assertTrue(alice_filtered[0]["is_favorite"])
        self.assertTrue(deleted)
        self.assertEqual(scenarios_after, [])

    def test_operational_scenario_catalog_can_save_list_and_instantiate_run(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "replay.db"
            con = connect(str(db_path))
            init_db(con)
            ph = auth.pbkdf2_hash_password("admin123")
            con.execute(
                "INSERT INTO users(username,password_hash,role,created_at_ms) VALUES(?,?,'admin',?)",
                ("admin", ph, now_ms()),
            )
            ph_ops = auth.pbkdf2_hash_password("operator123")
            con.execute(
                "INSERT INTO users(username,password_hash,role,created_at_ms) VALUES(?,?,'operator',?)",
                ("ops1", ph_ops, now_ms()),
            )
            user = con.execute("SELECT id FROM users WHERE username='admin'").fetchone()
            operator = con.execute("SELECT id FROM users WHERE username='ops1'").fetchone()

            scenario_id = CONTROL._save_operational_scenario(
                con,
                payload={
                    "name": "Stress pagamento 20x",
                    "description": "carga concorrente no fluxo de pagamento",
                    "scenario_type": "stress",
                    "squad": "migracao-core",
                    "area": "financeiro",
                    "tags": ["pagamento", "prioritario"],
                    "owner_name": "Time Operacoes Core",
                    "owner_contact": "core-ops@example.com",
                    "sla_max_failure_rate_pct": 20,
                    "sla_max_criticality_score": 40,
                    "log_dir": "/tmp/audit-pay",
                    "target_host": "recital24.example",
                    "target_user": "replay",
                    "target_command": "",
                    "mode": "parallel-sessions",
                    "params": {"concurrency": 20, "speed": 4, "target_user_pool": ["u1", "u2"]},
                },
                created_by=int(user["id"]),
            )
            CONTROL._save_operational_scenario(
                con,
                payload={
                    "name": "Replay consulta",
                    "description": "replay simples",
                    "scenario_type": "replay",
                    "squad": "consulta",
                    "area": "atendimento",
                    "tags": "consulta,hml",
                    "owner_name": "Squad Consulta",
                    "owner_contact": "consulta@example.com",
                    "sla_max_failure_rate_pct": 80,
                    "sla_max_criticality_score": 90,
                    "log_dir": "/tmp/audit-consulta",
                    "target_host": "recital24-hml.example",
                    "target_user": "replay",
                    "target_command": "",
                    "mode": "strict-global",
                    "params": {"environment": "hml"},
                },
                created_by=int(user["id"]),
            )
            scenarios = CONTROL._list_operational_scenarios(con)
            filtered = CONTROL._list_operational_scenarios(con, scenario_type="replay", environment="hml")
            run_id = CONTROL._instantiate_run_from_scenario(con, scenario_id, int(user["id"]))
            con.execute("UPDATE replay_runs SET status='success' WHERE id=?", (run_id,))
            failed_run_id = CONTROL._instantiate_run_from_scenario(con, scenario_id, int(user["id"]))
            con.execute("UPDATE replay_runs SET status='failed' WHERE id=?", (failed_run_id,))
            con.execute("UPDATE replay_runs SET created_at_ms=? WHERE id=?", (1_700_000_000_000, run_id))
            con.execute("UPDATE replay_runs SET created_at_ms=? WHERE id=?", (1_700_100_000_000, failed_run_id))
            failure = build_failure_record(
                failure_type="checkpoint_mismatch",
                severity="high",
                event_type="checkpoint",
                message="assinatura divergente",
                expected_value="sig-a",
                observed_value="sig-b",
                evidence={"session_id": "sess-1", "seq_global": 10},
                session_id="sess-1",
                seq_global=10,
                flow_name="pagamento",
            )
            add_run_failure(con, failed_run_id, failure)
            CONTROL._set_operational_scenario_favorite(con, scenario_id, int(operator["id"]), True)
            scenarios = CONTROL._list_operational_scenarios(con)
            recent_usage = CONTROL._list_operational_scenarios(con, usage_user="admin", used_from_ms=1_700_050_000_000, sort_by="recent-use")
            usage_sorted = CONTROL._list_operational_scenarios(con, sort_by="most-used")
            favorite_only = CONTROL._list_operational_scenarios(con, user_id=int(operator["id"]), favorite_only=True)
            criticality_sorted = CONTROL._list_operational_scenarios(con, user_id=int(operator["id"]), sort_by="criticality")
            finance_filtered = CONTROL._list_operational_scenarios(con, squad="migracao", area="finance", tag="prioritario")
            breached_only = CONTROL._list_operational_scenarios(con, owner="operacoes core", sla_status="breached")
            run = con.execute("SELECT * FROM replay_runs WHERE id=?", (run_id,)).fetchone()
            deleted = CONTROL._delete_operational_scenario(con, scenario_id)
            scenarios_after = CONTROL._list_operational_scenarios(con)
            con.close()

        stress_scenario = next(item for item in scenarios if item["name"] == "Stress pagamento 20x")
        self.assertEqual(len(scenarios), 2)
        self.assertEqual(len(filtered), 1)
        self.assertEqual(filtered[0]["name"], "Replay consulta")
        self.assertEqual(filtered[0]["description"], "replay simples")
        self.assertEqual(stress_scenario["scenario_type"], "stress")
        self.assertEqual(stress_scenario["squad"], "migracao-core")
        self.assertEqual(stress_scenario["area"], "financeiro")
        self.assertIn("prioritario", stress_scenario["tags"])
        self.assertEqual(stress_scenario["owner_name"], "Time Operacoes Core")
        self.assertEqual(stress_scenario["owner_contact"], "core-ops@example.com")
        self.assertEqual(stress_scenario["sla"]["max_failure_rate_pct"], 20.0)
        self.assertEqual(stress_scenario["sla"]["max_criticality_score"], 40.0)
        self.assertEqual(stress_scenario["params"]["concurrency"], 20)
        self.assertEqual(stress_scenario["usage_summary"]["total_runs"], 2)
        self.assertEqual(stress_scenario["usage_summary"]["success_runs"], 1)
        self.assertEqual(stress_scenario["usage_summary"]["failed_runs"], 1)
        self.assertEqual(stress_scenario["usage_summary"]["runs_with_failures"], 1)
        self.assertEqual(stress_scenario["usage_summary"]["failure_rate_pct"], 50.0)
        self.assertEqual(stress_scenario["usage_summary"]["total_failure_events"], 1)
        self.assertGreater(stress_scenario["usage_summary"]["criticality_score"], 0)
        self.assertEqual(stress_scenario["usage_summary"]["severity_counts"]["high"], 1)
        self.assertEqual(stress_scenario["usage_summary"]["last_used_by_username"], "admin")
        self.assertEqual(stress_scenario["sla_summary"]["status"], "breached")
        self.assertTrue(stress_scenario["sla_summary"]["breaches"])
        self.assertEqual(len(recent_usage), 1)
        self.assertEqual(recent_usage[0]["name"], "Stress pagamento 20x")
        self.assertEqual(recent_usage[0]["usage_summary"]["total_runs"], 1)
        self.assertEqual(usage_sorted[0]["name"], "Stress pagamento 20x")
        self.assertEqual(len(favorite_only), 1)
        self.assertTrue(favorite_only[0]["is_favorite"])
        self.assertEqual(favorite_only[0]["name"], "Stress pagamento 20x")
        self.assertEqual(criticality_sorted[0]["name"], "Stress pagamento 20x")
        self.assertEqual(len(finance_filtered), 1)
        self.assertEqual(finance_filtered[0]["name"], "Stress pagamento 20x")
        self.assertEqual(len(breached_only), 1)
        self.assertEqual(breached_only[0]["sla_summary"]["status"], "breached")
        self.assertEqual(run["target_host"], "recital24.example")
        self.assertEqual(run["mode"], "parallel-sessions")
        self.assertIn("scenario_id", json.loads(run["params_json"]))
        self.assertTrue(deleted)
        self.assertEqual(len(scenarios_after), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)

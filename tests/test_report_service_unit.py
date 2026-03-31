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

from control.services.report_service import (
    build_run_comparison,
    build_run_report,
    build_runs_trend_report,
    create_reprocess_run_from_failure,
)
from dakota_gateway.replay_control import add_run_event, create_run
from dakota_gateway.state_db import connect, exec1, init_db, now_ms


class ReportServiceUnitTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tmpdir.name) / "report.db")
        self.con = connect(self.db_path)
        init_db(self.con)
        self.user_id = exec1(
            self.con,
            "INSERT INTO users(username,password_hash,role,created_at_ms) VALUES(?,?,?,?)",
            ("admin", "hash", "admin", now_ms()),
        )

    def tearDown(self):
        self.con.close()
        self.tmpdir.cleanup()

    def _make_run(self, *, params=None, parent_run_id=None, status="queued") -> int:
        run_id = create_run(
            self.con,
            created_by=self.user_id,
            log_dir="/tmp/audit",
            target_host="recital24.example",
            target_user="replay",
            target_command="shell",
            mode="strict-global",
            parent_run_id=parent_run_id,
        )
        if params is not None:
            self.con.execute("UPDATE replay_runs SET params_json=?, status=? WHERE id=?", (json.dumps(params), status, run_id))
        else:
            self.con.execute("UPDATE replay_runs SET status=? WHERE id=?", (status, run_id))
        return run_id

    def test_build_run_report_and_comparison_summarize_failures(self):
        baseline_id = self._make_run(params={"environment": "HML"}, status="success")
        current_id = self._make_run(params={"environment": "HML"}, status="failed")

        exec1(
            self.con,
            """
            INSERT INTO replay_failures(run_id, ts_ms, session_id, seq_global, seq_session, flow_name, event_type, failure_type, severity, expected_value, observed_value, message, evidence_json)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (baseline_id, now_ms(), "sess-1", 10, 1, "login", "checkpoint", "screen_divergence", "medium", "A", "B", "baseline fail", "{}"),
        )
        exec1(
            self.con,
            """
            INSERT INTO replay_failures(run_id, ts_ms, session_id, seq_global, seq_session, flow_name, event_type, failure_type, severity, expected_value, observed_value, message, evidence_json)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (current_id, now_ms(), "sess-1", 11, 1, "login", "checkpoint", "screen_divergence", "medium", "A", "B", "same fail", "{}"),
        )
        exec1(
            self.con,
            """
            INSERT INTO replay_failures(run_id, ts_ms, session_id, seq_global, seq_session, flow_name, event_type, failure_type, severity, expected_value, observed_value, message, evidence_json)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (current_id, now_ms(), "sess-2", 12, 1, "pedido", "checkpoint", "navigation_error", "high", "X", "Y", "new fail", "{}"),
        )

        report = build_run_report(self.con, current_id)
        comparison = build_run_comparison(self.con, current_id, baseline_run_id=baseline_id)

        self.assertEqual(report["environment"], "HML")
        self.assertEqual(report["summary"]["failure_count"], 2)
        self.assertEqual(comparison["summary"]["baseline_failure_count"], 1)
        self.assertEqual(comparison["summary"]["new_failure_groups"], 1)
        self.assertTrue(comparison["summary"]["regression"])

    def test_create_reprocess_run_from_failure_carries_partial_params(self):
        source_id = self._make_run(params={"environment": "PRD", "match_mode": "strict"}, status="failed")
        failure_id = exec1(
            self.con,
            """
            INSERT INTO replay_failures(run_id, ts_ms, session_id, seq_global, seq_session, flow_name, event_type, failure_type, severity, expected_value, observed_value, message, evidence_json)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (source_id, now_ms(), "sess-9", 99, 9, "financeiro", "checkpoint", "screen_divergence", "high", "CHK-1", "OBS-1", "checkpoint fail", "{}"),
        )

        new_run_id = create_reprocess_run_from_failure(self.con, source_id, failure_id, "session-from-checkpoint", self.user_id)
        params = json.loads(self.con.execute("SELECT params_json FROM replay_runs WHERE id=?", (new_run_id,)).fetchone()["params_json"] or "{}")

        self.assertEqual(params["replay_session_id"], "sess-9")
        self.assertEqual(params["replay_from_checkpoint_sig"], "CHK-1")
        self.assertNotIn("replay_from_seq_global", params)

    def test_build_runs_trend_report_groups_by_environment(self):
        first_id = self._make_run(params={"environment": "HML"}, status="failed")
        second_id = self._make_run(params={"environment": "PRD"}, status="success")
        add_run_event(self.con, first_id, "api", "start", {"by": "admin"})
        add_run_event(self.con, second_id, "api", "start", {"by": "admin"})
        exec1(
            self.con,
            """
            INSERT INTO replay_failures(run_id, ts_ms, session_id, seq_global, seq_session, flow_name, event_type, failure_type, severity, expected_value, observed_value, message, evidence_json)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (first_id, now_ms(), "sess-1", 1, 1, "login", "checkpoint", "timeout", "high", "", "", "timeout", "{}"),
        )

        trend = build_runs_trend_report(self.con, run_limit=10)
        envs = {item["environment"]: item for item in trend["environments"]}

        self.assertEqual(trend["summary"]["run_count"], 2)
        self.assertIn("HML", envs)
        self.assertEqual(envs["HML"]["failures"], 1)


if __name__ == "__main__":
    unittest.main()

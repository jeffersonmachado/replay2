#!/usr/bin/env python3
from __future__ import annotations

import http.cookiejar
import importlib.util
import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from urllib.request import Request, build_opener, HTTPCookieProcessor

GATEWAY_DIR = Path(__file__).resolve().parents[1] / "gateway"
sys.path.insert(0, str(GATEWAY_DIR))

import dakota_gateway.auth as auth
from dakota_gateway.replay_control import add_run_failure, build_failure_record, create_run
from dakota_gateway.state_db import connect, init_db, now_ms

CONTROL_SERVER_PATH = GATEWAY_DIR / "control" / "server.py"
SPEC = importlib.util.spec_from_file_location("control_server", CONTROL_SERVER_PATH)
CONTROL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CONTROL)


class ReplayFailureApiTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tmpdir.name) / "test.db")
        self.cookie_secret = b"test_cookie_secret_32_bytes___"
        self.hmac_key = b"test_hmac_key_32_bytes__________"

        con = connect(self.db_path)
        init_db(con)
        ph = auth.pbkdf2_hash_password("admin123")
        con.execute(
            "INSERT INTO users(username,password_hash,role,created_at_ms) VALUES(?,?,'admin',?)",
            ("admin", ph, now_ms()),
        )
        user = con.execute("SELECT id FROM users WHERE username='admin'").fetchone()
        self.run_id = create_run(
            con,
            created_by=int(user["id"]),
            log_dir="/tmp/replay-audit",
            target_host="legacy.example",
            target_user="recital",
            target_command="",
            mode="strict-global",
        )
        add_run_failure(
            con,
            self.run_id,
            build_failure_record(
                session_id="s-001",
                seq_global=17,
                seq_session=5,
                event_type="checkpoint",
                failure_type="checkpoint_mismatch",
                severity="high",
                expected_value="SIG:MENU",
                observed_value="SIG:ERRO",
                message="checkpoint mismatch session=s-001",
                evidence={"screen_state": "MENU", "expected_screen": "MENU PRINCIPAL"},
            ),
        )
        con.close()

        try:
            self.server = CONTROL.ControlServer(
                ("127.0.0.1", 0),
                CONTROL.Handler,
                db_path=self.db_path,
                cookie_secret=self.cookie_secret,
                hmac_key=self.hmac_key,
            )
        except PermissionError as exc:
            raise unittest.SkipTest(f"sandbox sem permissao para abrir socket local: {exc}") from exc
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        time.sleep(0.2)

        self.opener = build_opener(HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self._request(
            "POST",
            "/api/login",
            {"username": "admin", "password": "admin123"},
        )

    def tearDown(self):
        if hasattr(self, "server"):
            self.server.shutdown()
            self.server.server_close()
        self.tmpdir.cleanup()

    def _request(self, method: str, path: str, data: dict | None = None):
        url = f"http://127.0.0.1:{self.port}{path}"
        body = None if data is None else json.dumps(data).encode("utf-8")
        req = Request(url, data=body, headers={"Content-Type": "application/json"}, method=method)
        with self.opener.open(req, timeout=5) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, json.loads(raw) if raw else {}

    def test_run_detail_includes_failure_summary(self):
        status, payload = self._request("GET", f"/api/runs/{self.run_id}")

        self.assertEqual(status, 200)
        self.assertEqual(payload["run"]["id"], self.run_id)
        self.assertEqual(payload["run"]["failure_summary"]["total"], 1)
        self.assertEqual(payload["run"]["failure_summary"]["by_type"]["checkpoint_mismatch"], 1)
        self.assertEqual(payload["run"]["failure_summary"]["by_severity"]["high"], 1)

    def test_failures_endpoint_returns_structured_evidence(self):
        status, payload = self._request("GET", f"/api/runs/{self.run_id}/failures")

        self.assertEqual(status, 200)
        self.assertEqual(len(payload["failures"]), 1)
        failure = payload["failures"][0]
        self.assertEqual(failure["session_id"], "s-001")
        self.assertEqual(failure["failure_type"], "checkpoint_mismatch")
        self.assertEqual(failure["expected_value"], "SIG:MENU")
        self.assertEqual(failure["observed_value"], "SIG:ERRO")
        self.assertEqual(failure["evidence"]["screen_state"], "MENU")

    def test_run_report_consolidates_failures_by_session_type_and_severity(self):
        status, payload = self._request("GET", f"/api/runs/{self.run_id}/report")

        self.assertEqual(status, 200)
        report = payload["report"]
        self.assertEqual(report["run"]["id"], self.run_id)
        self.assertEqual(report["summary"]["failure_count"], 1)
        self.assertEqual(report["summary"]["session_count_with_failures"], 1)
        self.assertEqual(report["summary"]["by_type"]["checkpoint_mismatch"], 1)
        self.assertEqual(report["summary"]["by_severity"]["high"], 1)
        self.assertEqual(report["sessions"][0]["session_id"], "s-001")
        self.assertEqual(report["grouped_failures"][0]["count"], 1)
        self.assertEqual(report["grouped_failures"][0]["failure_type"], "checkpoint_mismatch")


class ReplayFailurePersistenceTests(unittest.TestCase):
    def test_failure_record_is_persisted_with_expected_observed_and_evidence(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "test.db")
            con = connect(db_path)
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
                log_dir="/tmp/replay-audit",
                target_host="legacy.example",
                target_user="recital",
                target_command="",
                mode="strict-global",
            )
            add_run_failure(
                con,
                run_id,
                build_failure_record(
                    session_id="s-002",
                    seq_global=21,
                    seq_session=8,
                    event_type="checkpoint",
                    failure_type="checkpoint_mismatch",
                    severity="high",
                    expected_value="SIG:MENU",
                    observed_value="SIG:ERRO",
                    message="checkpoint mismatch session=s-002",
                    evidence={"screen_state": "CONSULTA", "expected_screen": "MENU"},
                ),
            )

            row = con.execute(
                """
                SELECT session_id, seq_global, seq_session, failure_type, severity,
                       expected_value, observed_value, message, evidence_json
                FROM replay_failures
                WHERE run_id=?
                """,
                (run_id,),
            ).fetchone()
            con.close()

        self.assertEqual(row["session_id"], "s-002")
        self.assertEqual(row["seq_global"], 21)
        self.assertEqual(row["seq_session"], 8)
        self.assertEqual(row["failure_type"], "checkpoint_mismatch")
        self.assertEqual(row["severity"], "high")
        self.assertEqual(row["expected_value"], "SIG:MENU")
        self.assertEqual(row["observed_value"], "SIG:ERRO")
        self.assertEqual(json.loads(row["evidence_json"])["screen_state"], "CONSULTA")


if __name__ == "__main__":
    unittest.main(verbosity=2)

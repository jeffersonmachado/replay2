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
from urllib.error import HTTPError
from urllib.request import Request, build_opener, HTTPCookieProcessor

ROOT = Path(__file__).resolve().parents[1]
GATEWAY_DIR = ROOT / "gateway"
sys.path.insert(0, str(GATEWAY_DIR))

import dakota_gateway.auth as auth
from dakota_gateway.replay_control import evaluate_checkpoint_match
from dakota_gateway.state_db import connect, init_db, now_ms

CONTROL_SERVER_PATH = GATEWAY_DIR / "control" / "server.py"
SPEC = importlib.util.spec_from_file_location("control_server", CONTROL_SERVER_PATH)
CONTROL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CONTROL)


class TargetAndProfileApiTests(unittest.TestCase):
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
        self._request("POST", "/api/login", {"username": "admin", "password": "admin123"})

    def tearDown(self):
        if hasattr(self, "server"):
            self.server.shutdown()
            self.server.server_close()
        self.tmpdir.cleanup()

    def _request(self, method: str, path: str, data: dict | None = None):
        url = f"http://127.0.0.1:{self.port}{path}"
        body = None if data is None else json.dumps(data).encode("utf-8")
        req = Request(url, data=body, headers={"Content-Type": "application/json"}, method=method)
        try:
            with self.opener.open(req, timeout=5) as resp:
                raw = resp.read().decode("utf-8")
                return resp.status, json.loads(raw) if raw else {}
        except HTTPError as exc:
            raw = exc.read().decode("utf-8")
            return exc.code, json.loads(raw) if raw else {}

    def _write_capture_log(self, log_dir: Path, entries: list[dict]) -> None:
        log_dir.mkdir(parents=True, exist_ok=True)
        (log_dir / "audit-20260330.part001.jsonl").write_text(
            "\n".join(json.dumps(item, ensure_ascii=False) for item in entries),
            encoding="utf-8",
        )

    def test_run_creation_can_resolve_target_environment_and_connection_profile(self):
        status, target_payload = self._request(
            "POST",
            "/api/targets",
            {
                "env_id": "recital24-hml",
                "name": "Recital 24 HML",
                "host": "recital24.example",
                "platform": "linux",
                "transport_hint": "ssh",
                "port": 2222,
            },
        )
        self.assertEqual(status, 200)

        status, profile_payload = self._request(
            "POST",
            "/api/connection-profiles",
            {
                "profile_id": "ssh-batch",
                "name": "SSH Batch",
                "transport": "ssh",
                "username": "replay",
                "port": 2200,
                "command": "recital24-shell",
                "credential_ref": "env:RECITAL24_SSH_KEY",
            },
        )
        self.assertEqual(status, 200)

        status, run_payload = self._request(
            "POST",
            "/api/runs",
            {
                "log_dir": "/tmp/replay-audit",
                "target_env_id": target_payload["id"],
                "connection_profile_id": profile_payload["id"],
                "mode": "strict-global",
                "params": {
                    "match_mode": "fuzzy",
                    "match_threshold": 0.8,
                },
            },
        )
        self.assertEqual(status, 200)

        status, detail = self._request("GET", f"/api/runs/{run_payload['id']}")
        self.assertEqual(status, 200)
        run = detail["run"]
        params = json.loads(run["params_json"] or "{}")

        self.assertEqual(run["target_env_id"], target_payload["id"])
        self.assertEqual(run["connection_profile_id"], profile_payload["id"])
        self.assertEqual(run["target_host"], "recital24.example")
        self.assertEqual(run["target_user"], "replay")
        self.assertEqual(run["target_command"], "recital24-shell")
        self.assertEqual(params["transport"], "ssh")
        self.assertEqual(params["target_port"], 2200)
        self.assertEqual(params["target_environment"], "recital24-hml")
        self.assertEqual(params["environment"], "Recital 24 HML")
        self.assertEqual(params["credential_ref"], "env:RECITAL24_SSH_KEY")
        self.assertEqual(params["match_mode"], "fuzzy")

    def test_list_targets_and_profiles_returns_registered_items(self):
        self._request(
            "POST",
            "/api/targets",
            {"name": "Recital 24 PRD", "host": "prd.example", "transport_hint": "ssh"},
        )
        self._request(
            "POST",
            "/api/connection-profiles",
            {"name": "Telnet Legacy", "transport": "telnet", "port": 23},
        )

        status, targets = self._request("GET", "/api/targets")
        self.assertEqual(status, 200)
        self.assertEqual(len(targets["targets"]), 1)
        self.assertEqual(targets["targets"][0]["host"], "prd.example")

        status, profiles = self._request("GET", "/api/connection-profiles")
        self.assertEqual(status, 200)
        self.assertEqual(len(profiles["connection_profiles"]), 1)
        self.assertEqual(profiles["connection_profiles"][0]["transport"], "telnet")

    def test_target_policy_fields_are_persisted_and_listed(self):
        status, payload = self._request(
            "POST",
            "/api/targets",
            {
                "name": "Recital 24 Controlado",
                "host": "controlado.example",
                "transport_hint": "ssh",
                "gateway_required": True,
                "direct_ssh_policy": "gateway_only",
                "capture_start_mode": "login_required",
                "capture_compliance_mode": "strict",
                "allow_admin_direct_access": True,
            },
        )
        self.assertEqual(status, 200)

        status, targets = self._request("GET", f"/api/targets/{payload['id']}")
        self.assertEqual(status, 200)
        target = targets["target"]
        self.assertTrue(target["gateway_required"])
        self.assertEqual(target["direct_ssh_policy"], "gateway_only")
        self.assertEqual(target["capture_start_mode"], "login_required")
        self.assertEqual(target["capture_compliance_mode"], "strict")
        self.assertTrue(target["allow_admin_direct_access"])

    def test_gateway_required_target_marks_direct_session_non_compliant_and_blocks_run_start(self):
        log_dir = Path(self.tmpdir.name) / "capture-direct"
        self._write_capture_log(
            log_dir,
            [
                {
                    "ts_ms": 1000,
                    "type": "session_start",
                    "session_id": "sess-direct",
                    "actor": "operator",
                    "seq_global": 1,
                    "seq_session": 1,
                    "entry_mode": "direct_ssh",
                    "via_gateway": False,
                    "gateway_session_id": "",
                    "gateway_endpoint": "",
                    "source_host": "legacy.example",
                    "source_user": "legacy",
                    "source_command": "legacy-shell",
                },
                {
                    "ts_ms": 1100,
                    "type": "bytes",
                    "session_id": "sess-direct",
                    "actor": "operator",
                    "seq_global": 2,
                    "seq_session": 2,
                    "dir": "out",
                    "n": 4,
                    "data_b64": "TUVOVQ==",
                },
                {
                    "ts_ms": 1200,
                    "type": "session_end",
                    "session_id": "sess-direct",
                    "actor": "operator",
                    "seq_global": 3,
                    "seq_session": 3,
                },
            ],
        )

        status, target_payload = self._request(
            "POST",
            "/api/targets",
            {
                "name": "Target Gateway Only",
                "host": "legacy.example",
                "transport_hint": "ssh",
                "gateway_required": True,
                "direct_ssh_policy": "gateway_only",
                "capture_start_mode": "login_required",
                "capture_compliance_mode": "strict",
            },
        )
        self.assertEqual(status, 200)

        status, run_payload = self._request(
            "POST",
            "/api/runs",
            {
                "log_dir": str(log_dir),
                "target_env_id": target_payload["id"],
                "mode": "strict-global",
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(run_payload["compliance_status"], "rejected")

        status, detail = self._request("GET", f"/api/runs/{run_payload['id']}")
        self.assertEqual(status, 200)
        run = detail["run"]
        self.assertEqual(run["compliance_status"], "rejected")
        self.assertEqual(run["entry_mode"], "direct_ssh")
        self.assertFalse(bool(run["via_gateway"]))
        self.assertIn("sessão", run["compliance_reason"])

        status, start_payload = self._request("POST", f"/api/runs/{run_payload['id']}/start", {})
        self.assertEqual(status, 409)
        self.assertEqual(start_payload["compliance_status"], "rejected")

    def test_warn_policy_keeps_run_but_flags_warning(self):
        log_dir = Path(self.tmpdir.name) / "capture-warn"
        self._write_capture_log(
            log_dir,
            [
                {
                    "ts_ms": 2000,
                    "type": "session_start",
                    "session_id": "sess-warn",
                    "actor": "operator",
                    "seq_global": 1,
                    "seq_session": 1,
                    "entry_mode": "direct_ssh",
                    "via_gateway": False,
                },
                {
                    "ts_ms": 2100,
                    "type": "session_end",
                    "session_id": "sess-warn",
                    "actor": "operator",
                    "seq_global": 2,
                    "seq_session": 2,
                },
            ],
        )
        status, target_payload = self._request(
            "POST",
            "/api/targets",
            {
                "name": "Target Warn",
                "host": "warn.example",
                "transport_hint": "ssh",
                "gateway_required": True,
                "direct_ssh_policy": "unrestricted",
                "capture_start_mode": "login_required",
                "capture_compliance_mode": "warn",
            },
        )
        self.assertEqual(status, 200)

        status, run_payload = self._request(
            "POST",
            "/api/runs",
            {
                "log_dir": str(log_dir),
                "target_env_id": target_payload["id"],
                "mode": "strict-global",
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(run_payload["compliance_status"], "warning")

    def test_session_compliance_endpoint_reports_gateway_evidence(self):
        log_dir = Path(self.tmpdir.name) / "capture-gateway"
        self._write_capture_log(
            log_dir,
            [
                {
                    "ts_ms": 3000,
                    "type": "session_start",
                    "session_id": "sess-gateway",
                    "actor": "operator",
                    "seq_global": 1,
                    "seq_session": 1,
                    "entry_mode": "gateway_ssh",
                    "via_gateway": True,
                    "gateway_session_id": "sess-gateway",
                    "gateway_endpoint": "gw.example",
                    "source_host": "legacy.example",
                    "source_user": "legacy",
                    "source_command": "",
                },
                {
                    "ts_ms": 3100,
                    "type": "bytes",
                    "session_id": "sess-gateway",
                    "actor": "operator",
                    "seq_global": 2,
                    "seq_session": 2,
                    "dir": "out",
                    "n": 6,
                    "data_b64": "bG9naW46",
                },
                {
                    "ts_ms": 3200,
                    "type": "session_end",
                    "session_id": "sess-gateway",
                    "actor": "operator",
                    "seq_global": 3,
                    "seq_session": 3,
                },
            ],
        )
        status, target_payload = self._request(
            "POST",
            "/api/targets",
            {
                "name": "Target Sessao",
                "host": "legacy.example",
                "transport_hint": "ssh",
                "gateway_required": True,
                "direct_ssh_policy": "unrestricted",
                "capture_start_mode": "login_required",
                "capture_compliance_mode": "strict",
            },
        )
        self.assertEqual(status, 200)

        status, sessions = self._request(
            "GET",
            f"/api/gateway/sessions?log_dir={log_dir}&target_env_id={target_payload['id']}",
        )
        self.assertEqual(status, 200)
        self.assertEqual(sessions["sessions"][0]["compliance_status"], "compliant")
        self.assertTrue(sessions["sessions"][0]["via_gateway"])

        status, compliance = self._request(
            "GET",
            f"/api/gateway/sessions/sess-gateway/compliance?log_dir={log_dir}&target_env_id={target_payload['id']}",
        )
        self.assertEqual(status, 200)
        self.assertEqual(compliance["compliance"]["compliance_status"], "compliant")
        self.assertEqual(compliance["compliance"]["gateway_endpoint"], "gw.example")


class ReplayMatchingTests(unittest.TestCase):
    def test_evaluate_checkpoint_match_supports_contains_regex_and_fuzzy(self):
        contains_match = evaluate_checkpoint_match(
            "tit=menu",
            "L=8;W=40;TIT=MENU PRINCIPAL;LBL=Opcao:",
            {"match_mode": "contains", "match_ignore_case": True},
        )
        regex_match = evaluate_checkpoint_match(
            r"TIT=MENU.*LBL=Opcao:",
            "L=8;W=40;TIT=MENU PRINCIPAL;LBL=Opcao:",
            {"match_mode": "regex"},
        )
        fuzzy_match = evaluate_checkpoint_match(
            "L=8;W=40;TIT=MENU PRINCIPAL;LBL=Opcao:",
            "L=8;W=40;TIT=MENU PRINCIPA1;LBL=Opcao:",
            {"match_mode": "fuzzy", "match_threshold": 0.9},
        )

        self.assertTrue(contains_match["matched"])
        self.assertTrue(regex_match["matched"])
        self.assertTrue(fuzzy_match["matched"])
        self.assertGreaterEqual(fuzzy_match["similarity"], 0.9)


if __name__ == "__main__":
    unittest.main(verbosity=2)

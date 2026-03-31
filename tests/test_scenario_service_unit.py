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

from control.services.scenario_service import (
    instantiate_run_from_scenario,
    list_analytics_scenarios,
    list_operational_scenarios,
    save_analytics_scenario,
    save_operational_scenario,
)
from dakota_gateway.state_db import connect, init_db, now_ms


class ScenarioServiceUnitTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tmpdir.name) / "scenario.db")
        self.con = connect(self.db_path)
        init_db(self.con)
        self.con.execute(
            "INSERT INTO users(username,password_hash,role,created_at_ms) VALUES(?,?,?,?)",
            ("admin", "x", "admin", now_ms()),
        )
        self.user_id = int(self.con.execute("SELECT id FROM users WHERE username='admin'").fetchone()["id"])

    def tearDown(self):
        self.con.close()
        self.tmpdir.cleanup()

    def test_save_and_list_analytics_scenario_normalizes_filters_and_tags(self):
        scenario_id = save_analytics_scenario(
            self.con,
            name="Ultima Semana HML",
            description="recorte de regressao",
            visibility="shared",
            tags=["hml", "regressao", "hml"],
            filters={"environment": "HML", "run_limit": 999},
            created_by=self.user_id,
        )
        self.assertGreater(scenario_id, 0)

        rows = list_analytics_scenarios(self.con, user_id=self.user_id, visibility="shared", tag="regre")
        self.assertEqual(len(rows), 1)
        item = rows[0]
        self.assertEqual(item["name"], "Ultima Semana HML")
        self.assertEqual(item["filters"]["environment"], "HML")
        self.assertEqual(item["filters"]["run_limit"], 200)
        self.assertEqual(item["tags"], ["hml", "regressao"])

    def test_save_operational_scenario_lists_usage_and_instantiates_run(self):
        scenario_id = save_operational_scenario(
            self.con,
            payload={
                "name": "Replay Critico",
                "scenario_type": "replay",
                "log_dir": "/tmp/audit",
                "target_host": "recital24.example",
                "target_user": "replay",
                "mode": "strict-global",
                "tags": ["core", "migracao"],
                "params": {"match_mode": "strict"},
            },
            created_by=self.user_id,
        )
        self.assertGreater(scenario_id, 0)

        run_id = instantiate_run_from_scenario(self.con, scenario_id, self.user_id)
        self.assertGreater(run_id, 0)

        run_row = self.con.execute("SELECT params_json FROM replay_runs WHERE id=?", (run_id,)).fetchone()
        params = json.loads(run_row["params_json"] or "{}")
        self.assertEqual(params["scenario_id"], scenario_id)
        self.assertEqual(params["scenario_name"], "Replay Critico")
        self.assertEqual(params["match_mode"], "strict")

        items = list_operational_scenarios(self.con, user_id=self.user_id, usage_user="admin")
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual(item["name"], "Replay Critico")
        self.assertEqual(item["usage_summary"]["total_runs"], 1)
        self.assertEqual(item["tags"], ["core", "migracao"])

    def test_operational_scenario_can_resolve_target_and_profile_ids(self):
        ts = now_ms()
        env_id = self.con.execute(
            """
            INSERT INTO target_environments(
              env_id, name, host, port, platform, transport_hint,
              gateway_required, direct_ssh_policy, capture_start_mode,
              capture_compliance_mode, allow_admin_direct_access,
              description, metadata_json, created_at_ms, updated_at_ms
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "env-hml",
                "HML",
                "recital24-hml.example",
                22,
                "linux",
                "ssh",
                1,
                "forbidden",
                "session_start_required",
                "warn",
                0,
                "ambiente hml",
                "{}",
                ts,
                ts,
            ),
        ).lastrowid
        profile_id = self.con.execute(
            """
            INSERT INTO connection_profiles(
              profile_id, name, transport, username, port, command,
              credential_ref, auth_mode, options_json, created_at_ms, updated_at_ms
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "perfil-hml",
                "Perfil HML",
                "ssh",
                "replay",
                22,
                "recital24-shell",
                "",
                "external",
                "{}",
                ts,
                ts,
            ),
        ).lastrowid

        scenario_id = save_operational_scenario(
            self.con,
            payload={
                "name": "Replay HML por IDs",
                "scenario_type": "replay",
                "log_dir": "/tmp/audit-hml",
                "target_env_id": int(env_id),
                "connection_profile_id": int(profile_id),
                "target_host": "",
                "target_user": "",
                "target_command": "",
                "mode": "strict-global",
                "params": {"match_mode": "contains"},
            },
            created_by=self.user_id,
        )
        run_id = instantiate_run_from_scenario(self.con, scenario_id, self.user_id)

        run = self.con.execute(
            "SELECT target_env_id, connection_profile_id, target_host, target_user, target_command, params_json FROM replay_runs WHERE id=?",
            (run_id,),
        ).fetchone()
        self.assertEqual(int(run["target_env_id"]), int(env_id))
        self.assertEqual(int(run["connection_profile_id"]), int(profile_id))
        self.assertEqual(run["target_host"], "recital24-hml.example")
        self.assertEqual(run["target_user"], "replay")
        self.assertEqual(run["target_command"], "recital24-shell")

        params = json.loads(run["params_json"] or "{}")
        self.assertEqual(params.get("scenario_name"), "Replay HML por IDs")
        self.assertEqual(params.get("connection_profile_name"), "Perfil HML")


if __name__ == "__main__":
    unittest.main()

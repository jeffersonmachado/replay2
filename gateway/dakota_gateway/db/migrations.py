from __future__ import annotations

import sqlite3
import time

from .schema import SCHEMA_SQL


def _now_ms() -> int:
    return int(time.time() * 1000)


def init_db(con: sqlite3.Connection) -> None:
    con.executescript(SCHEMA_SQL)
    # Ensure gateway_state singleton row exists
    con.execute(
        "INSERT OR IGNORE INTO gateway_state(id, active, updated_at_ms) VALUES(1, 0, ?)",
        (_now_ms(),),
    )

    cols = {row["name"] for row in con.execute("PRAGMA table_info(replay_runs)").fetchall()}
    if "target_env_id" not in cols:
        con.execute("ALTER TABLE replay_runs ADD COLUMN target_env_id INTEGER REFERENCES target_environments(id)")
    if "connection_profile_id" not in cols:
        con.execute("ALTER TABLE replay_runs ADD COLUMN connection_profile_id INTEGER REFERENCES connection_profiles(id)")
    if "params_json" not in cols:
        con.execute("ALTER TABLE replay_runs ADD COLUMN params_json TEXT")
    if "metrics_json" not in cols:
        con.execute("ALTER TABLE replay_runs ADD COLUMN metrics_json TEXT")
    if "entry_mode" not in cols:
        con.execute("ALTER TABLE replay_runs ADD COLUMN entry_mode TEXT")
    if "via_gateway" not in cols:
        con.execute("ALTER TABLE replay_runs ADD COLUMN via_gateway INTEGER NOT NULL DEFAULT 0")
    if "gateway_session_id" not in cols:
        con.execute("ALTER TABLE replay_runs ADD COLUMN gateway_session_id TEXT")
    if "gateway_endpoint" not in cols:
        con.execute("ALTER TABLE replay_runs ADD COLUMN gateway_endpoint TEXT")
    if "compliance_status" not in cols:
        con.execute("ALTER TABLE replay_runs ADD COLUMN compliance_status TEXT NOT NULL DEFAULT 'not_applicable'")
    if "compliance_reason" not in cols:
        con.execute("ALTER TABLE replay_runs ADD COLUMN compliance_reason TEXT")
    if "validated_at_ms" not in cols:
        con.execute("ALTER TABLE replay_runs ADD COLUMN validated_at_ms INTEGER")

    target_cols = {row["name"] for row in con.execute("PRAGMA table_info(target_environments)").fetchall()}
    if target_cols:
        if "gateway_required" not in target_cols:
            con.execute("ALTER TABLE target_environments ADD COLUMN gateway_required INTEGER NOT NULL DEFAULT 0")
        if "direct_ssh_policy" not in target_cols:
            con.execute("ALTER TABLE target_environments ADD COLUMN direct_ssh_policy TEXT NOT NULL DEFAULT 'unrestricted'")
        if "capture_start_mode" not in target_cols:
            con.execute("ALTER TABLE target_environments ADD COLUMN capture_start_mode TEXT NOT NULL DEFAULT 'session_start_required'")
        if "capture_compliance_mode" not in target_cols:
            con.execute("ALTER TABLE target_environments ADD COLUMN capture_compliance_mode TEXT NOT NULL DEFAULT 'off'")
        if "allow_admin_direct_access" not in target_cols:
            con.execute("ALTER TABLE target_environments ADD COLUMN allow_admin_direct_access INTEGER NOT NULL DEFAULT 0")

    scenario_cols = {row["name"] for row in con.execute("PRAGMA table_info(analytics_scenarios)").fetchall()}
    if scenario_cols:
        if "visibility" not in scenario_cols:
            con.execute("ALTER TABLE analytics_scenarios ADD COLUMN visibility TEXT NOT NULL DEFAULT 'private'")
        if "tags_csv" not in scenario_cols:
            con.execute("ALTER TABLE analytics_scenarios ADD COLUMN tags_csv TEXT")

    operational_cols = {row["name"] for row in con.execute("PRAGMA table_info(operational_scenarios)").fetchall()}
    if operational_cols:
        if "target_env_id" not in operational_cols:
            con.execute("ALTER TABLE operational_scenarios ADD COLUMN target_env_id INTEGER REFERENCES target_environments(id)")
        if "connection_profile_id" not in operational_cols:
            con.execute("ALTER TABLE operational_scenarios ADD COLUMN connection_profile_id INTEGER REFERENCES connection_profiles(id)")
        if "squad" not in operational_cols:
            con.execute("ALTER TABLE operational_scenarios ADD COLUMN squad TEXT")
        if "area" not in operational_cols:
            con.execute("ALTER TABLE operational_scenarios ADD COLUMN area TEXT")
        if "tags_csv" not in operational_cols:
            con.execute("ALTER TABLE operational_scenarios ADD COLUMN tags_csv TEXT")
        if "owner_name" not in operational_cols:
            con.execute("ALTER TABLE operational_scenarios ADD COLUMN owner_name TEXT")
        if "owner_contact" not in operational_cols:
            con.execute("ALTER TABLE operational_scenarios ADD COLUMN owner_contact TEXT")
        if "sla_max_failure_rate_pct" not in operational_cols:
            con.execute("ALTER TABLE operational_scenarios ADD COLUMN sla_max_failure_rate_pct REAL")
        if "sla_max_criticality_score" not in operational_cols:
            con.execute("ALTER TABLE operational_scenarios ADD COLUMN sla_max_criticality_score REAL")

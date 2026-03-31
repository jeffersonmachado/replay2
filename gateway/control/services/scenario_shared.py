from __future__ import annotations

import json
import sqlite3


def extract_environment(value: sqlite3.Row | dict | None) -> str:
    if not value:
        return ""
    if isinstance(value, sqlite3.Row):
        params_raw = value["params_json"] if "params_json" in value.keys() else None
        target_host = value["target_host"] if "target_host" in value.keys() else ""
    else:
        params_raw = value.get("params_json")
        target_host = value.get("target_host") or ""
    if params_raw:
        try:
            params = json.loads(params_raw) if isinstance(params_raw, str) else params_raw
        except Exception:
            params = {}
        if isinstance(params, dict):
            for key in ("environment", "env", "target_env", "target_environment"):
                resolved = str(params.get(key) or "").strip()
                if resolved:
                    return resolved
    return str(target_host or "").strip()


def normalize_scenario_tags(tags) -> list[str]:
    if isinstance(tags, str):
        parts = tags.split(",")
    elif isinstance(tags, list):
        parts = tags
    else:
        parts = []
    out = []
    for part in parts:
        clean = str(part or "").strip()
        if clean and clean not in out:
            out.append(clean)
    return out[:12]


def normalize_observability_filters(filters: dict | None) -> dict:
    raw = filters or {}
    return {
        "environment": str(raw.get("environment") or "").strip(),
        "created_from_ms": int(raw.get("created_from_ms") or 0),
        "created_to_ms": int(raw.get("created_to_ms") or 0),
        "run_limit": max(1, min(int(raw.get("run_limit") or 50), 200)),
        "log_dir": str(raw.get("log_dir") or "").strip(),
    }


def empty_usage_summary() -> dict:
    return {
        "total_runs": 0,
        "success_runs": 0,
        "failed_runs": 0,
        "cancelled_runs": 0,
        "active_runs": 0,
        "runs_with_failures": 0,
        "total_failure_events": 0,
        "failure_rate_pct": 0.0,
        "criticality_score": 0.0,
        "by_status": {},
        "severity_counts": {},
        "last_run_id": None,
        "last_status": None,
        "last_used_at_ms": 0,
        "last_used_by_username": None,
        "top_users": [],
    }

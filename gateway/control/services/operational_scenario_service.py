from __future__ import annotations

import json

from dakota_gateway.replay_control import add_run_event, create_run
from dakota_gateway.state_db import exec1, now_ms, query_all, query_one
from control.services.environment_service import resolve_run_target_request
from control.services.scenario_shared import empty_usage_summary, extract_environment, normalize_scenario_tags


def normalize_operational_scenario_payload(payload: dict | None) -> dict:
    raw = payload or {}
    scenario_type = str(raw.get("scenario_type") or "replay").strip().lower()
    if scenario_type not in {"replay", "stress"}:
        raise ValueError("scenario_type inválido")
    mode = str(raw.get("mode") or "strict-global").strip()
    if mode not in {"strict-global", "parallel-sessions"}:
        raise ValueError("mode inválido")

    def _as_optional_pct(value):
        text = str(value or "").strip()
        if not text:
            return None
        number = float(text)
        if number < 0 or number > 100:
            raise ValueError("sla_max_failure_rate_pct inválido")
        return round(number, 1)

    def _as_optional_score(value):
        text = str(value or "").strip()
        if not text:
            return None
        number = float(text)
        if number < 0 or number > 100:
            raise ValueError("sla_max_criticality_score inválido")
        return round(number, 1)

    params = raw.get("params") if isinstance(raw.get("params"), dict) else {}
    return {
        "name": str(raw.get("name") or "").strip(),
        "description": str(raw.get("description") or "").strip(),
        "scenario_type": scenario_type,
        "squad": str(raw.get("squad") or "").strip(),
        "area": str(raw.get("area") or "").strip(),
        "tags": normalize_scenario_tags(raw.get("tags")),
        "owner_name": str(raw.get("owner_name") or "").strip(),
        "owner_contact": str(raw.get("owner_contact") or "").strip(),
        "sla_max_failure_rate_pct": _as_optional_pct(raw.get("sla_max_failure_rate_pct")),
        "sla_max_criticality_score": _as_optional_score(raw.get("sla_max_criticality_score")),
        "target_env_id": int(raw.get("target_env_id")) if raw.get("target_env_id") not in (None, "") else None,
        "connection_profile_id": int(raw.get("connection_profile_id")) if raw.get("connection_profile_id") not in (None, "") else None,
        "log_dir": str(raw.get("log_dir") or "").strip(),
        "target_host": str(raw.get("target_host") or "").strip(),
        "target_user": str(raw.get("target_user") or "").strip(),
        "target_command": str(raw.get("target_command") or "").strip(),
        "mode": mode,
        "params": params,
    }


def build_operational_sla_summary(item: dict) -> dict:
    usage = item.get("usage_summary") if isinstance(item, dict) else {}
    failure_rate = float((usage or {}).get("failure_rate_pct") or 0.0)
    criticality_score = float((usage or {}).get("criticality_score") or 0.0)
    max_failure_rate = item.get("sla_max_failure_rate_pct")
    max_criticality = item.get("sla_max_criticality_score")
    thresholds = {
        "max_failure_rate_pct": float(max_failure_rate) if max_failure_rate not in (None, "") else None,
        "max_criticality_score": float(max_criticality) if max_criticality not in (None, "") else None,
    }
    breaches = []
    warnings = []
    if thresholds["max_failure_rate_pct"] is not None:
        limit = float(thresholds["max_failure_rate_pct"])
        if failure_rate > limit:
            breaches.append(f"falha {round(failure_rate, 1)}% > {round(limit, 1)}%")
        elif limit > 0 and failure_rate >= limit * 0.8:
            warnings.append(f"falha perto do limite ({round(failure_rate, 1)}%/{round(limit, 1)}%)")
    if thresholds["max_criticality_score"] is not None:
        limit = float(thresholds["max_criticality_score"])
        if criticality_score > limit:
            breaches.append(f"criticidade {round(criticality_score, 1)} > {round(limit, 1)}")
        elif limit > 0 and criticality_score >= limit * 0.8:
            warnings.append(f"criticidade perto do limite ({round(criticality_score, 1)}/{round(limit, 1)})")
    status = "breached" if breaches else "warning" if warnings else "ok"
    return {"status": status, "breaches": breaches, "warnings": warnings, "thresholds": thresholds}


def summarize_operational_scenario_usage(
    con,
    *,
    usage_user: str = "",
    used_from_ms: int = 0,
    used_to_ms: int = 0,
) -> dict[int, dict]:
    failure_rows = query_all(con, "SELECT run_id, severity, COUNT(*) AS count FROM replay_failures GROUP BY run_id, severity", ())
    failure_by_run: dict[int, int] = {}
    severity_by_run: dict[int, dict[str, int]] = {}
    for row in failure_rows:
        run_id = int(row["run_id"])
        count = int(row["count"] or 0)
        failure_by_run[run_id] = int(failure_by_run.get(run_id, 0)) + count
        sev = str(row["severity"] or "low")
        severity_counts = severity_by_run.setdefault(run_id, {})
        severity_counts[sev] = int(severity_counts.get(sev, 0)) + count

    run_rows = query_all(
        con,
        """
        SELECT r.id, r.created_at_ms, r.status, r.params_json, u.username AS created_by_username
        FROM replay_runs r
        LEFT JOIN users u ON u.id = r.created_by
        ORDER BY r.created_at_ms DESC, r.id DESC
        """,
        (),
    )
    usage: dict[int, dict] = {}
    usage_user_filter = str(usage_user or "").strip().lower()
    used_from_ms = max(0, int(used_from_ms or 0))
    used_to_ms = max(0, int(used_to_ms or 0))
    for row in run_rows:
        created_at_ms = int(row["created_at_ms"] or 0)
        if used_from_ms and created_at_ms < used_from_ms:
            continue
        if used_to_ms and created_at_ms > used_to_ms:
            continue
        username = str(row["created_by_username"] or "sistema").strip() or "sistema"
        if usage_user_filter and usage_user_filter not in username.lower():
            continue
        try:
            params = json.loads(row["params_json"] or "{}")
        except Exception:
            params = {}
        if not isinstance(params, dict):
            continue
        try:
            scenario_id = int(params.get("scenario_id") or 0)
        except Exception:
            scenario_id = 0
        if scenario_id <= 0:
            continue
        item = usage.setdefault(scenario_id, empty_usage_summary())
        status = str(row["status"] or "queued")
        item["total_runs"] += 1
        item["by_status"][status] = int(item["by_status"].get(status, 0)) + 1
        if status == "success":
            item["success_runs"] += 1
        elif status == "failed":
            item["failed_runs"] += 1
        elif status == "cancelled":
            item["cancelled_runs"] += 1
        elif status in {"queued", "running", "paused"}:
            item["active_runs"] += 1
        failure_count = int(failure_by_run.get(int(row["id"]), 0))
        failure_severity_counts = severity_by_run.get(int(row["id"]), {})
        item["total_failure_events"] += failure_count
        for sev, sev_count in failure_severity_counts.items():
            item["severity_counts"][sev] = int(item["severity_counts"].get(sev, 0)) + int(sev_count)
        if failure_count > 0 or status in {"failed", "cancelled"}:
            item["runs_with_failures"] += 1
        if created_at_ms >= int(item["last_used_at_ms"] or 0):
            item["last_used_at_ms"] = created_at_ms
            item["last_run_id"] = int(row["id"])
            item["last_status"] = status
            item["last_used_by_username"] = username
        existing = next((user for user in item["top_users"] if user["username"] == username), None)
        if existing:
            existing["count"] += 1
        else:
            item["top_users"].append({"username": username, "count": 1})
    for item in usage.values():
        total_runs = int(item["total_runs"] or 0)
        item["top_users"] = sorted(item["top_users"], key=lambda user: (-int(user["count"]), user["username"]))[:3]
        item["failure_rate_pct"] = round((float(item["runs_with_failures"]) / total_runs) * 100.0, 1) if total_runs else 0.0
        severity_counts = item["severity_counts"]
        weighted_failures = (
            int(severity_counts.get("critical", 0)) * 12
            + int(severity_counts.get("high", 0)) * 6
            + int(severity_counts.get("medium", 0)) * 3
            + int(severity_counts.get("low", 0)) * 1
        )
        usage_factor = min(total_runs * 4, 30)
        failure_factor = min(float(item["failure_rate_pct"]) * 0.55, 55)
        activity_factor = min(int(item["active_runs"] or 0) * 4, 10)
        item["criticality_score"] = min(100.0, round(usage_factor + failure_factor + min(weighted_failures, 25) + activity_factor, 1))
    return usage


def list_operational_scenarios(
    con,
    *,
    user_id: int | None = None,
    scenario_type: str = "",
    environment: str = "",
    squad: str = "",
    area: str = "",
    tag: str = "",
    owner: str = "",
    sla_status: str = "",
    favorite_only: bool = False,
    usage_user: str = "",
    used_from_ms: int = 0,
    used_to_ms: int = 0,
    sort_by: str = "updated",
) -> list[dict]:
    rows = query_all(
        con,
        """
        SELECT s.*, u.username AS created_by_username,
               CASE WHEN f.user_id IS NULL THEN 0 ELSE 1 END AS is_favorite
        FROM operational_scenarios s
        LEFT JOIN users u ON u.id = s.created_by
        LEFT JOIN operational_scenario_favorites f ON f.scenario_id = s.id AND f.user_id = ?
        ORDER BY is_favorite DESC, s.updated_at_ms DESC, s.id DESC
        """,
        (user_id,),
    )
    out = []
    usage_by_scenario = summarize_operational_scenario_usage(
        con,
        usage_user=usage_user,
        used_from_ms=used_from_ms,
        used_to_ms=used_to_ms,
    )
    type_filter = str(scenario_type or "").strip().lower()
    environment_filter = str(environment or "").strip().lower()
    squad_filter = str(squad or "").strip().lower()
    area_filter = str(area or "").strip().lower()
    tag_filter = str(tag or "").strip().lower()
    owner_filter = str(owner or "").strip().lower()
    sla_status_filter = str(sla_status or "").strip().lower()
    usage_filter_active = bool(str(usage_user or "").strip() or int(used_from_ms or 0) or int(used_to_ms or 0))
    for row in rows:
        item = dict(row)
        try:
            item["params"] = json.loads(item.pop("params_json") or "{}")
        except Exception:
            item["params"] = {}
        item["tags"] = normalize_scenario_tags(item.pop("tags_csv") or "")
        item["is_favorite"] = bool(int(item.get("is_favorite") or 0))
        item["environment"] = extract_environment(item)
        item["sla"] = {
            "max_failure_rate_pct": item.get("sla_max_failure_rate_pct"),
            "max_criticality_score": item.get("sla_max_criticality_score"),
        }
        item["usage_summary"] = usage_by_scenario.get(int(item["id"]), empty_usage_summary())
        item["sla_summary"] = build_operational_sla_summary(item)
        if type_filter and str(item.get("scenario_type") or "").lower() != type_filter:
            continue
        if environment_filter and environment_filter not in str(item.get("environment") or "").lower():
            continue
        if squad_filter and squad_filter not in str(item.get("squad") or "").lower():
            continue
        if area_filter and area_filter not in str(item.get("area") or "").lower():
            continue
        if tag_filter and not any(tag_filter in str(tag_name).lower() for tag_name in item["tags"]):
            continue
        if owner_filter and owner_filter not in str(item.get("owner_name") or "").lower():
            continue
        if sla_status_filter and str(item["sla_summary"]["status"]).lower() != sla_status_filter:
            continue
        if favorite_only and not item["is_favorite"]:
            continue
        if usage_filter_active and int(item["usage_summary"]["total_runs"] or 0) <= 0:
            continue
        out.append(item)
    sort_key = str(sort_by or "updated").strip().lower()
    if sort_key == "most-used":
        out.sort(key=lambda item: (-int(item["is_favorite"]), -int(item["usage_summary"]["total_runs"] or 0), -int(item["usage_summary"]["last_used_at_ms"] or 0), str(item.get("name") or "")))
    elif sort_key == "highest-failure-rate":
        out.sort(key=lambda item: (-int(item["is_favorite"]), -float(item["usage_summary"]["failure_rate_pct"] or 0.0), -float(item["usage_summary"]["criticality_score"] or 0.0), -int(item["usage_summary"]["total_runs"] or 0), str(item.get("name") or "")))
    elif sort_key == "criticality":
        out.sort(key=lambda item: (-int(item["is_favorite"]), -float(item["usage_summary"]["criticality_score"] or 0.0), -float(item["usage_summary"]["failure_rate_pct"] or 0.0), -int(item["usage_summary"]["total_runs"] or 0), str(item.get("name") or "")))
    elif sort_key == "recent-use":
        out.sort(key=lambda item: (-int(item["is_favorite"]), -int(item["usage_summary"]["last_used_at_ms"] or 0), -int(item.get("updated_at_ms") or 0), str(item.get("name") or "")))
    elif sort_key == "name":
        out.sort(key=lambda item: (-int(item["is_favorite"]), str(item.get("name") or "").lower()))
    return out


def set_operational_scenario_favorite(con, scenario_id: int, user_id: int, favorite: bool) -> bool:
    if favorite:
        con.execute(
            """
            INSERT OR IGNORE INTO operational_scenario_favorites(scenario_id, user_id, created_at_ms)
            VALUES(?,?,?)
            """,
            (int(scenario_id), int(user_id), now_ms()),
        )
        return True
    cur = con.execute(
        "DELETE FROM operational_scenario_favorites WHERE scenario_id=? AND user_id=?",
        (int(scenario_id), int(user_id)),
    )
    return int(cur.rowcount or 0) > 0


def save_operational_scenario(con, *, payload: dict, created_by: int | None = None) -> int:
    clean = normalize_operational_scenario_payload(payload)
    if not clean["name"]:
        raise ValueError("nome do cenário operacional não informado")
    ts = now_ms()
    existing = query_one(con, "SELECT id FROM operational_scenarios WHERE name=?", (clean["name"],))
    if existing:
        con.execute(
            """
            UPDATE operational_scenarios
            SET description=?, scenario_type=?, squad=?, area=?, tags_csv=?, owner_name=?, owner_contact=?, sla_max_failure_rate_pct=?, sla_max_criticality_score=?, target_env_id=?, connection_profile_id=?, log_dir=?, target_host=?, target_user=?, target_command=?, mode=?, params_json=?, updated_at_ms=?
            WHERE id=?
            """,
            (
                clean["description"] or None,
                clean["scenario_type"],
                clean["squad"] or None,
                clean["area"] or None,
                ",".join(clean["tags"]) or None,
                clean["owner_name"] or None,
                clean["owner_contact"] or None,
                clean["sla_max_failure_rate_pct"],
                clean["sla_max_criticality_score"],
                clean["target_env_id"],
                clean["connection_profile_id"],
                clean["log_dir"],
                clean["target_host"],
                clean["target_user"],
                clean["target_command"],
                clean["mode"],
                json.dumps(clean["params"], ensure_ascii=False),
                ts,
                int(existing["id"]),
            ),
        )
        return int(existing["id"])
    return exec1(
        con,
        """
        INSERT INTO operational_scenarios(
                    name, description, scenario_type, squad, area, tags_csv, owner_name, owner_contact, sla_max_failure_rate_pct, sla_max_criticality_score, target_env_id, connection_profile_id, log_dir, target_host, target_user, target_command, mode, params_json, created_by, created_at_ms, updated_at_ms
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            clean["name"],
            clean["description"] or None,
            clean["scenario_type"],
            clean["squad"] or None,
            clean["area"] or None,
            ",".join(clean["tags"]) or None,
            clean["owner_name"] or None,
            clean["owner_contact"] or None,
            clean["sla_max_failure_rate_pct"],
            clean["sla_max_criticality_score"],
            clean["target_env_id"],
            clean["connection_profile_id"],
            clean["log_dir"],
            clean["target_host"],
            clean["target_user"],
            clean["target_command"],
            clean["mode"],
            json.dumps(clean["params"], ensure_ascii=False),
            created_by,
            ts,
            ts,
        ),
    )


def delete_operational_scenario(con, scenario_id: int) -> bool:
    cur = con.execute("DELETE FROM operational_scenarios WHERE id=?", (int(scenario_id),))
    return int(cur.rowcount or 0) > 0


def instantiate_run_from_scenario(con, scenario_id: int, created_by: int) -> int:
    row = query_one(con, "SELECT * FROM operational_scenarios WHERE id=?", (int(scenario_id),))
    if not row:
        raise ValueError("cenário operacional inexistente")
    try:
        params = json.loads(row["params_json"] or "{}")
    except Exception:
        params = {}

    resolved_target, resolved_params = resolve_run_target_request(
        con,
        {
            "target_env_id": row["target_env_id"] if "target_env_id" in row.keys() else None,
            "connection_profile_id": row["connection_profile_id"] if "connection_profile_id" in row.keys() else None,
            "target_host": row["target_host"],
            "target_user": row["target_user"],
            "target_command": row["target_command"],
            "params": params,
        },
    )
    rid = create_run(
        con,
        created_by=created_by,
        log_dir=row["log_dir"],
        target_host=resolved_target["target_host"],
        target_user=resolved_target["target_user"],
        target_command=resolved_target["target_command"],
        mode=row["mode"],
        target_env_id=resolved_target.get("target_env_id"),
        connection_profile_id=resolved_target.get("connection_profile_id"),
    )
    scenario_params = {
        **(resolved_params if isinstance(resolved_params, dict) else {}),
        "scenario_id": int(row["id"]),
        "scenario_name": row["name"],
        "scenario_type": row["scenario_type"],
    }
    if scenario_params:
        con.execute("UPDATE replay_runs SET params_json=? WHERE id=?", (json.dumps(scenario_params, ensure_ascii=False), rid))
    add_run_event(con, rid, "scenario", "run criada a partir de cenário operacional", {"scenario_id": int(row["id"]), "scenario_name": row["name"]})
    return rid

from __future__ import annotations

from control.services.gateway_observability_service import read_gateway_monitor
from control.services.operational_scenario_service import list_operational_scenarios
from control.services.report_common import extract_run_environment
from control.services.report_run_service import (
    build_reprocess_trace,
    build_run_comparison,
    build_run_report,
)
from dakota_gateway.state_db import query_all, query_one
import sqlite3


def build_reprocess_analytics(con, limit: int = 100) -> dict:
    rows = query_all(
        con,
        """
        SELECT id, parent_run_id, status, created_at_ms
        FROM replay_runs
        WHERE parent_run_id IS NOT NULL
        ORDER BY created_at_ms DESC, id DESC
        LIMIT ?
        """,
        (max(1, min(int(limit or 100), 500)),),
    )
    traces = []
    by_flow: dict[str, dict] = {}
    by_environment: dict[str, dict] = {}
    by_signature: dict[str, dict] = {}
    pending_queue = []
    outcomes = {"resolved": 0, "repeated": 0, "not_repeated": 0, "inconclusive": 0}
    for row in rows:
        run_id = int(row["id"])
        trace = build_reprocess_trace(con, run_id)
        if not trace:
            continue
        traces.append(trace)
        outcome = str(trace.get("outcome") or "inconclusive")
        outcomes[outcome] = int(outcomes.get(outcome, 0)) + 1
        source_failure = trace.get("source_failure") or {}
        current_run = query_one(con, "SELECT * FROM replay_runs WHERE id=?", (run_id,))
        environment = extract_run_environment(current_run) or "sem_ambiente"
        flow_name = str(source_failure.get("flow_name") or "sem_fluxo")
        flow_bucket = by_flow.setdefault(flow_name, {"flow_name": flow_name, "attempts": 0, "resolved": 0, "repeated": 0, "not_repeated": 0, "inconclusive": 0})
        flow_bucket["attempts"] += 1
        flow_bucket[outcome] = int(flow_bucket.get(outcome, 0)) + 1
        env_bucket = by_environment.setdefault(environment, {"environment": environment, "attempts": 0, "resolved": 0, "repeated": 0, "not_repeated": 0, "inconclusive": 0})
        env_bucket["attempts"] += 1
        env_bucket[outcome] = int(env_bucket.get(outcome, 0)) + 1
        signature = "|".join([str(source_failure.get("failure_type") or ""), str(source_failure.get("severity") or ""), str(source_failure.get("expected_value") or ""), str(source_failure.get("observed_value") or "")])
        sig_bucket = by_signature.setdefault(signature, {"signature": signature, "failure_type": source_failure.get("failure_type") or "", "severity": source_failure.get("severity") or "", "attempts": 0, "resolved": 0, "repeated": 0, "not_repeated": 0, "inconclusive": 0})
        sig_bucket["attempts"] += 1
        sig_bucket[outcome] = int(sig_bucket.get(outcome, 0)) + 1
        if trace.get("current_status") in {"queued", "running", "paused"} or outcome == "repeated":
            pending_queue.append({"run_id": run_id, "source_run_id": int(trace.get("source_run_id") or 0), "failure_id": int(trace.get("failure_id") or 0), "scope": trace.get("scope") or "", "current_status": trace.get("current_status") or "", "outcome": outcome, "environment": environment, "flow_name": flow_name, "failure_type": source_failure.get("failure_type") or "", "severity": source_failure.get("severity") or "", "created_at_ms": int(row["created_at_ms"] or 0)})
    flow_rows = list(by_flow.values())
    for item in flow_rows:
        attempts = int(item.get("attempts") or 0)
        item["success_rate_pct"] = round((float(item.get("resolved") or 0) / attempts) * 100.0, 1) if attempts else 0.0
    flow_rows.sort(key=lambda item: (-int(item.get("attempts") or 0), -int(item.get("repeated") or 0), item.get("flow_name") or ""))
    environment_rows = list(by_environment.values())
    for item in environment_rows:
        attempts = int(item.get("attempts") or 0)
        item["success_rate_pct"] = round((float(item.get("resolved") or 0) / attempts) * 100.0, 1) if attempts else 0.0
    environment_rows.sort(key=lambda item: (-int(item.get("attempts") or 0), -int(item.get("repeated") or 0), item.get("environment") or ""))
    signature_rows = list(by_signature.values())
    for item in signature_rows:
        attempts = int(item.get("attempts") or 0)
        item["repeat_rate_pct"] = round((float(item.get("repeated") or 0) / attempts) * 100.0, 1) if attempts else 0.0
        item["automation_candidate_score"] = round(min(100.0, float(item.get("attempts") or 0) * 8 + float(item.get("repeat_rate_pct") or 0) * 0.7 + (20 if str(item.get("severity") or "") == "high" else 0)), 1)
    signature_rows.sort(key=lambda item: (-int(item.get("repeated") or 0), -int(item.get("attempts") or 0), item.get("failure_type") or ""))
    pending_queue.sort(key=lambda item: (0 if item.get("current_status") in {"running", "paused", "queued"} else 1, -int(item.get("created_at_ms") or 0), -int(item.get("run_id") or 0)))
    automation_candidates = sorted(signature_rows, key=lambda item: (-float(item.get("automation_candidate_score") or 0.0), -int(item.get("attempts") or 0), item.get("failure_type") or ""))
    return {
        "summary": {"total_attempts": len(traces), "resolved": int(outcomes.get("resolved") or 0), "repeated": int(outcomes.get("repeated") or 0), "not_repeated": int(outcomes.get("not_repeated") or 0), "inconclusive": int(outcomes.get("inconclusive") or 0), "pending_or_reincident": len(pending_queue)},
        "by_flow": flow_rows[:10],
        "by_environment": environment_rows[:10],
        "repeated_signatures": signature_rows[:10],
        "automation_candidates": automation_candidates[:10],
        "pending_queue": pending_queue[:10],
    }


def build_ops_overview(con, *, user_id: int | None = None) -> dict:
    try:
        run_status_rows = query_all(con, "SELECT status, COUNT(*) AS count FROM replay_runs GROUP BY status ORDER BY count DESC, status", ())
        failure_type_rows = query_all(con, "SELECT failure_type, COUNT(*) AS count FROM replay_failures GROUP BY failure_type ORDER BY count DESC, failure_type LIMIT 8", ())
        total_runs_row = query_one(con, "SELECT COUNT(*) AS count FROM replay_runs", ())
        total_failures_row = query_one(con, "SELECT COUNT(*) AS count FROM replay_failures", ())
        open_runs_row = query_one(con, "SELECT COUNT(*) AS count FROM replay_runs WHERE status IN ('queued','running','paused','resuming')", ())
        recent_runs = query_all(
            con,
            """
            SELECT id, status, mode, target_host, target_user, log_dir, params_json,
                   last_seq_global_applied, created_at_ms, compliance_status, compliance_reason
            FROM replay_runs
            ORDER BY id DESC
            LIMIT 6
            """,
            (),
        )
        recent_failures = query_all(con, "SELECT run_id, session_id, failure_type, severity, seq_global, ts_ms FROM replay_failures ORDER BY id DESC LIMIT 6", ())
    except sqlite3.OperationalError as exc:
        return {"enabled": False, "error": str(exc)}
    total_runs = int(total_runs_row["count"]) if total_runs_row else 0
    total_failures = int(total_failures_row["count"]) if total_failures_row else 0
    open_runs = int(open_runs_row["count"]) if open_runs_row else 0
    sla_breaches = list_operational_scenarios(con, user_id=user_id, sla_status="breached", sort_by="criticality")[:6]
    sla_warnings = list_operational_scenarios(con, user_id=user_id, sla_status="warning", sort_by="criticality")[:6]
    reprocess_analytics = build_reprocess_analytics(con)
    recent_runs_payload = []
    recent_regressions = []
    for row in recent_runs:
        item = dict(row)
        item["environment"] = extract_run_environment(row)
        comparison = build_run_comparison(con, int(item["id"]))
        if comparison:
            item["comparison_summary"] = comparison.get("summary") or {}
            item["baseline_run"] = comparison.get("baseline_run")
            item["flow_summary"] = comparison.get("flow_summary") or []
            if (comparison.get("summary") or {}).get("regression"):
                recent_regressions.append({"run_id": int(item["id"]), "baseline_run_id": int((comparison.get("baseline_run") or {}).get("id") or 0), "environment": comparison.get("environment") or item.get("environment") or "", "status": item.get("status") or "", "summary": comparison.get("summary") or {}, "flows": comparison.get("flow_summary") or []})
        recent_runs_payload.append(item)
    return {
        "enabled": True,
        "summary": {"total_runs": total_runs, "total_failures": total_failures, "open_runs": open_runs, "sla_breaches": len(sla_breaches), "sla_warnings": len(sla_warnings), "reprocess_attempts": int((reprocess_analytics.get("summary") or {}).get("total_attempts") or 0)},
        "run_status": [dict(row) for row in run_status_rows],
        "failure_types": [dict(row) for row in failure_type_rows],
        "recent_runs": recent_runs_payload,
        "recent_failures": [dict(row) for row in recent_failures],
        "recent_regressions": recent_regressions[:6],
        "sla_breaches": sla_breaches,
        "sla_warnings": sla_warnings,
        "reprocess_analytics": reprocess_analytics,
    }


def build_observability_overview(
    con,
    log_dir: str = "",
    limit: int = 40,
    *,
    user_id: int | None = None,
    environment: str = "",
    created_from_ms: int = 0,
    created_to_ms: int = 0,
    run_limit: int = 50,
) -> dict:
    requested_log_dir = str(log_dir or "").strip()
    effective_log_dir = requested_log_dir
    if not effective_log_dir:
        row = query_one(con, "SELECT log_dir FROM replay_runs WHERE TRIM(COALESCE(log_dir, '')) <> '' ORDER BY id DESC LIMIT 1", ())
        if row:
            effective_log_dir = str(row["log_dir"] or "").strip()
    gateway = read_gateway_monitor(effective_log_dir, limit=max(1, min(limit, 200)))
    ops = build_ops_overview(con, user_id=user_id)
    trend = build_runs_trend_report(con, run_limit=run_limit, environment=environment, created_from_ms=created_from_ms, created_to_ms=created_to_ms)
    return {
        "gateway": gateway,
        "ops": ops,
        "trend": trend,
        "summary": {
            "gateway_attention": int((gateway.get("summary") or {}).get("attention_events") or 0),
            "open_runs": int((ops.get("summary") or {}).get("open_runs") or 0),
            "total_failures": int((ops.get("summary") or {}).get("total_failures") or 0),
            "log_dir_source": "request" if requested_log_dir else ("recent_run" if effective_log_dir else "unset"),
            "trend_filters": {"environment": environment, "created_from_ms": int(created_from_ms or 0), "created_to_ms": int(created_to_ms or 0), "run_limit": int(run_limit or 0)},
        },
        "error": gateway.get("error") if gateway.get("error") and not requested_log_dir and not effective_log_dir else None,
    }


def build_runs_trend_report(
    con,
    run_limit: int = 50,
    *,
    environment: str = "",
    created_from_ms: int = 0,
    created_to_ms: int = 0,
) -> dict:
    rows = query_all(con, "SELECT * FROM replay_runs ORDER BY id DESC LIMIT ?", (max(1, min(int(run_limit or 50), 200)),))
    environments: dict[str, dict] = {}
    flows: dict[str, dict] = {}
    runs = []
    environment_filter = str(environment or "").strip().lower()
    for row in rows:
        run = dict(row)
        run_id = int(run["id"])
        run_environment = extract_run_environment(row) or "sem_ambiente"
        created_at_ms = int(run.get("created_at_ms") or 0)
        if environment_filter and environment_filter not in run_environment.lower():
            continue
        if created_from_ms and created_at_ms < int(created_from_ms):
            continue
        if created_to_ms and created_at_ms > int(created_to_ms):
            continue
        report = build_run_report(con, run_id)
        comparison = build_run_comparison(con, run_id)
        if not report:
            continue
        summary = report.get("summary") or {}
        cmp_summary = (comparison or {}).get("summary") or {}
        env_bucket = environments.setdefault(run_environment, {"environment": run_environment, "runs": 0, "failures": 0, "regressions": 0, "top_flow": "", "_flow_counts": {}})
        env_bucket["runs"] += 1
        env_bucket["failures"] += int(summary.get("failure_count") or 0)
        if cmp_summary.get("regression"):
            env_bucket["regressions"] += 1
        for flow in report.get("flows") or []:
            flow_name = str(flow.get("flow_name") or "sem_fluxo")
            count = int(flow.get("failure_count") or 0)
            env_bucket["_flow_counts"][flow_name] = int(env_bucket["_flow_counts"].get(flow_name) or 0) + count
            flow_bucket = flows.setdefault(flow_name, {"flow_name": flow_name, "failures": 0, "regressions": 0, "environments": set()})
            flow_bucket["failures"] += count
            flow_bucket["environments"].add(run_environment)
            if cmp_summary.get("regression") and any(str(item.get("flow_name") or "") == flow_name and bool(item.get("regression")) for item in ((comparison or {}).get("flow_summary") or [])):
                flow_bucket["regressions"] += 1
        runs.append({"run_id": run_id, "environment": run_environment, "status": run.get("status") or "", "failure_count": int(summary.get("failure_count") or 0), "regression": bool(cmp_summary.get("regression")), "created_at_ms": run.get("created_at_ms")})
    environment_rows = []
    for bucket in environments.values():
        flow_counts = bucket.pop("_flow_counts")
        if flow_counts:
            bucket["top_flow"] = sorted(flow_counts.items(), key=lambda item: (-item[1], item[0]))[0][0]
        environment_rows.append(bucket)
    environment_rows.sort(key=lambda item: (-int(item.get("regressions") or 0), -int(item.get("failures") or 0), item.get("environment") or ""))
    flow_rows = []
    for bucket in flows.values():
        bucket["environment_count"] = len(bucket["environments"])
        bucket["environments"] = sorted(bucket["environments"])
        flow_rows.append(bucket)
    flow_rows.sort(key=lambda item: (-int(item.get("regressions") or 0), -int(item.get("failures") or 0), item.get("flow_name") or ""))
    runs.sort(key=lambda item: (-int(item.get("created_at_ms") or 0), -int(item.get("run_id") or 0)))
    return {
        "summary": {"run_count": len(runs), "environment_count": len(environment_rows), "flow_count": len(flow_rows), "regression_runs": sum(1 for item in runs if item.get("regression")), "filters": {"environment": environment, "created_from_ms": int(created_from_ms or 0), "created_to_ms": int(created_to_ms or 0), "run_limit": max(1, min(int(run_limit or 50), 200))}},
        "environments": environment_rows[:10],
        "flows": flow_rows[:10],
        "recent_runs": runs[:20],
    }

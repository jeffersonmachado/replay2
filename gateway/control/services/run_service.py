from __future__ import annotations

import json

from dakota_gateway.compliance import (
    compliance_blocks_execution,
    derive_gateway_route_from_capture,
    evaluate_run_compliance,
    normalize_target_policy,
    policy_to_params,
)
from dakota_gateway.replay_control import (
    add_run_event,
    cancel_run,
    create_run,
    pause_run,
    query_all,
    query_one,
    resume_run,
    retry_run,
    set_run_compliance,
)
from control.services.report_service import (
    build_run_comparison,
    build_run_family,
    build_run_report,
    build_reprocess_trace,
    report_to_csv,
    report_to_markdown,
)
from control.services.environment_service import resolve_run_target_request


def list_runs_payload(con, *, limit: int = 200, compliance_status: str = "") -> dict:
    rows = query_all(con, "SELECT * FROM replay_runs ORDER BY id DESC LIMIT ?", (max(1, min(int(limit or 200), 2000)),))
    runs = [dict(r) for r in rows]
    compliance_filter = str(compliance_status or "").strip().lower()
    if compliance_filter:
        runs = [run for run in runs if str(run.get("compliance_status") or "").strip().lower() == compliance_filter]
    return {"runs": runs}


def get_run_detail_payload(con, run_id: int) -> dict | None:
    row = query_one(con, "SELECT * FROM replay_runs WHERE id=?", (int(run_id),))
    if not row:
        return None
    failure_rows = query_all(
        con,
        """
        SELECT id, ts_ms, session_id, seq_global, seq_session, flow_name,
               event_type, failure_type, severity, expected_value,
               observed_value, message, evidence_json
        FROM replay_failures
        WHERE run_id=?
        ORDER BY id DESC
        LIMIT 200
        """,
        (int(run_id),),
    )
    failure_summary = {"total": 0, "by_type": {}, "by_severity": {}}
    for item in failure_rows:
        failure_summary["total"] += 1
        failure_summary["by_type"][item["failure_type"]] = failure_summary["by_type"].get(item["failure_type"], 0) + 1
        failure_summary["by_severity"][item["severity"]] = failure_summary["by_severity"].get(item["severity"], 0) + 1
    payload = dict(row)
    if row["target_env_id"] not in (None, ""):
        target_row = query_one(con, "SELECT * FROM target_environments WHERE id=?", (int(row["target_env_id"]),))
        if target_row:
            payload["target_policy"] = policy_to_params(dict(target_row))
    payload["failure_summary"] = failure_summary
    payload["family"] = build_run_family(con, int(run_id))
    payload["reprocess_trace"] = build_reprocess_trace(con, int(run_id))
    return {"run": payload}


def get_run_compliance_payload(con, run_id: int) -> dict | None:
    row = query_one(con, "SELECT * FROM replay_runs WHERE id=?", (int(run_id),))
    if not row:
        return None
    return {
        "run_id": int(run_id),
        "compliance": {
            "entry_mode": row["entry_mode"],
            "via_gateway": bool(row["via_gateway"]),
            "gateway_session_id": row["gateway_session_id"],
            "gateway_endpoint": row["gateway_endpoint"],
            "compliance_status": row["compliance_status"],
            "compliance_reason": row["compliance_reason"],
            "validated_at_ms": row["validated_at_ms"],
        },
    }


def get_run_report_payload(con, run_id: int) -> dict | None:
    report = build_run_report(con, int(run_id))
    if not report:
        return None
    return {"report": report}


def export_run_report_payload(con, run_id: int, *, fmt: str = "md", baseline_run_id: int = 0) -> tuple[str, str]:
    report = build_run_report(con, int(run_id))
    if not report:
        raise ValueError("run inexistente")
    comparison = build_run_comparison(con, int(run_id), baseline_run_id=int(baseline_run_id or 0))
    clean_fmt = str(fmt or "md").strip().lower()
    if clean_fmt == "json":
        return "application/json; charset=utf-8", json.dumps({"report": report, "comparison": comparison}, ensure_ascii=False)
    if clean_fmt == "csv":
        return "text/csv; charset=utf-8", report_to_csv(report)
    return "text/markdown; charset=utf-8", report_to_markdown(report, comparison)


def get_run_comparison_payload(con, run_id: int, *, baseline_run_id: int = 0) -> dict | None:
    comparison = build_run_comparison(con, int(run_id), baseline_run_id=int(baseline_run_id or 0))
    if not comparison:
        return None
    return {"comparison": comparison}


def get_run_events_payload(con, run_id: int) -> dict:
    rows = query_all(con, "SELECT * FROM replay_run_events WHERE run_id=? ORDER BY id DESC LIMIT 200", (int(run_id),))
    return {"events": [dict(r) for r in rows]}


def get_run_failures_payload(con, run_id: int) -> dict:
    rows = query_all(
        con,
        """
        SELECT id, ts_ms, session_id, seq_global, seq_session, flow_name,
               event_type, failure_type, severity, expected_value,
               observed_value, message, evidence_json
        FROM replay_failures
        WHERE run_id=?
        ORDER BY id DESC
        LIMIT 200
        """,
        (int(run_id),),
    )
    failures = []
    for row in rows:
        item = dict(row)
        try:
            item["evidence"] = json.loads(item.pop("evidence_json") or "{}")
        except Exception:
            item["evidence"] = {"raw": item.pop("evidence_json")}
        failures.append(item)
    return {"failures": failures}


def create_run_request_payload(con, *, created_by: int, body: dict) -> dict:
    log_dir = str(body.get("log_dir") or "")
    mode = str(body.get("mode") or "strict-global")
    resolved_target, resolved_params = resolve_run_target_request(con, body)
    rid = create_run(
        con,
        created_by,
        log_dir,
        resolved_target["target_host"],
        resolved_target["target_user"],
        resolved_target["target_command"],
        mode,
        target_env_id=resolved_target["target_env_id"],
        connection_profile_id=resolved_target["connection_profile_id"],
    )
    target_policy = resolved_target.get("target_policy") or {}
    if normalize_target_policy(target_policy)["gateway_required"] and not str(resolved_params.get("gateway_host") or "").strip():
        resolved_params.update(
            {
                key: value
                for key, value in derive_gateway_route_from_capture(log_dir, target_policy=target_policy).items()
                if value not in (None, "")
            }
        )
    compliance = evaluate_run_compliance(
        log_dir,
        target_policy=target_policy,
        resolved_target=resolved_target,
        resolved_params=resolved_params,
    )
    set_run_compliance(con, rid, compliance)
    if resolved_params:
        con.execute("UPDATE replay_runs SET params_json=? WHERE id=?", (json.dumps(resolved_params, ensure_ascii=False), rid))
    return {"id": rid, "compliance_status": compliance["compliance_status"]}


def apply_run_action(con, *, run_id: int, action: str, body: dict, actor: dict) -> dict:
    clean_action = str(action or "").strip().lower()
    if clean_action == "start":
        current_run = query_one(con, "SELECT compliance_status, compliance_reason FROM replay_runs WHERE id=?", (int(run_id),))
        if current_run and compliance_blocks_execution(str(current_run["compliance_status"] or "")):
            return {
                "status_code": 409,
                "payload": {
                    "ok": False,
                    "error": str(current_run["compliance_reason"] or "run bloqueado pela policy do target"),
                    "compliance_status": str(current_run["compliance_status"] or "rejected"),
                },
            }
        con.execute("UPDATE replay_runs SET status='running' WHERE id=? AND status='queued'", (int(run_id),))
        add_run_event(con, int(run_id), "api", "start solicitado", {"by": actor["username"]})
        return {"status_code": 200, "payload": {"ok": True}, "start_async": True}

    if clean_action == "pause":
        pause_run(con, int(run_id))
        return {"status_code": 200, "payload": {"ok": True}}

    if clean_action == "resume":
        resume_run(con, int(run_id))
        return {"status_code": 200, "payload": {"ok": True}, "start_async": True}

    if clean_action == "cancel":
        cancel_run(con, int(run_id))
        return {"status_code": 200, "payload": {"ok": True}}

    if clean_action == "retry":
        nid = retry_run(con, int(run_id), created_by=int(actor["id"]))
        source_run = query_one(con, "SELECT * FROM replay_runs WHERE id=?", (int(nid),))
        if source_run:
            try:
                params = json.loads(source_run["params_json"] or "{}") if source_run["params_json"] else {}
            except Exception:
                params = {}
            target_policy = {}
            if source_run["target_env_id"] not in (None, ""):
                row = query_one(con, "SELECT * FROM target_environments WHERE id=?", (int(source_run["target_env_id"]),))
                target_policy = dict(row) if row else {}
            compliance = evaluate_run_compliance(
                str(source_run["log_dir"] or ""),
                target_policy=target_policy,
                resolved_target=dict(source_run),
                resolved_params=params,
            )
            set_run_compliance(con, int(nid), compliance)
        return {"status_code": 200, "payload": {"id": nid}}

    if clean_action == "reprocess-from-failure":
        nid = create_reprocess_run_from_failure(
            con,
            int(run_id),
            int(body.get("failure_id") or 0),
            str(body.get("scope") or "from-failure"),
            created_by=int(actor["id"]),
        )
        return {"status_code": 200, "payload": {"id": nid}}

    return {"status_code": 404, "payload": None}

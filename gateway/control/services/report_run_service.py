from __future__ import annotations

import json

from dakota_gateway.replay_control import add_run_event, create_run
from dakota_gateway.state_db import query_all, query_one
from control.services.report_common import extract_run_environment


def build_run_report(con, run_id: int) -> dict | None:
    run = query_one(con, "SELECT * FROM replay_runs WHERE id=?", (run_id,))
    if not run:
        return None
    environment = extract_run_environment(run)
    failure_rows = query_all(
        con,
        """
        SELECT id, run_id, ts_ms, session_id, seq_global, seq_session, flow_name,
               event_type, failure_type, severity, expected_value, observed_value,
               message, evidence_json
        FROM replay_failures
        WHERE run_id=?
        ORDER BY id DESC
        """,
        (run_id,),
    )
    event_rows = query_all(
        con,
        """
        SELECT id, ts_ms, kind, message, data_json
        FROM replay_run_events
        WHERE run_id=?
        ORDER BY id DESC
        LIMIT 500
        """,
        (run_id,),
    )

    by_session: dict[str, dict] = {}
    by_type: dict[str, int] = {}
    by_severity: dict[str, int] = {}
    by_flow: dict[str, int] = {}
    flow_severity: dict[str, dict[str, int]] = {}
    grouped: dict[str, dict] = {}
    failures = []
    for row in failure_rows:
        item = dict(row)
        try:
            item["evidence"] = json.loads(item.pop("evidence_json") or "{}")
        except Exception:
            item["evidence"] = {"raw": item.pop("evidence_json")}
        failures.append(item)
        sid = str(item.get("session_id") or "").strip() or "sem_sessao"
        sess = by_session.setdefault(sid, {"session_id": sid, "failure_count": 0, "severities": {}, "types": {}, "last_seq_global": 0})
        sess["failure_count"] += 1
        sev = str(item.get("severity") or "unknown")
        ftype = str(item.get("failure_type") or "unknown")
        flow_name = str(item.get("flow_name") or "").strip() or "sem_fluxo"
        sess["severities"][sev] = int(sess["severities"].get(sev) or 0) + 1
        sess["types"][ftype] = int(sess["types"].get(ftype) or 0) + 1
        sess["last_seq_global"] = max(int(sess.get("last_seq_global") or 0), int(item.get("seq_global") or 0))
        by_type[ftype] = by_type.get(ftype, 0) + 1
        by_severity[sev] = by_severity.get(sev, 0) + 1
        by_flow[flow_name] = by_flow.get(flow_name, 0) + 1
        flow_severity.setdefault(flow_name, {})
        flow_severity[flow_name][sev] = int(flow_severity[flow_name].get(sev) or 0) + 1
        group_key = "|".join([ftype, sev, str(item.get("expected_value") or ""), str(item.get("observed_value") or "")])
        group = grouped.setdefault(group_key, {"signature": group_key, "failure_type": ftype, "severity": sev, "expected_value": item.get("expected_value") or "", "observed_value": item.get("observed_value") or "", "count": 0, "sessions": set(), "seq_globals": set(), "messages": []})
        group["count"] += 1
        if sid:
            group["sessions"].add(sid)
        seq_global = int(item.get("seq_global") or 0)
        if seq_global:
            group["seq_globals"].add(seq_global)
        msg = str(item.get("message") or "")
        if msg and msg not in group["messages"]:
            group["messages"].append(msg)

    grouped_failures = []
    for item in grouped.values():
        grouped_failures.append(
            {
                "signature": item["signature"],
                "failure_type": item["failure_type"],
                "severity": item["severity"],
                "expected_value": item["expected_value"],
                "observed_value": item["observed_value"],
                "count": item["count"],
                "sessions": sorted(item["sessions"]),
                "seq_globals": sorted(item["seq_globals"]),
                "messages": item["messages"][:5],
            }
        )
    grouped_failures.sort(key=lambda item: (-int(item.get("count") or 0), item.get("failure_type") or ""))
    sessions_report = sorted(by_session.values(), key=lambda item: (-int(item.get("failure_count") or 0), item.get("session_id") or ""))
    flows_report = [{"flow_name": flow_name, "failure_count": count, "severities": flow_severity.get(flow_name) or {}} for flow_name, count in sorted(by_flow.items(), key=lambda item: (-item[1], item[0]))]
    events = [dict(row) for row in event_rows]
    return {
        "run": dict(run),
        "environment": environment,
        "summary": {
            "failure_count": len(failures),
            "session_count_with_failures": len(by_session),
            "flow_count_with_failures": len(by_flow),
            "by_type": by_type,
            "by_severity": by_severity,
            "by_flow": by_flow,
            "event_count": len(events),
        },
        "sessions": sessions_report,
        "flows": flows_report,
        "grouped_failures": grouped_failures,
        "recent_events": events,
        "failures": failures[:200],
    }


def build_run_family(con, run_id: int) -> dict:
    rows = query_all(con, "SELECT id, parent_run_id, status, created_at_ms, mode, target_host FROM replay_runs ORDER BY id", ())
    by_id = {int(row["id"]): dict(row) for row in rows}
    if int(run_id) not in by_id:
      return {"root_run_id": int(run_id), "members": []}
    root_run_id = int(run_id)
    seen = set()
    while root_run_id and root_run_id in by_id and root_run_id not in seen:
        seen.add(root_run_id)
        parent_run_id = int(by_id[root_run_id].get("parent_run_id") or 0)
        if parent_run_id <= 0:
            break
        root_run_id = parent_run_id
    members = []
    pending = [root_run_id]
    while pending:
        current_id = pending.pop(0)
        row = by_id.get(int(current_id))
        if not row:
            continue
        members.append({"id": int(row["id"]), "parent_run_id": int(row.get("parent_run_id") or 0), "status": row.get("status") or "", "created_at_ms": int(row.get("created_at_ms") or 0), "mode": row.get("mode") or "", "target_host": row.get("target_host") or "", "is_current": int(row["id"]) == int(run_id)})
        pending.extend([int(item["id"]) for item in rows if int(item["parent_run_id"] or 0) == int(current_id)])
    members.sort(key=lambda item: (int(item.get("created_at_ms") or 0), int(item.get("id") or 0)))
    return {"root_run_id": root_run_id, "members": members}


def build_reprocess_trace(con, run_id: int) -> dict | None:
    event = query_one(
        con,
        """
        SELECT data_json FROM replay_run_events
        WHERE run_id=? AND kind='reprocess'
        ORDER BY id DESC LIMIT 1
        """,
        (int(run_id),),
    )
    if not event:
        return None
    try:
        data = json.loads(event["data_json"] or "{}")
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}
    source_run_id = int(data.get("source_run_id") or 0)
    failure_id = int(data.get("failure_id") or 0)
    scope = str(data.get("scope") or "")
    source_failure = None
    repeated_in_current = False
    if failure_id and source_run_id:
        source_failure = query_one(
            con,
            """
            SELECT id, session_id, seq_global, seq_session, flow_name, event_type, failure_type,
                   severity, expected_value, observed_value, message
            FROM replay_failures
            WHERE id=? AND run_id=?
            """,
            (failure_id, source_run_id),
        )
    if source_failure:
        current_report = build_run_report(con, int(run_id)) or {}
        signature = "|".join([str(source_failure["failure_type"] or ""), str(source_failure["severity"] or ""), str(source_failure["expected_value"] or ""), str(source_failure["observed_value"] or "")])
        repeated_in_current = any(str(item.get("signature") or "") == signature for item in (current_report.get("grouped_failures") or []))
    outcome = "inconclusive"
    current_run = query_one(con, "SELECT status FROM replay_runs WHERE id=?", (int(run_id),))
    current_status = str((current_run or {}).get("status") or "") if isinstance(current_run, dict) else str(current_run["status"] if current_run else "")
    if source_failure:
        if repeated_in_current:
            outcome = "repeated"
        elif current_status == "success":
            outcome = "resolved"
        elif current_status in {"failed", "cancelled"}:
            outcome = "not_repeated"
    trace = {"source_run_id": source_run_id, "failure_id": failure_id, "scope": scope, "outcome": outcome, "repeated_in_current": repeated_in_current, "current_status": current_status}
    if source_failure:
        trace["source_failure"] = dict(source_failure)
    return trace


def create_reprocess_run_from_failure(con, run_id: int, failure_id: int, scope: str, created_by: int) -> int:
    run = query_one(con, "SELECT * FROM replay_runs WHERE id=?", (int(run_id),))
    if not run:
        raise ValueError("run inexistente")
    failure = query_one(
        con,
        """
        SELECT id, run_id, session_id, seq_global, event_type, expected_value, observed_value, message
        FROM replay_failures
        WHERE id=? AND run_id=?
        """,
        (int(failure_id), int(run_id)),
    )
    if not failure:
        raise ValueError("falha inexistente para esta run")
    clean_scope = str(scope or "from-failure").strip().lower()
    if clean_scope not in {"from-failure", "session-from-failure", "session-from-checkpoint"}:
        raise ValueError("scope inválido")
    try:
        params = json.loads(run["params_json"] or "{}") if run["params_json"] else {}
    except Exception:
        params = {}
    if not isinstance(params, dict):
        params = {}
    seq_global = int(failure["seq_global"] or 0)
    session_id = str(failure["session_id"] or "").strip()
    if clean_scope in {"from-failure", "session-from-failure"} and seq_global > 0:
        params["replay_from_seq_global"] = seq_global
        params.pop("replay_from_checkpoint_sig", None)
    if clean_scope in {"session-from-failure", "session-from-checkpoint"} and session_id:
        params["replay_session_id"] = session_id
    if clean_scope == "session-from-checkpoint":
        checkpoint_sig = str(failure["expected_value"] or "").strip()
        if not checkpoint_sig:
            raise ValueError("falha sem checkpoint esperado para reprocessamento por checkpoint")
        params["replay_from_checkpoint_sig"] = checkpoint_sig
        params.pop("replay_from_seq_global", None)
    rid = create_run(con, created_by=created_by, log_dir=run["log_dir"], target_host=run["target_host"], target_user=run["target_user"], target_command=run["target_command"], mode=run["mode"], parent_run_id=int(run_id))
    con.execute("UPDATE replay_runs SET params_json=? WHERE id=?", (json.dumps(params, ensure_ascii=False), rid))
    add_run_event(con, rid, "reprocess", "run criada a partir de falha anterior", {"source_run_id": int(run_id), "failure_id": int(failure_id), "scope": clean_scope, "session_id": session_id, "seq_global": seq_global})
    return rid


def find_baseline_run(con, run_id: int, run=None, baseline_run_id: int = 0):
    current = run or query_one(con, "SELECT * FROM replay_runs WHERE id=?", (run_id,))
    if not current:
        return None, "missing"
    if baseline_run_id:
        baseline = query_one(con, "SELECT * FROM replay_runs WHERE id=?", (int(baseline_run_id),))
        return baseline, "explicit" if baseline else "missing"
    parent_run_id = int(current["parent_run_id"] or 0)
    if parent_run_id:
        baseline = query_one(con, "SELECT * FROM replay_runs WHERE id=?", (parent_run_id,))
        if baseline:
            return baseline, "parent"
    baseline = query_one(
        con,
        """
        SELECT * FROM replay_runs
        WHERE id < ? AND log_dir=? AND target_host=? AND target_user=? AND target_command=? AND mode=?
        ORDER BY id DESC LIMIT 1
        """,
        (run_id, current["log_dir"], current["target_host"], current["target_user"], current["target_command"], current["mode"]),
    )
    return baseline, "previous_match" if baseline else "missing"


def build_run_comparison(con, run_id: int, baseline_run_id: int = 0) -> dict | None:
    current = query_one(con, "SELECT * FROM replay_runs WHERE id=?", (run_id,))
    if not current:
        return None
    baseline, baseline_mode = find_baseline_run(con, run_id, run=current, baseline_run_id=baseline_run_id)
    current_report = build_run_report(con, run_id)
    if not current_report:
        return None
    current_environment = extract_run_environment(current)
    if not baseline:
        return {
            "run_id": run_id,
            "environment": current_environment,
            "baseline_mode": baseline_mode,
            "baseline_run": None,
            "summary": {
                "current_failure_count": int((current_report.get("summary") or {}).get("failure_count") or 0),
                "baseline_failure_count": 0,
                "new_failure_groups": 0,
                "recurring_failure_groups": 0,
                "resolved_failure_groups": 0,
                "regression": False,
            },
            "flow_summary": [],
            "new_failures": current_report.get("grouped_failures") or [],
            "recurring_failures": [],
            "resolved_failures": [],
        }
    baseline_report = build_run_report(con, int(baseline["id"]))
    if not baseline_report:
        return None
    current_groups = {str(item.get("signature") or ""): item for item in (current_report.get("grouped_failures") or [])}
    baseline_groups = {str(item.get("signature") or ""): item for item in (baseline_report.get("grouped_failures") or [])}
    new_failures = []
    recurring_failures = []
    resolved_failures = []
    current_by_flow: dict[str, int] = {}
    baseline_by_flow: dict[str, int] = {}
    for signature, item in current_groups.items():
        baseline_item = baseline_groups.get(signature)
        if not baseline_item:
            new_failures.append(item)
            continue
        delta = int(item.get("count") or 0) - int(baseline_item.get("count") or 0)
        recurring_failures.append({**item, "baseline_count": int(baseline_item.get("count") or 0), "delta_count": delta})
    for signature, item in baseline_groups.items():
        if signature not in current_groups:
            resolved_failures.append(item)
    for failure in current_report.get("failures") or []:
        flow_name = str(failure.get("flow_name") or "").strip() or "sem_fluxo"
        current_by_flow[flow_name] = current_by_flow.get(flow_name, 0) + 1
    for failure in baseline_report.get("failures") or []:
        flow_name = str(failure.get("flow_name") or "").strip() or "sem_fluxo"
        baseline_by_flow[flow_name] = baseline_by_flow.get(flow_name, 0) + 1
    flow_summary = []
    for flow_name in sorted(set(current_by_flow) | set(baseline_by_flow)):
        current_count = int(current_by_flow.get(flow_name) or 0)
        baseline_count = int(baseline_by_flow.get(flow_name) or 0)
        flow_summary.append({"flow_name": flow_name, "current_count": current_count, "baseline_count": baseline_count, "delta_count": current_count - baseline_count, "regression": current_count > baseline_count})
    flow_summary.sort(key=lambda item: (-abs(int(item.get("delta_count") or 0)), item.get("flow_name") or ""))
    new_failures.sort(key=lambda item: (-int(item.get("count") or 0), item.get("failure_type") or ""))
    recurring_failures.sort(key=lambda item: (-abs(int(item.get("delta_count") or 0)), item.get("failure_type") or ""))
    resolved_failures.sort(key=lambda item: (-int(item.get("count") or 0), item.get("failure_type") or ""))
    current_status = str(current["status"] or "")
    baseline_status = str(baseline["status"] or "")
    current_failure_count = int((current_report.get("summary") or {}).get("failure_count") or 0)
    baseline_failure_count = int((baseline_report.get("summary") or {}).get("failure_count") or 0)
    regression = bool(new_failures) or current_failure_count > baseline_failure_count
    if baseline_status in {"success"} and current_status in {"failed", "cancelled"}:
        regression = True
    return {
        "run_id": run_id,
        "environment": current_environment,
        "baseline_mode": baseline_mode,
        "baseline_run": {"id": int(baseline["id"]), "status": baseline_status, "created_at_ms": baseline["created_at_ms"], "mode": baseline["mode"], "target_host": baseline["target_host"], "environment": extract_run_environment(baseline)},
        "summary": {
            "current_failure_count": current_failure_count,
            "baseline_failure_count": baseline_failure_count,
            "new_failure_groups": len(new_failures),
            "recurring_failure_groups": len(recurring_failures),
            "resolved_failure_groups": len(resolved_failures),
            "delta_failures": current_failure_count - baseline_failure_count,
            "current_status": current_status,
            "baseline_status": baseline_status,
            "regression": regression,
        },
        "flow_summary": flow_summary[:20],
        "new_failures": new_failures[:20],
        "recurring_failures": recurring_failures[:20],
        "resolved_failures": resolved_failures[:20],
    }

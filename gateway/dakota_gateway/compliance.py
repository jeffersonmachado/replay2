from __future__ import annotations

import base64
import json
import re
from pathlib import Path


DIRECT_SSH_POLICIES = {"gateway_only", "admin_only", "unrestricted", "disabled"}
CAPTURE_START_MODES = {"login_required", "session_start_required"}
CAPTURE_COMPLIANCE_MODES = {"strict", "warn", "off"}
COMPLIANCE_STATUSES = {"compliant", "warning", "non_compliant", "rejected", "not_applicable"}

_LOGIN_HINTS = (
    "login",
    "username",
    "usuario",
    "usuário",
    "senha",
    "password",
    "signon",
)


def _as_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if value in (None, "", 0, "0"):
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "sim", "on"}


def normalize_target_policy(raw: dict | None) -> dict:
    if raw is None:
        item = {}
    elif isinstance(raw, dict):
        item = raw
    else:
        try:
            item = dict(raw)
        except Exception:
            item = {}
    gateway_required = _as_bool(item.get("gateway_required"))
    direct_ssh_policy = str(item.get("direct_ssh_policy") or "").strip().lower()
    capture_start_mode = str(item.get("capture_start_mode") or "").strip().lower()
    capture_compliance_mode = str(item.get("capture_compliance_mode") or "").strip().lower()

    if direct_ssh_policy not in DIRECT_SSH_POLICIES:
        direct_ssh_policy = "gateway_only" if gateway_required else "unrestricted"
    if capture_start_mode not in CAPTURE_START_MODES:
        capture_start_mode = "login_required" if gateway_required else "session_start_required"
    if capture_compliance_mode not in CAPTURE_COMPLIANCE_MODES:
        capture_compliance_mode = "strict" if gateway_required else "off"

    return {
        "gateway_required": gateway_required,
        "direct_ssh_policy": direct_ssh_policy,
        "capture_start_mode": capture_start_mode,
        "capture_compliance_mode": capture_compliance_mode,
        "allow_admin_direct_access": _as_bool(item.get("allow_admin_direct_access")),
    }


def target_policy_reason(policy: dict) -> str:
    clean = normalize_target_policy(policy)
    if not clean["gateway_required"]:
        return "target sem exigência de gateway exclusivo"
    return (
        f"gateway obrigatório, ssh direto={clean['direct_ssh_policy']}, "
        f"início={clean['capture_start_mode']}, compliance={clean['capture_compliance_mode']}"
    )


def policy_to_params(policy: dict) -> dict:
    clean = normalize_target_policy(policy)
    return {
        "gateway_required": clean["gateway_required"],
        "direct_ssh_policy": clean["direct_ssh_policy"],
        "capture_start_mode": clean["capture_start_mode"],
        "capture_compliance_mode": clean["capture_compliance_mode"],
        "allow_admin_direct_access": clean["allow_admin_direct_access"],
    }


def _safe_decode_b64(raw: str) -> str:
    try:
        return base64.b64decode(raw or "").decode("utf-8", errors="replace")
    except Exception:
        return ""


def _looks_like_login(text: str) -> bool:
    lower = str(text or "").strip().lower()
    if not lower:
        return False
    return any(token in lower for token in _LOGIN_HINTS)


def _has_gateway_route(params: dict | None) -> bool:
    item = params if isinstance(params, dict) else {}
    if str(item.get("gateway_host") or "").strip():
        return True
    if str(item.get("gateway_endpoint") or "").strip():
        return True
    route_mode = str(item.get("gateway_route_mode") or "").strip().lower()
    return route_mode in {"proxyjump", "gateway", "bastion"}


def _iter_audit_events(log_dir: str):
    for file_path in sorted(Path(log_dir).glob("audit-*.jsonl")):
        try:
            lines = file_path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except Exception:
                continue
            if isinstance(item, dict):
                yield item


def summarize_capture_sessions(log_dir: str, *, target_policy: dict | None = None) -> dict:
    clean_policy = normalize_target_policy(target_policy)
    sessions: dict[str, dict] = {}

    for item in _iter_audit_events(log_dir):
        session_id = str(item.get("session_id") or "").strip()
        if not session_id:
            continue
        typ = str(item.get("type") or "").strip().lower()
        ts_ms = int(item.get("ts_ms") or 0)
        agg = sessions.setdefault(
            session_id,
            {
                "session_id": session_id,
                "actor": str(item.get("actor") or "").strip(),
                "started_at_ms": ts_ms or None,
                "ended_at_ms": None,
                "last_ts_ms": ts_ms or None,
                "event_count": 0,
                "checkpoint_count": 0,
                "deterministic_input_count": 0,
                "bytes_in": 0,
                "bytes_out": 0,
                "last_seq_global": 0,
                "last_seq_session": 0,
                "status": "open",
                "event_types": set(),
                "entry_mode": "",
                "via_gateway": None,
                "gateway_session_id": "",
                "gateway_endpoint": "",
                "source_host": "",
                "source_user": "",
                "source_command": "",
                "session_start_observed": False,
                "login_sequence_observed": False,
                "first_checkpoint_sig": "",
                "first_output_excerpt": "",
            },
        )

        if agg["started_at_ms"] in (None, 0) or (ts_ms and ts_ms < int(agg["started_at_ms"] or 0)):
            agg["started_at_ms"] = ts_ms or None
        if ts_ms and (not agg["last_ts_ms"] or ts_ms > int(agg["last_ts_ms"] or 0)):
            agg["last_ts_ms"] = ts_ms
        agg["event_count"] += 1
        agg["event_types"].add(typ or "unknown")
        agg["last_seq_global"] = max(int(agg["last_seq_global"] or 0), int(item.get("seq_global") or 0))
        agg["last_seq_session"] = max(int(agg["last_seq_session"] or 0), int(item.get("seq_session") or 0))
        if typ == "checkpoint":
            agg["checkpoint_count"] += 1
            if not agg["first_checkpoint_sig"]:
                agg["first_checkpoint_sig"] = str(item.get("sig") or "")
                if _looks_like_login(agg["first_checkpoint_sig"]):
                    agg["login_sequence_observed"] = True
        elif typ == "deterministic_input":
            agg["deterministic_input_count"] += 1
        elif typ == "session_end":
            agg["ended_at_ms"] = ts_ms or agg["ended_at_ms"]
            agg["status"] = "closed"
        elif typ == "bytes":
            direction = str(item.get("dir") or "").strip().lower()
            if direction == "in":
                agg["bytes_in"] += int(item.get("n") or 0)
            elif direction == "out":
                agg["bytes_out"] += int(item.get("n") or 0)
                if not agg["first_output_excerpt"]:
                    agg["first_output_excerpt"] = _safe_decode_b64(str(item.get("data_b64") or ""))[:240]
                    if _looks_like_login(agg["first_output_excerpt"]):
                        agg["login_sequence_observed"] = True
        elif typ == "session_start":
            agg["session_start_observed"] = True
            agg["entry_mode"] = str(item.get("entry_mode") or agg["entry_mode"] or "gateway_ssh")
            raw_via_gateway = item.get("via_gateway")
            agg["via_gateway"] = True if raw_via_gateway is None else _as_bool(raw_via_gateway)
            agg["gateway_session_id"] = str(item.get("gateway_session_id") or agg["gateway_session_id"] or session_id)
            agg["gateway_endpoint"] = str(item.get("gateway_endpoint") or agg["gateway_endpoint"] or "")
            agg["source_host"] = str(item.get("source_host") or agg["source_host"] or "")
            agg["source_user"] = str(item.get("source_user") or agg["source_user"] or "")
            agg["source_command"] = str(item.get("source_command") or agg["source_command"] or "")

    session_items = []
    compliant = 0
    warnings = 0
    non_compliant = 0
    for item in sessions.values():
        if item["via_gateway"] is None:
            item["via_gateway"] = True if item["session_start_observed"] else False
        if not item["entry_mode"]:
            item["entry_mode"] = "gateway_ssh" if item["via_gateway"] else "direct"
        if not item["gateway_session_id"] and item["via_gateway"]:
            item["gateway_session_id"] = item["session_id"]

        status, reason = evaluate_session_compliance(item, clean_policy)
        item["compliance_status"] = status
        item["compliance_reason"] = reason
        item["validated_at_ms"] = item["last_ts_ms"]
        if status == "compliant":
            compliant += 1
        elif status == "warning":
            warnings += 1
        elif status not in {"not_applicable"}:
            non_compliant += 1
        item["event_types"] = sorted(item["event_types"])
        session_items.append(item)

    session_items.sort(key=lambda current: (-int(current.get("last_ts_ms") or 0), current.get("session_id") or ""))
    summary_status = "not_applicable"
    if session_items:
        if non_compliant > 0:
            summary_status = "rejected" if clean_policy["capture_compliance_mode"] == "strict" else "warning"
        elif warnings > 0:
            summary_status = "warning"
        else:
            summary_status = "compliant"

    return {
        "log_dir": str(log_dir or ""),
        "policy": clean_policy,
        "sessions": session_items,
        "summary": {
            "total_sessions": len(session_items),
            "compliant_sessions": compliant,
            "warning_sessions": warnings,
            "non_compliant_sessions": non_compliant,
            "compliance_status": summary_status,
        },
    }


def derive_gateway_route_from_capture(log_dir: str, *, target_policy: dict | None = None) -> dict:
    summary = summarize_capture_sessions(log_dir, target_policy=target_policy)
    sessions = summary.get("sessions") or []
    if not sessions:
        return {}
    first_session = sessions[0]
    gateway_endpoint = str(first_session.get("gateway_endpoint") or "").strip()
    if not gateway_endpoint:
        return {}
    return {
        "gateway_host": gateway_endpoint,
        "gateway_route_mode": "proxyjump",
    }


def evaluate_session_compliance(session: dict, policy: dict | None = None) -> tuple[str, str]:
    clean_policy = normalize_target_policy(policy)
    if clean_policy["capture_compliance_mode"] == "off" and not clean_policy["gateway_required"]:
        return "not_applicable", "target não exige compliance de captura"

    reasons = []
    if clean_policy["gateway_required"] and not _as_bool(session.get("via_gateway")):
        reasons.append("target exige gateway e a sessão não confirmou entrada via gateway")
    if clean_policy["capture_start_mode"] == "session_start_required" and not _as_bool(session.get("session_start_observed")):
        reasons.append("captura sem evento session_start desde o primeiro estado")
    if clean_policy["capture_start_mode"] == "login_required":
        if str(session.get("source_command") or "").strip():
            reasons.append("captura iniciou com source_command e não no login shell")
        elif not _as_bool(session.get("login_sequence_observed")):
            reasons.append("não houve evidência de tela inicial de login na sessão capturada")

    if reasons:
        if clean_policy["capture_compliance_mode"] == "strict":
            return "non_compliant", "; ".join(reasons)
        if clean_policy["capture_compliance_mode"] == "warn":
            return "warning", "; ".join(reasons)
        return "non_compliant", "; ".join(reasons)

    return "compliant", "sessão compatível com a policy do target"


def evaluate_run_compliance(
    log_dir: str,
    *,
    target_policy: dict | None,
    resolved_target: dict | None,
    resolved_params: dict | None,
) -> dict:
    summary = summarize_capture_sessions(log_dir, target_policy=target_policy)
    sessions = summary["sessions"]
    first_session = sessions[0] if len(sessions) == 1 else None
    policy = normalize_target_policy(target_policy)
    params = resolved_params if isinstance(resolved_params, dict) else {}
    target = resolved_target if isinstance(resolved_target, dict) else {}

    entry_mode = "unknown"
    via_gateway = False
    gateway_session_id = ""
    gateway_endpoint = ""
    if first_session:
        entry_mode = str(first_session.get("entry_mode") or "unknown")
        via_gateway = _as_bool(first_session.get("via_gateway"))
        gateway_session_id = str(first_session.get("gateway_session_id") or "")
        gateway_endpoint = str(first_session.get("gateway_endpoint") or "")
    elif sessions:
        via_gateway = all(_as_bool(item.get("via_gateway")) for item in sessions)
        entry_mode = "gateway_ssh" if via_gateway else "mixed"

    reasons = []
    if not sessions:
        reasons.append("log_dir sem sessões capturadas auditáveis")
    if summary["summary"]["non_compliant_sessions"] > 0:
        reasons.append(f"{summary['summary']['non_compliant_sessions']} sessão(ões) não conformes na origem")
    if summary["summary"]["warning_sessions"] > 0:
        reasons.append(f"{summary['summary']['warning_sessions']} sessão(ões) com alerta de origem")

    transport = str(params.get("transport") or "ssh").strip().lower()
    gateway_route = _has_gateway_route(params)
    if policy["gateway_required"] and transport in {"ssh", "telnet"} and policy["direct_ssh_policy"] in {"gateway_only", "admin_only", "disabled"} and not gateway_route:
        reasons.append(
            f"replay usa transporte direto {transport} para target com política {policy['direct_ssh_policy']}; "
            "é obrigatório informar rota via gateway/bastion"
        )

    if reasons:
        if policy["capture_compliance_mode"] == "strict":
            compliance_status = "rejected"
        elif policy["capture_compliance_mode"] == "warn":
            compliance_status = "warning"
        else:
            compliance_status = "non_compliant"
    else:
        compliance_status = "not_applicable" if summary["summary"]["compliance_status"] == "not_applicable" else "compliant"

    target_host = str(target.get("target_host") or params.get("target_host") or "")
    reason = "; ".join(reasons) if reasons else "origem da captura e rota do run compatíveis com a policy"
    return {
        "entry_mode": entry_mode,
        "via_gateway": via_gateway,
        "gateway_session_id": gateway_session_id,
        "gateway_endpoint": gateway_endpoint,
        "compliance_status": compliance_status,
        "compliance_reason": reason,
        "validated_at_ms": max([int(item.get("validated_at_ms") or 0) for item in sessions] + [0]),
        "source_session_count": len(sessions),
        "target_host": target_host,
        "target_policy": policy,
        "capture_summary": summary["summary"],
        "gateway_route": gateway_route,
    }


def compliance_blocks_execution(compliance_status: str) -> bool:
    return str(compliance_status or "").strip().lower() == "rejected"


def normalize_direct_ssh_policy_payload(raw: dict | None) -> dict:
    item = raw or {}
    return normalize_target_policy(
        {
            "gateway_required": item.get("gateway_required"),
            "direct_ssh_policy": item.get("direct_ssh_policy"),
            "capture_start_mode": item.get("capture_start_mode"),
            "capture_compliance_mode": item.get("capture_compliance_mode"),
            "allow_admin_direct_access": item.get("allow_admin_direct_access"),
        }
    )

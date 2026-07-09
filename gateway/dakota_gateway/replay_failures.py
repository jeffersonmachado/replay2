from __future__ import annotations

import json
import re
from difflib import SequenceMatcher

from .state_db import exec1, now_ms


ALLOWED_FAILURE_TYPES = {
    "functional",
    "timeout",
    "screen_divergence",
    "technical_error",
    "navigation_error",
    "concurrency_error",
    "checkpoint_mismatch",
    "integrity_error",
    "cancelled",
}

ALLOWED_SEVERITIES = {"low", "medium", "high", "critical"}


def build_failure_record(
    *,
    session_id: str,
    seq_global: int,
    seq_session: int | None = None,
    flow_name: str = "",
    event_type: str,
    failure_type: str,
    severity: str,
    expected_value: str = "",
    observed_value: str = "",
    message: str,
    evidence: dict | None = None,
) -> dict:
    clean_failure_type = str(failure_type or "technical_error").strip() or "technical_error"
    if clean_failure_type not in ALLOWED_FAILURE_TYPES:
        clean_failure_type = "technical_error"
    clean_severity = str(severity or "high").strip() or "high"
    if clean_severity not in ALLOWED_SEVERITIES:
        clean_severity = "high"
    return {
        "session_id": session_id or "",
        "seq_global": int(seq_global or 0),
        "seq_session": int(seq_session or 0),
        "flow_name": flow_name or "",
        "event_type": event_type,
        "failure_type": clean_failure_type,
        "severity": clean_severity,
        "expected_value": expected_value or "",
        "observed_value": observed_value or "",
        "message": message,
        "evidence": evidence or {},
    }


def _clean_match_text(value: str, *, ignore_case: bool) -> str:
    text = str(value or "").strip()
    return text.lower() if ignore_case else text


def _checkpoint_match_settings(params: dict | None) -> dict:
    raw = params if isinstance(params, dict) else {}
    mode = str(raw.get("match_mode") or raw.get("checkpoint_match_mode") or "strict").strip().lower()
    if mode not in {"strict", "contains", "regex", "fuzzy"}:
        mode = "strict"
    threshold_raw = raw.get("match_threshold")
    if threshold_raw in (None, ""):
        threshold_raw = raw.get("checkpoint_match_threshold")
    try:
        threshold = float(threshold_raw if threshold_raw not in (None, "") else 0.92)
    except Exception:
        threshold = 0.92
    threshold = max(0.0, min(1.0, threshold))
    return {
        "mode": mode,
        "threshold": threshold,
        "ignore_case": str(raw.get("match_ignore_case") or raw.get("checkpoint_match_ignore_case") or "").strip().lower() in {"1", "true", "yes", "sim"},
    }


def evaluate_checkpoint_match(expected: str, observed: str, params: dict | None = None) -> dict:
    settings = _checkpoint_match_settings(params)
    mode = settings["mode"]
    ignore_case = bool(settings["ignore_case"])
    clean_expected = _clean_match_text(expected, ignore_case=ignore_case)
    clean_observed = _clean_match_text(observed, ignore_case=ignore_case)
    ratio = SequenceMatcher(None, clean_expected, clean_observed).ratio() if (clean_expected or clean_observed) else 1.0
    matched = False
    regex_error = ""
    if mode == "strict":
        matched = clean_expected == clean_observed
    elif mode == "contains":
        matched = bool(clean_expected) and clean_expected in clean_observed
    elif mode == "regex":
        try:
            matched = bool(re.search(clean_expected, clean_observed))
        except re.error as exc:
            regex_error = str(exc)
            matched = False
    else:
        matched = ratio >= float(settings["threshold"])
    return {
        "matched": matched,
        "mode": mode,
        "ignore_case": ignore_case,
        "similarity": round(ratio, 4),
        "threshold": float(settings["threshold"]),
        "regex_error": regex_error,
    }


def classify_checkpoint_failure(
    *,
    expected_sig: str,
    observed_sig: str,
    params: dict | None,
    timeout_reached: bool,
    concurrent_mode: bool,
) -> tuple[str, str, str]:
    if timeout_reached and not str(observed_sig or "").strip():
        return "timeout", "critical", "checkpoint sem resposta observável dentro da janela"
    if timeout_reached:
        return "timeout", "high", "checkpoint não estabilizou dentro da janela esperada"
    observed_text = str(observed_sig or "").lower()
    expected_text = str(expected_sig or "").lower()
    if concurrent_mode and observed_text and expected_text and SequenceMatcher(None, expected_text, observed_text).ratio() < 0.35:
        return "concurrency_error", "high", "divergência forte em modo concorrente"
    navigation_tokens = ("login", "menu", "erro", "opcao", "opção", "comando")
    if any(token in observed_text for token in navigation_tokens) and expected_text and observed_text != expected_text:
        return "navigation_error", "high", "sessão caiu em navegação diferente da esperada"
    return "screen_divergence", "high", "saída divergiu da tela esperada"


def add_run_failure(con, run_id: int, failure: dict) -> int:
    evidence = failure.get("evidence") or {}
    return exec1(
        con,
        """
        INSERT INTO replay_failures(
            run_id, ts_ms, session_id, seq_global, seq_session, flow_name,
            event_type, failure_type, severity, expected_value, observed_value,
            message, evidence_json
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            run_id,
            now_ms(),
            failure.get("session_id") or None,
            int(failure.get("seq_global") or 0),
            int(failure.get("seq_session") or 0),
            failure.get("flow_name") or None,
            failure.get("event_type") or "runtime",
            failure.get("failure_type") or "technical_error",
            failure.get("severity") or "high",
            failure.get("expected_value") or None,
            failure.get("observed_value") or None,
            failure.get("message") or "",
            json.dumps(evidence, ensure_ascii=False),
        ),
    )

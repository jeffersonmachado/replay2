from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from difflib import SequenceMatcher
from dataclasses import dataclass
from pathlib import Path

from .state_db import exec1, init_db, now_ms, query_all, query_one
from .verifier import verify_log, VerificationError
from .replay import ReplayConfig, ReplayError, _TargetSession, _decode_replay_input  # type: ignore
from .compliance import compliance_blocks_execution

import base64
import selectors
import random
from dataclasses import dataclass
from threading import Lock, Semaphore, Thread


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


def compute_last_hash_hint(log_dir: str) -> str:
    """
    Best-effort: read newest manifest and use last_hash; fallback to scan last JSONL line.
    """
    p = Path(log_dir)
    manifests = sorted(p.glob("audit-*.jsonl.manifest.json"))
    if manifests:
        # newest by name
        m = manifests[-1]
        try:
            d = json.loads(m.read_text(encoding="utf-8"))
            lh = d.get("last_hash") or ""
            if lh:
                return str(lh)
        except Exception:
            pass

    jsonls = sorted(p.glob("audit-*.jsonl"))
    if not jsonls:
        return ""
    last = jsonls[-1]
    try:
        # read last non-empty line
        lines = last.read_text(encoding="utf-8", errors="replace").splitlines()
        for line in reversed(lines):
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            if isinstance(d, dict):
                return str(d.get("hash") or "")
    except Exception:
        return ""
    return ""


def compute_fingerprint(log_dir: str, target_host: str, target_user: str, target_command: str, mode: str) -> str:
    hint = compute_last_hash_hint(log_dir)
    raw = f"{log_dir}|{target_host}|{target_user}|{target_command}|{mode}|{hint}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _iter_events(log_dir: str):
    for f in sorted(Path(log_dir).glob("audit-*.jsonl")):
        with open(f, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except Exception:
                    continue
                if isinstance(ev, dict):
                    yield ev


def _normalize_replay_window_params(params: dict | None) -> dict:
    raw = params if isinstance(params, dict) else {}

    def _as_int(name: str) -> int:
        value = raw.get(name)
        if value in (None, ""):
            return 0
        try:
            return max(0, int(value))
        except Exception as exc:
            raise ValueError(f"{name} inválido") from exc

    replay_from_seq_global = _as_int("replay_from_seq_global")
    replay_to_seq_global = _as_int("replay_to_seq_global")
    if replay_from_seq_global and replay_to_seq_global and replay_from_seq_global > replay_to_seq_global:
        raise ValueError("replay_from_seq_global maior que replay_to_seq_global")

    return {
        "replay_from_seq_global": replay_from_seq_global,
        "replay_to_seq_global": replay_to_seq_global,
        "replay_session_id": str(raw.get("replay_session_id") or "").strip(),
        "replay_from_checkpoint_sig": str(raw.get("replay_from_checkpoint_sig") or "").strip(),
        "input_mode": str(raw.get("input_mode") or "raw").strip().lower() or "raw",
        "on_deterministic_mismatch": str(raw.get("on_deterministic_mismatch") or "fail-fast").strip().lower() or "fail-fast",
    }


def _resolve_replay_window(log_dir: str, params: dict | None) -> dict:
    window = _normalize_replay_window_params(params)
    session_id_filter = window["replay_session_id"]
    checkpoint_sig = window["replay_from_checkpoint_sig"]
    if checkpoint_sig:
        checkpoint_event = None
        for ev in _iter_events(log_dir):
            if str(ev.get("type") or "") != "checkpoint":
                continue
            if session_id_filter and str(ev.get("session_id") or "") != session_id_filter:
                continue
            if str(ev.get("sig") or "") != checkpoint_sig:
                continue
            checkpoint_event = ev
            break
        if not checkpoint_event:
            raise ReplayError(f"checkpoint inicial não encontrado: sig={checkpoint_sig!r}")
        checkpoint_seq = int(checkpoint_event.get("seq_global") or 0)
        if checkpoint_seq > 0:
            current_from = int(window.get("replay_from_seq_global") or 0)
            window["replay_from_seq_global"] = max(current_from, checkpoint_seq) if current_from else checkpoint_seq
            window["resolved_checkpoint_seq_global"] = checkpoint_seq
            window["resolved_checkpoint_session_id"] = str(checkpoint_event.get("session_id") or "")
    return window


def _event_in_replay_window(ev: dict, window: dict | None) -> bool:
    if not isinstance(ev, dict):
        return False
    selected = window if isinstance(window, dict) else {}
    session_id_filter = str(selected.get("replay_session_id") or "")
    if session_id_filter and str(ev.get("session_id") or "") != session_id_filter:
        return False
    seq_global = int(ev.get("seq_global") or 0)
    seq_from = int(selected.get("replay_from_seq_global") or 0)
    seq_to = int(selected.get("replay_to_seq_global") or 0)
    if seq_from and seq_global < seq_from:
        return False
    if seq_to and seq_global > seq_to:
        return False
    return True


def _selected_events(log_dir: str, params: dict | None):
    window = _resolve_replay_window(log_dir, params)
    for ev in _iter_events(log_dir):
        if _event_in_replay_window(ev, window):
            yield ev


def compute_seq_end(log_dir: str, params: dict | None = None) -> int:
    if params:
        last_seq = 0
        for ev in _selected_events(log_dir, params):
            last_seq = max(last_seq, int(ev.get("seq_global") or 0))
        if last_seq:
            return last_seq
    # best-effort: read newest manifest and use seq_end; fallback 0
    p = Path(log_dir)
    manifests = sorted(p.glob("audit-*.jsonl.manifest.json"))
    if manifests:
        try:
            d = json.loads(manifests[-1].read_text(encoding="utf-8"))
            return int(d.get("seq_end") or 0)
        except Exception:
            return 0
    return 0


def _replay_input_mode(params: dict | None) -> str:
    mode = str((params or {}).get("input_mode") or "raw").strip().lower()
    return mode if mode in {"raw", "deterministic"} else "raw"


def _on_deterministic_mismatch(params: dict | None) -> str:
    mode = str((params or {}).get("on_deterministic_mismatch") or "fail-fast").strip().lower()
    return mode if mode in {"fail-fast", "skip", "send-anyway"} else "fail-fast"


def _is_replay_input_event(ev: dict, *, input_mode: str) -> bool:
    typ = str(ev.get("type") or "")
    if input_mode == "deterministic":
        return typ == "deterministic_input"
    return typ == "bytes" and ev.get("dir") == "in"


def _deterministic_failure(
    *,
    sid: str,
    seq_global: int,
    seq_session: int,
    expected_sig: str,
    observed_sig: str,
    params: dict | None,
    checkpoint_timeout_ms: int,
    checkpoint_quiet_ms: int,
    mode_label: str,
    concurrent_mode: bool,
) -> dict:
    match = evaluate_checkpoint_match(expected_sig, observed_sig, params)
    failure_type, severity, reason = classify_checkpoint_failure(
        expected_sig=expected_sig,
        observed_sig=observed_sig,
        params=params,
        timeout_reached=True,
        concurrent_mode=concurrent_mode,
    )
    mismatch_mode = _on_deterministic_mismatch(params)
    action = "failed"
    if mismatch_mode == "skip":
        action = "skipped"
    elif mismatch_mode == "send-anyway":
        action = "sent_anyway"
    return build_failure_record(
        session_id=sid,
        seq_global=seq_global,
        seq_session=seq_session,
        event_type="deterministic_input",
        failure_type=failure_type,
        severity=severity,
        expected_value=expected_sig,
        observed_value=observed_sig,
        message=f"{reason} session={sid}: expected={expected_sig!r} got={observed_sig!r} action={action}",
        evidence={
            "checkpoint_timeout_ms": checkpoint_timeout_ms,
            "checkpoint_quiet_ms": checkpoint_quiet_ms,
            "mode": mode_label,
            "match": match,
            "action": action,
            "mismatch_mode": mismatch_mode,
        },
    )


def _should_apply_deterministic_input(on_failure, failure: dict, *, params: dict | None) -> bool:
    on_failure(failure)
    mode = _on_deterministic_mismatch(params)
    if mode == "skip":
        return False
    if mode == "send-anyway":
        return True
    raise ReplayError(str(failure.get("message") or "deterministic replay mismatch"))


def replay_strict_global_controlled(
    cfg: ReplayConfig,
    params: dict | None = None,
    *,
    should_pause_or_cancel,
    on_progress,
    on_failure,
    checkpoint_timeout_ms: int = 5000,
):
    sessions: dict[str, _TargetSession] = {}
    sel = selectors.DefaultSelector()
    input_mode = _replay_input_mode(params)

    def get_sess(sid: str) -> _TargetSession:
        if sid not in sessions:
            s = _TargetSession(cfg, sid)
            sessions[sid] = s
            sel.register(s.master_fd, selectors.EVENT_READ, data=sid)
        return sessions[sid]

    def drain_output(timeout: float = 0.05):
        events = sel.select(timeout=timeout)
        for key, _ in events:
            sid2 = key.data
            try:
                _ = sessions[sid2].read_out()
            except Exception:
                pass

    def wait_checkpoint(sid: str, expected_sig: str, seq_global: int, seq_session: int = 0):
        s = get_sess(sid)
        deadline = int(time.time() * 1000) + checkpoint_timeout_ms
        last_got = ""
        while int(time.time() * 1000) < deadline:
            should_pause_or_cancel()
            drain_output(0.05)
            quiet = int(time.time() * 1000) - s.last_out_ms
            if quiet >= cfg.checkpoint_quiet_ms:
                got = s.signature_now()
                last_got = got
                match = evaluate_checkpoint_match(expected_sig, got, params)
                if match["matched"]:
                    return
            time.sleep(0.02)
        got = s.signature_now() or last_got
        match = evaluate_checkpoint_match(expected_sig, got, params)
        failure_type, severity, reason = classify_checkpoint_failure(
            expected_sig=expected_sig,
            observed_sig=got,
            params=params,
            timeout_reached=True,
            concurrent_mode=False,
        )
        on_failure(
            build_failure_record(
                session_id=sid,
                seq_global=seq_global,
                seq_session=seq_session,
                event_type="checkpoint",
                failure_type=failure_type,
                severity=severity,
                expected_value=expected_sig,
                observed_value=got,
                message=f"{reason} session={sid}: expected={expected_sig!r} got={got!r}",
                evidence={
                    "checkpoint_timeout_ms": checkpoint_timeout_ms,
                    "checkpoint_quiet_ms": cfg.checkpoint_quiet_ms,
                    "mode": "strict-global",
                    "match": match,
                },
            )
        )
        raise ReplayError(f"{reason} session={sid}: expected={expected_sig!r} got={got!r}")

    try:
        for ev in _selected_events(cfg.log_dir, params):
            should_pause_or_cancel()
            seq_global = int(ev.get("seq_global") or 0)
            typ = ev.get("type") or ""
            sid = ev.get("session_id") or ""

            if _is_replay_input_event(ev, input_mode=input_mode) and sid:
                expected_sig = str(ev.get("screen_sig") or "") if input_mode == "deterministic" else ""
                if expected_sig:
                    try:
                        wait_checkpoint(sid, expected_sig, seq_global, int(ev.get("seq_session") or 0))
                    except ReplayError:
                        if input_mode != "deterministic":
                            raise
                        failure = _deterministic_failure(
                            sid=sid,
                            seq_global=seq_global,
                            seq_session=int(ev.get("seq_session") or 0),
                            expected_sig=expected_sig,
                            observed_sig=get_sess(sid).signature_now(),
                            params=params,
                            checkpoint_timeout_ms=checkpoint_timeout_ms,
                            checkpoint_quiet_ms=cfg.checkpoint_quiet_ms,
                            mode_label="strict-global-deterministic",
                            concurrent_mode=False,
                        )
                        if not _should_apply_deterministic_input(on_failure, failure, params=params):
                            on_progress(seq_global, None)
                            continue
                data = _decode_replay_input(ev)
                if data:
                    s = get_sess(sid)
                    s.write_in(data)
                on_progress(seq_global, expected_sig or None)
                drain_output(0.0)
            elif typ == "checkpoint" and sid:
                expected_sig = ev.get("sig") or ""
                if expected_sig:
                    wait_checkpoint(sid, expected_sig, seq_global, int(ev.get("seq_session") or 0))
                    on_progress(seq_global, expected_sig)

        end_deadline = time.time() + 0.25
        while time.time() < end_deadline:
            should_pause_or_cancel()
            drain_output(0.05)
    finally:
        try:
            sel.close()
        except Exception:
            pass
        for s in sessions.values():
            s.close()


def replay_parallel_sessions_controlled(
    cfg: ReplayConfig,
    params: dict | None = None,
    *,
    should_pause_or_cancel,
    on_progress,
    on_failure,
    checkpoint_timeout_ms: int = 5000,
):
    input_mode = _replay_input_mode(params)
    per_session: dict[str, list[dict]] = {}
    for ev in _selected_events(cfg.log_dir, params):
        sid = ev.get("session_id") or ""
        if sid:
            per_session.setdefault(sid, []).append(ev)

    for sid, events in per_session.items():
        should_pause_or_cancel()
        s = _TargetSession(cfg, sid)
        sel = selectors.DefaultSelector()
        sel.register(s.master_fd, selectors.EVENT_READ, data=sid)
        try:
            for ev in events:
                should_pause_or_cancel()
                seq_global = int(ev.get("seq_global") or 0)
                typ = ev.get("type") or ""
                if _is_replay_input_event(ev, input_mode=input_mode):
                    expected_sig = str(ev.get("screen_sig") or "") if input_mode == "deterministic" else ""
                    if expected_sig:
                        deadline = int(time.time() * 1000) + checkpoint_timeout_ms
                        last_got = ""
                        while int(time.time() * 1000) < deadline:
                            should_pause_or_cancel()
                            events2 = sel.select(timeout=0.05)
                            for _, _ in events2:
                                try:
                                    _ = s.read_out()
                                except Exception:
                                    pass
                            quiet = int(time.time() * 1000) - s.last_out_ms
                            if quiet >= cfg.checkpoint_quiet_ms:
                                got = s.signature_now()
                                last_got = got
                                match = evaluate_checkpoint_match(expected_sig, got, params)
                                if match["matched"]:
                                    break
                            time.sleep(0.02)
                        else:
                            got = s.signature_now() or last_got
                            failure = _deterministic_failure(
                                sid=sid,
                                seq_global=seq_global,
                                seq_session=int(ev.get("seq_session") or 0),
                                expected_sig=expected_sig,
                                observed_sig=got,
                                params=params,
                                checkpoint_timeout_ms=checkpoint_timeout_ms,
                                checkpoint_quiet_ms=cfg.checkpoint_quiet_ms,
                                mode_label="parallel-sessions-deterministic",
                                concurrent_mode=False,
                            )
                            if not _should_apply_deterministic_input(on_failure, failure, params=params):
                                on_progress(seq_global, None)
                                continue
                    data = _decode_replay_input(ev)
                    if data:
                        s.write_in(data)
                    on_progress(seq_global, expected_sig or None)
                elif typ == "checkpoint":
                    expected_sig = ev.get("sig") or ""
                    if expected_sig:
                        deadline = int(time.time() * 1000) + checkpoint_timeout_ms
                        last_got = ""
                        while int(time.time() * 1000) < deadline:
                            should_pause_or_cancel()
                            events2 = sel.select(timeout=0.05)
                            for _, _ in events2:
                                try:
                                    _ = s.read_out()
                                except Exception:
                                    pass
                            quiet = int(time.time() * 1000) - s.last_out_ms
                            if quiet >= cfg.checkpoint_quiet_ms:
                                got = s.signature_now()
                                last_got = got
                                match = evaluate_checkpoint_match(expected_sig, got, params)
                                if match["matched"]:
                                    break
                            time.sleep(0.02)
                        else:
                            got = s.signature_now() or last_got
                            match = evaluate_checkpoint_match(expected_sig, got, params)
                            failure_type, severity, reason = classify_checkpoint_failure(
                                expected_sig=expected_sig,
                                observed_sig=got,
                                params=params,
                                timeout_reached=True,
                                concurrent_mode=False,
                            )
                            on_failure(
                                build_failure_record(
                                    session_id=sid,
                                    seq_global=seq_global,
                                    seq_session=int(ev.get("seq_session") or 0),
                                    event_type="checkpoint",
                                    failure_type=failure_type,
                                    severity=severity,
                                    expected_value=expected_sig,
                                    observed_value=got,
                                    message=f"{reason} session={sid}: expected={expected_sig!r} got={got!r}",
                                    evidence={
                                        "checkpoint_timeout_ms": checkpoint_timeout_ms,
                                        "checkpoint_quiet_ms": cfg.checkpoint_quiet_ms,
                                        "mode": "parallel-sessions",
                                        "match": match,
                                    },
                                )
                            )
                            raise ReplayError(f"{reason} session={sid}: expected={expected_sig!r} got={got!r}")
                        on_progress(seq_global, expected_sig)
        finally:
            try:
                sel.close()
            except Exception:
                pass
            s.close()


@dataclass
class LoadTestParams:
    concurrency: int = 10
    ramp_up_per_sec: float = 1.0
    speed: float = 1.0
    jitter_ms: int = 0
    on_checkpoint_mismatch: str = "continue"  # continue|fail-fast
    target_user_pool: list[str] | None = None
    match_mode: str = "strict"
    match_threshold: float = 0.92
    match_ignore_case: bool = False
    input_mode: str = "raw"
    on_deterministic_mismatch: str = "fail-fast"


def replay_parallel_sessions_concurrent_controlled(
    cfg: ReplayConfig,
    load_params: LoadTestParams,
    *,
    window_params: dict | None = None,
    should_pause_or_cancel,
    on_progress,
    on_session_result,
    on_failure,
    checkpoint_timeout_ms: int = 5000,
):
    """
    Replay por sessão com concorrência limitada e ramp-up.
    - Cada session_id roda em uma thread, preservando ordem por sessão.
    - Checkpoint mismatch pode falhar só a sessão (continue) ou o run inteiro (fail-fast).
    - speed/jitter controlam pacing entre eventos de input (bytes dir=in) baseado em ts_ms.
    - target_user_pool distribui sessões entre usuários no destino.
    """

    input_mode = _replay_input_mode(load_params.__dict__)
    per_session: dict[str, list[dict]] = {}
    for ev in _selected_events(cfg.log_dir, window_params):
        sid = ev.get("session_id") or ""
        if sid:
            per_session.setdefault(sid, []).append(ev)

    session_ids = sorted(per_session.keys())
    if load_params.concurrency < 1:
        load_params.concurrency = 1
    sem = Semaphore(load_params.concurrency)

    stop_all = {"flag": False, "err": ""}
    stop_lock = Lock()

    def pick_user(sid: str) -> str | None:
        pool = load_params.target_user_pool or []
        if not pool:
            return None
        # stable mapping by hash
        idx = (hash(sid) & 0x7FFFFFFF) % len(pool)
        return pool[idx]

    def sleep_scaled(ms: int):
        if ms <= 0:
            return
        time.sleep(ms / 1000.0)

    def worker(sid: str, events: list[dict]):
        nonlocal stop_all
        sem.acquire()
        try:
            should_pause_or_cancel()
            with stop_lock:
                if stop_all["flag"]:
                    on_session_result(sid, "skipped", stop_all["err"])
                    return

            user_override = pick_user(sid)
            s = _TargetSession(cfg, sid, target_user_override=user_override)
            sel = selectors.DefaultSelector()
            sel.register(s.master_fd, selectors.EVENT_READ, data=sid)
            last_in_ts = None
            try:
                for ev in events:
                    should_pause_or_cancel()
                    with stop_lock:
                        if stop_all["flag"]:
                            on_session_result(sid, "stopped", stop_all["err"])
                            return

                    seq_global = int(ev.get("seq_global") or 0)
                    typ = ev.get("type") or ""
                    if _is_replay_input_event(ev, input_mode=input_mode):
                        ts = int(ev.get("ts_ms") or 0)
                        if last_in_ts is not None and load_params.speed > 0:
                            delta = max(0, ts - last_in_ts)
                            scaled = int(delta / float(load_params.speed))
                            if load_params.jitter_ms > 0:
                                scaled += random.randint(0, load_params.jitter_ms)
                            # sleep is cooperative with pause/cancel (chunked)
                            end = time.time() + (scaled / 1000.0)
                            while time.time() < end:
                                should_pause_or_cancel()
                                time.sleep(min(0.05, end - time.time()))
                        last_in_ts = ts

                        expected_sig = str(ev.get("screen_sig") or "") if input_mode == "deterministic" else ""
                        if expected_sig:
                            deadline = int(time.time() * 1000) + checkpoint_timeout_ms
                            last_got = ""
                            while int(time.time() * 1000) < deadline:
                                should_pause_or_cancel()
                                events2 = sel.select(timeout=0.05)
                                for _, _ in events2:
                                    try:
                                        _ = s.read_out()
                                    except Exception:
                                        pass
                                quiet = int(time.time() * 1000) - s.last_out_ms
                                if quiet >= cfg.checkpoint_quiet_ms:
                                    got = s.signature_now()
                                    last_got = got
                                    match = evaluate_checkpoint_match(expected_sig, got, load_params.__dict__)
                                    if match["matched"]:
                                        break
                                time.sleep(0.02)
                            else:
                                got = s.signature_now() or last_got
                                failure = _deterministic_failure(
                                    sid=sid,
                                    seq_global=seq_global,
                                    seq_session=int(ev.get("seq_session") or 0),
                                    expected_sig=expected_sig,
                                    observed_sig=got,
                                    params=load_params.__dict__,
                                    checkpoint_timeout_ms=checkpoint_timeout_ms,
                                    checkpoint_quiet_ms=cfg.checkpoint_quiet_ms,
                                    mode_label="parallel-sessions-concurrent-deterministic",
                                    concurrent_mode=True,
                                )
                                msg = str(failure.get("message") or "")
                                try:
                                    should_apply = _should_apply_deterministic_input(on_failure, failure, params=load_params.__dict__)
                                except ReplayError:
                                    on_session_result(sid, "failed", msg)
                                    if load_params.on_checkpoint_mismatch == "fail-fast":
                                        with stop_lock:
                                            stop_all["flag"] = True
                                            stop_all["err"] = msg
                                    return
                                if not should_apply:
                                    on_progress(seq_global, None)
                                    continue

                        data = _decode_replay_input(ev)
                        if data:
                            s.write_in(data)
                        on_progress(seq_global, expected_sig or None)
                    elif typ == "checkpoint":
                        expected_sig = ev.get("sig") or ""
                        if expected_sig:
                            deadline = int(time.time() * 1000) + checkpoint_timeout_ms
                            last_got = ""
                            while int(time.time() * 1000) < deadline:
                                should_pause_or_cancel()
                                events2 = sel.select(timeout=0.05)
                                for _, _ in events2:
                                    try:
                                        _ = s.read_out()
                                    except Exception:
                                        pass
                                quiet = int(time.time() * 1000) - s.last_out_ms
                                if quiet >= cfg.checkpoint_quiet_ms:
                                    got = s.signature_now()
                                    last_got = got
                                    match = evaluate_checkpoint_match(expected_sig, got, load_params.__dict__)
                                    if match["matched"]:
                                        on_progress(seq_global, expected_sig)
                                        break
                                time.sleep(0.02)
                            else:
                                got = s.signature_now() or last_got
                                match = evaluate_checkpoint_match(expected_sig, got, load_params.__dict__)
                                failure_type, severity, reason = classify_checkpoint_failure(
                                    expected_sig=expected_sig,
                                    observed_sig=got,
                                    params=load_params.__dict__,
                                    timeout_reached=True,
                                    concurrent_mode=True,
                                )
                                msg = f"{reason} session={sid}: expected={expected_sig!r} got={got!r}"
                                on_failure(
                                    build_failure_record(
                                        session_id=sid,
                                        seq_global=seq_global,
                                        seq_session=int(ev.get("seq_session") or 0),
                                        event_type="checkpoint",
                                        failure_type=failure_type,
                                        severity=severity,
                                        expected_value=expected_sig,
                                        observed_value=got,
                                        message=msg,
                                        evidence={
                                            "checkpoint_timeout_ms": checkpoint_timeout_ms,
                                            "checkpoint_quiet_ms": cfg.checkpoint_quiet_ms,
                                            "mode": "parallel-sessions-concurrent",
                                            "match": match,
                                        },
                                    )
                                )
                                on_session_result(sid, "failed", msg)
                                if load_params.on_checkpoint_mismatch == "fail-fast":
                                    with stop_lock:
                                        stop_all["flag"] = True
                                        stop_all["err"] = msg
                                return

                on_session_result(sid, "success", "")
            finally:
                try:
                    sel.close()
                except Exception:
                    pass
                s.close()
        except ReplayError as e:
            msg = str(e)
            on_session_result(sid, "failed", msg)
            if load_params.on_checkpoint_mismatch == "fail-fast":
                with stop_lock:
                    stop_all["flag"] = True
                    stop_all["err"] = msg
        finally:
            sem.release()

    threads: list[Thread] = []
    # ramp-up: start threads gradually
    interval = 0.0
    if load_params.ramp_up_per_sec and load_params.ramp_up_per_sec > 0:
        interval = 1.0 / float(load_params.ramp_up_per_sec)

    for idx, sid in enumerate(session_ids):
        should_pause_or_cancel()
        t = Thread(target=worker, args=(sid, per_session[sid]), daemon=True)
        threads.append(t)
        t.start()
        if interval > 0 and idx < len(session_ids) - 1:
            end = time.time() + interval
            while time.time() < end:
                should_pause_or_cancel()
                time.sleep(min(0.05, end - time.time()))

    # wait all
    for t in threads:
        while t.is_alive():
            should_pause_or_cancel()
            t.join(timeout=0.1)

    with stop_lock:
        if stop_all["flag"] and load_params.on_checkpoint_mismatch == "fail-fast":
            raise ReplayError(stop_all["err"])

def create_run(
    con,
    created_by: int,
    log_dir: str,
    target_host: str,
    target_user: str,
    target_command: str,
    mode: str,
    parent_run_id: int | None = None,
    *,
    target_env_id: int | None = None,
    connection_profile_id: int | None = None,
) -> int:
    fp = compute_fingerprint(log_dir, target_host, target_user, target_command, mode)
    rid = exec1(
        con,
        """
        INSERT INTO replay_runs(created_at_ms, created_by, target_env_id, connection_profile_id, log_dir, target_host, target_user, target_command, mode,
                               params_json, metrics_json, run_fingerprint, status, parent_run_id)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?, 'queued', ?)
        """,
        (
            now_ms(),
            created_by,
            target_env_id,
            connection_profile_id,
            log_dir,
            target_host,
            target_user,
            target_command,
            mode,
            None,
            None,
            fp,
            parent_run_id,
        ),
    )
    add_run_event(con, rid, "created", f"run criado (mode={mode})", {"fingerprint": fp})
    return rid


def set_run_compliance(con, run_id: int, compliance: dict | None) -> None:
    clean = compliance if isinstance(compliance, dict) else {}
    con.execute(
        """
        UPDATE replay_runs
        SET entry_mode=?, via_gateway=?, gateway_session_id=?, gateway_endpoint=?,
            compliance_status=?, compliance_reason=?, validated_at_ms=?
        WHERE id=?
        """,
        (
            str(clean.get("entry_mode") or "") or None,
            1 if clean.get("via_gateway") else 0,
            str(clean.get("gateway_session_id") or "") or None,
            str(clean.get("gateway_endpoint") or "") or None,
            str(clean.get("compliance_status") or "not_applicable"),
            str(clean.get("compliance_reason") or "") or None,
            int(clean.get("validated_at_ms") or 0) or None,
            run_id,
        ),
    )
    add_run_event(
        con,
        run_id,
        "compliance",
        str(clean.get("compliance_reason") or "compliance avaliado"),
        {
            "compliance_status": str(clean.get("compliance_status") or "not_applicable"),
            "via_gateway": bool(clean.get("via_gateway")),
            "gateway_session_id": str(clean.get("gateway_session_id") or ""),
            "gateway_endpoint": str(clean.get("gateway_endpoint") or ""),
        },
    )


def add_run_event(con, run_id: int, kind: str, message: str, data: dict | None = None) -> None:
    exec1(
        con,
        "INSERT INTO replay_run_events(run_id, ts_ms, kind, message, data_json) VALUES(?,?,?,?,?)",
        (run_id, now_ms(), kind, message, json.dumps(data or {}, ensure_ascii=False)),
    )


def set_run_status(con, run_id: int, status: str, error: str | None = None) -> None:
    exec1(
        con,
        "UPDATE replay_runs SET status=?, error=? WHERE id=?",
        (status, error or None, run_id),
    )
    add_run_event(con, run_id, "status", f"status={status}", {"error": error or ""})


def update_progress(con, run_id: int, last_seq_global: int, last_sig: str | None = None) -> None:
    exec1(
        con,
        "UPDATE replay_runs SET last_seq_global_applied=?, last_checkpoint_sig=? WHERE id=?",
        (int(last_seq_global), last_sig, run_id),
    )


def get_run(con, run_id: int):
    return query_one(con, "SELECT * FROM replay_runs WHERE id=?", (run_id,))


def pause_run(con, run_id: int) -> None:
    set_run_status(con, run_id, "paused")


def resume_run(con, run_id: int) -> None:
    set_run_status(con, run_id, "running")


def cancel_run(con, run_id: int) -> None:
    set_run_status(con, run_id, "cancelled")


def retry_run(con, run_id: int, created_by: int) -> int:
    run = get_run(con, run_id)
    if not run:
        raise ValueError("run inexistente")
    new_run_id = create_run(
        con,
        created_by=created_by,
        log_dir=run["log_dir"],
        target_host=run["target_host"],
        target_user=run["target_user"],
        target_command=run["target_command"],
        mode=run["mode"],
        parent_run_id=run_id,
        target_env_id=int(run["target_env_id"]) if "target_env_id" in run.keys() and run["target_env_id"] is not None else None,
        connection_profile_id=int(run["connection_profile_id"]) if "connection_profile_id" in run.keys() and run["connection_profile_id"] is not None else None,
    )
    if run["params_json"]:
        con.execute("UPDATE replay_runs SET params_json=? WHERE id=?", (run["params_json"], new_run_id))
    return new_run_id


class Runner:
    """
    In-process runner registry. Suitable for a single control plane instance.
    """

    def __init__(self, db_path: str, hmac_key: bytes):
        self.db_path = db_path
        self.hmac_key = hmac_key
        self._threads: dict[int, threading.Thread] = {}

    def start_run_async(self, run_id: int) -> None:
        if run_id in self._threads and self._threads[run_id].is_alive():
            return
        t = threading.Thread(target=self._run, args=(run_id,), daemon=True)
        self._threads[run_id] = t
        t.start()

    def run_foreground(self, run_id: int) -> None:
        self._run(run_id)

    def _run(self, run_id: int) -> None:
        import sqlite3

        con = sqlite3.connect(self.db_path, isolation_level=None, timeout=30)
        con.row_factory = sqlite3.Row
        init_db(con)

        run = get_run(con, run_id)
        if not run:
            return
        if run["status"] not in ("queued", "running", "paused"):
            return
        if compliance_blocks_execution(str(run["compliance_status"] or "")):
            message = str(run["compliance_reason"] or "run bloqueado pela policy de gateway-only")
            add_run_failure(
                con,
                run_id,
                build_failure_record(
                    session_id=str(run["gateway_session_id"] or ""),
                    seq_global=0,
                    event_type="run_compliance",
                    failure_type="technical_error",
                    severity="critical",
                    expected_value="run conforme com a policy do target",
                    observed_value=str(run["compliance_status"] or "rejected"),
                    message=message,
                    evidence={
                        "entry_mode": str(run["entry_mode"] or ""),
                        "via_gateway": bool(run["via_gateway"]),
                        "gateway_endpoint": str(run["gateway_endpoint"] or ""),
                    },
                ),
            )
            set_run_status(con, run_id, "failed", error=message)
            exec1(con, "UPDATE replay_runs SET finished_at_ms=? WHERE id=?", (now_ms(), run_id))
            return

        # mark running
        exec1(con, "UPDATE replay_runs SET status='running', started_at_ms=? WHERE id=?", (now_ms(), run_id))
        add_run_event(con, run_id, "start", "runner iniciou", {})

        # verify integrity before replay
        try:
            verify_log(run["log_dir"], self.hmac_key)
            exec1(con, "UPDATE replay_runs SET verify_ok=1, verify_error=NULL WHERE id=?", (run_id,))
        except VerificationError as e:
            add_run_failure(
                con,
                run_id,
                build_failure_record(
                    session_id="",
                    seq_global=0,
                    event_type="integrity_verify",
                    failure_type="integrity_error",
                    severity="critical",
                    expected_value="hash-chain+hmac válido",
                    observed_value=str(e),
                    message=f"integrity verify failed: {e}",
                    evidence={"log_dir": run["log_dir"]},
                ),
            )
            exec1(con, "UPDATE replay_runs SET verify_ok=0, verify_error=? WHERE id=?", (str(e), run_id))
            set_run_status(con, run_id, "failed", error=f"integrity verify failed: {e}")
            exec1(con, "UPDATE replay_runs SET finished_at_ms=? WHERE id=?", (now_ms(), run_id))
            return

        mode = run["mode"]

        last_seq = int(run["last_seq_global_applied"] or 0)

        def wait_if_paused_or_cancelled():
            while True:
                r = get_run(con, run_id)
                if not r:
                    raise ReplayError("run desapareceu")
                st = r["status"]
                if st == "cancelled":
                    raise ReplayError("cancelled")
                if st == "paused":
                    time.sleep(0.2)
                    continue
                return

        try:
            # Update progress by scanning seq_end from manifests when available.
            wait_if_paused_or_cancelled()

            cfg = ReplayConfig(
                log_dir=run["log_dir"],
                target_host=run["target_host"],
                target_user=run["target_user"],
                target_command=run["target_command"],
                transport=str(params.get("transport") or "ssh"),
                target_port=int(params.get("target_port") or params.get("port") or 0),
                gateway_host=str(params.get("gateway_host") or ""),
                gateway_user=str(params.get("gateway_user") or ""),
                gateway_port=int(params.get("gateway_port") or 0),
            )

            # Runner executes replay synchronously; pause/cancel are checked between coarse phases.
            # For MVP, we also emit heartbeat while running.
            add_run_event(con, run_id, "heartbeat", "running", {"last_seq_global_applied": last_seq})

            last_progress_write_ms = 0

            def on_progress(seq_global: int, sig: str | None):
                nonlocal last_seq, last_progress_write_ms
                if seq_global > last_seq:
                    last_seq = seq_global
                now = now_ms()
                if now - last_progress_write_ms >= 500:
                    update_progress(con, run_id, last_seq_global=last_seq, last_sig=sig)
                    last_progress_write_ms = now

            def should_pause_or_cancel():
                wait_if_paused_or_cancelled()

            # Load-test params
            params = {}
            try:
                if run["params_json"]:
                    params = json.loads(run["params_json"]) if isinstance(run["params_json"], str) else {}
            except Exception:
                params = {}
            cfg.input_mode = _replay_input_mode(params)
            cfg.on_deterministic_mismatch = _on_deterministic_mismatch(params)

            # Metrics aggregation (thread-safe because callbacks can be invoked from worker threads)
            m_lock = Lock()
            metrics = {
                "sessions_total": 0,
                "sessions_started": 0,
                "sessions_success": 0,
                "sessions_failed": 0,
                "sessions_skipped": 0,
                "checkpoints_ok": 0,
                "checkpoints_fail": 0,
                "last_seq_global_applied": last_seq,
                "last_checkpoint_sig": None,
                "failure_types": {},
                "severity_counts": {},
            }

            def write_metrics(throttle_ms: int = 500):
                # minimal throttling by timestamp in metrics dict (store last write)
                now = now_ms()
                last = getattr(write_metrics, "_last", 0)
                if now - last < throttle_ms:
                    return
                setattr(write_metrics, "_last", now)
                with m_lock:
                    exec1(
                        con,
                        "UPDATE replay_runs SET metrics_json=? WHERE id=?",
                        (json.dumps(metrics, ensure_ascii=False), run_id),
                    )

            def on_progress(seq_global: int, sig: str | None):
                nonlocal last_seq, last_progress_write_ms
                if seq_global > last_seq:
                    last_seq = seq_global
                now = now_ms()
                with m_lock:
                    metrics["last_seq_global_applied"] = last_seq
                    if sig:
                        metrics["last_checkpoint_sig"] = sig
                        metrics["checkpoints_ok"] += 1
                if now - last_progress_write_ms >= 500:
                    update_progress(con, run_id, last_seq_global=last_seq, last_sig=sig)
                    last_progress_write_ms = now
                write_metrics()

            def on_session_result(session_id: str, status: str, message: str):
                with m_lock:
                    if status == "success":
                        metrics["sessions_success"] += 1
                    elif status == "failed":
                        metrics["sessions_failed"] += 1
                    elif status == "skipped":
                        metrics["sessions_skipped"] += 1
                add_run_event(con, run_id, "session", f"{session_id} {status}", {"message": message})
                write_metrics()

            def on_failure(failure: dict):
                add_run_failure(con, run_id, failure)
                add_run_event(
                    con,
                    run_id,
                    "failure",
                    failure.get("message") or failure.get("failure_type") or "failure",
                    {
                        "session_id": failure.get("session_id") or "",
                        "seq_global": int(failure.get("seq_global") or 0),
                        "failure_type": failure.get("failure_type") or "",
                        "severity": failure.get("severity") or "",
                        "expected_value": failure.get("expected_value") or "",
                        "observed_value": failure.get("observed_value") or "",
                    },
                )
                with m_lock:
                    ftype = str(failure.get("failure_type") or "technical_error")
                    severity = str(failure.get("severity") or "high")
                    metrics["failure_types"][ftype] = int(metrics["failure_types"].get(ftype) or 0) + 1
                    metrics["severity_counts"][severity] = int(metrics["severity_counts"].get(severity) or 0) + 1
                    if failure.get("event_type") == "checkpoint":
                        metrics["checkpoints_fail"] += 1
                write_metrics()

            def should_pause_or_cancel():
                wait_if_paused_or_cancelled()

            if mode == "strict-global":
                replay_strict_global_controlled(
                    cfg,
                    params=params,
                    should_pause_or_cancel=should_pause_or_cancel,
                    on_progress=on_progress,
                    on_failure=on_failure,
                )
            else:
                # Decide between sequential and concurrent based on params.concurrency
                concurrency = int(params.get("concurrency") or 0)
                if concurrency and concurrency > 1:
                    lp = LoadTestParams(
                        concurrency=concurrency,
                        ramp_up_per_sec=float(params.get("ramp_up_per_sec") or 1.0),
                        speed=float(params.get("speed") or 1.0),
                        jitter_ms=int(params.get("jitter_ms") or 0),
                        on_checkpoint_mismatch=str(params.get("on_checkpoint_mismatch") or "continue"),
                        target_user_pool=list(params.get("target_user_pool") or []) or None,
                        match_mode=str(params.get("match_mode") or "strict"),
                        match_threshold=float(params.get("match_threshold") or 0.92),
                        match_ignore_case=bool(params.get("match_ignore_case") in (True, 1, "1", "true", "yes", "sim")),
                        input_mode=_replay_input_mode(params),
                        on_deterministic_mismatch=_on_deterministic_mismatch(params),
                    )
                    # precompute totals
                    with m_lock:
                        metrics["sessions_total"] = 0
                        metrics["sessions_total"] = len({(ev.get("session_id") or "") for ev in _iter_events(cfg.log_dir) if (ev.get("session_id") or "")})
                    write_metrics(throttle_ms=0)
                    replay_parallel_sessions_concurrent_controlled(
                        cfg,
                        lp,
                        window_params=params,
                        should_pause_or_cancel=should_pause_or_cancel,
                        on_progress=on_progress,
                        on_session_result=on_session_result,
                        on_failure=on_failure,
                    )
                else:
                    replay_parallel_sessions_controlled(
                        cfg,
                        params=params,
                        should_pause_or_cancel=should_pause_or_cancel,
                        on_progress=on_progress,
                        on_failure=on_failure,
                    )

            # set success
            update_progress(con, run_id, last_seq_global=compute_seq_end(run["log_dir"], params))
            write_metrics(throttle_ms=0)
            exec1(con, "UPDATE replay_runs SET finished_at_ms=? WHERE id=?", (now_ms(), run_id))
            # If any session failed in loadtest mode, mark failed (but run completed)
            try:
                mj = query_one(con, "SELECT metrics_json FROM replay_runs WHERE id=?", (run_id,))
                m = json.loads(mj["metrics_json"]) if mj and mj["metrics_json"] else {}
                if int(m.get("sessions_failed") or 0) > 0:
                    set_run_status(con, run_id, "failed", error="algumas sessões falharam (load test)")
                else:
                    set_run_status(con, run_id, "success")
            except Exception:
                set_run_status(con, run_id, "success")
        except ReplayError as e:
            msg = str(e)
            if msg == "cancelled":
                add_run_failure(
                    con,
                    run_id,
                    build_failure_record(
                        session_id="",
                        seq_global=last_seq,
                        event_type="run_control",
                        failure_type="cancelled",
                        severity="medium",
                        expected_value="run concluído",
                        observed_value="cancelled",
                        message="execução cancelada pelo operador",
                        evidence={"last_seq_global_applied": last_seq},
                    ),
                )
                exec1(con, "UPDATE replay_runs SET finished_at_ms=? WHERE id=?", (now_ms(), run_id))
                set_run_status(con, run_id, "cancelled")
            else:
                if msg != "run desapareceu":
                    add_run_failure(
                        con,
                        run_id,
                        build_failure_record(
                            session_id="",
                            seq_global=last_seq,
                            event_type="runner",
                            failure_type="technical_error",
                            severity="high",
                            expected_value="replay concluído sem exceção",
                            observed_value=msg,
                            message=msg,
                            evidence={"last_seq_global_applied": last_seq},
                        ),
                    )
                exec1(con, "UPDATE replay_runs SET finished_at_ms=? WHERE id=?", (now_ms(), run_id))
                set_run_status(con, run_id, "failed", error=msg)

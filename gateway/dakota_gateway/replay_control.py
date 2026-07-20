from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from .state_db import exec1, init_db, now_ms, query_all, query_one
from .verifier import verify_log, VerificationError
from .replay import ReplayConfig, ReplayError, SessionReplayState, _TargetSession, _decode_replay_input, _session_config_from_event  # type: ignore
from .compliance import compliance_blocks_execution
from .replay_failures import (
    add_run_failure,
    build_failure_record,
    classify_checkpoint_failure,
)
from .replay_run_state import add_run_event, get_run, set_run_status, update_progress
from .terminal_config import TerminalGeometry, normalize_encoding, validate_terminal_geometry
from dakota_terminal.comparison import compare_signatures, resolve_comparison_mode

import base64
import selectors
import random
from dataclasses import dataclass
from threading import Lock, Semaphore, Thread

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


def _first_session_start(log_dir: str, session_id: str | None = None) -> dict:
    clean_sid = str(session_id or "").strip()
    for ev in _iter_events(log_dir):
        if isinstance(ev, dict) and ev.get("type") == "session_start":
            if clean_sid and str(ev.get("session_id") or "").strip() != clean_sid:
                continue
            return ev
    return {}


def _terminal_options_from_run(log_dir: str, params: dict) -> dict:
    session_start = _first_session_start(log_dir, params.get("replay_session_id"))
    try:
        if session_start.get("rows") is not None and session_start.get("cols") is not None:
            geom = validate_terminal_geometry(int(session_start.get("rows")), int(session_start.get("cols")))
        else:
            geom = TerminalGeometry(25, 80)
        if params.get("rows") is not None or params.get("cols") is not None:
            rows = geom.rows if params.get("rows") is None else int(params.get("rows"))
            cols = geom.cols if params.get("cols") is None else int(params.get("cols"))
            geom = validate_terminal_geometry(rows, cols)
    except Exception:
        geom = TerminalGeometry(25, 80)
    return {
        "rows": geom.rows,
        "cols": geom.cols,
        "term": str(params.get("term") or session_start.get("term") or "xterm"),
        "encoding": normalize_encoding(params.get("encoding") or session_start.get("encoding") or "utf-8"),
    }


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
    match: dict | None = None,
) -> dict:
    match = match or {
        "comparison_mode_requested": _comparison_mode_from_params(params),
        "comparison_mode_used": "legacy_screen_sig",
        "expected_sig": expected_sig,
        "observed_sig": observed_sig,
        "matched": expected_sig == observed_sig,
        "fallback_reason": "legacy_deterministic_failure_adapter",
    }
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


def _comparison_mode_from_params(params: dict | None, default: str = "visual") -> str:
    return resolve_comparison_mode(replay=params, default=default)["comparison_mode"]


def _legacy_checkpoint_expected(sig: str) -> dict:
    return {"screen_sig": str(sig or "")}


def _expected_snapshot_from_event(ev: dict, *, legacy_sig: str = "") -> dict:
    return {
        "text_sig": str(ev.get("expected_text_sig") or ev.get("text_sig") or ""),
        "visual_sig": str(ev.get("expected_visual_sig") or ev.get("visual_sig") or ""),
        "semantic_sig": str(ev.get("expected_semantic_sig") or ev.get("semantic_sig") or ""),
        "screen_sig": str(legacy_sig or ev.get("screen_sig") or ev.get("sig") or ""),
    }


def _event_requires_deterministic_comparison(
    ev: dict,
    params: dict | None,
    *,
    session_config: ReplayConfig | SessionReplayState | dict | None = None,
    replay_config: ReplayConfig | dict | None = None,
) -> bool:
    expected = _expected_snapshot_from_event(ev)
    mode = resolve_comparison_mode(event=ev, session=session_config, replay=replay_config or params)["comparison_mode"]
    if mode == "visual":
        has_canonical = bool(expected.get("visual_sig"))
    elif mode == "text":
        has_canonical = bool(expected.get("text_sig"))
    elif mode == "semantic":
        has_canonical = bool(expected.get("semantic_sig"))
    else:
        has_canonical = any(
            bool(expected.get(key))
            for key in ("visual_sig", "text_sig", "semantic_sig")
        )
    # Legacy screen_sig also triggers comparison (backward compat)
    return has_canonical or bool(expected.get("screen_sig"))


def _match_failure_values(match: dict, expected_snapshot: dict, observed_snapshot: dict) -> tuple[str, str]:
    expected_sig = str(match.get("expected_sig") or expected_snapshot.get("screen_sig") or "")
    observed_sig = str(match.get("observed_sig") or observed_snapshot.get("screen_sig") or "")
    return expected_sig, observed_sig


def _observed_snapshot_from_session(session: _TargetSession) -> dict:
    if hasattr(session, "canonical_snapshot_now"):
        return session.canonical_snapshot_now()
    return {"text_sig": "", "visual_sig": "", "semantic_sig": "", "screen_sig": ""}


def _session_start_by_id(events: list[dict], sid: str) -> dict:
    for ev in events:
        if ev.get("type") == "session_start" and str(ev.get("session_id") or "") == sid:
            return ev
    return {"session_id": sid}


def _state_for_session(cfg: ReplayConfig, sid: str, ev: dict | None = None) -> SessionReplayState:
    session_cfg = _session_config_from_event(cfg, ev or {"session_id": sid})
    return SessionReplayState(
        session_id=sid,
        config=session_cfg,
        rows=session_cfg.rows,
        cols=session_cfg.cols,
        term=session_cfg.term,
        encoding=session_cfg.encoding,
        comparison_mode=session_cfg.comparison_mode,
    )


def compare_expected_observed(
    expected_snapshot: dict,
    observed_snapshot: dict,
    params: dict | None,
    *,
    event: dict | None = None,
    session_config: ReplayConfig | SessionReplayState | dict | None = None,
    replay_config: ReplayConfig | dict | None = None,
) -> dict:
    return compare_signatures(
        expected_snapshot,
        observed_snapshot,
        mode=resolve_comparison_mode(event=event, session=session_config, replay=replay_config or params)["comparison_mode"],
        legacy_expected_screen_sig=str(expected_snapshot.get("screen_sig") or ""),
        legacy_observed_screen_sig=str(observed_snapshot.get("screen_sig") or ""),
    )


def _wait_for_expected_observed(
    *,
    session: _TargetSession,
    selector: selectors.BaseSelector,
    expected_event: dict,
    params: dict | None,
    should_pause_or_cancel,
    checkpoint_quiet_ms: int,
    checkpoint_timeout_ms: int,
    session_config: ReplayConfig | SessionReplayState | dict | None = None,
    replay_config: ReplayConfig | dict | None = None,
) -> tuple[bool, dict, dict]:
    expected_snapshot = _expected_snapshot_from_event(expected_event)
    deadline = int(time.time() * 1000) + checkpoint_timeout_ms
    last_observed = {}
    last_match = compare_expected_observed(expected_snapshot, {}, params, event=expected_event, session_config=session_config, replay_config=replay_config)
    while int(time.time() * 1000) < deadline:
        should_pause_or_cancel()
        for _, _ in selector.select(timeout=0.05):
            try:
                session.read_out()
            except Exception:
                pass
        quiet = int(time.time() * 1000) - session.last_out_ms
        if quiet >= checkpoint_quiet_ms:
            observed = _observed_snapshot_from_session(session)
            last_observed = observed
            last_match = compare_expected_observed(expected_snapshot, observed, params, event=expected_event, session_config=session_config, replay_config=replay_config)
            if last_match["matched"]:
                return True, last_match, observed
        time.sleep(0.02)
    observed = last_observed or _observed_snapshot_from_session(session)
    return False, compare_expected_observed(expected_snapshot, observed, params, event=expected_event, session_config=session_config, replay_config=replay_config), observed


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
    states: dict[str, SessionReplayState] = {}
    session_configs: dict[str, ReplayConfig] = {}
    sel = selectors.DefaultSelector()
    input_mode = _replay_input_mode(params)

    def remember_session_start(sid: str, ev: dict) -> None:
        if sid not in session_configs:
            state = _state_for_session(cfg, sid, ev)
            states[sid] = state
            session_configs[sid] = state.config

    def get_sess(sid: str, ev: dict | None = None) -> _TargetSession:
        if sid not in sessions:
            if sid not in session_configs:
                state = _state_for_session(cfg, sid, ev)
                states[sid] = state
                session_configs[sid] = state.config
            s = _TargetSession(session_configs[sid], sid)
            states[sid].engine = s.screen_state
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

    def wait_checkpoint(sid: str, expected_event: dict, seq_global: int, seq_session: int = 0):
        s = get_sess(sid)
        deadline = int(time.time() * 1000) + checkpoint_timeout_ms
        expected_snapshot = _expected_snapshot_from_event(expected_event)
        last_observed = {}
        while int(time.time() * 1000) < deadline:
            should_pause_or_cancel()
            drain_output(0.05)
            quiet = int(time.time() * 1000) - s.last_out_ms
            if quiet >= cfg.checkpoint_quiet_ms:
                observed = _observed_snapshot_from_session(s)
                last_observed = observed
                match = compare_expected_observed(expected_snapshot, observed, params, event=expected_event, session_config=session_configs.get(sid), replay_config=cfg)
                if match["matched"]:
                    return
            time.sleep(0.02)
        observed = last_observed or _observed_snapshot_from_session(s)
        match = compare_expected_observed(expected_snapshot, observed, params, event=expected_event, session_config=session_configs.get(sid), replay_config=cfg)
        expected_sig = match.get("expected_sig") or expected_snapshot.get("screen_sig") or ""
        got = match.get("observed_sig") or observed.get("screen_sig") or ""
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

            if typ == "session_start" and sid:
                remember_session_start(sid, ev)
                continue

            if _is_replay_input_event(ev, input_mode=input_mode) and sid:
                expected_sig = str(ev.get("screen_sig") or "") if input_mode == "deterministic" else ""
                expected_snapshot = _expected_snapshot_from_event(ev)
                if input_mode == "deterministic" and _event_requires_deterministic_comparison(ev, params, session_config=session_configs.get(sid), replay_config=cfg):
                    try:
                        wait_checkpoint(sid, ev, seq_global, int(ev.get("seq_session") or 0))
                    except ReplayError:
                        if input_mode != "deterministic":
                            raise
                        observed_snapshot = _observed_snapshot_from_session(get_sess(sid, ev))
                        match = compare_expected_observed(expected_snapshot, observed_snapshot, params, event=ev, session_config=session_configs.get(sid), replay_config=cfg)
                        expected_failure_sig, observed_failure_sig = _match_failure_values(match, expected_snapshot, observed_snapshot)
                        failure = _deterministic_failure(
                            sid=sid,
                            seq_global=seq_global,
                            seq_session=int(ev.get("seq_session") or 0),
                            expected_sig=expected_failure_sig,
                            observed_sig=observed_failure_sig,
                            params=params,
                            checkpoint_timeout_ms=checkpoint_timeout_ms,
                            checkpoint_quiet_ms=cfg.checkpoint_quiet_ms,
                            mode_label="strict-global-deterministic",
                            concurrent_mode=False,
                            match=match,
                        )
                        if not _should_apply_deterministic_input(on_failure, failure, params=params):
                            on_progress(seq_global, None)
                            continue
                data = _decode_replay_input(ev)
                if data:
                    s = get_sess(sid, ev)
                    s.write_in(data)
                on_progress(seq_global, expected_sig or None)
                drain_output(0.0)
            elif typ == "checkpoint" and sid:
                if _event_requires_deterministic_comparison(ev, params, session_config=session_configs.get(sid), replay_config=cfg):
                    wait_checkpoint(sid, ev, seq_global, int(ev.get("seq_session") or 0))
                    expected_snapshot = _expected_snapshot_from_event(ev)
                    on_progress(seq_global, expected_snapshot.get("screen_sig") or expected_snapshot.get("visual_sig") or expected_snapshot.get("text_sig") or expected_snapshot.get("semantic_sig") or None)

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
        state = _state_for_session(cfg, sid, _session_start_by_id(events, sid))
        s = _TargetSession(state.config, sid)
        state.engine = s.screen_state
        sel = selectors.DefaultSelector()
        sel.register(s.master_fd, selectors.EVENT_READ, data=sid)
        try:
            for ev in events:
                should_pause_or_cancel()
                seq_global = int(ev.get("seq_global") or 0)
                typ = ev.get("type") or ""
                if _is_replay_input_event(ev, input_mode=input_mode):
                    expected_sig = str(ev.get("screen_sig") or "") if input_mode == "deterministic" else ""
                    expected_snapshot = _expected_snapshot_from_event(ev)
                    if input_mode == "deterministic" and _event_requires_deterministic_comparison(ev, params, session_config=state.config, replay_config=cfg):
                        matched, match, observed = _wait_for_expected_observed(
                            session=s,
                            selector=sel,
                            expected_event=ev,
                            params=params,
                            should_pause_or_cancel=should_pause_or_cancel,
                            checkpoint_quiet_ms=cfg.checkpoint_quiet_ms,
                            checkpoint_timeout_ms=checkpoint_timeout_ms,
                            session_config=state.config,
                            replay_config=cfg,
                        )
                        if not matched:
                            expected_failure_sig, got = _match_failure_values(match, expected_snapshot, observed)
                            failure = _deterministic_failure(
                                sid=sid,
                                seq_global=seq_global,
                                seq_session=int(ev.get("seq_session") or 0),
                                expected_sig=expected_failure_sig,
                                observed_sig=got,
                                params=params,
                                checkpoint_timeout_ms=checkpoint_timeout_ms,
                                checkpoint_quiet_ms=cfg.checkpoint_quiet_ms,
                                mode_label="parallel-sessions-deterministic",
                                concurrent_mode=False,
                                match=match,
                            )
                            if not _should_apply_deterministic_input(on_failure, failure, params=params):
                                on_progress(seq_global, None)
                                continue
                    data = _decode_replay_input(ev)
                    if data:
                        s.write_in(data)
                    on_progress(seq_global, expected_sig or None)
                elif typ == "checkpoint":
                    if _event_requires_deterministic_comparison(ev, params, session_config=state.config, replay_config=cfg):
                        matched, match, observed = _wait_for_expected_observed(
                            session=s,
                            selector=sel,
                            expected_event=ev,
                            params=params,
                            should_pause_or_cancel=should_pause_or_cancel,
                            checkpoint_quiet_ms=cfg.checkpoint_quiet_ms,
                            checkpoint_timeout_ms=checkpoint_timeout_ms,
                            session_config=state.config,
                            replay_config=cfg,
                        )
                        if not matched:
                            expected_snapshot = _expected_snapshot_from_event(ev)
                            expected_sig = match.get("expected_sig") or expected_snapshot.get("screen_sig") or ""
                            got = match.get("observed_sig") or observed.get("screen_sig") or ""
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
                        expected_snapshot = _expected_snapshot_from_event(ev)
                        expected_sig = match.get("expected_sig") or expected_snapshot.get("screen_sig") or expected_snapshot.get("visual_sig") or expected_snapshot.get("text_sig") or expected_snapshot.get("semantic_sig") or ""
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
            state = _state_for_session(cfg, sid, _session_start_by_id(events, sid))
            s = _TargetSession(state.config, sid, target_user_override=user_override)
            state.engine = s.screen_state
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
                        expected_snapshot = _expected_snapshot_from_event(ev)
                        if input_mode == "deterministic" and _event_requires_deterministic_comparison(ev, load_params.__dict__, session_config=state.config, replay_config=cfg):
                            matched, match, observed = _wait_for_expected_observed(
                                session=s,
                                selector=sel,
                                expected_event=ev,
                                params=load_params.__dict__,
                                should_pause_or_cancel=should_pause_or_cancel,
                                checkpoint_quiet_ms=cfg.checkpoint_quiet_ms,
                                checkpoint_timeout_ms=checkpoint_timeout_ms,
                                session_config=state.config,
                                replay_config=cfg,
                            )
                            if not matched:
                                expected_failure_sig, got = _match_failure_values(match, expected_snapshot, observed)
                                failure = _deterministic_failure(
                                    sid=sid,
                                    seq_global=seq_global,
                                    seq_session=int(ev.get("seq_session") or 0),
                                    expected_sig=expected_failure_sig,
                                    observed_sig=got,
                                    params=load_params.__dict__,
                                    checkpoint_timeout_ms=checkpoint_timeout_ms,
                                    checkpoint_quiet_ms=cfg.checkpoint_quiet_ms,
                                    mode_label="parallel-sessions-concurrent-deterministic",
                                    concurrent_mode=True,
                                    match=match,
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
                        if _event_requires_deterministic_comparison(ev, load_params.__dict__, session_config=state.config, replay_config=cfg):
                            matched, match, observed = _wait_for_expected_observed(
                                session=s,
                                selector=sel,
                                expected_event=ev,
                                params=load_params.__dict__,
                                should_pause_or_cancel=should_pause_or_cancel,
                                checkpoint_quiet_ms=cfg.checkpoint_quiet_ms,
                                checkpoint_timeout_ms=checkpoint_timeout_ms,
                                session_config=state.config,
                                replay_config=cfg,
                            )
                            if matched:
                                expected_snapshot = _expected_snapshot_from_event(ev)
                                expected_sig = match.get("expected_sig") or expected_snapshot.get("screen_sig") or ""
                                on_progress(seq_global, expected_sig)
                            else:
                                expected_snapshot = _expected_snapshot_from_event(ev)
                                expected_sig = match.get("expected_sig") or expected_snapshot.get("screen_sig") or ""
                                got = match.get("observed_sig") or observed.get("screen_sig") or ""
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

            params = {}
            try:
                if run["params_json"]:
                    params = json.loads(run["params_json"]) if isinstance(run["params_json"], str) else {}
            except Exception:
                params = {}
            term_opts = _terminal_options_from_run(run["log_dir"], params)

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
                rows=term_opts["rows"],
                cols=term_opts["cols"],
                term=term_opts["term"],
                encoding=term_opts["encoding"],
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

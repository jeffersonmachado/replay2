"""Executores de replay do replay_control (decomposição do módulo monolítico)."""
from __future__ import annotations

import hashlib
import random
import selectors
import time
from dataclasses import dataclass
from threading import Lock, Semaphore, Thread

from ..replay import ReplayConfig, ReplayError, SessionReplayState, _TargetSession, _decode_replay_input  # type: ignore
from ..replay_compare import (
    expected_screen_text_from_event,
    observed_screen_text_from_session,
    wait_for_signature_match,
)
from ..replay_failures import (
    build_failure_record,
    classify_checkpoint_failure,
    evaluate_checkpoint_match,
)

from .deterministic import (
    _deterministic_failure,
    _event_requires_deterministic_comparison,
    _expected_snapshot_from_event,
    _match_failure_values,
    _observed_snapshot_from_session,
    _session_start_by_id,
    _should_apply_deterministic_input,
    _state_for_session,
    _wait_for_expected_observed,
    compare_expected_observed,
)
from .window import _is_replay_input_event, _replay_input_mode, _selected_events

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
        expected_snapshot = _expected_snapshot_from_event(expected_event)

        def compare(observed: dict) -> dict:
            return compare_expected_observed(expected_snapshot, observed, params, event=expected_event, session_config=session_configs.get(sid), replay_config=cfg)

        matched, match, observed = wait_for_signature_match(
            s,
            sel,
            compare=compare,
            checkpoint_quiet_ms=cfg.checkpoint_quiet_ms,
            checkpoint_timeout_ms=checkpoint_timeout_ms,
            should_pause_or_cancel=should_pause_or_cancel,
            drain_event=lambda key: sessions[key.data].read_out(),
        )
        if matched:
            return
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
                    "expected_screen": expected_screen_text_from_event(expected_event, session_configs.get(sid) or cfg),
                    "observed_screen": observed_screen_text_from_session(s),
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
                            expected_screen=expected_screen_text_from_event(ev, session_configs.get(sid) or cfg),
                            observed_screen=observed_screen_text_from_session(get_sess(sid, ev)),
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
                                expected_screen=expected_screen_text_from_event(ev, state.config),
                                observed_screen=observed_screen_text_from_session(s),
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
                                        "expected_screen": expected_screen_text_from_event(ev, state.config),
                                        "observed_screen": observed_screen_text_from_session(s),
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


def _soft_checkpoint_match(expected_sig: str, observed_sig: str, params: dict | None) -> dict | None:
    """Aplica match_mode não-estrito (contains/regex/fuzzy) sobre as assinaturas.

    Usa evaluate_checkpoint_match com match_mode/match_threshold/
    match_ignore_case dos params. Retorna o resultado quando o modo não é
    "strict" e o match é positivo; caso contrário, None (segue o fluxo de
    falha normal).
    """
    raw = params if isinstance(params, dict) else {}
    mode = str(raw.get("match_mode") or "strict").strip().lower()
    if mode == "strict":
        return None
    result = evaluate_checkpoint_match(expected_sig, observed_sig, raw)
    return result if result.get("matched") else None


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
        # stable mapping by hash (sha256 é estável entre processos)
        idx = int(hashlib.sha256(str(sid).encode("utf-8")).hexdigest(), 16) % len(pool)
        return pool[idx]

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
                                if _soft_checkpoint_match(expected_failure_sig, got, load_params.__dict__) is not None:
                                    matched = True
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
                                    expected_screen=expected_screen_text_from_event(ev, state.config),
                                    observed_screen=observed_screen_text_from_session(s),
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
                            if not matched:
                                expected_snapshot = _expected_snapshot_from_event(ev)
                                expected_sig = match.get("expected_sig") or expected_snapshot.get("screen_sig") or ""
                                got = match.get("observed_sig") or observed.get("screen_sig") or ""
                                if _soft_checkpoint_match(expected_sig, got, load_params.__dict__) is not None:
                                    matched = True
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
                                            "expected_screen": expected_screen_text_from_event(ev, state.config),
                                            "observed_screen": observed_screen_text_from_session(s),
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

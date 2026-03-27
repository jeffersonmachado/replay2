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
from .replay import ReplayConfig, ReplayError, _TargetSession  # type: ignore

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


def replay_strict_global_controlled(
    cfg: ReplayConfig,
    *,
    should_pause_or_cancel,
    on_progress,
    checkpoint_timeout_ms: int = 5000,
):
    sessions: dict[str, _TargetSession] = {}
    sel = selectors.DefaultSelector()

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

    def wait_checkpoint(sid: str, expected_sig: str):
        s = get_sess(sid)
        deadline = int(time.time() * 1000) + checkpoint_timeout_ms
        while int(time.time() * 1000) < deadline:
            should_pause_or_cancel()
            drain_output(0.05)
            quiet = int(time.time() * 1000) - s.last_out_ms
            if quiet >= cfg.checkpoint_quiet_ms:
                got = s.signature_now()
                if got == expected_sig:
                    return
            time.sleep(0.02)
        got = s.signature_now()
        raise ReplayError(f"checkpoint mismatch session={sid}: expected={expected_sig!r} got={got!r}")

    try:
        for ev in _iter_events(cfg.log_dir):
            should_pause_or_cancel()
            seq_global = int(ev.get("seq_global") or 0)
            typ = ev.get("type") or ""
            sid = ev.get("session_id") or ""

            if typ == "bytes" and ev.get("dir") == "in" and sid:
                data = base64.b64decode(ev.get("data_b64") or "")
                if data:
                    s = get_sess(sid)
                    s.write_in(data)
                on_progress(seq_global, None)
                drain_output(0.0)
            elif typ == "checkpoint" and sid:
                expected_sig = ev.get("sig") or ""
                if expected_sig:
                    wait_checkpoint(sid, expected_sig)
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
    *,
    should_pause_or_cancel,
    on_progress,
    checkpoint_timeout_ms: int = 5000,
):
    per_session: dict[str, list[dict]] = {}
    for ev in _iter_events(cfg.log_dir):
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
                if typ == "bytes" and ev.get("dir") == "in":
                    data = base64.b64decode(ev.get("data_b64") or "")
                    if data:
                        s.write_in(data)
                    on_progress(seq_global, None)
                elif typ == "checkpoint":
                    expected_sig = ev.get("sig") or ""
                    if expected_sig:
                        deadline = int(time.time() * 1000) + checkpoint_timeout_ms
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
                                if got == expected_sig:
                                    break
                            time.sleep(0.02)
                        else:
                            got = s.signature_now()
                            raise ReplayError(f"checkpoint mismatch session={sid}: expected={expected_sig!r} got={got!r}")
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


def replay_parallel_sessions_concurrent_controlled(
    cfg: ReplayConfig,
    params: LoadTestParams,
    *,
    should_pause_or_cancel,
    on_progress,
    on_session_result,
    checkpoint_timeout_ms: int = 5000,
):
    """
    Replay por sessão com concorrência limitada e ramp-up.
    - Cada session_id roda em uma thread, preservando ordem por sessão.
    - Checkpoint mismatch pode falhar só a sessão (continue) ou o run inteiro (fail-fast).
    - speed/jitter controlam pacing entre eventos de input (bytes dir=in) baseado em ts_ms.
    - target_user_pool distribui sessões entre usuários no destino.
    """

    per_session: dict[str, list[dict]] = {}
    for ev in _iter_events(cfg.log_dir):
        sid = ev.get("session_id") or ""
        if sid:
            per_session.setdefault(sid, []).append(ev)

    session_ids = sorted(per_session.keys())
    if params.concurrency < 1:
        params.concurrency = 1
    sem = Semaphore(params.concurrency)

    stop_all = {"flag": False, "err": ""}
    stop_lock = Lock()

    def pick_user(sid: str) -> str | None:
        pool = params.target_user_pool or []
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
                    if typ == "bytes" and ev.get("dir") == "in":
                        ts = int(ev.get("ts_ms") or 0)
                        if last_in_ts is not None and params.speed > 0:
                            delta = max(0, ts - last_in_ts)
                            scaled = int(delta / float(params.speed))
                            if params.jitter_ms > 0:
                                scaled += random.randint(0, params.jitter_ms)
                            # sleep is cooperative with pause/cancel (chunked)
                            end = time.time() + (scaled / 1000.0)
                            while time.time() < end:
                                should_pause_or_cancel()
                                time.sleep(min(0.05, end - time.time()))
                        last_in_ts = ts

                        data = base64.b64decode(ev.get("data_b64") or "")
                        if data:
                            s.write_in(data)
                        on_progress(seq_global, None)
                    elif typ == "checkpoint":
                        expected_sig = ev.get("sig") or ""
                        if expected_sig:
                            deadline = int(time.time() * 1000) + checkpoint_timeout_ms
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
                                    if got == expected_sig:
                                        on_progress(seq_global, expected_sig)
                                        break
                                time.sleep(0.02)
                            else:
                                got = s.signature_now()
                                msg = f"checkpoint mismatch session={sid}: expected={expected_sig!r} got={got!r}"
                                on_session_result(sid, "failed", msg)
                                if params.on_checkpoint_mismatch == "fail-fast":
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
            if params.on_checkpoint_mismatch == "fail-fast":
                with stop_lock:
                    stop_all["flag"] = True
                    stop_all["err"] = msg
        finally:
            sem.release()

    threads: list[Thread] = []
    # ramp-up: start threads gradually
    interval = 0.0
    if params.ramp_up_per_sec and params.ramp_up_per_sec > 0:
        interval = 1.0 / float(params.ramp_up_per_sec)

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
        if stop_all["flag"] and params.on_checkpoint_mismatch == "fail-fast":
            raise ReplayError(stop_all["err"])

def create_run(con, created_by: int, log_dir: str, target_host: str, target_user: str, target_command: str, mode: str, parent_run_id: int | None = None) -> int:
    fp = compute_fingerprint(log_dir, target_host, target_user, target_command, mode)
    rid = exec1(
        con,
        """
        INSERT INTO replay_runs(created_at_ms, created_by, log_dir, target_host, target_user, target_command, mode,
                               params_json, metrics_json, run_fingerprint, status, parent_run_id)
        VALUES(?,?,?,?,?,?,?,?,?,?, 'queued', ?)
        """,
        (now_ms(), created_by, log_dir, target_host, target_user, target_command, mode, None, None, fp, parent_run_id),
    )
    add_run_event(con, rid, "created", f"run criado (mode={mode})", {"fingerprint": fp})
    return rid


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
    return create_run(
        con,
        created_by=created_by,
        log_dir=run["log_dir"],
        target_host=run["target_host"],
        target_user=run["target_user"],
        target_command=run["target_command"],
        mode=run["mode"],
        parent_run_id=run_id,
    )


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

        # mark running
        exec1(con, "UPDATE replay_runs SET status='running', started_at_ms=? WHERE id=?", (now_ms(), run_id))
        add_run_event(con, run_id, "start", "runner iniciou", {})

        # verify integrity before replay
        try:
            verify_log(run["log_dir"], self.hmac_key)
            exec1(con, "UPDATE replay_runs SET verify_ok=1, verify_error=NULL WHERE id=?", (run_id,))
        except VerificationError as e:
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
                        metrics["checkpoints_fail"] += 1
                    elif status == "skipped":
                        metrics["sessions_skipped"] += 1
                add_run_event(con, run_id, "session", f"{session_id} {status}", {"message": message})
                write_metrics()

            def should_pause_or_cancel():
                wait_if_paused_or_cancelled()

            if mode == "strict-global":
                replay_strict_global_controlled(cfg, should_pause_or_cancel=should_pause_or_cancel, on_progress=on_progress)
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
                    )
                    # precompute totals
                    with m_lock:
                        metrics["sessions_total"] = 0
                        metrics["sessions_total"] = len({(ev.get("session_id") or "") for ev in _iter_events(cfg.log_dir) if (ev.get("session_id") or "")})
                    write_metrics(throttle_ms=0)
                    replay_parallel_sessions_concurrent_controlled(
                        cfg,
                        lp,
                        should_pause_or_cancel=should_pause_or_cancel,
                        on_progress=on_progress,
                        on_session_result=on_session_result,
                    )
                else:
                    replay_parallel_sessions_controlled(cfg, should_pause_or_cancel=should_pause_or_cancel, on_progress=on_progress)

            # set success
            update_progress(con, run_id, last_seq_global=compute_seq_end(run["log_dir"]))
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
                exec1(con, "UPDATE replay_runs SET finished_at_ms=? WHERE id=?", (now_ms(), run_id))
                set_run_status(con, run_id, "cancelled")
            else:
                exec1(con, "UPDATE replay_runs SET finished_at_ms=? WHERE id=?", (now_ms(), run_id))
                set_run_status(con, run_id, "failed", error=msg)


def compute_seq_end(log_dir: str) -> int:
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


"""Ciclo de vida de runs do replay_control (decomposição do módulo monolítico)."""
from __future__ import annotations

import json
import shutil
import threading
import time
from pathlib import Path
from threading import Lock

from ..state_db import exec1, init_db, now_ms, query_one
from ..db.connection import connect as db_connect
from ..verifier import verify_log, VerificationError
from ..replay import ReplayConfig, ReplayError  # type: ignore
from ..compliance import compliance_blocks_execution
from ..replay_failures import add_run_failure, build_failure_record
from ..replay_run_state import add_run_event, get_run, set_run_status, update_progress

from .executors import (
    LoadTestParams,
    replay_parallel_sessions_concurrent_controlled,
    replay_parallel_sessions_controlled,
    replay_strict_global_controlled,
)
from .window import (
    _iter_events,
    _on_deterministic_mismatch,
    _replay_input_mode,
    _terminal_options_from_run,
    compute_fingerprint,
    compute_seq_end,
)

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
        try:
            self._run_inner(run_id)
        finally:
            self._cleanup_ephemeral_log_dir(run_id)

    def _cleanup_ephemeral_log_dir(self, run_id: int) -> None:
        """Remove o log_dir efêmero de runs sintéticos (params.ephemeral_log_dir).

        Runs criados pelo fluxo Synthetic → Replay (X5) materializam a trilha
        em ``<state>/synthetic_runs/<uuid>/``; ao fim do run (sucesso ou
        falha) o diretório é removido. Nada fora desse prefixo é tocado.
        """
        try:
            con = db_connect(self.db_path)
            try:
                run = get_run(con, run_id)
            finally:
                con.close()
        except Exception:
            return
        if not run:
            return
        try:
            params = json.loads(run["params_json"]) if run["params_json"] else {}
        except Exception:
            params = {}
        if not isinstance(params, dict) or not params.get("ephemeral_log_dir"):
            return
        log_dir = str(run["log_dir"] or "").strip()
        if not log_dir:
            return
        base = (Path(self.db_path).resolve().parent / "synthetic_runs").resolve()
        target = Path(log_dir).resolve()
        if target == base or base not in target.parents:
            return
        shutil.rmtree(target, ignore_errors=True)

    def _run_inner(self, run_id: int) -> None:
        # Conexão compartilhada com os workers (check_same_thread=False);
        # todo acesso é serializado por db_lock porque os callbacks de
        # progresso/falha são invocados a partir das threads de sessão.
        con = db_connect(self.db_path)
        db_lock = Lock()
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
                with db_lock:
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
                term_override=str(params.get("term") or ""),
            )

            # Runner executes replay synchronously; pause/cancel are checked between coarse phases.
            # For MVP, we also emit heartbeat while running.
            add_run_event(con, run_id, "heartbeat", "running", {"last_seq_global_applied": last_seq})

            last_progress_write_ms = 0

            cfg.input_mode = _replay_input_mode(params)
            cfg.on_deterministic_mismatch = _on_deterministic_mismatch(params)

            # Gravação da sessão observada (v0.8.66): runs determinísticas
            # gravam a saída real do destino como trilha auditável assinada
            # (params.record_observed=0 desliga). Falha na gravação nunca
            # derruba a run — o recorder se autodesativa em replay.py.
            record_observed = (
                cfg.input_mode == "deterministic"
                and str(params.get("record_observed", "1")).strip().lower()
                not in ("0", "false", "no", "nao", "não", "off")
            )
            if record_observed:
                observed_dir = Path(self.db_path).resolve().parent / "observed_runs" / f"run-{run_id}"
                cfg.observed_dir = str(observed_dir)
                cfg.observed_hmac_key = self.hmac_key
                with db_lock:
                    exec1(con, "UPDATE replay_runs SET observed_dir=? WHERE id=?", (str(observed_dir), run_id))

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
                    payload = json.dumps(metrics, ensure_ascii=False)
                with db_lock:
                    exec1(
                        con,
                        "UPDATE replay_runs SET metrics_json=? WHERE id=?",
                        (payload, run_id),
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
                    with db_lock:
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
                with db_lock:
                    add_run_event(con, run_id, "session", f"{session_id} {status}", {"message": message})
                write_metrics()

            def on_failure(failure: dict):
                with db_lock:
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
        except Exception as e:  # exceção inesperada: não deixar o run eternamente "running"
            msg = f"{type(e).__name__}: {e}"
            try:
                with db_lock:
                    add_run_failure(
                        con,
                        run_id,
                        build_failure_record(
                            session_id="",
                            seq_global=last_seq,
                            event_type="runner",
                            failure_type="technical_error",
                            severity="critical",
                            expected_value="replay concluído sem exceção",
                            observed_value=msg,
                            message=msg,
                            evidence={"last_seq_global_applied": last_seq},
                        ),
                    )
                    exec1(con, "UPDATE replay_runs SET finished_at_ms=? WHERE id=?", (now_ms(), run_id))
                    set_run_status(con, run_id, "failed", error=msg)
            except Exception:
                # best effort: se o banco também falhou, nada mais a fazer na thread
                pass

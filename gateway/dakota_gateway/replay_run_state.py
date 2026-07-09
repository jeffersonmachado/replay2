from __future__ import annotations

import json

from .state_db import exec1, now_ms, query_one


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

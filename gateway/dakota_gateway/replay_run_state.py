from __future__ import annotations

import json

from .state_db import exec1, now_ms, query_all, query_one

_STALE_RUN_ERROR = (
    "run interrompida: servidor reiniciado com a run ativa "
    "(processo anterior encerrado)"
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


def interrupt_stale_runs(con, *, now_ms_fn=now_ms) -> int:
    """Marca runs ativas ('queued'/'running'/'paused') de um processo anterior
    como 'failed' no boot do servidor — espelha ``interrupt_stale_captures``.

    Sem isso, uma run abandonada pelo restart (deploy) ficava ativa para
    sempre e o índice UNIQUE parcial de ``run_fingerprint`` bloqueava a
    criação de uma nova run sobre a mesma trilha
    (``UNIQUE constraint failed: replay_runs.run_fingerprint``).
    Retorna o número de runs marcadas.
    """
    stale = query_all(
        con,
        "SELECT id FROM replay_runs WHERE status IN ('queued','running','paused')",
    )
    for row in stale:
        set_run_status(con, int(row["id"]), "failed", error=_STALE_RUN_ERROR)
    return len(stale)

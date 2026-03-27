from __future__ import annotations

import os
import queue
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


SCHEMA_SQL = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  username TEXT NOT NULL UNIQUE,
  password_hash TEXT NOT NULL,
  role TEXT NOT NULL CHECK(role IN ('admin','operator','viewer')),
  created_at_ms INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  token_hash TEXT NOT NULL UNIQUE,
  created_at_ms INTEGER NOT NULL,
  expires_at_ms INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS replay_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at_ms INTEGER NOT NULL,
  created_by INTEGER NOT NULL REFERENCES users(id),

  log_dir TEXT NOT NULL,
  target_host TEXT NOT NULL,
  target_user TEXT NOT NULL,
  target_command TEXT NOT NULL,
  mode TEXT NOT NULL CHECK(mode IN ('strict-global','parallel-sessions')),

  params_json TEXT,
  metrics_json TEXT,

  run_fingerprint TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('queued','running','paused','failed','success','cancelled')),

  started_at_ms INTEGER,
  finished_at_ms INTEGER,

  verify_ok INTEGER,
  verify_error TEXT,

  last_seq_global_applied INTEGER NOT NULL DEFAULT 0,
  last_checkpoint_sig TEXT,

  parent_run_id INTEGER REFERENCES replay_runs(id),
  error TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS replay_runs_fingerprint_unique
ON replay_runs(run_fingerprint) WHERE status IN ('queued','running','paused');

CREATE TABLE IF NOT EXISTS replay_run_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id INTEGER NOT NULL REFERENCES replay_runs(id) ON DELETE CASCADE,
  ts_ms INTEGER NOT NULL,
  kind TEXT NOT NULL,
  message TEXT NOT NULL,
  data_json TEXT
);

CREATE INDEX IF NOT EXISTS replay_run_events_run_ts
ON replay_run_events(run_id, ts_ms);

-- Performance indexes (created in init_db with error handling)
CREATE INDEX IF NOT EXISTS sessions_user_id
ON sessions(user_id);

CREATE INDEX IF NOT EXISTS replay_runs_status
ON replay_runs(status);

CREATE INDEX IF NOT EXISTS replay_runs_created_by
ON replay_runs(created_by);

CREATE INDEX IF NOT EXISTS replay_runs_created_at_ms
ON replay_runs(created_at_ms DESC);

CREATE INDEX IF NOT EXISTS replay_run_events_kind
ON replay_run_events(kind);
"""


def default_db_path() -> str:
    return str(Path(__file__).resolve().parents[1] / "state" / "replay.db")


def connect(db_path: str) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db_path, isolation_level=None, timeout=30, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA busy_timeout=30000")
    return con


class ConnectionPool:
    def __init__(self, db_path: str, min_size: int = 1, max_size: int = 16):
        self.db_path = db_path
        self.min_size = max(1, int(min_size))
        self.max_size = max(self.min_size, int(max_size))
        self._q: queue.LifoQueue[sqlite3.Connection] = queue.LifoQueue(maxsize=self.max_size)
        self._lock = threading.Lock()
        self._created = 0

        for _ in range(self.min_size):
            self._q.put(self._new_connection())

    def _new_connection(self) -> sqlite3.Connection:
        con = connect(self.db_path)
        with self._lock:
            self._created += 1
        return con

    def acquire(self, timeout: float = 30.0) -> sqlite3.Connection:
        try:
            return self._q.get_nowait()
        except queue.Empty:
            with self._lock:
                can_create = self._created < self.max_size
            if can_create:
                return self._new_connection()
            return self._q.get(timeout=timeout)

    def release(self, con: sqlite3.Connection) -> None:
        try:
            self._q.put_nowait(con)
        except queue.Full:
            try:
                con.close()
            finally:
                with self._lock:
                    self._created = max(0, self._created - 1)

    def close_all(self) -> None:
        while True:
            try:
                con = self._q.get_nowait()
            except queue.Empty:
                break
            try:
                con.close()
            finally:
                with self._lock:
                    self._created = max(0, self._created - 1)


def init_db(con: sqlite3.Connection) -> None:
    con.executescript(SCHEMA_SQL)

    # Best-effort migration for new columns (keep backward compatible).
    cols = {row["name"] for row in con.execute("PRAGMA table_info(replay_runs)").fetchall()}
    if "params_json" not in cols:
        con.execute("ALTER TABLE replay_runs ADD COLUMN params_json TEXT")
    if "metrics_json" not in cols:
        con.execute("ALTER TABLE replay_runs ADD COLUMN metrics_json TEXT")


def now_ms() -> int:
    return int(time.time() * 1000)


def query_one(con: sqlite3.Connection, sql: str, args: Iterable[Any] = ()) -> sqlite3.Row | None:
    cur = con.execute(sql, tuple(args))
    return cur.fetchone()


def query_all(con: sqlite3.Connection, sql: str, args: Iterable[Any] = ()) -> list[sqlite3.Row]:
    cur = con.execute(sql, tuple(args))
    return cur.fetchall()


def exec1(con: sqlite3.Connection, sql: str, args: Iterable[Any] = ()) -> int:
    cur = con.execute(sql, tuple(args))
    return int(cur.lastrowid)


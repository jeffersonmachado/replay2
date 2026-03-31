from __future__ import annotations

import queue
import sqlite3
import threading
from pathlib import Path


def default_db_path() -> str:
    return str(Path(__file__).resolve().parents[2] / "state" / "replay.db")


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

from __future__ import annotations

import sqlite3
import time
from typing import Any, Iterable

from .db.connection import ConnectionPool, connect, default_db_path
from .db.migrations import init_db
from .db.schema import SCHEMA_SQL


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


__all__ = [
    "ConnectionPool",
    "SCHEMA_SQL",
    "connect",
    "default_db_path",
    "exec1",
    "init_db",
    "now_ms",
    "query_all",
    "query_one",
]

from __future__ import annotations

import os
import queue
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable, Iterator, Sequence


def default_db_path() -> str:
    return str(Path(__file__).resolve().parents[2] / "state" / "replay.db")


def connect(db_path: str) -> sqlite3.Connection:
    """Abre a conexão SQLite padrão do projeto (autocommit, Row factory).

    Journal mode e synchronous são configuráveis por ambiente:

    - ``DAKOTA_DB_JOURNAL_MODE`` (ex.: ``wal``) — default: não definido, o
      banco mantém o journal mode atual (``delete`` em bancos novos);
    - ``DAKOTA_DB_SYNCHRONOUS`` (``OFF``/``NORMAL``/``FULL``) — default:
      não definido (``FULL``).

    Compromisso de durabilidade: ``WAL + synchronous=NORMAL`` é medido ~14x
    mais rápido em escrita em lote que os defaults (dev/bench_sqlite_batch.py),
    mas ``NORMAL`` pode perder as últimas transações comitadas em queda de
    energia/SO (nunca corrompe o banco). Como a trilha auditável do projeto
    prioriza integridade, os defaults NÃO são alterados — o operador opta
    conscientemente via env. ``synchronous=OFF`` não é aceito: entrega a
    durabilidade ao SO sem ganho relevante sobre NORMAL.
    """
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db_path, isolation_level=None, timeout=30, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA busy_timeout=30000")
    journal = (os.environ.get("DAKOTA_DB_JOURNAL_MODE") or "").strip()
    if journal:
        if journal.lower() not in ("delete", "truncate", "persist", "wal", "memory", "off"):
            raise ValueError(f"DAKOTA_DB_JOURNAL_MODE inválido: {journal!r}")
        con.execute(f"PRAGMA journal_mode={journal}")
    sync = (os.environ.get("DAKOTA_DB_SYNCHRONOUS") or "").strip()
    if sync:
        if sync.upper() not in ("NORMAL", "FULL", "EXTRA"):
            raise ValueError(
                f"DAKOTA_DB_SYNCHRONOUS inválido ou inseguro: {sync!r} "
                "(OFF é proibido; use NORMAL com ciência do compromisso de durabilidade)")
        con.execute(f"PRAGMA synchronous={sync}")
    return con


@contextmanager
def transaction(con: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Transação explícita com rollback seguro (BEGIN IMMEDIATE/COMMIT/ROLLBACK).

    As conexões do projeto rodam em autocommit (``isolation_level=None``):
    sem esta API cada statement é uma transação própria (fsync por linha).
    Em falha dentro do bloco, a transação inteira é descartada (sem estado
    parcial) e a exceção é propagada.
    """
    con.execute("BEGIN IMMEDIATE")
    try:
        yield con
    except BaseException:
        con.execute("ROLLBACK")
        raise
    else:
        con.execute("COMMIT")


def batch_insert(con: sqlite3.Connection, sql: str, rows: Iterable[Sequence],
                 *, chunk_size: int = 500) -> int:
    """Insere ``rows`` em lote: transação explícita + executemany + chunking.

    Cada chunk é uma transação curta e ATÔMICA (falha descarta o chunk
    inteiro, sem linhas parciais), o que evita transações longas bloqueando
    o control plane. Chunks anteriores a uma falha permanecem comitados —
    para semântica tudo-ou-nada no lote inteiro, use ``chunk_size`` >= total
    de linhas. Retorna o total de linhas inseridas com sucesso.
    """
    if chunk_size < 1:
        raise ValueError("chunk_size deve ser >= 1")
    total = 0
    chunk: list[Sequence] = []
    for row in rows:
        chunk.append(row)
        if len(chunk) >= chunk_size:
            with transaction(con):
                con.executemany(sql, chunk)
            total += len(chunk)
            chunk = []
    if chunk:
        with transaction(con):
            con.executemany(sql, chunk)
        total += len(chunk)
    return total


class ConnectionPool:
    def __init__(self, db_path: str, min_size: int = 1, max_size: int = 16):
        self.db_path = db_path
        self.min_size = max(1, int(min_size))
        self.max_size = max(self.min_size, int(max_size))
        self._q: queue.LifoQueue[sqlite3.Connection] = queue.LifoQueue(maxsize=self.max_size)
        self._lock = threading.Lock()
        self._created = 0
        self._in_use: set[int] = set()

        for _ in range(self.min_size):
            con = self._new_connection()
            self._q.put(con)
            # conexões do min_size contam como criadas: sem isto o pool
            # estoura max_size (criava min_size + max_size no total).
            self._created += 1

    def _new_connection(self) -> sqlite3.Connection:
        # o slot em _created já foi reservado pelo chamador sob _lock
        # (no __init__, a reserva é o incremento logo após a criação —
        # single-threaded, sem corrida)
        return connect(self.db_path)

    def acquire(self, timeout: float = 30.0) -> sqlite3.Connection:
        try:
            con = self._q.get_nowait()
        except queue.Empty:
            # reserva o slot sob lock para não ultrapassar max_size em corrida
            with self._lock:
                if self._created < self.max_size:
                    self._created += 1
                    can_create = True
                else:
                    can_create = False
            if can_create:
                try:
                    con = self._new_connection()
                except Exception:
                    # falha na criação devolve o slot reservado
                    with self._lock:
                        self._created = max(0, self._created - 1)
                    raise
            else:
                con = self._q.get(timeout=timeout)
        with self._lock:
            self._in_use.add(id(con))
        return con

    def release(self, con: sqlite3.Connection) -> None:
        with self._lock:
            if id(con) not in self._in_use:
                raise ValueError("conexão não pertence ao pool ou já foi liberada (double-release)")
            self._in_use.discard(id(con))
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

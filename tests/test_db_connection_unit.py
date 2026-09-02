#!/usr/bin/env python3
"""Testes do pool de conexões e das APIs de transação/lote (Fase 5).

Cobrem:
- ConnectionPool: min_size conta como criada; nunca ultrapassa max_size sob
  concorrência; falha na criação devolve o slot; close_all mantém contadores
  coerentes; double-release é erro.
- transaction(): commit em sucesso, rollback sem estado parcial em falha.
- batch_insert(): mesmo conteúdo que inserção uma a uma; chunking com
  atomicidade por chunk; lote único é tudo-ou-nada.
"""
from __future__ import annotations

import queue
import sqlite3
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GATEWAY_DIR = ROOT / "gateway"
sys.path.insert(0, str(GATEWAY_DIR))

from dakota_gateway.db import connection as conn_module
from dakota_gateway.db.connection import ConnectionPool, connect

# transaction/batch_insert são a API nova da Fase 5: importadas por fixture
# (setUp) para que os testes do pool rodem — e falhem pelos bugs reais —
# mesmo antes de a API existir.
def _api():
    from dakota_gateway.db.connection import batch_insert, transaction
    return transaction, batch_insert

DDL = (
    "CREATE TABLE metricas (id INTEGER PRIMARY KEY AUTOINCREMENT,"
    " ts_ms INTEGER NOT NULL, valor REAL)"
)
INSERT = "INSERT INTO metricas (ts_ms, valor) VALUES (?, ?)"


def _linhas(n: int) -> list[tuple]:
    return [(1_700_000_000_000 + i * 1000, float(i % 97)) for i in range(n)]


class _ConnectCounter:
    """Wrapper de connect() que conta criações e o pico de conexões vivas."""

    def __init__(self, fail_on_call: int = -1):
        self.total_created = 0
        self.calls = 0
        self.fail_on_call = fail_on_call
        self._lock = threading.Lock()

    def __call__(self, db_path: str) -> sqlite3.Connection:
        with self._lock:
            self.calls += 1
            if self.calls == self.fail_on_call:
                raise OSError("falha simulada na abertura do banco")
            self.total_created += 1
        return _real_connect(db_path)


_real_connect = conn_module.connect


class ConnectionPoolTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tmp.name) / "pool.db")
        self._orig_connect = conn_module.connect

    def tearDown(self):
        conn_module.connect = self._orig_connect
        self.tmp.cleanup()

    def _patch_connect(self, counter: _ConnectCounter) -> None:
        conn_module.connect = counter

    def test_min_size_conta_como_criadas(self):
        counter = _ConnectCounter()
        self._patch_connect(counter)
        pool = ConnectionPool(self.db_path, min_size=2, max_size=2)
        try:
            self.assertEqual(counter.total_created, 2)
            self.assertEqual(pool._created, 2,
                             "conexões do min_size devem contar em _created")
        finally:
            pool.close_all()

    def test_nunca_ultrapassa_max_size_sob_concorrencia(self):
        counter = _ConnectCounter()
        self._patch_connect(counter)
        min_size, max_size = 2, 4
        pool = ConnectionPool(self.db_path, min_size=min_size, max_size=max_size)
        erros: list[BaseException] = []
        barrier = threading.Barrier(16)

        def worker():
            try:
                barrier.wait(timeout=10)
                for _ in range(25):
                    con = pool.acquire(timeout=10)
                    try:
                        con.execute("SELECT 1").fetchone()
                    finally:
                        pool.release(con)
            except BaseException as exc:  # noqa: BLE001 - reporta no assert final
                erros.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(16)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)
        try:
            self.assertEqual(erros, [], f"erros nos workers: {erros!r}")
            self.assertLessEqual(counter.total_created, max_size,
                                 f"pool criou {counter.total_created} conexões, "
                                 f"acima de max_size={max_size}")
            self.assertLessEqual(pool._created, max_size)
        finally:
            pool.close_all()

    def test_falha_na_criacao_devolve_slot(self):
        counter = _ConnectCounter(fail_on_call=2)  # 1ª (min_size) ok, 2ª falha
        self._patch_connect(counter)
        pool = ConnectionPool(self.db_path, min_size=1, max_size=2)
        try:
            con = pool.acquire()
            try:
                with self.assertRaises(OSError):
                    pool.acquire(timeout=1)
                self.assertEqual(pool._created, 1,
                                 "slot da criação falha deve ser devolvido")
                # Pool segue utilizável: libera e readquire sem criar nada novo.
            finally:
                pool.release(con)
            con2 = pool.acquire(timeout=1)
            pool.release(con2)
            self.assertEqual(counter.total_created, 1)
        finally:
            pool.close_all()

    def test_close_all_mantem_contadores_coerentes(self):
        counter = _ConnectCounter()
        self._patch_connect(counter)
        pool = ConnectionPool(self.db_path, min_size=2, max_size=4)
        con1 = pool.acquire()
        con2 = pool.acquire()  # força criação além do min_size
        pool.release(con1)
        pool.release(con2)
        self.assertEqual(pool._created, counter.total_created)
        pool.close_all()
        self.assertEqual(pool._created, 0, "close_all deve zerar _created")
        # Pool segue funcional após close_all (recria sob demanda até max_size).
        con = pool.acquire(timeout=1)
        pool.release(con)
        self.assertEqual(pool._created, 1)
        pool.close_all()

    def test_double_release_e_erro(self):
        pool = ConnectionPool(self.db_path, min_size=1, max_size=2)
        try:
            con = pool.acquire()
            pool.release(con)
            with self.assertRaises(ValueError):
                pool.release(con)
        finally:
            pool.close_all()

    def test_acquire_bloqueia_ate_timeout_quando_cheio(self):
        pool = ConnectionPool(self.db_path, min_size=1, max_size=1)
        try:
            con = pool.acquire()
            with self.assertRaises(queue.Empty):
                pool.acquire(timeout=0.2)
            pool.release(con)
        finally:
            pool.close_all()


class TransactionTests(unittest.TestCase):
    def setUp(self):
        self.transaction, _ = _api()
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tmp.name) / "tx.db")
        self.con = connect(self.db_path)
        self.con.executescript(DDL)

    def tearDown(self):
        self.con.close()
        self.tmp.cleanup()

    def test_commit_em_sucesso(self):
        with self.transaction(self.con) as con:
            con.execute(INSERT, (1, 10.0))
            con.execute(INSERT, (2, 20.0))
        total = self.con.execute("SELECT COUNT(*) FROM metricas").fetchone()[0]
        self.assertEqual(total, 2)

    def test_rollback_sem_estado_parcial(self):
        with self.assertRaises(RuntimeError):
            with self.transaction(self.con) as con:
                con.execute(INSERT, (1, 10.0))
                raise RuntimeError("falha no meio da transação")
        total = self.con.execute("SELECT COUNT(*) FROM metricas").fetchone()[0]
        self.assertEqual(total, 0, "rollback deve descartar a transação inteira")

    def test_conexao_segue_utilizavel_apos_rollback(self):
        with self.assertRaises(RuntimeError):
            with self.transaction(self.con) as con:
                con.execute(INSERT, (1, 10.0))
                raise RuntimeError("falha")
        with self.transaction(self.con) as con:
            con.execute(INSERT, (2, 20.0))
        total = self.con.execute("SELECT COUNT(*) FROM metricas").fetchone()[0]
        self.assertEqual(total, 1)


class BatchInsertTests(unittest.TestCase):
    def setUp(self):
        _, self.batch_insert = _api()
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def _db(self, nome: str) -> sqlite3.Connection:
        con = connect(str(Path(self.tmp.name) / nome))
        con.executescript(DDL)
        return con

    def test_mesmo_conteudo_que_um_a_um(self):
        linhas = _linhas(1234)
        con_batch = self._db("batch.db")
        con_solo = self._db("solo.db")
        try:
            self.batch_insert(con_batch, INSERT, linhas, chunk_size=500)
            for linha in linhas:
                con_solo.execute(INSERT, linha)
            a = con_batch.execute(
                "SELECT ts_ms, valor FROM metricas ORDER BY id").fetchall()
            b = con_solo.execute(
                "SELECT ts_ms, valor FROM metricas ORDER BY id").fetchall()
        finally:
            con_batch.close()
            con_solo.close()
        self.assertEqual([tuple(r) for r in a], [tuple(r) for r in b])

    def test_retorna_total_inserido_e_aceita_iteravel(self):
        con = self._db("gen.db")
        try:
            total = self.batch_insert(con, INSERT, iter(_linhas(10)), chunk_size=3)
            gravadas = con.execute("SELECT COUNT(*) FROM metricas").fetchone()[0]
        finally:
            con.close()
        self.assertEqual(total, 10)
        self.assertEqual(gravadas, 10)

    def test_lista_vazia_nao_insere_nada(self):
        con = self._db("vazio.db")
        try:
            self.assertEqual(self.batch_insert(con, INSERT, []), 0)
        finally:
            con.close()

    def test_chunk_com_falha_e_atomico(self):
        """Violação no meio de um chunk descarta o chunk inteiro (sem linhas
        parciais do chunk); chunks anteriores, já comitados, permanecem."""
        con = self._db("chunks.db")
        try:
            con.executescript(
                "CREATE TABLE unica (k INTEGER PRIMARY KEY, v REAL)")
            # chunk 1: chaves 1..4 (ok); chunk 2: chave 3 repetida no meio.
            linhas = [(1, 1.0), (2, 2.0), (3, 3.0), (4, 4.0),
                      (5, 5.0), (3, 99.0), (7, 7.0), (8, 8.0)]
            with self.assertRaises(sqlite3.IntegrityError):
                self.batch_insert(con, "INSERT INTO unica (k, v) VALUES (?, ?)",
                             linhas, chunk_size=4)
            chaves = [r[0] for r in con.execute(
                "SELECT k FROM unica ORDER BY k").fetchall()]
        finally:
            con.close()
        self.assertEqual(chaves, [1, 2, 3, 4],
                         "chunk 2 deve ser descartado por inteiro")

    def test_lote_unico_e_tudo_ou_nada(self):
        """Com chunk_size >= N, falha no meio não deixa nenhuma linha."""
        con = self._db("tudo_ou_nada.db")
        try:
            con.executescript(
                "CREATE TABLE unica (k INTEGER PRIMARY KEY, v REAL)")
            linhas = [(1, 1.0), (1, 2.0), (2, 2.0)]
            with self.assertRaises(sqlite3.IntegrityError):
                self.batch_insert(con, "INSERT INTO unica (k, v) VALUES (?, ?)",
                             linhas, chunk_size=1000)
            total = con.execute("SELECT COUNT(*) FROM unica").fetchone()[0]
        finally:
            con.close()
        self.assertEqual(total, 0, "nenhuma linha pode sobreviver ao rollback")


if __name__ == "__main__":
    unittest.main()

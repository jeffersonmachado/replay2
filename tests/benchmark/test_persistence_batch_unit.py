#!/usr/bin/env python3
"""Escrita em lote do benchmark/persistence (Fase 5): chunking e corretude.

Cobre save_app_samples/save_host_samples com chunk_size pequeno: todo o
conteúdo gravado bate com as amostras, independente do número de chunks.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
GATEWAY_DIR = ROOT / "gateway"
sys.path.insert(0, str(GATEWAY_DIR))

from dakota_gateway.benchmark import persistence as bp
from dakota_gateway.db.connection import connect


def _app_samples(n: int) -> list[SimpleNamespace]:
    return [
        SimpleNamespace(
            virtual_user_id=f"vu-{i % 4}", journey_id="j1",
            step_id=f"step-{i}", phase="steady",
            started_ns=1_000 + i, finished_ns=2_000 + i,
            latency_ms=10.5 + i, success=(i % 2 == 0),
            timeout=False, functional_divergence=False, error_code=None,
        )
        for i in range(n)
    ]


class PersistenceBatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.con = connect(str(Path(self.tmp.name) / "bench.db"))
        bp.ensure_benchmark_tables(self.con)
        self.con.execute(
            "INSERT INTO benchmark_experiments"
            "(experiment_id, contract_json, contract_sha256, created_at_ms,"
            " status, verdict, reason) VALUES('exp1','{}','sha',1,'RUNNING',"
            "'INCONCLUSIVE','')")
        self.con.execute(
            "INSERT INTO benchmark_runs"
            "(run_id, experiment_id, environment_id, iteration, concurrency,"
            " phase_order, status) VALUES('run1','exp1','linux',1,4,'[]','OK')")

    def tearDown(self):
        self.con.close()
        self.tmp.cleanup()

    def test_app_samples_chunked_preserva_conteudo(self):
        samples = _app_samples(10)
        total = bp.save_app_samples(self.con, "run1", samples, chunk_size=3)
        self.assertEqual(total, 10)
        rows = self.con.execute(
            "SELECT virtual_user_id, step_id, latency_ms, success"
            " FROM benchmark_app_samples ORDER BY id").fetchall()
        self.assertEqual(len(rows), 10)
        for i, row in enumerate(rows):
            self.assertEqual(row["step_id"], f"step-{i}")
            self.assertAlmostEqual(row["latency_ms"], 10.5 + i)
            self.assertEqual(row["success"], 1 if i % 2 == 0 else 0)

    def test_app_samples_sem_amostras_retorna_zero(self):
        self.assertEqual(bp.save_app_samples(self.con, "run1", []), 0)

    def test_host_samples_chunked_preserva_conteudo_e_nulls(self):
        samples = [
            {"ts_ms": 100 + i, "host_id": "h1", "platform": "linux",
             "architecture": "x86_64", "cpu_user": float(i),
             "campo_extra": f"e{i}"}  # fora das colunas diretas → extra_json
            for i in range(7)
        ]
        total = bp.save_host_samples(
            self.con, experiment_id="exp1", environment_id="linux",
            run_id="run1", iteration=1, concurrency=4, phase="steady",
            samples=samples, chunk_size=2)
        self.assertEqual(total, 7)
        rows = self.con.execute(
            "SELECT ts_ms, cpu_user, iops, extra_json"
            " FROM benchmark_host_samples ORDER BY id").fetchall()
        self.assertEqual(len(rows), 7)
        for i, row in enumerate(rows):
            self.assertEqual(row["ts_ms"], 100 + i)
            self.assertAlmostEqual(row["cpu_user"], float(i))
            self.assertIsNone(row["iops"], "campo ausente fica NULL, nunca zero fingido")
            self.assertIn(f'"campo_extra": "e{i}"', row["extra_json"])


if __name__ == "__main__":
    unittest.main()

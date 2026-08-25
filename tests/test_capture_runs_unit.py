"""Testes do endpoint/service de runs sintéticas geradas por captura."""
from __future__ import annotations

import json
import sqlite3
import unittest

from control.services.run_service import list_capture_runs_payload


def _make_db() -> sqlite3.Connection:
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.execute(
        """
        CREATE TABLE replay_runs (
            id INTEGER PRIMARY KEY,
            status TEXT,
            mode TEXT,
            created_at_ms INTEGER,
            finished_at_ms INTEGER,
            target_user TEXT,
            target_host TEXT,
            params_json TEXT
        )
        """
    )
    return con


def _insert_run(con, run_id, *, synthetic=None, source_capture_id=None, status="success"):
    params = {}
    if synthetic is not None:
        params["synthetic"] = synthetic
    if source_capture_id is not None:
        params["source_capture_id"] = source_capture_id
        params["journey_id"] = "j-1"
    con.execute(
        "INSERT INTO replay_runs (id, status, mode, created_at_ms, finished_at_ms, target_user, target_host, params_json)"
        " VALUES (?, ?, 'strict-global', 1000, 2000, 'ferblo', '127.0.0.1', ?)",
        (run_id, status, json.dumps(params)),
    )


class ListCaptureRunsTests(unittest.TestCase):
    def setUp(self):
        self.con = _make_db()

    def tearDown(self):
        self.con.close()

    def test_so_runs_sinteticas_da_captura(self):
        _insert_run(self.con, 1, synthetic=True, source_capture_id=13)
        _insert_run(self.con, 2, synthetic=True, source_capture_id=7)
        _insert_run(self.con, 3)  # run real, sem params
        _insert_run(self.con, 4, synthetic=True, source_capture_id=13, status="failed")
        payload = list_capture_runs_payload(self.con, 13)
        ids = [r["id"] for r in payload["runs"]]
        self.assertEqual(ids, [4, 1])  # ordenado por id DESC
        self.assertEqual(payload["capture_id"], 13)
        self.assertTrue(all(r["synthetic"] for r in payload["runs"]))
        self.assertEqual(payload["runs"][0]["journey_id"], "j-1")
        self.assertNotIn("params_json", payload["runs"][0])

    def test_captura_sem_runs_retorna_vazio(self):
        _insert_run(self.con, 1, synthetic=True, source_capture_id=7)
        payload = list_capture_runs_payload(self.con, 13)
        self.assertEqual(payload["runs"], [])

    def test_params_invalidos_sao_ignorados(self):
        self.con.execute(
            "INSERT INTO replay_runs (id, status, mode, created_at_ms, finished_at_ms, target_user, target_host, params_json)"
            " VALUES (9, 'success', 'strict-global', 1000, 2000, 'u', 'h', 'nao-e-json')"
        )
        self.con.execute(
            "INSERT INTO replay_runs (id, status, mode, created_at_ms, finished_at_ms, target_user, target_host, params_json)"
            " VALUES (10, 'success', 'strict-global', 1000, 2000, 'u', 'h', NULL)"
        )
        payload = list_capture_runs_payload(self.con, 13)
        self.assertEqual(payload["runs"], [])

    def test_source_capture_id_string_tambem_casa(self):
        self.con.execute(
            "INSERT INTO replay_runs (id, status, mode, created_at_ms, finished_at_ms, target_user, target_host, params_json)"
            " VALUES (5, 'success', 'strict-global', 1000, 2000, 'u', 'h', ?)",
            (json.dumps({"synthetic": True, "source_capture_id": "13"}),),
        )
        payload = list_capture_runs_payload(self.con, 13)
        self.assertEqual([r["id"] for r in payload["runs"]], [5])


if __name__ == "__main__":
    unittest.main()

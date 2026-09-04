#!/usr/bin/env python3
"""Regressão: runs órfãs ('queued'/'running'/'paused') de um processo anterior
não podem ficar ativas para sempre nem bloquear novas runs pelo
``run_fingerprint`` (índice UNIQUE parcial em replay_runs).

Bug (0.9.4): o restart do control plane (deploy) abandonou a run 69 em
'queued'; a síntese seguinte da mesma captura regenerou a mesma trilha
(determinística) e o INSERT da nova run falhou com
``UNIQUE constraint failed: replay_runs.run_fingerprint``, derrubando o job
de replay sintético na fase "criando run de replay".

A correção espelha ``interrupt_stale_captures``: no boot, runs ativas de um
processo morto viram 'failed' com erro explicativo — o que também libera o
fingerprint para uma nova run.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "gateway"))

from dakota_gateway.replay_run_state import interrupt_stale_runs
from dakota_gateway.state_db import connect, init_db, now_ms


def _insert_run(con, status: str, fingerprint: str) -> int:
    cur = con.execute(
        "INSERT INTO replay_runs(status,created_by,target_host,target_user,"
        "target_command,mode,log_dir,run_fingerprint,created_at_ms)"
        " VALUES(?,?,?,?,?,?,?,?,?)",
        (status, 1, "127.0.0.1", "op", "", "strict-global", "/tmp/x",
         fingerprint, now_ms()),
    )
    return int(cur.lastrowid)


class InterruptStaleRunsTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.con = connect(str(Path(self.tmpdir.name) / "test.db"))
        init_db(self.con)
        self.con.execute(
            "INSERT INTO users(username,password_hash,role,created_at_ms)"
            " VALUES(?,?,?,?)",
            ("admin", "x", "admin", now_ms()),
        )

    def tearDown(self):
        self.con.close()
        self.tmpdir.cleanup()

    def _status(self, run_id: int) -> str:
        row = self.con.execute(
            "SELECT status FROM replay_runs WHERE id=?", (run_id,)
        ).fetchone()
        return str(row["status"])

    def test_runs_ativas_viram_failed_com_erro_explicativo(self):
        ids = [
            _insert_run(self.con, "queued", "fp-q"),
            _insert_run(self.con, "running", "fp-r"),
            _insert_run(self.con, "paused", "fp-p"),
        ]
        count = interrupt_stale_runs(self.con, now_ms_fn=now_ms)
        self.assertEqual(count, 3)
        for run_id in ids:
            self.assertEqual(self._status(run_id), "failed")
            row = self.con.execute(
                "SELECT error FROM replay_runs WHERE id=?", (run_id,)
            ).fetchone()
            self.assertIn("reinici", str(row["error"]).lower())

    def test_runs_terminais_nao_sao_tocadas(self):
        ids = [
            _insert_run(self.con, "success", "fp-s"),
            _insert_run(self.con, "failed", "fp-f"),
            _insert_run(self.con, "cancelled", "fp-c"),
        ]
        count = interrupt_stale_runs(self.con, now_ms_fn=now_ms)
        self.assertEqual(count, 0)
        for run_id, expected in zip(ids, ("success", "failed", "cancelled")):
            self.assertEqual(self._status(run_id), expected)

    def test_interrupcao_registra_evento_na_run(self):
        run_id = _insert_run(self.con, "running", "fp-ev")
        interrupt_stale_runs(self.con, now_ms_fn=now_ms)
        row = self.con.execute(
            "SELECT kind, message FROM replay_run_events"
            " WHERE run_id=? AND kind='status'",
            (run_id,),
        ).fetchone()
        self.assertIsNotNone(row)
        self.assertIn("failed", str(row["message"]))

    def test_fingerprint_liberado_para_nova_run(self):
        """Depois da interrupção, o índice UNIQUE parcial não bloqueia mais
        uma nova run com o mesmo fingerprint da run zumbi."""
        _insert_run(self.con, "queued", "fp-zumbi")
        interrupt_stale_runs(self.con, now_ms_fn=now_ms)
        nova_id = _insert_run(self.con, "queued", "fp-zumbi")  # não levanta
        self.assertGreater(nova_id, 0)

    def test_sem_runs_ativas_retorna_zero(self):
        self.assertEqual(interrupt_stale_runs(self.con, now_ms_fn=now_ms), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)

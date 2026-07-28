#!/usr/bin/env python3
"""Regressão: ``runs create`` deve sempre persistir params_json.

Bug (<=0.7.18): o UPDATE final de params_json só rodava quando havia janela
parcial (replay-from/to/session). Sem ela, flags como ``--input-mode
deterministic`` eram descartadas e a run executava com defaults (raw),
divergindo do pedido do operador.
"""
from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "gateway"))

from dakota_gateway import auth, cli  # noqa: E402
from dakota_gateway.state_db import connect, init_db, now_ms  # noqa: E402


def _mk_log_dir(base: Path) -> str:
    log_dir = base / "capture"
    log_dir.mkdir()
    events = [
        {"v": "v2", "seq_global": 1, "ts_ms": 1, "type": "session_start",
         "actor": "op", "session_id": "s1", "seq_session": 1, "logname": "op"},
        {"v": "v2", "seq_global": 2, "ts_ms": 2, "type": "bytes",
         "session_id": "s1", "seq_session": 2, "dir": "in", "data_b64": "b2s=", "n": 2},
        {"v": "v2", "seq_global": 3, "ts_ms": 3, "type": "session_end",
         "session_id": "s1", "seq_session": 3},
    ]
    with (log_dir / "audit-teste.part001.jsonl").open("w") as fh:
        for ev in events:
            fh.write(json.dumps(ev) + "\n")
    return str(log_dir)


class RunsCreateParamsUnitTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name)
        self.db = str(base / "replay.db")
        con = connect(self.db)
        init_db(con)
        con.execute(
            "INSERT INTO users(username,password_hash,role,created_at_ms) VALUES(?,?,?,?)",
            ("admin", auth.pbkdf2_hash_password("admin123"), "admin", now_ms()),
        )
        con.close()
        self.log_dir = _mk_log_dir(base)

    def tearDown(self):
        self._tmp.cleanup()

    def _create(self, *extra: str) -> tuple[int, dict]:
        buf = io.StringIO()
        argv = [
            "runs", "--db", self.db, "create",
            "--created-by", "admin",
            "--log-dir", self.log_dir,
            "--target-host", "127.0.0.1",
            "--target-user", "op",
            *extra,
        ]
        with contextlib.redirect_stdout(buf):
            rc = cli.main(argv)
        self.assertEqual(rc, 0, f"runs create falhou: {argv}")
        rid = int(buf.getvalue().strip().splitlines()[-1])
        con = connect(self.db)
        row = con.execute("SELECT params_json FROM replay_runs WHERE id=?", (rid,)).fetchone()
        con.close()
        raw = row["params_json"] if isinstance(row, dict) else row[0]
        self.assertIsNotNone(raw, "params_json não pode ficar NULL")
        return rid, json.loads(raw)

    def test_input_mode_deterministic_e_persistido(self):
        _, params = self._create(
            "--mode", "strict-global",
            "--input-mode", "deterministic",
            "--on-deterministic-mismatch", "skip",
            "--match-mode", "contains",
        )
        self.assertEqual(params["input_mode"], "deterministic")
        self.assertEqual(params["on_deterministic_mismatch"], "skip")
        self.assertEqual(params["match_mode"], "contains")

    def test_defaults_tambem_persistem(self):
        _, params = self._create()
        self.assertEqual(params["input_mode"], "raw")
        self.assertEqual(params["on_deterministic_mismatch"], "fail-fast")
        self.assertEqual(params["match_mode"], "strict")

    def test_janela_parcial_mescla_com_demais_params(self):
        _, params = self._create(
            "--input-mode", "deterministic",
            "--replay-from-seq-global", "2",
            "--replay-to-seq-global", "3",
        )
        self.assertEqual(params["input_mode"], "deterministic")
        self.assertEqual(params["replay_from_seq_global"], 2)
        self.assertEqual(params["replay_to_seq_global"], 3)


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GATEWAY_DIR = ROOT / "gateway"
sys.path.insert(0, str(GATEWAY_DIR))

from dakota_gateway.db.connection import ConnectionPool, connect
from dakota_gateway.db.migrations import init_db
from dakota_gateway.state_db import exec1, query_all, query_one


class DbLayerUnitTests(unittest.TestCase):
    def test_init_db_creates_core_tables_via_new_db_layer(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "layer.db")
            con = connect(db_path)
            try:
                init_db(con)
                tables = {
                    row["name"]
                    for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
                }
            finally:
                con.close()
        self.assertIn("users", tables)
        self.assertIn("replay_runs", tables)
        self.assertIn("target_environments", tables)

    def test_state_db_helpers_remain_compatible_after_extraction(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "shim.db")
            con = connect(db_path)
            try:
                init_db(con)
                user_id = exec1(
                    con,
                    "INSERT INTO users(username,password_hash,role,created_at_ms) VALUES(?,?,?,?)",
                    ("alice", "hash", "admin", 1),
                )
                row = query_one(con, "SELECT username FROM users WHERE id=?", (user_id,))
                rows = query_all(con, "SELECT id FROM users ORDER BY id", ())
            finally:
                con.close()
        self.assertEqual(row["username"], "alice")
        self.assertEqual(len(rows), 1)

    def test_connection_pool_uses_extracted_connection_layer(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "pool.db")
            pool = ConnectionPool(db_path, min_size=1, max_size=2)
            con = pool.acquire()
            try:
                init_db(con)
                names = [row["name"] for row in con.execute("PRAGMA table_info(users)").fetchall()]
            finally:
                pool.release(con)
                pool.close_all()
        self.assertIn("username", names)


if __name__ == "__main__":
    unittest.main()

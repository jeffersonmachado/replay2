#!/usr/bin/env python3
"""Regressão: DELETE /api/runs/{id} não pode falhar com 500.

Bug (<=0.8.27): a rota executava ``DELETE FROM journey_reports WHERE run_id=?``,
mas ``journey_reports`` não tem coluna ``run_id`` (nem vínculo com runs) —
sqlite3.OperationalError: no such column: run_id → 500. Eventos e falhas da
run já saem por ON DELETE CASCADE (FK de replay_run_events/replay_failures).
"""
from __future__ import annotations

import http.cookiejar
import importlib.util
import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, build_opener, HTTPCookieProcessor

ROOT = Path(__file__).resolve().parents[1]
GATEWAY_DIR = ROOT / "gateway"
sys.path.insert(0, str(GATEWAY_DIR))

import dakota_gateway.auth as auth
from dakota_gateway.state_db import connect, init_db, now_ms

HMAC_KEY = b"test_hmac_key_run_delete"

CONTROL_SERVER_PATH = GATEWAY_DIR / "control" / "server.py"
SPEC = importlib.util.spec_from_file_location("control_server", CONTROL_SERVER_PATH)
CONTROL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CONTROL)


def _insert_run(con, status: str) -> int:
    cur = con.execute(
        "INSERT INTO replay_runs(status,created_by,target_host,target_user,target_command,mode,log_dir,run_fingerprint,created_at_ms)"
        " VALUES(?,?,?,?,?,?,?,?,?)",
        (status, 1, "127.0.0.1", "op", "", "strict-global", "/tmp/x",
         f"fp-{status}-{now_ms()}", now_ms()),
    )
    return int(cur.lastrowid)


class RunDeleteRouteTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        base = Path(self.tmpdir.name)
        self.db_path = str(base / "test.db")
        con = connect(self.db_path)
        init_db(con)
        ph = auth.pbkdf2_hash_password("admin123")
        con.execute(
            "INSERT INTO users(username,password_hash,role,created_at_ms) VALUES(?,?,?,?)",
            ("admin", ph, "admin", now_ms()),
        )
        self.run_success = _insert_run(con, "success")
        con.close()

        try:
            self.server = CONTROL.ControlServer(
                ("127.0.0.1", 0),
                CONTROL.Handler,
                db_path=self.db_path,
                cookie_secret=b"test_cookie_secret_32_bytes___",
                hmac_key=HMAC_KEY,
            )
        except PermissionError as exc:
            raise unittest.SkipTest(f"sandbox sem permissao para abrir socket local: {exc}") from exc
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        time.sleep(0.2)

        # A run "em execução" precisa ser criada DEPOIS do boot: o servidor
        # marca runs ativas de processos anteriores como failed no startup
        # (interrupt_stale_runs) — criada depois, ela segue 'running' de fato.
        con = connect(self.db_path)
        self.run_running = _insert_run(con, "running")
        con.close()

        self.opener = build_opener(HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self._request("POST", "/api/login", {"username": "admin", "password": "admin123"})

    def tearDown(self):
        if hasattr(self, "server"):
            self.server.shutdown()
            self.server.server_close()
        self.tmpdir.cleanup()

    def _request(self, method: str, path: str, data: dict | None = None):
        url = f"http://127.0.0.1:{self.port}{path}"
        body = None if data is None else json.dumps(data).encode("utf-8")
        req = Request(url, data=body, headers={"Content-Type": "application/json"}, method=method)
        try:
            with self.opener.open(req, timeout=5) as resp:
                raw = resp.read().decode("utf-8")
                return resp.status, json.loads(raw) if raw else {}
        except HTTPError as exc:
            raw = exc.read().decode("utf-8")
            return exc.code, json.loads(raw) if raw else {}

    def test_delete_run_finalizada_remove_registro(self):
        status, payload = self._request("DELETE", f"/api/runs/{self.run_success}")
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        con = connect(self.db_path)
        row = con.execute(
            "SELECT id FROM replay_runs WHERE id=?", (self.run_success,)
        ).fetchone()
        con.close()
        self.assertIsNone(row)

    def test_delete_run_em_execucao_retorna_409(self):
        status, payload = self._request("DELETE", f"/api/runs/{self.run_running}")
        self.assertEqual(status, 409)
        self.assertFalse(payload["ok"])

    def test_delete_run_inexistente_retorna_404(self):
        status, _ = self._request("DELETE", "/api/runs/99999")
        self.assertEqual(status, 404)


if __name__ == "__main__":
    unittest.main(verbosity=2)

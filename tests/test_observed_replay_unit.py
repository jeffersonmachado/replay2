#!/usr/bin/env python3
"""Testes da gravação da sessão observada da run (v0.8.66).

Cobre o ObservedTrailRecorder (trilha auditável assinada da saída real do
destino), o seek server-side (resolve_seek_offset), a migração
replay_runs.observed_dir e a rota GET /api/runs/{id}/replay.
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

GATEWAY_DIR = Path(__file__).resolve().parents[1] / "gateway"
sys.path.insert(0, str(GATEWAY_DIR))

import dakota_gateway.auth as auth
from dakota_gateway.replay import ObservedTrailRecorder
from dakota_gateway.replay_control import create_run
from dakota_gateway.state_db import connect, init_db, now_ms
from dakota_gateway.verifier import verify_log

from control.services.session_replay_service import (
    prepare_session_replay_data,
    resolve_seek_offset,
)

CONTROL_SERVER_PATH = GATEWAY_DIR / "control" / "server.py"
SPEC = importlib.util.spec_from_file_location("control_server", CONTROL_SERVER_PATH)
CONTROL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CONTROL)

HMAC_KEY = b"test_hmac_key_32_bytes__________"


def _write_observed_session(observed_dir: Path, session_id: str, chunks: list[bytes]) -> Path:
    """Grava uma sessão observada sintética (start + chunks out + end)."""
    recorder = ObservedTrailRecorder(
        str(observed_dir),
        session_id,
        HMAC_KEY,
        actor="recital",
        rows=58,
        cols=80,
        term="xterm",
        encoding="utf-8",
    )
    recorder.start()
    for chunk in chunks:
        recorder.record_out(chunk)
    recorder.end()
    return observed_dir / session_id


class ObservedTrailRecorderTests(unittest.TestCase):
    def test_grava_trilha_assinada_e_reproduzivel(self):
        with tempfile.TemporaryDirectory() as tmp:
            session_dir = _write_observed_session(
                Path(tmp), "sess-abc", [b"\x1b[2JPROMPT> ", b"saida do destino\r\n"]
            )

            # Trilha íntegra: hash-chain + HMAC válidos com a mesma chave.
            verify_log(str(session_dir), HMAC_KEY)

            payload = prepare_session_replay_data(str(session_dir), "sess-abc", offset=0, limit=100)
            self.assertIsNone(payload.get("error"))
            self.assertEqual(len(payload["events"]), 2)
            self.assertEqual(payload["geometry"]["rows"], 58)
            self.assertEqual(payload["geometry"]["cols"], 80)
            self.assertEqual(payload["session_start"]["entry_mode"], "replay")
            # Manifest gerado no end()
            manifests = list(session_dir.glob("audit-*.jsonl.manifest.json"))
            self.assertTrue(manifests, "manifest da trilha observada não foi gravado")

    def test_session_id_eh_sanitizado_para_nome_de_diretorio(self):
        with tempfile.TemporaryDirectory() as tmp:
            recorder = ObservedTrailRecorder(
                str(tmp), "../etc/passwd", HMAC_KEY,
                actor="a", rows=25, cols=80, term="xterm", encoding="utf-8",
            )
            recorder.start()
            recorder.end()
            self.assertNotIn("..", recorder.session_dir.name)
            self.assertNotIn("/", recorder.session_dir.name)
            self.assertTrue(str(recorder.session_dir).startswith(str(Path(tmp))))


class ResolveSeekOffsetTests(unittest.TestCase):
    def _write_log(self, log_dir: Path) -> None:
        # bytes com seq 1..10 intercalados com deterministic_input
        lines = []
        for seq in range(1, 11):
            lines.append({"seq_global": seq, "type": "bytes", "session_id": "s1", "dir": "out", "n": 1})
            lines.append({"seq_global": 100 + seq, "type": "deterministic_input", "session_id": "s1"})
        lines.append({"seq_global": 5, "type": "bytes", "session_id": "outra-sessao", "dir": "out", "n": 1})
        (log_dir / "audit-20260828-000000.part001.jsonl").write_text(
            "\n".join(json.dumps(ev) for ev in lines) + "\n", encoding="utf-8"
        )

    def test_seek_com_contexto_maior_que_o_inicio_retorna_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp)
            self._write_log(log_dir)
            # 6 eventos bytes antes do seq 7; contexto default 40 → 0
            self.assertEqual(resolve_seek_offset(str(log_dir), "s1", 7), 0)

    def test_seek_respeita_o_lookback(self):
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp)
            self._write_log(log_dir)
            # 6 eventos bytes antes do seq 7; contexto 2 → 4
            self.assertEqual(resolve_seek_offset(str(log_dir), "s1", 7, context_events=2), 4)

    def test_seek_alem_do_fim_aponta_para_a_cauda(self):
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp)
            self._write_log(log_dir)
            self.assertEqual(resolve_seek_offset(str(log_dir), "s1", 999, context_events=2), 8)

    def test_seek_invalido_ou_zero_retorna_inicio(self):
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp)
            self._write_log(log_dir)
            self.assertEqual(resolve_seek_offset(str(log_dir), "s1", 0), 0)
            self.assertEqual(resolve_seek_offset(str(log_dir), "s1", -5), 0)


class ObservedDirMigrationTests(unittest.TestCase):
    def test_init_db_cria_coluna_observed_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            con = connect(str(Path(tmp) / "test.db"))
            try:
                init_db(con)
                cols = {row["name"] for row in con.execute("PRAGMA table_info(replay_runs)").fetchall()}
            finally:
                con.close()
            self.assertIn("observed_dir", cols)


class RunObservedReplayRouteTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tmpdir.name) / "test.db")
        self.cookie_secret = b"test_cookie_secret_32_bytes___"

        con = connect(self.db_path)
        init_db(con)
        ph = auth.pbkdf2_hash_password("admin123")
        con.execute(
            "INSERT INTO users(username,password_hash,role,created_at_ms) VALUES(?,?,'admin',?)",
            ("admin", ph, now_ms()),
        )
        user = con.execute("SELECT id FROM users WHERE username='admin'").fetchone()
        self.user_id = int(user["id"])
        con.close()

        try:
            self.server = CONTROL.ControlServer(
                ("127.0.0.1", 0),
                CONTROL.Handler,
                db_path=self.db_path,
                cookie_secret=self.cookie_secret,
                hmac_key=HMAC_KEY,
            )
        except PermissionError as exc:
            raise unittest.SkipTest(f"sandbox sem permissao para abrir socket local: {exc}") from exc
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        time.sleep(0.2)

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
            with self.opener.open(req, timeout=10) as resp:
                raw = resp.read().decode("utf-8")
                return resp.status, json.loads(raw) if raw else {}
        except HTTPError as exc:
            raw = exc.read().decode("utf-8")
            try:
                return exc.code, json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                return exc.code, {}

    def _create_run(self, *, observed_dir: str = "") -> int:
        con = connect(self.db_path)
        try:
            run_id = create_run(
                con,
                created_by=self.user_id,
                log_dir="/tmp/replay-audit",
                target_host="legacy.example",
                target_user="recital",
                target_command="",
                mode="strict-global",
            )
            if observed_dir:
                con.execute("UPDATE replay_runs SET observed_dir=? WHERE id=?", (observed_dir, run_id))
                con.commit()
            return run_id
        finally:
            con.close()

    def test_run_sem_trilha_observada_retorna_404(self):
        run_id = self._create_run()
        status, payload = self._request("GET", f"/api/runs/{run_id}/replay")
        self.assertEqual(status, 404)
        self.assertEqual(payload["error"]["code"], "no_observed_trail")

    def test_run_inexistente_retorna_404(self):
        status, _payload = self._request("GET", "/api/runs/99999/replay")
        self.assertEqual(status, 404)

    def test_sem_session_id_lista_sessoes_gravadas(self):
        observed_dir = Path(self.tmpdir.name) / "observed_runs" / "run-x"
        _write_observed_session(observed_dir, "sess-abc", [b"hello"])
        _write_observed_session(observed_dir, "sess-def", [b"world"])
        run_id = self._create_run(observed_dir=str(observed_dir))

        status, payload = self._request("GET", f"/api/runs/{run_id}/replay")
        self.assertEqual(status, 200)
        self.assertEqual(payload["run_id"], run_id)
        self.assertEqual(payload["sessions"], ["sess-abc", "sess-def"])

    def test_com_session_id_retorna_eventos(self):
        observed_dir = Path(self.tmpdir.name) / "observed_runs" / "run-y"
        _write_observed_session(observed_dir, "sess-abc", [b"\x1b[2JTELA", b"CONTEUDO"])
        run_id = self._create_run(observed_dir=str(observed_dir))

        status, payload = self._request(
            "GET", f"/api/runs/{run_id}/replay?session_id=sess-abc&offset=0&limit=100"
        )
        self.assertEqual(status, 200)
        self.assertEqual(len(payload["events"]), 2)
        self.assertEqual(payload["geometry"]["rows"], 58)
        self.assertEqual(payload["geometry"]["cols"], 80)
        self.assertEqual(payload["run_id"], run_id)

    def test_session_id_invalido_retorna_400(self):
        observed_dir = Path(self.tmpdir.name) / "observed_runs" / "run-z"
        _write_observed_session(observed_dir, "sess-abc", [b"x"])
        run_id = self._create_run(observed_dir=str(observed_dir))

        status, _payload = self._request(
            "GET", f"/api/runs/{run_id}/replay?session_id=..%2F..%2Fetc"
        )
        self.assertEqual(status, 400)

    def test_seek_seq_posiciona_janela(self):
        observed_dir = Path(self.tmpdir.name) / "observed_runs" / "run-w"
        _write_observed_session(observed_dir, "sess-abc", [b"chunk-%02d" % i for i in range(10)])
        run_id = self._create_run(observed_dir=str(observed_dir))

        # seek no último evento (seq 11 = session_start + 10 bytes):
        # offset = max(0, 9 - 40) = 0 → janela completa mesmo assim.
        status, payload = self._request(
            "GET", f"/api/runs/{run_id}/replay?session_id=sess-abc&seek_seq=11&limit=3"
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["window"]["offset"], 0)
        self.assertEqual(len(payload["events"]), 3)


if __name__ == "__main__":
    unittest.main()

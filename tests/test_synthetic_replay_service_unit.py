#!/usr/bin/env python3
"""Testes do fluxo Synthetic → Replay real (dívida X5).

Cobre:
- formato auditável do jsonl sintético (``data_b64`` real, decodificável
  pelo executor de replay via ``_decode_replay_input``);
- integridade da trilha (hash-chain + HMAC aceitos pelo ``verify_log``);
- serviço ``synthetic_replay_service`` (criação do run + disparo do Runner);
- cleanup do ``log_dir`` efêmero no Runner (sucesso e falha);
- rota ``POST /api/synthetic/stress/real``.
"""
from __future__ import annotations

import http.cookiejar
import importlib.util
import json
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock
from urllib.error import HTTPError
from urllib.request import Request, build_opener, HTTPCookieProcessor

ROOT = Path(__file__).resolve().parents[1]
GATEWAY_DIR = ROOT / "gateway"
sys.path.insert(0, str(GATEWAY_DIR))

import dakota_gateway.auth as auth
from dakota_gateway.replay import ReplayError, _decode_replay_input
from dakota_gateway.replay_control import Runner, create_run
from dakota_gateway.state_db import connect, init_db, now_ms, query_one
from dakota_gateway.synthetic.journey import JourneyDefinition, JourneyStep
from dakota_gateway.synthetic.journey_builder import JourneyBuilder
from dakota_gateway.synthetic.replay_adapter import ReplayAdapter
from dakota_gateway.verifier import verify_log

HMAC_KEY = b"test_hmac_key_synthetic_x5___"

CONTROL_SERVER_PATH = GATEWAY_DIR / "control" / "server.py"
SPEC = importlib.util.spec_from_file_location("control_server", CONTROL_SERVER_PATH)
CONTROL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CONTROL)


def _journey(journey_id: str = "x5_journey") -> JourneyDefinition:
    return JourneyDefinition(
        journey_id=journey_id,
        name="Jornada X5",
        category="test",
        steps=[
            JourneyStep(step_order=0, screen_id="menu", screen_title="Menu",
                        action="navigate", trigger="1"),
            JourneyStep(step_order=1, screen_id="cadastro", screen_title="Cadastro",
                        action="input", input_template="{{cliente.nome}}\n{{cliente.cpf}}"),
            JourneyStep(step_order=2, screen_id="cadastro", action="submit", trigger="F10"),
        ],
    )


def _save_journey(con, journey: JourneyDefinition) -> None:
    JourneyBuilder(db_connection=con).save_journey(journey)


def _create_user(con, username: str = "admin", role: str = "admin") -> int:
    ph = auth.pbkdf2_hash_password("admin123")
    cur = con.execute(
        "INSERT INTO users(username,password_hash,role,created_at_ms) VALUES(?,?,?,?)",
        (username, ph, role, now_ms()),
    )
    return int(cur.lastrowid)


def _expected_session_bytes(journey, session_count, seed, session_index, workdir) -> bytes:
    """Reconstrói os bytes esperados da sessão (inputs do gerador + ENTER)."""
    db = str(Path(workdir) / f"expected-{session_index}.db")
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    try:
        jds = JourneyBuilder(db_connection=con).build_journey_dataset(
            journey, session_count=session_count, seed=seed,
        )
    finally:
        con.close()
    inputs = ReplayAdapter().generate_synthetic_inputs(journey, jds, session_index)
    return "".join(f"{inp}\r" for inp in inputs).encode("utf-8")


class SyntheticJsonlFormatTests(unittest.TestCase):
    """Formato e integridade do jsonl sintético gerado pelo ReplayAdapter."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tmpdir.name) / "test.db")
        self.con = connect(self.db_path)
        init_db(self.con)
        self.journey = _journey()
        _save_journey(self.con, self.journey)

    def tearDown(self):
        self.con.close()
        self.tmpdir.cleanup()

    def _generate(self, session_count: int = 2, seed: int = 42):
        out = str(Path(self.tmpdir.name) / "jsonl")
        files = ReplayAdapter().generate_synthetic_jsonl(
            self.journey, session_count=session_count, seed=seed,
            output_dir=out, hmac_key=HMAC_KEY,
        )
        return out, files

    def test_bytes_decodificam_inputs_da_jornada(self):
        """data_b64 deve decodificar (via _decode_replay_input) para os bytes da jornada."""
        _, files = self._generate()
        self.assertEqual(len(files), 2)
        for session_id, path in sorted(files.items()):
            events = [
                json.loads(line)
                for line in Path(path).read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            decoded = b"".join(
                _decode_replay_input(ev) for ev in events if ev.get("type") == "bytes"
            )
            sess_idx = int(session_id.rsplit("-", 1)[1])
            expected = _expected_session_bytes(self.journey, 2, 42, sess_idx, self.tmpdir.name)
            self.assertGreater(len(decoded), 0)
            self.assertEqual(decoded, expected)

    def test_jsonl_passa_no_verify_log(self):
        """A trilha sintética deve ser auditável: hash-chain + HMAC válidos."""
        out, files = self._generate(session_count=3)
        audit_files = list(Path(out).glob("audit-*.jsonl"))
        self.assertEqual(len(audit_files), 3)
        for path in files.values():
            self.assertTrue(Path(path).name.startswith("audit-"))
        verify_log(out, HMAC_KEY)  # não levanta


class SyntheticReplayServiceTests(unittest.TestCase):
    """Serviço start_synthetic_replay_run (X5)."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tmpdir.name) / "test.db")
        self.con = connect(self.db_path)
        init_db(self.con)
        self.user_id = _create_user(self.con)
        _save_journey(self.con, _journey())

    def tearDown(self):
        self.con.close()
        self.tmpdir.cleanup()

    @staticmethod
    def _fake_runner():
        class _FakeRunner:
            def __init__(self):
                self.started = []

            def start_run_async(self, run_id):
                self.started.append(run_id)

        return _FakeRunner()

    def _start(self, body):
        from control.services.synthetic_replay_service import start_synthetic_replay_run

        fake = self._fake_runner()
        result = start_synthetic_replay_run(
            self.con,
            created_by=self.user_id,
            body=body,
            db_path=self.db_path,
            hmac_key=HMAC_KEY,
            runner=fake,
        )
        return result, fake

    def test_start_cria_run_real_e_dispara_runner(self):
        result, fake = self._start({
            "journey_id": "x5_journey",
            "target_host": "legacy.example",
            "target_user": "recital",
            "sessions": 2,
            "concurrency": 1,
            "seed": 7,
        })
        self.assertEqual(result["status_code"], 202)
        payload = result["payload"]
        self.assertEqual(payload["status"], "queued")
        self.assertEqual(payload["sessions"], 2)
        self.assertEqual(payload["simulation"], False)
        run_id = payload["run_id"]
        self.assertEqual(fake.started, [run_id])

        row = query_one(self.con, "SELECT * FROM replay_runs WHERE id=?", (run_id,))
        self.assertIsNotNone(row)
        base = Path(self.tmpdir.name) / "synthetic_runs"
        self.assertTrue(str(row["log_dir"]).startswith(str(base) + "/"))
        params = json.loads(row["params_json"])
        self.assertTrue(params["synthetic"])
        self.assertEqual(params["journey_id"], "x5_journey")
        self.assertEqual(params["seed"], 7)
        self.assertTrue(params["ephemeral_log_dir"])
        self.assertEqual(row["mode"], "parallel-sessions")
        # trilha materializada é auditável com a mesma chave do Runner
        verify_log(row["log_dir"], HMAC_KEY)
        self.assertEqual(len(list(Path(row["log_dir"]).glob("audit-*.jsonl"))), 2)

    def test_start_sem_journey_id_retorna_400(self):
        result, fake = self._start({"target_host": "legacy.example"})
        self.assertEqual(result["status_code"], 400)
        self.assertEqual(fake.started, [])

    def test_start_journey_inexistente_retorna_404(self):
        result, fake = self._start({
            "journey_id": "journey-que-nao-existe",
            "target_host": "legacy.example",
        })
        self.assertEqual(result["status_code"], 404)
        self.assertEqual(fake.started, [])
        row = query_one(self.con, "SELECT COUNT(*) AS c FROM replay_runs")
        self.assertEqual(int(row["c"]), 0)

    def test_start_mode_invalido_retorna_400(self):
        result, fake = self._start({
            "journey_id": "x5_journey",
            "target_host": "legacy.example",
            "mode": "modo-inventado",
        })
        self.assertEqual(result["status_code"], 400)
        self.assertEqual(fake.started, [])


class RunnerEphemeralLogDirTests(unittest.TestCase):
    """Cleanup do log_dir efêmero no Runner (params.ephemeral_log_dir)."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tmpdir.name) / "test.db")
        self.con = connect(self.db_path)
        init_db(self.con)
        self.user_id = _create_user(self.con)
        self.journey = _journey()
        _save_journey(self.con, self.journey)
        self.runner = Runner(self.db_path, HMAC_KEY)

    def tearDown(self):
        self.con.close()
        self.tmpdir.cleanup()

    def _make_run(self, params):
        log_dir = Path(self.tmpdir.name) / "synthetic_runs" / "run-abc123"
        ReplayAdapter().generate_synthetic_jsonl(
            self.journey, 1, 42, str(log_dir), hmac_key=HMAC_KEY,
        )
        rid = create_run(
            self.con,
            created_by=self.user_id,
            log_dir=str(log_dir),
            target_host="legacy.example",
            target_user="recital",
            target_command="",
            mode="parallel-sessions",
        )
        if params is not None:
            self.con.execute(
                "UPDATE replay_runs SET params_json=? WHERE id=?",
                (json.dumps(params), rid),
            )
        return rid, log_dir

    @mock.patch("dakota_gateway.replay_control.runner.replay_parallel_sessions_controlled")
    def test_log_dir_efemero_removido_ao_fim_sucesso(self, executor):
        rid, log_dir = self._make_run({"ephemeral_log_dir": True})
        self.runner.run_foreground(rid)
        row = query_one(self.con, "SELECT status FROM replay_runs WHERE id=?", (rid,))
        self.assertEqual(row["status"], "success")
        self.assertFalse(log_dir.exists())

    @mock.patch("dakota_gateway.replay_control.runner.replay_parallel_sessions_controlled")
    def test_log_dir_efemero_removido_ao_fim_falha(self, executor):
        executor.side_effect = ReplayError("boom")
        rid, log_dir = self._make_run({"ephemeral_log_dir": True})
        self.runner.run_foreground(rid)
        row = query_one(self.con, "SELECT status FROM replay_runs WHERE id=?", (rid,))
        self.assertEqual(row["status"], "failed")
        self.assertFalse(log_dir.exists())

    @mock.patch("dakota_gateway.replay_control.runner.replay_parallel_sessions_controlled")
    def test_log_dir_permanece_sem_flag(self, executor):
        rid, log_dir = self._make_run(None)
        self.runner.run_foreground(rid)
        row = query_one(self.con, "SELECT status FROM replay_runs WHERE id=?", (rid,))
        self.assertEqual(row["status"], "success")
        self.assertTrue(log_dir.exists())


class SyntheticReplayRouteTests(unittest.TestCase):
    """Rota POST /api/synthetic/stress/real (X5)."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tmpdir.name) / "test.db")
        self.cookie_secret = b"test_cookie_secret_32_bytes___"
        self.hmac_key = HMAC_KEY

        con = connect(self.db_path)
        init_db(con)
        _create_user(con)
        _save_journey(con, _journey())
        con.close()

        # Executores reais fariam SSH; aqui são neutralizados — o caminho
        # interno do Runner (verify_log, métricas, status, cleanup) é real.
        self._executor_patches = [
            mock.patch("dakota_gateway.replay_control.runner.replay_parallel_sessions_controlled"),
            mock.patch("dakota_gateway.replay_control.runner.replay_parallel_sessions_concurrent_controlled"),
            mock.patch("dakota_gateway.replay_control.runner.replay_strict_global_controlled"),
        ]
        for patcher in self._executor_patches:
            patcher.start()
            self.addCleanup(patcher.stop)

        try:
            self.server = CONTROL.ControlServer(
                ("127.0.0.1", 0),
                CONTROL.Handler,
                db_path=self.db_path,
                cookie_secret=self.cookie_secret,
                hmac_key=self.hmac_key,
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
            with self.opener.open(req, timeout=5) as resp:
                raw = resp.read().decode("utf-8")
                return resp.status, json.loads(raw) if raw else {}
        except HTTPError as exc:
            raw = exc.read().decode("utf-8")
            return exc.code, json.loads(raw) if raw else {}

    def test_post_stress_real_retorna_202_e_executa_run(self):
        status, payload = self._request("POST", "/api/synthetic/stress/real", {
            "journey_id": "x5_journey",
            "target_host": "legacy.example",
            "target_user": "recital",
            "sessions": 2,
            "concurrency": 1,
        })
        self.assertEqual(status, 202)
        self.assertEqual(payload["status"], "queued")
        self.assertEqual(payload["sessions"], 2)
        self.assertEqual(payload["simulation"], False)
        run_id = payload["run_id"]

        # aguarda a thread do Runner (executores mockados) concluir
        thread = self.server.runner._threads.get(run_id)
        if thread:
            thread.join(timeout=10)
        status, detail = self._request("GET", f"/api/runs/{run_id}")
        self.assertEqual(status, 200)
        self.assertEqual(detail["run"]["status"], "success")
        self.assertEqual(detail["run"]["verify_ok"], 1)
        # log_dir efêmero removido ao fim do run
        self.assertFalse(Path(detail["run"]["log_dir"]).exists())

    def test_post_sem_journey_id_retorna_400(self):
        status, _ = self._request("POST", "/api/synthetic/stress/real", {
            "target_host": "legacy.example",
        })
        self.assertEqual(status, 400)

    def test_post_journey_inexistente_retorna_404(self):
        status, _ = self._request("POST", "/api/synthetic/stress/real", {
            "journey_id": "journey-que-nao-existe",
            "target_host": "legacy.example",
        })
        self.assertEqual(status, 404)


if __name__ == "__main__":
    unittest.main(verbosity=2)

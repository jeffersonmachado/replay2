"""Testes das rotas REST do benchmark real (contrato §21).

Cobre: criação de experimento via POST, detalhe, métricas vazias (veredito
INCONCLUSIVE, sem números inventados), comparison sem execução, neutralização
do endpoint legado ``/api/synthetic/benchmark`` (simulation=true, sem
recomendação de migração), exigência de autenticação e o ciclo
start → execução real (com adapter fake injetado) → comparison/metrics/report.
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
from urllib.request import HTTPCookieProcessor, Request, build_opener

ROOT = Path(__file__).resolve().parents[2]
GATEWAY_DIR = ROOT / "gateway"
if str(GATEWAY_DIR) not in sys.path:
    sys.path.insert(0, str(GATEWAY_DIR))

import dakota_gateway.auth as auth
from dakota_gateway.benchmark.models import OperationSample
from dakota_gateway.state_db import connect, init_db, now_ms

CONTROL_SERVER_PATH = GATEWAY_DIR / "control" / "server.py"
SPEC = importlib.util.spec_from_file_location("control_server", CONTROL_SERVER_PATH)
CONTROL = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(CONTROL)


def _sample(experiment_id: str, env_id: str, *, latency_ms: float,
            phase: str) -> OperationSample:
    """Amostra mínima de aplicação para o adapter fake."""
    inicio = time.monotonic_ns()
    return OperationSample(
        experiment_id=experiment_id,
        environment_id=env_id,
        iteration=1,
        concurrency=1,
        virtual_user_id="vu-1",
        journey_id="j1",
        step_id="s1",
        phase=phase,
        started_ns=inicio,
        finished_ns=inicio + int(latency_ms * 1e6),
        latency_ms=latency_ms,
        success=True,
        timeout=False,
        functional_divergence=False,
        error_code=None,
    )


class FakeAdapter:
    """Adapter de teste (§8): produz amostras reais sem SSH."""

    def __init__(self, env, contract):
        self.env = env
        self.contract = contract
        self._iteration = 0
        self._concurrency = 0

    def set_iteration_context(self, iteration: int, concurrency: int) -> None:
        self._iteration = int(iteration)
        self._concurrency = int(concurrency)

    def preflight(self) -> dict:
        return {"ok": True, "checks": [{"name": "fake", "ok": True, "detail": ""}]}

    def prepare_dataset(self, dataset_ref: dict) -> dict:
        return {"ok": True}

    def start_session(self, virtual_user_id: str) -> str:
        return f"fake-{virtual_user_id}"

    def execute_journey(self, session_handle: str, journey: dict,
                        *, phase: str) -> list:
        base = 100.0 if self.env.environment_id == "env-a" else 120.0
        amostra = _sample(self.contract.experiment_id, self.env.environment_id,
                          latency_ms=base, phase=phase)
        amostra.iteration = self._iteration
        amostra.concurrency = self._concurrency
        return [amostra]

    def stop_session(self, session_handle: str) -> None:
        return None

    def collect_application_metrics(self) -> dict:
        return {}

    def collect_host_metrics(self, from_ms: int, to_ms: int) -> list:
        return [{
            "ts_ms": from_ms,
            "host_id": self.env.host,
            "platform": self.env.platform,
            "architecture": self.env.architecture,
            "cpu_user": 10.0,
            "cpu_system": 5.0,
            "mem_total_mb": 1024.0,
            "mem_used_mb": 512.0,
        }]

    def collect_database_metrics(self) -> dict:
        return {"available": False, "reason": "collector_not_supported"}

    def cleanup(self) -> None:
        return None


def _valid_body(**overrides) -> dict:
    body = {
        "journey_set_sha256": "a" * 64,
        "dataset_sha256": "b" * 64,
        "seed": 7,
        "concurrency_levels": [1],
        "iterations": 1,
        "warmup_seconds": 0,
        "measurement_seconds": 0,
        "cooldown_seconds": 0,
        "environments": [
            {"environment_id": "env-a", "platform": "AIX",
             "architecture": "POWER", "host": "192.0.2.25",
             "cpu": {"model": "POWER10", "virtual_processors": 4,
                     "entitled_capacity": 1.0},
             "memory_mb": 16384},
            {"environment_id": "env-b", "platform": "Linux",
             "architecture": "x86_64", "host": "192.0.2.30",
             "cpu": {"model": "x86", "virtual_processors": 4},
             "memory_mb": 16384},
        ],
        "journeys": [{"journey_id": "j1", "steps": [{"step_id": "s1"}]}],
    }
    body.update(overrides)
    return body


class BenchmarkRoutesTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tmpdir.name) / "test.db")
        self.artifacts_dir = str(Path(self.tmpdir.name) / "artifacts")
        self.cookie_secret = b"test_cookie_secret_32_bytes___"
        self.hmac_key = b"test_hmac_key_32_bytes__________"

        con = connect(self.db_path)
        init_db(con)
        ph = auth.pbkdf2_hash_password("admin123")
        con.execute(
            "INSERT INTO users(username,password_hash,role,created_at_ms) VALUES(?,?,'admin',?)",
            ("admin", ph, now_ms()),
        )
        con.close()

        self.server = CONTROL.ControlServer(
            ("127.0.0.1", 0),
            CONTROL.Handler,
            db_path=self.db_path,
            cookie_secret=self.cookie_secret,
            hmac_key=self.hmac_key,
            benchmark_artifacts_dir=self.artifacts_dir,
        )
        # Adapter fake: execução real do executor sem depender de SSH.
        self.server.benchmark_supervisor._adapter_factory = (
            lambda env, contract: FakeAdapter(env, contract))
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        time.sleep(0.2)
        self.opener = build_opener(HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self._request("POST", "/api/login", {"username": "admin", "password": "admin123"})

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmpdir.cleanup()

    def _request(self, method: str, path: str, data: dict | None = None):
        url = f"http://127.0.0.1:{self.port}{path}"
        body = None if data is None else json.dumps(data).encode("utf-8")
        req = Request(url, data=body, headers={"Content-Type": "application/json"}, method=method)
        with self.opener.open(req, timeout=10) as resp:
            return resp.status, resp.read().decode("utf-8"), dict(resp.headers)

    def _request_any(self, method: str, path: str, data: dict | None = None):
        url = f"http://127.0.0.1:{self.port}{path}"
        body = None if data is None else json.dumps(data).encode("utf-8")
        req = Request(url, data=body, headers={"Content-Type": "application/json"}, method=method)
        try:
            with self.opener.open(req, timeout=10) as resp:
                return resp.status, resp.read().decode("utf-8"), dict(resp.headers)
        except HTTPError as exc:
            return exc.code, exc.read().decode("utf-8"), dict(exc.headers)

    def _create_experiment(self, **overrides) -> dict:
        status, body, _ = self._request("POST", "/api/benchmarks", _valid_body(**overrides))
        self.assertEqual(status, 201, body)
        payload = json.loads(body)
        self.assertTrue(payload["ok"])
        return payload

    # ── criação e leitura ──────────────────────────────────────────────

    def test_create_experiment_and_get_detail(self):
        created = self._create_experiment()
        experiment_id = created["experiment_id"]
        self.assertTrue(created["contract_sha256"])
        manifest = Path(created["manifest"])
        self.assertTrue(manifest.is_file())

        status, body, _ = self._request("GET", f"/api/benchmarks/{experiment_id}")
        self.assertEqual(status, 200)
        detail = json.loads(body)["experiment"]
        self.assertEqual(detail["experiment_id"], experiment_id)
        self.assertEqual(detail["status"], "CREATED")
        self.assertEqual(detail["verdict"], "INCONCLUSIVE")
        self.assertEqual(detail["contract"]["journey_set_sha256"], "a" * 64)
        self.assertEqual(sorted(detail["environments"]), ["env-a", "env-b"])
        self.assertEqual(
            detail["environments"]["env-a"]["cpu"]["model"], "POWER10")

        # lista
        status, body, _ = self._request("GET", "/api/benchmarks")
        self.assertEqual(status, 200)
        ids = [e["experiment_id"] for e in json.loads(body)["experiments"]]
        self.assertIn(experiment_id, ids)

    def test_create_experiment_invalid_body(self):
        status, body, _ = self._request_any(
            "POST", "/api/benchmarks", {"environments": []})
        self.assertEqual(status, 400)
        self.assertIn("error", json.loads(body))

    def test_metrics_empty_means_inconclusive(self):
        created = self._create_experiment()
        experiment_id = created["experiment_id"]
        status, body, _ = self._request(
            "GET", f"/api/benchmarks/{experiment_id}/metrics")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["verdict"], "INCONCLUSIVE")
        self.assertEqual(payload["app_aggregates"], [])
        self.assertEqual(payload["host_aggregates"], [])

    def test_comparison_without_execution_is_inconclusive(self):
        created = self._create_experiment()
        experiment_id = created["experiment_id"]
        status, body, _ = self._request(
            "GET", f"/api/benchmarks/{experiment_id}/comparison")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertEqual(payload["verdict"], "INCONCLUSIVE")
        self.assertIsNone(payload["recommendation"])
        self.assertIsNone(payload["comparison"])
        self.assertEqual(payload["result_type"], "INCONCLUSIVE")

    def test_report_missing_before_execution(self):
        created = self._create_experiment()
        status, _body, _ = self._request_any(
            "GET", f"/api/benchmarks/{created['experiment_id']}/report")
        self.assertEqual(status, 404)

    def test_unknown_experiment_returns_404(self):
        status, _body, _ = self._request_any("GET", "/api/benchmarks/inexistente")
        self.assertEqual(status, 404)

    # ── autenticação ───────────────────────────────────────────────────

    def test_routes_require_auth(self):
        opener = build_opener(HTTPCookieProcessor(http.cookiejar.CookieJar()))

        def call(method, path, data=None):
            url = f"http://127.0.0.1:{self.port}{path}"
            body = None if data is None else json.dumps(data).encode("utf-8")
            req = Request(url, data=body,
                          headers={"Content-Type": "application/json"}, method=method)
            try:
                with opener.open(req, timeout=5) as resp:
                    return resp.status
            except HTTPError as exc:
                return exc.code

        self.assertEqual(call("GET", "/api/benchmarks"), 401)
        self.assertEqual(call("POST", "/api/benchmarks", _valid_body()), 401)
        self.assertEqual(call("POST", "/api/benchmarks/x/start", {}), 401)

    # ── endpoint legado neutralizado ───────────────────────────────────

    def test_legacy_synthetic_benchmark_always_simulation_no_recommendation(self):
        status, body, _ = self._request("POST", "/api/synthetic/benchmark", {
            "name": "sim", "journey_id": "j1",
            "environments": [{"name": "aix", "host": "h1"},
                             {"name": "linux", "host": "h2"}],
        })
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertIs(payload["simulation"], True)
        self.assertIn("simulation_notice", payload)
        self.assertNotIn("recommendation", payload)
        for comp in payload.get("comparisons") or []:
            self.assertNotIn("recommendations", comp)
            self.assertNotIn("recommendation", comp)
        # nenhuma chave de recomendação em lugar nenhum do payload
        self.assertNotIn("recommendation", body.lower())

    # ── ciclo de execução real (adapter fake) ──────────────────────────

    def test_start_executes_and_produces_real_results(self):
        created = self._create_experiment()
        experiment_id = created["experiment_id"]

        status, body, _ = self._request(
            "POST", f"/api/benchmarks/{experiment_id}/start", {})
        self.assertEqual(status, 202, body)
        self.assertEqual(json.loads(body)["status"], "RUNNING")

        # start duplicado durante a execução → 409 (pode já ter concluído)
        # Espera baseada no sinal real de conclusão: a thread supervisionada
        # (join retorna no instante do fim). O polling anterior com teto fixo
        # de 20 s estourava sob contenção de CPU da suíte cheia (DEBT_MAP,
        # intermitências 0.8.0/0.8.4). O teto de 120 s só dispara se a thread
        # travar de verdade — não é espera cega: concluiu, segue imediato.
        concluiu = self.server.benchmark_supervisor.wait_completion(
            experiment_id, timeout=120)
        self.assertTrue(concluiu, "thread do experimento travada (deadlock?)")

        status, body, _ = self._request(
            "GET", f"/api/benchmarks/{experiment_id}")
        final = json.loads(body)["experiment"]
        self.assertEqual(final["status"], "COMPLETED", final.get("reason"))
        self.assertGreater(final["runs_count"], 0)

        # comparison agora é REAL, com estatísticas e decisão computadas
        status, body, _ = self._request(
            "GET", f"/api/benchmarks/{experiment_id}/comparison")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertEqual(payload["result_type"], "REAL")
        self.assertIn(payload["verdict"], ("PASS", "WARN", "FAIL", "INCONCLUSIVE"))
        stats = payload["comparison"]["stats_by_env"]
        self.assertIn("env-a", stats)
        self.assertGreater(stats["env-a"]["n"], 0)
        self.assertGreater(stats["env-a"]["p95"], 0)
        self.assertIn("tps_per_vcpu",
                      payload["comparison"]["normalization"]["per_environment"]["env-a"])

        # métricas agregadas por ambiente (app + host)
        status, body, _ = self._request(
            "GET", f"/api/benchmarks/{experiment_id}/metrics")
        self.assertEqual(status, 200)
        metrics = json.loads(body)
        medicao = [a for a in metrics["app_aggregates"]
                   if a["phase"] == "MEASUREMENT"]
        self.assertTrue(medicao)
        self.assertTrue(metrics["host_aggregates"])
        self.assertIsNotNone(metrics["host_aggregates"][0]["cpu_busy_avg"])

        # runs
        status, body, _ = self._request(
            "GET", f"/api/benchmarks/{experiment_id}/runs")
        self.assertEqual(status, 200)
        runs = json.loads(body)["runs"]
        self.assertTrue(runs)

        # report.json e report.md
        status, body, headers = self._request(
            "GET", f"/api/benchmarks/{experiment_id}/report")
        self.assertEqual(status, 200)
        self.assertIn("application/json", headers.get("Content-Type", ""))
        self.assertIn("verdict", json.loads(body))
        status, body, headers = self._request(
            "GET", f"/api/benchmarks/{experiment_id}/report?format=md")
        self.assertEqual(status, 200)
        self.assertIn("text/markdown", headers.get("Content-Type", ""))
        self.assertIn("# Relatório de Benchmark", body)

    def test_cancel_not_running_returns_409(self):
        created = self._create_experiment()
        status, body, _ = self._request_any(
            "POST", f"/api/benchmarks/{created['experiment_id']}/cancel", {})
        self.assertEqual(status, 409)
        self.assertIn("error", json.loads(body))


class WaitCompletionUnitTests(unittest.TestCase):
    """Cobre o sinal de conclusão do supervisor (sem servidor HTTP)."""

    def _supervisor(self, tmp: str):
        from control.services import benchmark_service
        return benchmark_service.BenchmarkSupervisor(
            f"{tmp}/bench.db", f"{tmp}/artifacts")

    def test_sem_thread_conclui_imediato(self):
        with tempfile.TemporaryDirectory() as tmp:
            sup = self._supervisor(tmp)
            self.assertTrue(sup.wait_completion("inexistente", timeout=0.01))

    def test_thread_viva_timeout_false_e_apos_fim_true(self):
        with tempfile.TemporaryDirectory() as tmp:
            sup = self._supervisor(tmp)
            started = threading.Event()
            release = threading.Event()

            def work():
                started.set()
                release.wait(5)

            t = threading.Thread(target=work, daemon=True)
            sup._threads["exp-1"] = t
            t.start()
            self.assertTrue(started.wait(2))
            # viva: timeout expira e informa NÃO concluído
            self.assertFalse(sup.wait_completion("exp-1", timeout=0.05))
            # sinal real de conclusão: retorna True logo após o fim
            release.set()
            inicio = time.monotonic()
            self.assertTrue(sup.wait_completion("exp-1", timeout=5))
            self.assertLess(time.monotonic() - inicio, 2)


if __name__ == "__main__":
    unittest.main()

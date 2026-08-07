"""Regressão: importar experimentos de benchmark dos artefatos para o banco.

O tarball de release inclui ``artifacts/benchmarks/<experiment_id>/`` (§33),
mas a listagem da UI lê apenas ``benchmark_experiments`` — servidor recém-
instalado/atualizado mostrava a lista de experimentos vazia mesmo com
relatórios reais em disco. A importação (boot do control plane) adota os
experimentos encontrados nos artefatos: contrato do manifesto imutável,
status/verdict/reason do execution-result.json e runs de runs/*/.

O teste DEVE FALHAR antes da implementação e PASSAR depois dela.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GATEWAY_DIR = ROOT / "gateway"
if str(GATEWAY_DIR) not in sys.path:
    sys.path.insert(0, str(GATEWAY_DIR))

from dakota_gateway.benchmark.contract import create_contract, load_contract
from dakota_gateway.state_db import connect, init_db

from control.services import benchmark_service as svc


def _make_experiment(artifacts_dir: Path, experiment_id: str, *,
                     status: str = "COMPLETED", verdict: str = "WARN",
                     reason: str = "stop_condition:test",
                     runs: int = 2) -> Path:
    """Cria um diretório de experimento mínimo no layout do §24."""
    exp_dir = artifacts_dir / experiment_id
    contract = create_contract(
        experiment_id=experiment_id,
        journey_set_sha256="a" * 64,
        dataset_sha256="b" * 64,
        application_version_sha256="c" * 64,
        seed=42,
        terminal_geometry="80x24",
        concurrency_levels=[1, 5],
        warmup_seconds=30,
        measurement_seconds=120,
        cooldown_seconds=30,
        iterations=1,
        think_time_profile={"type": "deterministic", "sha256": "d" * 64,
                            "params": {}},
        stop_conditions={"error_rate_pct": 5, "p99_limit_ms": 5000,
                         "host_cpu_pct": 95, "swap_growth_mb": 512,
                         "host_cpu_sustained_samples": 3},
        environments=["aix-power", "linux-x86"],
    )
    contract.write_manifest(exp_dir)
    (exp_dir / "execution-result.json").write_text(json.dumps({
        "experiment_id": experiment_id,
        "contract_sha256": contract.sha256(),
        "status": status,
        "verdict": verdict,
        "reason": reason,
    }), encoding="utf-8")
    for i in range(runs):
        env = "aix-power" if i % 2 == 0 else "linux-x86"
        run_id = f"{env}-iter1-conc{i + 1}"
        run_dir = exp_dir / "runs" / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "execution-result.json").write_text(json.dumps({
            "run_id": run_id,
            "experiment_id": experiment_id,
            "environment_id": env,
            "iteration": 1,
            "concurrency": i + 1,
            "environment_order": ["aix-power", "linux-x86"],
            "status": "COMPLETED",
            "error_reason": "",
        }), encoding="utf-8")
    return exp_dir


class BenchmarkImportTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        tmp = Path(self.tmpdir.name)
        self.db_path = str(tmp / "test.db")
        self.artifacts_dir = tmp / "benchmarks"
        self.artifacts_dir.mkdir()
        con = connect(self.db_path)
        init_db(con)
        con.close()

    def tearDown(self):
        self.tmpdir.cleanup()

    def _con(self):
        return connect(self.db_path)

    def test_import_adopts_experiment_from_artifacts(self):
        _make_experiment(self.artifacts_dir, "exp-import-1")

        con = self._con()
        try:
            resumo = svc.import_experiments_from_artifacts(
                con, artifacts_dir=self.artifacts_dir)
            lista = svc.list_experiments_payload(con)
            detalhe = svc.experiment_detail_payload(
                con, "exp-import-1", artifacts_dir=self.artifacts_dir)
            runs = svc.list_runs_payload(con, "exp-import-1")
        finally:
            con.close()

        self.assertEqual(resumo["imported"], ["exp-import-1"])
        self.assertEqual(resumo["errors"], [])
        ids = [e["experiment_id"] for e in lista["experiments"]]
        self.assertEqual(ids, ["exp-import-1"])
        self.assertEqual(lista["experiments"][0]["status"], "COMPLETED")
        self.assertEqual(lista["experiments"][0]["verdict"], "WARN")
        self.assertEqual(lista["experiments"][0]["reason"], "stop_condition:test")
        self.assertIsNotNone(detalhe)
        self.assertEqual(detalhe["experiment"]["runs_count"], 2)
        self.assertEqual(len(runs["runs"]), 2)

    def test_import_is_idempotent_and_never_overwrites(self):
        _make_experiment(self.artifacts_dir, "exp-import-2", verdict="PASS")

        con = self._con()
        try:
            primeiro = svc.import_experiments_from_artifacts(
                con, artifacts_dir=self.artifacts_dir)
            # Reescrita local simulando evolução posterior do banco
            con.execute(
                "UPDATE benchmark_experiments SET verdict='FAIL' WHERE experiment_id=?",
                ("exp-import-2",))
            con.commit()
            segundo = svc.import_experiments_from_artifacts(
                con, artifacts_dir=self.artifacts_dir)
            row = con.execute(
                "SELECT verdict FROM benchmark_experiments WHERE experiment_id=?",
                ("exp-import-2",)).fetchone()
        finally:
            con.close()

        self.assertEqual(primeiro["imported"], ["exp-import-2"])
        self.assertEqual(segundo["imported"], [])
        self.assertEqual(segundo["skipped"], ["exp-import-2"])
        self.assertEqual(row["verdict"], "FAIL",
                         "importação não pode sobrescrever estado já no banco")

    def test_import_ignores_dirs_without_manifest_and_bad_json(self):
        (self.artifacts_dir / "sem-manifesto").mkdir()
        ruim = self.artifacts_dir / "manifesto-quebrado"
        ruim.mkdir()
        (ruim / "experiment-manifest.json").write_text("{invalido", encoding="utf-8")

        con = self._con()
        try:
            resumo = svc.import_experiments_from_artifacts(
                con, artifacts_dir=self.artifacts_dir)
            lista = svc.list_experiments_payload(con)
        finally:
            con.close()

        self.assertEqual(resumo["imported"], [])
        self.assertIn("manifesto-quebrado", resumo["errors"])
        self.assertEqual(lista["experiments"], [])

    def test_import_missing_artifacts_dir_is_noop(self):
        con = self._con()
        try:
            resumo = svc.import_experiments_from_artifacts(
                con, artifacts_dir=Path(self.tmpdir.name) / "inexistente")
        finally:
            con.close()
        self.assertEqual(resumo, {"imported": [], "skipped": [], "errors": []})

    def test_control_server_boot_adopts_experiments(self):
        """Boot do ControlServer adota experimentos dos artefatos (deploy → UI)."""
        import importlib.util

        from dakota_gateway import auth
        from dakota_gateway.state_db import now_ms

        control_server_path = GATEWAY_DIR / "control" / "server.py"
        spec = importlib.util.spec_from_file_location(
            "control_server_boot_import", control_server_path)
        control = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(control)

        _make_experiment(self.artifacts_dir, "exp-boot-import", verdict="PASS")
        con = self._con()
        con.execute(
            "INSERT INTO users(username,password_hash,role,created_at_ms) VALUES(?,?,'admin',?)",
            ("admin", auth.pbkdf2_hash_password("admin123"), now_ms()),
        )
        con.close()

        server = control.ControlServer(
            ("127.0.0.1", 0),
            control.Handler,
            db_path=self.db_path,
            cookie_secret=b"test_cookie_secret_32_bytes___",
            hmac_key=b"test_hmac_key_32_bytes__________",
            capture_log_dir=str(Path(self.tmpdir.name) / "captures"),
            benchmark_artifacts_dir=str(self.artifacts_dir),
        )
        try:
            con = self._con()
            try:
                lista = svc.list_experiments_payload(con)
            finally:
                con.close()
        finally:
            server.server_close()

        ids = [e["experiment_id"] for e in lista["experiments"]]
        self.assertEqual(ids, ["exp-boot-import"])
        self.assertEqual(lista["experiments"][0]["verdict"], "PASS")


if __name__ == "__main__":
    unittest.main()

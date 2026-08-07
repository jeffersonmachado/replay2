"""Regressão: subcomando CLI ``benchmark import`` adota experimentos dos artefatos.

A importação de experimentos de benchmark dos artefatos para o banco só
acontecia no boot do control plane (UI). Operadores sem UI (servidor headless,
scripts de homologação) não tinham como adotar manualmente os experimentos de
``artifacts/benchmarks/<experiment_id>/`` — o princípio do projeto é que todo
recurso de UI tenha cobertura equivalente em CLI quando fizer sentido
operacional (AGENTS.md §5).

O teste DEVE FALHAR antes da implementação e PASSAR depois dela.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GATEWAY_DIR = ROOT / "gateway"
if str(GATEWAY_DIR) not in sys.path:
    sys.path.insert(0, str(GATEWAY_DIR))

from dakota_gateway.cli import main as cli_main
from dakota_gateway.state_db import connect, init_db

from control.services import benchmark_service as svc

sys.path.insert(0, str(ROOT / "gateway" / "tests"))
from test_benchmark_import import _make_experiment  # noqa: E402


class BenchmarkCliImportTests(unittest.TestCase):
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

    def _run_import(self) -> int:
        return cli_main([
            "benchmark", "import",
            "--db", self.db_path,
            "--artifacts-dir", str(self.artifacts_dir),
        ])

    def test_import_populates_empty_db(self):
        _make_experiment(self.artifacts_dir, "exp-cli-import", verdict="PASS")

        rc = self._run_import()

        self.assertEqual(rc, 0)
        con = connect(self.db_path)
        try:
            lista = svc.list_experiments_payload(con)
        finally:
            con.close()
        ids = [e["experiment_id"] for e in lista["experiments"]]
        self.assertEqual(ids, ["exp-cli-import"])
        self.assertEqual(lista["experiments"][0]["verdict"], "PASS")

    def test_import_is_idempotent(self):
        _make_experiment(self.artifacts_dir, "exp-cli-import-2")

        self.assertEqual(self._run_import(), 0)
        self.assertEqual(self._run_import(), 0)

        con = connect(self.db_path)
        try:
            lista = svc.list_experiments_payload(con)
        finally:
            con.close()
        self.assertEqual(len(lista["experiments"]), 1)

    def test_import_returns_nonzero_on_broken_manifest(self):
        ruim = self.artifacts_dir / "manifesto-quebrado"
        ruim.mkdir()
        (ruim / "experiment-manifest.json").write_text("{invalido", encoding="utf-8")

        self.assertEqual(self._run_import(), 1)

    def test_import_missing_artifacts_dir_is_noop_success(self):
        inexistente = Path(self.tmpdir.name) / "inexistente"
        rc = cli_main([
            "benchmark", "import",
            "--db", self.db_path,
            "--artifacts-dir", str(inexistente),
        ])
        self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()

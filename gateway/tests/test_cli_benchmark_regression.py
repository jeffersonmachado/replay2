"""Regressão: ``import time`` local em ``_handle_synthetic`` (cli.py).

Antes da correção, o ``import time`` dentro do branch ``watch`` tornava
``time`` uma variável LOCAL de ``_handle_synthetic``, quebrando com
``UnboundLocalError`` o branch ``benchmark`` (``time.time()`` na criação do
``benchmark_id``). Este teste executa o subcomando legado
``synthetic benchmark`` de ponta a ponta e exige rc=0.

Também cobre o smoke do subcomando top-level ``benchmark create`` (§22):
cria o contrato, grava o manifesto e persiste o experimento no SQLite.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "gateway"))

from dakota_gateway.cli import main  # noqa: E402


class TestCliSyntheticBenchmarkTimeRegression(unittest.TestCase):
    """O branch benchmark de _handle_synthetic não pode quebrar por `time`."""

    def test_synthetic_benchmark_nao_levanta_unbound_local_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = main([
                    "synthetic", "--db", os.path.join(tmp, "t.db"),
                    "benchmark", "--name", "regressao", "--journey-id", "j1",
                    "--envs", '[{"name":"aix","host":"h1"},'
                              '{"name":"linux","host":"h2"}]',
                    "--iterations", "1",
                ])
            self.assertEqual(0, rc)
            saida = json.loads(buf.getvalue())
            # endpoint/subcomando legado: marcado como simulação, sem decisão
            self.assertTrue(saida.get("simulation"))
            self.assertEqual(2, len(saida["environments"]))


class TestCliBenchmarkTopLevel(unittest.TestCase):
    """Smoke do subcomando top-level ``benchmark`` (§22 do contrato)."""

    def _contrato(self, tmp: str) -> str:
        dados = {
            "experiment_id": "exp-cli-regr",
            "journey_set_sha256": "a" * 64,
            "dataset_sha256": "b" * 64,
            "application_version_sha256": "c" * 64,
            "seed": 42,
            "terminal_geometry": "80x24",
            "concurrency_levels": [1],
            "warmup_seconds": 1,
            "measurement_seconds": 1,
            "cooldown_seconds": 1,
            "iterations": 1,
            "think_time_profile": {"type": "none", "sha256": "d" * 64,
                                   "params": {}},
            "stop_conditions": {},
            "environments": ["env-x"],
        }
        caminho = os.path.join(tmp, "contract.json")
        with open(caminho, "w", encoding="utf-8") as fh:
            json.dump(dados, fh)
        return caminho

    def test_create_status_e_exit_code_inconclusive_no_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            artifacts = os.path.join(tmp, "artifacts")
            db = os.path.join(tmp, "t.db")
            contrato = self._contrato(tmp)

            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = main(["benchmark", "create", "--contract", contrato,
                           "--artifacts-dir", artifacts, "--db", db])
            self.assertEqual(0, rc)
            criacao = json.loads(buf.getvalue())
            self.assertEqual("exp-cli-regr", criacao["experiment_id"])
            self.assertTrue(
                Path(criacao["manifest"]).is_file(), "manifesto não gravado")

            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = main(["benchmark", "status", "--experiment-id",
                           "exp-cli-regr", "--artifacts-dir", artifacts,
                           "--db", db])
            self.assertEqual(0, rc)
            status = json.loads(buf.getvalue())
            self.assertEqual("CREATED", status["experiment"]["status"])
            self.assertEqual("INCONCLUSIVE", status["experiment"]["verdict"])

            # report sem nenhuma run real → INCONCLUSIVE → exit code != 0
            with contextlib.redirect_stdout(io.StringIO()):
                rc = main(["benchmark", "report", "--experiment-id",
                           "exp-cli-regr", "--artifacts-dir", artifacts,
                           "--db", db])
            self.assertNotEqual(0, rc)


if __name__ == "__main__":
    unittest.main()

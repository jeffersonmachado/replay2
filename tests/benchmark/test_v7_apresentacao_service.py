"""Regressão: apresentação do benchmark histórico v7 (veredito INCONCLUSIVE).

Caso real: o v7 (`artifacts/benchmarks/cap13-aix-linux-oficial-v7`) foi
executado ANTES da FASE 4 (gates de proveniência/cobertura). O payload
persistido em ``benchmark_comparisons`` não tem ``provenance_problems`` nem
``collector_coverage`` e diz ``verdict=WARN`` com recomendação de
"Equivalência funcional comprovada" — mas o recálculo com o contrato
(``TestRegressaoV7`` em test_provenance_hashes.py) prova INCONCLUSIVE:
hashes de proveniência idênticos + think time placeholder, grupo "rede"
ausente no sampler e clock skew fora do gate.

O serviço (`comparison_payload`) não pode servir o payload persistido stale
quando há manifesto + runs em disco para recalcular com o contrato; ao
recalcular, corrige também ``benchmark_experiments``/``benchmark_comparisons``
para que a lista e o boot não reintroduzam o WARN. Sem dados para recalcular
(experimento sem manifesto), cai no persistido — nunca quebra.

Estes testes DEVEM FALHAR antes da correção e PASSAR depois dela.
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

from dakota_gateway.benchmark import persistence as bp  # noqa: E402
from dakota_gateway.benchmark.contract import create_contract  # noqa: E402
from dakota_gateway.state_db import connect, init_db  # noqa: E402

from control.services import benchmark_service as svc  # noqa: E402

V7_ID = "cap13-aix-linux-oficial-v7"
V7_ARTIFACTS = ROOT / "artifacts" / "benchmarks"
V7_DIR = V7_ARTIFACTS / V7_ID

#: Payload no formato pré-FASE 4 (sem provenance_problems/collector_coverage),
#: reproduzindo o estado persistido real do v7 no banco.
PAYLOAD_STALE = {
    "verdict": "WARN",
    "recommendation": ("Equivalência funcional comprovada, mas há ressalvas "
                       "de desempenho/eficiência — revisar os avisos."),
    "reasons": ["escada interrompida: stop_condition:session_admission_limit"],
    "comparison": {"stats_by_env": {}, "tps_by_env": {}},
}


def _db(tmp: Path):
    db_path = str(tmp / "test.db")
    con = connect(db_path)
    init_db(con)
    return con


@unittest.skipUnless((V7_DIR / "experiment-manifest.json").is_file(),
                     "artefatos oficiais v7 ausentes")
class TestApresentacaoV7(unittest.TestCase):
    """Serviço sobre o experimento v7 real: nunca mais WARN/recomendação."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.con = _db(Path(self.tmpdir.name))
        # Estado legado: importação copia o veredito do execution-result.json
        # e o banco carrega um payload de comparação pré-FASE 4 (WARN).
        svc.import_experiments_from_artifacts(
            self.con, artifacts_dir=V7_ARTIFACTS)
        self.con.execute(
            "UPDATE benchmark_experiments SET verdict='WARN' "
            "WHERE experiment_id=?", (V7_ID,))
        self.con.commit()
        bp.save_comparison(self.con, V7_ID, dict(PAYLOAD_STALE))

    def tearDown(self):
        self.con.close()
        self.tmpdir.cleanup()

    def test_comparison_payload_recalcula_veredito(self):
        payload = svc.comparison_payload(
            self.con, V7_ID, artifacts_dir=V7_ARTIFACTS)

        self.assertEqual("INCONCLUSIVE", payload["verdict"])
        self.assertIsNone(payload["recommendation"])
        self.assertIn("proveni", " ".join(payload["reasons"]))
        problemas = payload["comparison"]["provenance_problems"]
        self.assertTrue(problemas)
        self.assertIn("collector_coverage", payload["comparison"])

    def test_recalculo_corrige_banco(self):
        """Lista e boot não podem reintroduzir o WARN após o recálculo."""
        svc.comparison_payload(self.con, V7_ID, artifacts_dir=V7_ARTIFACTS)

        exp = bp.get_experiment(self.con, V7_ID)
        self.assertEqual("INCONCLUSIVE", exp["verdict"])
        row = self.con.execute(
            "SELECT payload_json FROM benchmark_comparisons"
            " WHERE experiment_id=? ORDER BY id DESC LIMIT 1",
            (V7_ID,)).fetchone()
        persistido = json.loads(row["payload_json"])
        self.assertEqual("INCONCLUSIVE", persistido["verdict"])
        self.assertIsNone(persistido["recommendation"])
        self.assertIn("provenance_problems", persistido["comparison"])
        self.assertIn("collector_coverage", persistido["comparison"])

    def test_report_payload_sem_recomendacao(self):
        """report.json/report.md em disco exibem o veredito recalculado."""
        ct, conteudo = svc.report_payload(
            V7_ID, artifacts_dir=V7_ARTIFACTS, fmt="json")
        self.assertEqual("application/json; charset=utf-8", ct)
        rep = json.loads(conteudo)
        self.assertEqual("INCONCLUSIVE", rep["verdict"])
        self.assertFalse(rep.get("recommendation"))

        _, md = svc.report_payload(V7_ID, artifacts_dir=V7_ARTIFACTS, fmt="md")
        self.assertIn("INCONCLUSIVE", md)
        self.assertNotIn("**Veredito:** WARN", md)
        self.assertNotIn("Equivalência funcional: OK", md)
        self.assertNotIn("Equivalência funcional comprovada", md)


@unittest.skipUnless((V7_DIR / "experiment-manifest.json").is_file(),
                     "artefatos oficiais v7 ausentes")
class TestPayloadPersistidoFresco(TestApresentacaoV7):
    """Payload pós-FASE 4 (com os marcadores) NÃO é recalculado."""

    def setUp(self):
        super().setUp()
        fresco = dict(PAYLOAD_STALE)
        fresco["comparison"] = {
            "stats_by_env": {},
            "provenance_problems": [],
            "collector_coverage": {},
        }
        bp.save_comparison(self.con, V7_ID, fresco)

    def test_comparison_payload_recalcula_veredito(self):
        payload = svc.comparison_payload(
            self.con, V7_ID, artifacts_dir=V7_ARTIFACTS)
        # short-circuit: payload fresco é servido verbatim
        self.assertEqual("WARN", payload["verdict"])

    def test_recalculo_corrige_banco(self):
        self.skipTest("payload fresco não dispara recálculo")

    def test_report_payload_sem_recomendacao(self):
        self.skipTest("coberto por TestApresentacaoV7")


class TestFallbackSemArtefatos(unittest.TestCase):
    """Sem manifesto/runs em disco, o persistido (mesmo stale) é servido."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        tmp = Path(self.tmpdir.name)
        self.con = _db(tmp)
        self.artifacts_dir = tmp / "benchmarks"
        self.artifacts_dir.mkdir()
        contrato = create_contract(
            experiment_id="exp-sem-artefatos",
            journey_set_sha256="a" * 64,
            dataset_sha256="b" * 64,
            application_version_sha256="c" * 64,
            seed=42,
            terminal_geometry="80x24",
            concurrency_levels=[1],
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
        bp.save_experiment(self.con, contrato, status="COMPLETED",
                           verdict="WARN", reason="stop_condition:test")
        bp.save_comparison(self.con, "exp-sem-artefatos", dict(PAYLOAD_STALE))

    def tearDown(self):
        self.con.close()
        self.tmpdir.cleanup()

    def test_cai_no_persistido(self):
        payload = svc.comparison_payload(
            self.con, "exp-sem-artefatos", artifacts_dir=self.artifacts_dir)
        self.assertEqual("WARN", payload["verdict"])
        self.assertEqual(PAYLOAD_STALE["recommendation"],
                         payload["recommendation"])
        # fallback não inventa correção no banco
        exp = bp.get_experiment(self.con, "exp-sem-artefatos")
        self.assertEqual("WARN", exp["verdict"])


if __name__ == "__main__":
    unittest.main()

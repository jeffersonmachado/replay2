#!/usr/bin/env python3
"""Teste de integração end-to-end: pipeline completo."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GATEWAY_DIR = ROOT / "gateway"
sys.path.insert(0, str(GATEWAY_DIR))

from dakota_gateway.state_db import connect, init_db


class EndToEndPipelineTests(unittest.TestCase):
    """Valida o pipeline completo: analyze → infer → journey → stress → report → junit."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tmpdir.name) / "test.db")
        self.con = connect(self.db_path)
        init_db(self.con)
        self._setup_source_files()

    def tearDown(self):
        self.con.close()
        self.tmpdir.cleanup()

    def _setup_source_files(self):
        src = Path(self.tmpdir.name) / "src"
        src.mkdir()
        (src / "menu.prg").write_text("""*PROG: Menu Principal
DO WHILE .T.
   DO cad0101
   DO cad0102
   DO fin0101
ENDDO
""")

        cad = src / "cad"
        cad.mkdir()
        (cad / "cad0101.prg").write_text("""*PROG: Cadastro de Clientes
PROCEDURE cad0101a
DO cad0102
DO cad0103
USE CLIENTES
APPEND BLANK
REPLACE NOME WITH cNome, CPF WITH cCpf
""")
        (cad / "cad0102.prg").write_text("""*PROG: Cadastro de Contratos
PROCEDURE cad0102a
DO cad0103
USE CONTRATOS
APPEND BLANK
REPLACE NUMERO WITH cNum
""")
        (cad / "cad0103.prg").write_text("""*PROG: Consulta Clientes
PROCEDURE cad0103a
DO cad0101
SEEK cCodigo
""")

        fin = src / "fin"
        fin.mkdir()
        (fin / "fin0101.prg").write_text("""*PROG: Fluxo de Caixa
PROCEDURE flu0101a
DO fin0102
INSERT INTO FLUXO (DATA, ENTRADAS, SAIDAS) VALUES ('2024-01-01', 1000, 500)
""")
        (fin / "fin0102.prg").write_text("""*PROG: Contas a Pagar
PROCEDURE fin0102a
DO fin0101
USE PAGAM
""")
        (fin / "menu.prg").write_text("""*PROG: Menu Financeiro
@ 10,10 PROMPT "Fluxo de Caixa"
@ 11,10 PROMPT "Contas a Pagar"
MENU TO v_opcao
""")

        return str(src)

    # ------------------------------------------------------------------
    # Passo 1: Analyze Source
    # ------------------------------------------------------------------

    def test_step1_analyze_source(self):
        from dakota_gateway.synthetic.engine import SyntheticEngine
        engine = SyntheticEngine(db_connection=self.con)
        result = engine.analyze_source(str(Path(self.tmpdir.name) / "src"))
        engine.register_screens(result)
        entities, _ = engine.inferencer._parser.parse_all() if engine.inferencer._parser else ([], [])
        engine.save_entities(entities)

        self.assertGreaterEqual(len(result.screens), 1)
        self.assertGreaterEqual(len(entities), 1)

        # Verificar persistência
        screens = engine.screen_registry.list_screens()
        self.assertGreater(len(screens), 0)

    # ------------------------------------------------------------------
    # Passo 2: Infer Journeys
    # ------------------------------------------------------------------

    def test_step2_infer_journeys(self):
        # Primeiro analyze
        from dakota_gateway.synthetic.engine import SyntheticEngine
        engine = SyntheticEngine(db_connection=self.con)
        engine.analyze_source(str(Path(self.tmpdir.name) / "src"))
        engine.register_screens(engine.inferencer.analyze_source(str(Path(self.tmpdir.name) / "src")))

        # Inferir jornadas
        from dakota_gateway.synthetic.journey_inferencer import JourneyInferencer
        from dakota_gateway.synthetic.journey_builder import JourneyBuilder

        inferencer = JourneyInferencer()
        journeys = inferencer.infer_from_source(str(Path(self.tmpdir.name) / "src"))
        self.assertGreaterEqual(len(journeys), 1)

        builder = JourneyBuilder(db_connection=self.con)
        saved = 0
        for j in journeys:
            builder.save_journey(j)
            saved += 1
        self.assertGreaterEqual(saved, 1)

        # Também inferir de menu
        menu_journey = inferencer.infer_from_menus(str(Path(self.tmpdir.name) / "src" / "fin" / "menu.prg"))
        self.assertIsNotNone(menu_journey)

    # ------------------------------------------------------------------
    # Passo 3: Generate Dataset
    # ------------------------------------------------------------------

    def test_step3_generate_dataset(self):
        # Setup: analyze + register
        from dakota_gateway.synthetic.engine import SyntheticEngine
        engine = SyntheticEngine(db_connection=self.con)
        result = engine.analyze_source(str(Path(self.tmpdir.name) / "src"))
        engine.register_screens(result)

        # Gerar dataset
        from dakota_gateway.synthetic.screen_registry import ScreenRegistry
        reg = ScreenRegistry(self.con)
        screens = reg.list_screens()
        self.assertGreater(len(screens), 0)

        dataset = engine.generate_dataset_by_screen_id(screens[0].id, quantity=10, seed=42)
        self.assertIsNotNone(dataset)
        self.assertEqual(dataset.quantity, 10)

        ds_id = engine.save_dataset(dataset)
        self.assertGreater(ds_id, 0)

    # ------------------------------------------------------------------
    # Passo 4: Run Stress
    # ------------------------------------------------------------------

    def test_step4_run_stress(self):
        # Setup: analyze + journeys
        from dakota_gateway.synthetic.engine import SyntheticEngine
        engine = SyntheticEngine(db_connection=self.con)
        src_dir = str(Path(self.tmpdir.name) / "src")
        engine.analyze_source(src_dir)
        engine.register_screens(engine.inferencer.analyze_source(src_dir))

        from dakota_gateway.synthetic.journey_inferencer import JourneyInferencer
        from dakota_gateway.synthetic.journey_builder import JourneyBuilder
        inferencer = JourneyInferencer()
        journeys = inferencer.infer_from_source(src_dir)
        if not journeys:
            self.skipTest("No journeys inferred from test source")
        builder = JourneyBuilder(db_connection=self.con)
        for j in journeys:
            builder.save_journey(j)

        # Executar stress
        from dakota_gateway.synthetic.stress_runner import SyntheticStressRunner, SyntheticStressConfig
        config = SyntheticStressConfig(
            journey_id=journeys[0].journey_id,
            concurrency=2, max_sessions=5, seed=42, db_path=self.db_path,
        )
        runner = SyntheticStressRunner(db_path=self.db_path)
        result = runner.run(config)

        self.assertGreaterEqual(result.total_sessions, 1)
        self.assertGreaterEqual(result.completed, 1)

    # ------------------------------------------------------------------
    # Passo 5: Generate Report
    # ------------------------------------------------------------------

    def test_step5_generate_report(self):
        from dakota_gateway.synthetic.stress_runner import StressRunResult, StressSessionResult
        from dakota_gateway.synthetic.homologation_report import HomologationReport

        result = StressRunResult(
            total_sessions=10, completed=9, failed=1, errors=2, duration_ms=5000,
        )
        result.session_results = [
            StressSessionResult(session_index=0, status="success", duration_ms=100),
            StressSessionResult(session_index=1, status="failed", duration_ms=200,
                errors=[{"type": "validation", "severity": "medium", "message": "inválido"}]),
        ]

        report = HomologationReport(title="Pipeline E2E Test")
        html = report.generate_html(stress_result=result, journey_name="E2E Journey")
        json_data = report.generate_json(result)

        self.assertIn("Pipeline E2E Test", html)
        self.assertIn("E2E Journey", html)
        self.assertIn("Distribuição de Erros", html)
        self.assertEqual(json_data["summary"]["total_sessions"], 10)

    # ------------------------------------------------------------------
    # Passo 6: Export JUnit
    # ------------------------------------------------------------------

    def test_step6_export_junit(self):
        from dakota_gateway.synthetic.stress_runner import StressRunResult, StressSessionResult
        from dakota_gateway.synthetic.junit_exporter import JUnitExporter

        result = StressRunResult(
            total_sessions=5, completed=5, failed=0, duration_ms=2000,
        )
        result.session_results = [
            StressSessionResult(session_index=i, status="success", duration_ms=100)
            for i in range(5)
        ]

        xml_str = JUnitExporter.export(result, journey_name="CI Test", threshold_pct=80.0)
        self.assertIn('tests="5"', xml_str)
        self.assertIn('failures="0"', xml_str)
        self.assertIn("PASS", xml_str)

        # Salvar em arquivo
        output_path = str(Path(self.tmpdir.name) / "junit.xml")
        JUnitExporter.save_xml(xml_str, output_path)
        self.assertTrue(Path(output_path).exists())

    # ------------------------------------------------------------------
    # Passo 7: Full Pipeline (todos os passos em sequência)
    # ------------------------------------------------------------------

    def test_full_pipeline(self):
        """Executa o pipeline completo em sequência."""
        src_dir = str(Path(self.tmpdir.name) / "src")

        # 1. Analyze
        from dakota_gateway.synthetic.engine import SyntheticEngine
        engine = SyntheticEngine(db_connection=self.con)
        analyze_result = engine.analyze_source(src_dir)
        engine.register_screens(analyze_result)
        entities, _ = engine.inferencer._parser.parse_all() if engine.inferencer._parser else ([], [])
        engine.save_entities(entities)
        self.assertGreaterEqual(len(analyze_result.screens), 1)

        # 2. Infer journeys
        from dakota_gateway.synthetic.journey_inferencer import JourneyInferencer
        from dakota_gateway.synthetic.journey_builder import JourneyBuilder
        inferencer = JourneyInferencer()
        journeys = inferencer.infer_from_source(src_dir)
        if not journeys:
            self.skipTest("No journeys inferred from test source")
        builder = JourneyBuilder(db_connection=self.con)
        for j in journeys:
            builder.save_journey(j)
        self.assertGreaterEqual(len(journeys), 1)

        # 3. Generate dataset
        from dakota_gateway.synthetic.screen_registry import ScreenRegistry
        reg = ScreenRegistry(self.con)
        screens = reg.list_screens()
        if screens:
            dataset = engine.generate_dataset_by_screen_id(screens[0].id, quantity=5, seed=42)
            self.assertIsNotNone(dataset)

        # 4. Run stress
        from dakota_gateway.synthetic.stress_runner import SyntheticStressRunner, SyntheticStressConfig
        config = SyntheticStressConfig(
            journey_id=journeys[0].journey_id,
            concurrency=2, max_sessions=3, seed=42, db_path=self.db_path,
        )
        runner = SyntheticStressRunner(db_path=self.db_path)
        stress_result = runner.run(config)
        self.assertGreaterEqual(stress_result.completed, 1)

        # 5. Generate report
        from dakota_gateway.synthetic.homologation_report import HomologationReport
        report = HomologationReport(title="Full Pipeline")
        html = report.generate_html(stress_result=stress_result, journey_name=journeys[0].name)
        self.assertIn("Full Pipeline", html)

        # 6. Export JUnit
        from dakota_gateway.synthetic.junit_exporter import JUnitExporter
        xml_str = JUnitExporter.export(stress_result, journey_name=journeys[0].name)
        self.assertIn("testsuite", xml_str)

        # 7. Verificar detecção de erros
        from dakota_gateway.synthetic.error_detector import ErrorDetector
        detector = ErrorDetector()
        errors = detector.detect("Ocorreu um erro fatal no sistema")
        self.assertGreaterEqual(len(errors), 1)

        # 8. Session recorder
        from dakota_gateway.synthetic.session_recorder import SessionRecorder
        rec = SessionRecorder()
        session = rec.start_recording("e2e-session")
        rec.record_event(screen_text="MENU", screen_sig="L=5", input_text="1")
        recorded = rec.stop_recording()
        self.assertIsNotNone(recorded)

        # 9. Screen explorer
        from dakota_gateway.synthetic.screen_explorer import ScreenExplorer
        explorer = ScreenExplorer()
        exp_result = explorer.explore_from_source(src_dir)
        self.assertGreaterEqual(exp_result.total_screens, 0)


if __name__ == "__main__":
    unittest.main()

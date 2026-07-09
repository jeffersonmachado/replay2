#!/usr/bin/env python3
"""Testes para: ReplayAdapter, HomologationReport, MacroJourneyRunner, ScreenDiffer, CLI progress."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GATEWAY_DIR = ROOT / "gateway"
sys.path.insert(0, str(GATEWAY_DIR))

from dakota_gateway.synthetic.screen_differ import ScreenDiffer, ScreenDiff
from dakota_gateway.synthetic.macro_journey import (
    MacroJourneyRunner,
    MacroJourneyDefinition,
    MacroJourneyStep,
)
from dakota_gateway.synthetic.homologation_report import HomologationReport
from dakota_gateway.synthetic.replay_adapter import ReplayAdapter, ReplayAdapterConfig
from dakota_gateway.synthetic.journey import JourneyDefinition, JourneyStep
from dakota_gateway.state_db import connect, init_db


class ScreenDifferTests(unittest.TestCase):

    def test_diff_identical_screens(self):
        expected = "+-- MENU --+\n| 1. Cadastro |\n| 2. Financeiro |"
        observed = "+-- MENU --+\n| 1. Cadastro |\n| 2. Financeiro |"

        diff = ScreenDiffer.diff(expected, observed)
        self.assertEqual(diff.similarity, 1.0)
        self.assertEqual(diff.added_lines, 0)
        self.assertEqual(diff.removed_lines, 0)

    def test_diff_different_screens(self):
        expected = "+-- MENU --+\n| 1. Cadastro |"
        observed = "+-- ERRO --+\n| Registro nao encontrado |"

        diff = ScreenDiffer.diff(expected, observed)
        self.assertLess(diff.similarity, 1.0)
        self.assertGreater(diff.added_lines + diff.removed_lines, 0)

    def test_diff_ansi_screens(self):
        expected = "\x1b[2J\x1b[H+-- MENU --+\n| Opcao: _ |"
        observed = "\x1b[2J\x1b[H+-- ERRO --+\n| Falha |"

        diff = ScreenDiffer.diff(expected, observed)
        # ANSI deve ser removido antes do diff
        self.assertNotIn("\x1b", str(diff.lines))

    def test_diff_to_json(self):
        diff = ScreenDiffer.diff("A\nB", "A\nC")
        result = ScreenDiffer.to_json(diff)
        self.assertIn("similarity", result)
        self.assertIn("lines", result)
        self.assertGreater(len(result["lines"]), 0)

    def test_diff_to_html(self):
        diff = ScreenDiffer.diff("A", "B")
        html = ScreenDiffer.to_html(diff)
        self.assertIn("Similaridade", html)
        self.assertIn("font-family:monospace", html)


class MacroJourneyTests(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tmpdir.name) / "test.db")
        self.con = connect(self.db_path)
        init_db(self.con)

    def tearDown(self):
        self.con.close()
        self.tmpdir.cleanup()

    def _setup_journey(self, journey_id: str, name: str) -> None:
        from dakota_gateway.synthetic.journey_builder import JourneyBuilder
        builder = JourneyBuilder(db_connection=self.con)
        journey = JourneyDefinition(
            journey_id=journey_id, name=name, category="test",
            steps=[JourneyStep(step_order=0, screen_id="menu", action="navigate")],
        )
        builder.save_journey(journey)

    def test_macro_topological_sort(self):
        steps = [
            MacroJourneyStep(module_name="fin", journey_id="fin", order=2, depends_on=["cad"]),
            MacroJourneyStep(module_name="cad", journey_id="cad", order=0),
            MacroJourneyStep(module_name="cop", journey_id="cop", order=1, depends_on=["cad"]),
        ]
        sorted_steps = MacroJourneyRunner._topological_sort(steps)
        # cad deve vir antes de fin e cop
        cad_pos = next(i for i, s in enumerate(sorted_steps) if s.module_name == "cad")
        fin_pos = next(i for i, s in enumerate(sorted_steps) if s.module_name == "fin")
        cop_pos = next(i for i, s in enumerate(sorted_steps) if s.module_name == "cop")
        self.assertLess(cad_pos, fin_pos)
        self.assertLess(cad_pos, cop_pos)

    def test_macro_run_sequential(self):
        self._setup_journey("j1", "J1")
        self._setup_journey("j2", "J2")

        runner = MacroJourneyRunner(db_path=self.db_path)
        result = runner.run_sequential(
            journey_ids=["j1", "j2"],
            session_count=3,
            concurrency=1,
        )

        self.assertGreater(result.total_sessions, 0)
        self.assertIn("j1", result.module_results)
        self.assertIn("j2", result.module_results)
        self.assertGreater(len(result.report_html), 100)


class HomologationReportTests(unittest.TestCase):

    def test_generate_html_empty(self):
        report = HomologationReport(title="Test Report")
        html = report.generate_html(journey_name="Test Journey")
        self.assertIn("Test Report", html)
        self.assertIn("Test Journey", html)
        self.assertIn("Resumo", html)
        self.assertIn("</html>", html)

    def test_generate_json(self):
        from dakota_gateway.synthetic.stress_runner import StressRunResult, StressSessionResult

        result = StressRunResult(
            total_sessions=10, completed=8, failed=2, errors=3,
            duration_ms=5000,
        )
        result.session_results = [
            StressSessionResult(session_index=0, status="success", duration_ms=100),
            StressSessionResult(session_index=1, status="failed", duration_ms=200,
                errors=[{"type": "validation", "severity": "medium"}]),
        ]

        report = HomologationReport(title="Test")
        json_data = report.generate_json(result)
        self.assertEqual(json_data["summary"]["total_sessions"], 10)
        self.assertEqual(json_data["summary"]["completed"], 8)
        self.assertEqual(len(json_data["sessions"]), 2)

    def test_generate_html_with_errors(self):
        from dakota_gateway.synthetic.stress_runner import StressRunResult, StressSessionResult

        result = StressRunResult(
            total_sessions=5, completed=3, failed=2, errors=4,
            duration_ms=3000,
        )
        result.session_results = [
            StressSessionResult(session_index=0, status="success", duration_ms=100),
            StressSessionResult(session_index=1, status="failed", duration_ms=200,
                errors=[{"type": "fatal", "severity": "critical"}]),
            StressSessionResult(session_index=2, status="failed", duration_ms=150,
                errors=[{"type": "not_found", "severity": "low"}, {"type": "validation", "severity": "medium"}]),
        ]

        report = HomologationReport(title="Error Test")
        html = report.generate_html(stress_result=result, journey_name="Error Journey")
        self.assertIn("Distribuição de Erros", html)
        self.assertIn("fatal", html.lower())
        self.assertIn("critical", html.lower())


class ReplayAdapterTests(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tmpdir.name) / "test.db")
        self.con = connect(self.db_path)
        init_db(self.con)

    def tearDown(self):
        self.con.close()
        self.tmpdir.cleanup()

    def _setup_journey(self):
        from dakota_gateway.synthetic.journey_builder import JourneyBuilder
        builder = JourneyBuilder(db_connection=self.con)
        journey = JourneyDefinition(
            journey_id="adapter_test",
            name="Adapter Test",
            category="test",
            steps=[
                JourneyStep(step_order=0, screen_id="menu", screen_title="Menu",
                           action="navigate", trigger="1"),
                JourneyStep(step_order=1, screen_id="cadastro", screen_title="Cadastro",
                           action="input", input_template="{{cliente.nome}}\n{{cliente.cpf}}"),
                JourneyStep(step_order=2, screen_id="cadastro", action="submit", trigger="F10"),
            ],
        )
        builder.save_journey(journey)
        return journey

    def test_generate_synthetic_inputs(self):
        journey = self._setup_journey()
        from dakota_gateway.synthetic.journey_builder import JourneyBuilder
        # Usar JourneyBuilder sem db_connection (não precisa de screen_registry)
        builder = JourneyBuilder()
        jds = builder.build_journey_dataset(journey, session_count=3, seed=42)

        adapter = ReplayAdapter()
        inputs = adapter.generate_synthetic_inputs(journey, jds, session_index=0)

        self.assertGreater(len(inputs), 0)

    def test_generate_synthetic_jsonl(self):
        journey = self._setup_journey()
        output_dir = str(Path(self.tmpdir.name) / "jsonl_output")

        adapter = ReplayAdapter()
        files = adapter.generate_synthetic_jsonl(journey, session_count=2, seed=42, output_dir=output_dir)

        self.assertEqual(len(files), 2)
        for session_id, path in files.items():
            self.assertTrue(Path(path).exists())
            # Verificar que é JSONL válido
            with open(path) as f:
                for line in f:
                    self.assertTrue(line.strip().startswith("{"))

    def test_run_via_adapter_minimal(self):
        journey = self._setup_journey()
        config = ReplayAdapterConfig(
            journey_id="adapter_test",
            concurrency=2,
            max_sessions=3,
            seed=42,
            db_path=self.db_path,
            verify_screens=True,
        )

        adapter = ReplayAdapter()
        result = adapter.run_via_runner(config)

        self.assertEqual(result.total_sessions, 3)
        self.assertGreater(len(result.session_results), 0)


if __name__ == "__main__":
    unittest.main()

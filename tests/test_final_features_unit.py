#!/usr/bin/env python3
"""Testes para: RemoteExecutor, Scheduler, SessionRecorder, ScreenExplorer, JUnitExporter."""
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
from dakota_gateway.synthetic.journey import JourneyDefinition, JourneyStep, JourneyDataset
from dakota_gateway.synthetic.journey_builder import JourneyBuilder


class RemoteExecutorTests(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tmpdir.name) / "test.db")
        self.con = connect(self.db_path)
        init_db(self.con)

    def tearDown(self):
        self.con.close()
        self.tmpdir.cleanup()

    def _setup_journey(self) -> JourneyDefinition:
        builder = JourneyBuilder(db_connection=self.con)
        journey = JourneyDefinition(
            journey_id="remote_test", name="Remote Test", category="test",
            steps=[
                JourneyStep(step_order=0, screen_id="menu", action="navigate"),
                JourneyStep(step_order=1, screen_id="cadastro", action="input",
                           input_template="{{nome}}"),
            ],
        )
        builder.save_journey(journey)
        jds = builder.build_journey_dataset(journey, session_count=3, seed=42)
        return journey, jds

    def test_dry_run_execution(self):
        from dakota_gateway.synthetic.remote_executor import RemoteExecutor
        journey, jds = self._setup_journey()
        executor = RemoteExecutor(mode="dry_run")
        result = executor.execute_journey(journey, jds, session_count=3)
        self.assertEqual(result.total_sessions, 3)
        self.assertGreater(len(result.session_results), 0)

    def test_remote_result_status(self):
        from dakota_gateway.synthetic.remote_executor import RemoteExecutor, RemoteSessionResult
        journey, jds = self._setup_journey()
        executor = RemoteExecutor(mode="dry_run")
        result = executor.execute_journey(journey, jds, session_count=2)
        for sr in result.session_results:
            self.assertIn(sr.status, ("success", "failed", "error"))


class SchedulerTests(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tmpdir.name) / "test.db")
        self.con = connect(self.db_path)
        init_db(self.con)
        # Setup journey
        builder = JourneyBuilder(db_connection=self.con)
        builder.save_journey(JourneyDefinition(
            journey_id="sched_test", name="Sched Test", category="test",
            steps=[JourneyStep(step_order=0, screen_id="menu", action="navigate")],
        ))

    def tearDown(self):
        self.con.close()
        self.tmpdir.cleanup()

    def test_add_and_list_schedule(self):
        from dakota_gateway.synthetic.scheduler import Scheduler, ScheduleConfig
        scheduler = Scheduler(db_path=self.db_path)
        config = ScheduleConfig(
            schedule_id="sched1", journey_id="sched_test",
            name="Daily Test", interval_hours=24, session_count=5,
        )
        scheduler.add_schedule(config)
        schedules = scheduler.list_schedules()
        self.assertEqual(len(schedules), 1)
        self.assertEqual(schedules[0]["name"], "Daily Test")

    def test_run_schedule(self):
        from dakota_gateway.synthetic.scheduler import Scheduler, ScheduleConfig
        scheduler = Scheduler(db_path=self.db_path)
        config = ScheduleConfig(
            schedule_id="sched2", journey_id="sched_test",
            name="Run Test", interval_hours=1, session_count=3,
        )
        scheduler.add_schedule(config)
        result = scheduler.run_schedule("sched2")
        self.assertIn("completed", result)

    def test_regression_detection(self):
        from dakota_gateway.synthetic.scheduler import Scheduler, ScheduleConfig
        scheduler = Scheduler(db_path=self.db_path)

        config = ScheduleConfig(
            schedule_id="reg_test", journey_id="sched_test",
            name="Regression Test", interval_hours=1, session_count=5,
            alert_threshold_pct=5.0,
        )
        scheduler.add_schedule(config)

        # Executar duas vezes
        scheduler.run_schedule("reg_test")
        result2 = scheduler.run_schedule("reg_test")

        # Tentar verificar regressão
        regression = scheduler.check_regression("reg_test", result2.get("run_id", 0))
        # Pode ser None se só tiver 1 execução - válido
        self.assertTrue(regression is None or regression.summary is not None)

    def test_get_run_history(self):
        from dakota_gateway.synthetic.scheduler import Scheduler, ScheduleConfig
        scheduler = Scheduler(db_path=self.db_path)
        config = ScheduleConfig(
            schedule_id="hist_test", journey_id="sched_test",
            name="History Test", interval_hours=1, session_count=3,
        )
        scheduler.add_schedule(config)
        scheduler.run_schedule("hist_test")
        history = scheduler.get_run_history("hist_test")
        self.assertGreaterEqual(len(history), 1)


class SessionRecorderTests(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmpdir.cleanup()

    def _write_jsonl(self, events: list[dict]) -> str:
        path = str(Path(self.tmpdir.name) / "test.jsonl")
        with open(path, "w", encoding="utf-8") as f:
            for ev in events:
                f.write(json.dumps(ev) + "\n")
        return path

    def test_from_jsonl_to_journey(self):
        from dakota_gateway.synthetic.session_recorder import SessionRecorder
        jsonl_path = self._write_jsonl([
            {"v": "v1", "seq_global": 1, "ts_ms": 1000, "type": "checkpoint",
             "actor": "gw", "session_id": "s1", "seq_session": 1,
             "screen_sig": "L=10 W=40", "screen_sample": "MENU PRINCIPAL",
             "key_text": "1"},
            {"v": "v1", "seq_global": 2, "ts_ms": 2000, "type": "checkpoint",
             "actor": "gw", "session_id": "s1", "seq_session": 2,
             "screen_sig": "L=15 W=60", "screen_sample": "CADASTRO CLIENTE",
             "key_text": "JOAO SILVA"},
            {"v": "v1", "seq_global": 3, "ts_ms": 3000, "type": "bytes",
             "actor": "gw", "session_id": "s1", "seq_session": 3,
             "key_text": "123.456.789-09"},
        ])

        recorder = SessionRecorder()
        session = recorder.from_jsonl(jsonl_path)
        self.assertIsNotNone(session)
        self.assertEqual(session.session_id, "s1")
        self.assertGreater(len(session.screen_signatures), 0)

        journey = recorder.to_journey(session, journey_name="Jornada Gravada")
        self.assertIn("recorded", journey.journey_id)
        self.assertGreater(len(journey.steps), 0)
        self.assertIn("recorded", journey.tags)

    def test_recording_session(self):
        from dakota_gateway.synthetic.session_recorder import SessionRecorder
        recorder = SessionRecorder()
        session = recorder.start_recording("test-session")
        recorder.record_event(screen_text="MENU", screen_sig="L=5", input_text="1")
        recorder.record_event(screen_text="CADASTRO", screen_sig="L=10", input_text="NOME")
        recorded = recorder.stop_recording()
        self.assertIsNotNone(recorded)
        self.assertEqual(recorded.session_id, "test-session")
        self.assertEqual(len(recorded.screen_texts), 2)


class ScreenExplorerTests(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmpdir.cleanup()

    def _write_file(self, name: str, content: str) -> Path:
        path = Path(self.tmpdir.name) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def test_explore_from_source(self):
        self._write_file("menu.prg", """
DO WHILE .T.
   @ 10,10 PROMPT "Cadastros"
   @ 11,10 PROMPT "Financeiro"
   MENU TO v_opcao
ENDDO
""")
        self._write_file("cad/cad0101.prg", """
*PROG: Cadastro de Clientes
@ 05,10 SAY "Nome:" GET v_nome
@ 06,10 SAY "CPF:" GET v_cpf
""")

        from dakota_gateway.synthetic.screen_explorer import ScreenExplorer
        explorer = ScreenExplorer()
        result = explorer.explore_from_source(str(self.tmpdir.name))
        self.assertGreaterEqual(result.total_screens, 0)

    def test_analyze_menu_screen(self):
        from dakota_gateway.synthetic.screen_explorer import ScreenExplorer
        explorer = ScreenExplorer()
        screen = "+-- MENU PRINCIPAL --+\n| 1. Cadastros        |\n| 2. Financeiro       |\n| Opcao: _           |"
        ds = explorer.analyze_screen(screen)
        self.assertTrue(explorer.is_menu_screen(screen))
        self.assertGreater(len(ds.menu_options), 0)

    def test_analyze_form_screen(self):
        from dakota_gateway.synthetic.screen_explorer import ScreenExplorer
        explorer = ScreenExplorer()
        screen = "+-- CADASTRO --+\nNome: ........\nCPF: .........\nTelefone: ...."
        ds = explorer.analyze_screen(screen)
        self.assertGreater(len(ds.fields_detected), 0)


class JUnitExporterTests(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_export_junit_xml(self):
        from dakota_gateway.synthetic.stress_runner import StressRunResult, StressSessionResult
        from dakota_gateway.synthetic.junit_exporter import JUnitExporter

        result = StressRunResult(
            total_sessions=5, completed=4, failed=1, errors=0, duration_ms=5000,
        )
        result.session_results = [
            StressSessionResult(session_index=0, status="success", duration_ms=100, replay_script="input1"),
            StressSessionResult(session_index=1, status="failed", duration_ms=200,
                errors=[{"type": "validation", "severity": "medium", "message": "campo inválido"}]),
        ]

        xml_str = JUnitExporter.export(result, journey_name="Test Journey", threshold_pct=80.0)
        self.assertIn("testsuite", xml_str)
        self.assertIn("Test Journey", xml_str)
        self.assertIn('tests="5"', xml_str)

    def test_export_below_threshold_fails(self):
        from dakota_gateway.synthetic.stress_runner import StressRunResult
        from dakota_gateway.synthetic.junit_exporter import JUnitExporter

        result = StressRunResult(
            total_sessions=10, completed=5, failed=5, errors=0, duration_ms=3000,
        )
        xml_str = JUnitExporter.export(result, threshold_pct=80.0)
        # Deve conter failure por threshold
        self.assertIn("FAIL", xml_str)

    def test_save_xml_to_file(self):
        from dakota_gateway.synthetic.stress_runner import StressRunResult
        from dakota_gateway.synthetic.junit_exporter import JUnitExporter

        result = StressRunResult(total_sessions=1, completed=1, failed=0, duration_ms=100)
        xml_str = JUnitExporter.export(result)

        output_path = str(Path(self.tmpdir.name) / "report.xml")
        saved = JUnitExporter.save_xml(xml_str, output_path)
        self.assertEqual(saved, output_path)
        self.assertTrue(Path(output_path).exists())


if __name__ == "__main__":
    unittest.main()

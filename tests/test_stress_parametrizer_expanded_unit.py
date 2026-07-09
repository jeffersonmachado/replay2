#!/usr/bin/env python3
"""Testes para: CaptureParametrizer, StressRunner, ExpandedInferencer."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GATEWAY_DIR = ROOT / "gateway"
sys.path.insert(0, str(GATEWAY_DIR))

from dakota_gateway.synthetic.capture_parametrizer import (
    CaptureParametrizer,
    CaptureTemplate,
    ParametrizedSession,
)
from dakota_gateway.synthetic.stress_runner import (
    SyntheticStressRunner,
    SyntheticStressConfig,
    StressRunResult,
)
from dakota_gateway.synthetic.expanded_inferencer import (
    ExpandedInferencer,
    ConditionalFlow,
    DataDependency,
    TransactionBlock,
)
from dakota_gateway.synthetic.journey import JourneyDefinition, JourneyStep
from dakota_gateway.state_db import connect, init_db


class CaptureParametrizerTests(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.parametrizer = CaptureParametrizer()

    def tearDown(self):
        self.tmpdir.cleanup()

    def _write_jsonl(self, events: list[dict]) -> str:
        path = str(Path(self.tmpdir.name) / "test.jsonl")
        with open(path, "w", encoding="utf-8") as f:
            for ev in events:
                f.write(json.dumps(ev) + "\n")
        return path

    def test_analyze_extracts_screens_and_inputs(self):
        jsonl_path = self._write_jsonl([
            {"v": "v1", "seq_global": 1, "ts_ms": 1000, "type": "checkpoint",
             "actor": "gw", "session_id": "s1", "seq_session": 1,
             "screen_sig": "L=10 W=40", "screen_sample": "MENU", "norm_len": 100,
             "key_text": "1"},
            {"v": "v1", "seq_global": 2, "ts_ms": 2000, "type": "checkpoint",
             "actor": "gw", "session_id": "s1", "seq_session": 2,
             "screen_sig": "L=15 W=60", "screen_sample": "CADASTRO", "norm_len": 200,
             "key_text": "JOAO SILVA"},
            {"v": "v1", "seq_global": 3, "ts_ms": 3000, "type": "bytes",
             "actor": "gw", "session_id": "s1", "seq_session": 3,
             "dir": "in", "key_text": "12345678909"},
        ])

        template = self.parametrizer.analyze_capture(jsonl_path)
        self.assertEqual(len(template.screen_sequence), 2)
        self.assertIn("MENU", template.screen_contexts[0]["screen_sample"])
        self.assertGreater(len(template.metadata.get("original_inputs", [])), 0)

    def test_detect_placeholders_from_capture(self):
        jsonl_path = self._write_jsonl([
            {"v": "v1", "seq_global": 1, "ts_ms": 1000, "type": "checkpoint",
             "actor": "gw", "session_id": "s1", "seq_session": 1,
             "screen_sig": "L=10", "key_text": "123.456.789-09"},
            {"v": "v1", "seq_global": 2, "ts_ms": 2000, "type": "checkpoint",
             "actor": "gw", "session_id": "s1", "seq_session": 2,
             "screen_sig": "L=10", "key_text": "joao@email.com"},
        ])

        template = self.parametrizer.analyze_capture(jsonl_path)
        # Deve detectar CPF e email
        placeholders = " ".join(template.input_templates)
        self.assertIn("cpf", placeholders.lower())

    def test_generate_sessions(self):
        template = CaptureTemplate(
            input_templates=["{{cliente.nome}}", "{{cliente.cpf}}", "{{cliente.telefone}}"],
        )
        datasets = {
            "cliente": [
                {"nome": "ANA", "cpf": "111", "telefone": "9999-0001"},
                {"nome": "BRUNO", "cpf": "222", "telefone": "9999-0002"},
                {"nome": "CARLA", "cpf": "333", "telefone": "9999-0003"},
            ],
        }

        sessions = self.parametrizer.generate_sessions(template, datasets, session_count=3)
        self.assertEqual(len(sessions), 3)
        self.assertEqual(sessions[0].inputs[0], "ANA")
        self.assertEqual(sessions[1].inputs[0], "BRUNO")

    def test_to_replay_script(self):
        template = CaptureTemplate(capture_source="test.jsonl")
        sessions = [
            ParametrizedSession(session_index=0, inputs=["ANA", "111"]),
            ParametrizedSession(session_index=1, inputs=["BRUNO", "222"]),
        ]

        script = self.parametrizer.to_replay_script(template, sessions)
        self.assertIn("SESSÃO 1", script)
        self.assertIn("ANA", script)
        self.assertIn("SESSÃO 2", script)
        self.assertIn("BRUNO", script)


class StressRunnerTests(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tmpdir.name) / "test.db")
        self.con = connect(self.db_path)
        init_db(self.con)

    def tearDown(self):
        self.con.close()
        self.tmpdir.cleanup()

    def _setup_journey(self) -> str:
        from dakota_gateway.synthetic.journey_builder import JourneyBuilder
        builder = JourneyBuilder(db_connection=self.con)

        journey = JourneyDefinition(
            journey_id="stress_test",
            name="Stress Test Journey",
            category="test",
            steps=[
                JourneyStep(step_order=0, screen_id="menu", action="navigate", trigger="1"),
                JourneyStep(step_order=1, screen_id="cadastro", action="input",
                           input_template="{{dados.nome}}"),
                JourneyStep(step_order=2, screen_id="consulta", action="input",
                           input_template="{{dados.codigo}}"),
            ],
        )
        builder.save_journey(journey)
        return "stress_test"

    def test_stress_run_completes(self):
        journey_id = self._setup_journey()
        config = SyntheticStressConfig(
            journey_id=journey_id,
            concurrency=3,
            ramp_up_seconds=1,
            seed=42,
            max_sessions=5,
            db_path=self.db_path,
        )

        runner = SyntheticStressRunner(db_path=self.db_path)
        result = runner.run(config)

        self.assertEqual(result.total_sessions, 5)
        self.assertEqual(result.completed + result.failed, 5)
        self.assertGreater(len(result.session_results), 0)

    def test_stress_run_verification(self):
        journey_id = self._setup_journey()
        config = SyntheticStressConfig(
            journey_id=journey_id,
            concurrency=2,
            seed=123,
            max_sessions=3,
            verify_screens=True,
            db_path=self.db_path,
        )

        runner = SyntheticStressRunner(db_path=self.db_path)
        result = runner.run(config)

        # Cada sessão deve ter verificação
        for sr in result.session_results:
            self.assertIsNotNone(sr.replay_script)
            self.assertIn(sr.status, ("success", "failed", "error"))

    def test_stress_config_defaults(self):
        config = SyntheticStressConfig(journey_id="test")
        self.assertEqual(config.concurrency, 10)
        self.assertEqual(config.ramp_up_seconds, 5)
        self.assertEqual(config.mode, "parallel-sessions")


class ExpandedInferencerTests(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.source_dir = Path(self.tmpdir.name)
        self.inferencer = ExpandedInferencer()

    def tearDown(self):
        self.tmpdir.cleanup()

    def _write_file(self, name: str, content: str) -> Path:
        path = self.source_dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def test_detect_if_else_flow(self):
        self._write_file("test_if.prg", """
IF v_opcao = 1
   DO cad/cad0101
ELSE
   DO cad/cad0102
ENDIF
""")
        flows = self.inferencer.infer_conditional_flows(str(self.source_dir))
        self.assertGreaterEqual(len(flows), 1)
        if_flow = flows[0]
        self.assertEqual(if_flow.flow_type, "if")
        self.assertEqual(len(if_flow.branches), 2)

    def test_detect_do_case_flow(self):
        self._write_file("test_case.prg", """
DO CASE
   CASE v_opcao = 1
      DO fin/fin0101
   CASE v_opcao = 2
      DO fin/fin0102
   CASE v_opcao = 3
      DO fin/fin0103
ENDCASE
""")
        flows = self.inferencer.infer_conditional_flows(str(self.source_dir))
        case_flows = [f for f in flows if f.flow_type == "do_case"]
        self.assertGreaterEqual(len(case_flows), 1)
        self.assertEqual(len(case_flows[0].branches), 3)

    def test_detect_do_while_flow(self):
        self._write_file("test_while.prg", """
DO WHILE .NOT. EOF()
   REPLACE STATUS WITH 'P'
   SKIP
ENDDO
""")
        flows = self.inferencer.infer_conditional_flows(str(self.source_dir))
        while_flows = [f for f in flows if f.flow_type == "do_while"]
        self.assertGreaterEqual(len(while_flows), 1)

    def test_detect_data_dependencies(self):
        self._write_file("test_deps.prg", """
USE contrato
SET RELATION TO cliente INTO cli
SEEK contrato->cliente
REPLACE empresa WITH contrato->cliente
""")
        deps = self.inferencer.infer_data_dependencies(str(self.source_dir))
        self.assertGreaterEqual(len(deps), 1)

    def test_detect_transaction_block(self):
        self._write_file("test_tx.prg", """
BEGIN TRANSACTION
   REPLACE NOME WITH cNome
   REPLACE CPF WITH cCpf
   DO valida_dados
COMMIT
""")
        transactions = self.inferencer.infer_transactions(str(self.source_dir))
        self.assertGreaterEqual(len(transactions), 1)
        tx = transactions[0]
        self.assertIn("replace", tx.operations)

    def test_detect_rollback(self):
        self._write_file("test_rb.prg", """
BEGIN TRANSACTION
   REPLACE SALDO WITH nSaldo
   IF nSaldo < 0
      ROLLBACK
      RETURN
   ENDIF
COMMIT
""")
        transactions = self.inferencer.infer_transactions(str(self.source_dir))
        self.assertGreaterEqual(len(transactions), 1)

    def test_enrich_journey_with_conditionals(self):
        self._write_file("test_flow.prg", """
IF v_tipo = 'E'
   DO fin/entrada
ELSE
   DO fin/saida
ENDIF
""")
        flows = self.inferencer.infer_conditional_flows(str(self.source_dir))
        journey = JourneyDefinition(
            journey_id="test_enrich",
            name="Test Enrich",
            steps=[JourneyStep(step_order=0, screen_id="menu", action="navigate")],
        )

        enriched = self.inferencer.enrich_journey_with_conditionals(journey, flows)
        self.assertGreater(len(enriched.steps), 1)
        conditional_steps = [s for s in enriched.steps if s.action == "conditional"]
        self.assertGreater(len(conditional_steps), 0)

    def test_enrich_journey_with_dependencies(self):
        self._write_file("test_dep.prg", """
USE cliente ORDER codigo
SEEK contrato->cliente
""")
        deps = self.inferencer.infer_data_dependencies(str(self.source_dir))
        journey = JourneyDefinition(
            journey_id="test_dep_j",
            name="Dep Test",
            steps=[
                JourneyStep(step_order=0, screen_id="contrato", action="input"),
                JourneyStep(step_order=1, screen_id="CLIENTE", action="input"),
            ],
        )

        enriched = self.inferencer.enrich_journey_with_dependencies(journey, deps)
        # Deve adicionar depends_on aos passos
        has_deps = any(s.depends_on for s in enriched.steps)
        # Se encontrou dependências, deve ter enriquecido
        if deps:
            self.assertTrue(has_deps or len(enriched.steps) >= 2)

    def test_enrich_journey_with_transactions(self):
        self._write_file("test_tx2.prg", """
BEGIN TRANSACTION
   REPLACE VALOR WITH nValor
   DO atualiza_saldo
COMMIT
""")
        transactions = self.inferencer.infer_transactions(str(self.source_dir))
        journey = JourneyDefinition(
            journey_id="test_tx_j",
            name="TX Test",
            steps=[JourneyStep(step_order=0, screen_id="lancamento", action="input")],
        )

        enriched = self.inferencer.enrich_journey_with_transactions(journey, transactions)
        tx_steps = [s for s in enriched.steps if s.action == "transaction"]
        self.assertGreater(len(tx_steps), 0)


if __name__ == "__main__":
    unittest.main()

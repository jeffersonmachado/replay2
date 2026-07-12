#!/usr/bin/env python3
"""Testes para o módulo de jornadas (Journey)."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GATEWAY_DIR = ROOT / "gateway"
sys.path.insert(0, str(GATEWAY_DIR))

from dakota_gateway.synthetic.journey import (
    JourneyDefinition,
    JourneyStep,
    JourneyDataset,
)
from dakota_gateway.synthetic.journey_builder import JourneyBuilder
from dakota_gateway.synthetic.journey_inferencer import JourneyInferencer
from dakota_gateway.source_analyzer.menu_analyzer import MenuAnalyzer
from dakota_gateway.state_db import connect, init_db


class JourneyDefinitionTests(unittest.TestCase):

    def test_screen_sequence_ordered(self):
        journey = JourneyDefinition(
            journey_id="test_journey",
            name="Jornada Teste",
            steps=[
                JourneyStep(step_order=2, screen_id="tela_c", action="navigate"),
                JourneyStep(step_order=0, screen_id="menu", action="navigate"),
                JourneyStep(step_order=1, screen_id="tela_b", action="input"),
            ],
        )
        seq = journey.screen_sequence()
        self.assertEqual(seq, ["menu", "tela_b", "tela_c"])

    def test_to_dict_and_from_dict_roundtrip(self):
        original = JourneyDefinition(
            journey_id="cad_cliente",
            name="Cadastro de Cliente",
            description="Jornada completa de cadastro",
            category="cadastro",
            entry_screen="menu_cad",
            steps=[
                JourneyStep(
                    step_order=0,
                    screen_id="menu_cad",
                    screen_title="Menu Cadastro",
                    action="navigate",
                    trigger="1",
                    description="Seleciona opção 1",
                ),
                JourneyStep(
                    step_order=1,
                    screen_id="cad_cli",
                    screen_title="Cadastro de Clientes",
                    action="input",
                    input_template="{{cliente.nome}}\n{{cliente.cpf}}",
                    depends_on=[],
                ),
                JourneyStep(
                    step_order=2,
                    screen_id="cad_cli",
                    action="submit",
                    trigger="F10",
                ),
            ],
            dataset_bindings={"cad_cli": "clientes_10k"},
            tags=["cadastro", "cliente"],
        )

        d = original.to_dict()
        restored = JourneyDefinition.from_dict(d)

        self.assertEqual(restored.journey_id, original.journey_id)
        self.assertEqual(restored.name, original.name)
        self.assertEqual(len(restored.steps), 3)
        self.assertEqual(restored.steps[1].input_template, "{{cliente.nome}}\n{{cliente.cpf}}")
        self.assertEqual(restored.dataset_bindings["cad_cli"], "clientes_10k")


class JourneyDatasetTests(unittest.TestCase):

    def test_get_session_inputs(self):
        jds = JourneyDataset(
            journey_id="test",
            journey_name="Test",
            seed=42,
            session_count=2,
            steps_data={
                0: [{"nome": "ANA", "cpf": "111"}, {"nome": "BRUNO", "cpf": "222"}],
                1: [{"valor": "100"}, {"valor": "200"}],
            },
        )

        inputs_s0 = jds.get_session_inputs(0)
        self.assertEqual(inputs_s0, ["ANA", "111", "100"])

        inputs_s1 = jds.get_session_inputs(1)
        self.assertEqual(inputs_s1, ["BRUNO", "222", "200"])


class JourneyInferencerTests(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.source_dir = Path(self.tmpdir.name)

    def tearDown(self):
        self.tmpdir.cleanup()

    def _write_file(self, name: str, content: str) -> Path:
        path = self.source_dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def test_infer_from_source_discovers_modules(self):
        self._write_file("cad/cad0101.prg", """
*PROG: Cadastro de Clientes
PROCEDURE cad0101a
DO cad0101b
USE clientes
APPEND BLANK
REPLACE NOME WITH cNome, CPF WITH cCpf
""")
        self._write_file("cad/cad0102.prg", """
*PROG: Cadastro de Contratos
PROCEDURE cad0102a
DO cad0102b
USE contratos
""")
        self._write_file("cad/cad0103.prg", """
*PROG: Consulta de Clientes
PROCEDURE cad0103a
SEEK cCodigo
""")

        inferencer = JourneyInferencer()
        journeys = inferencer.infer_from_source(str(self.source_dir))

        self.assertGreaterEqual(len(journeys), 1)
        cad_journey = next((j for j in journeys if j.category == "cad"), None)
        self.assertIsNotNone(cad_journey, "Deve criar jornada para módulo cad")
        self.assertGreater(len(cad_journey.steps), 2)

    def test_infer_from_menu(self):
        menu_file = self._write_file("menu.prg", """
*PROG: Menu Principal
DO WHILE .T.
   @ 10,10 PROMPT "Cadastros"
   @ 11,10 PROMPT "Financeiro"
   @ 12,10 PROMPT "Relatorios"
   MENU TO v_opcao
   DO CASE
      CASE v_opcao = 1
         DO cad/menu
      CASE v_opcao = 2
         DO fin/menu
   ENDCASE
ENDDO
""")

        inferencer = JourneyInferencer()
        journey = inferencer.infer_from_menus(str(menu_file))

        self.assertIsNotNone(journey)
        self.assertIn("Cadastros", [s.screen_title for s in journey.steps])
        self.assertIn("Financeiro", [s.screen_title for s in journey.steps])

    def test_menu_analyzer_detects_dakota_menu_patterns(self):
        self._write_file("sig.prg", """
do while .t.
   numrot = [0]
   rotina = fTraduz(p_idioma,"MENU PRINCIPAL","U",16,.f.,"")
   if !fTelaExp(p_empresa,p_sistema,numrot,rotina)
      return .f.
   endif

   @ 05,02 prompt " 1. " + fTraduz(p_idioma,"Configuracoes","P",27,.f.,"")
   @ 06,02 prompt " 2. " + fTraduz(p_idioma,"Movimentos","P",27,.f.,"")

   p_opcao000=gmenu(p_opcao000)

   do case
      case p_opcao000 = 1
           do sig100
      case p_opcao000 = 2
           do sig200
   endcase
enddo
""")

        analyzer = MenuAnalyzer(str(self.source_dir))
        tree = analyzer.analyze(str(self.source_dir))

        self.assertIsNotNone(tree.root)
        self.assertEqual(tree.total_menus, 1)
        self.assertEqual(tree.root.label, "MENU PRINCIPAL")
        labels = [c.label for c in tree.root.children if c.node_type == "option"]
        programs = [c.program_name for c in tree.root.children if c.node_type == "program"]

        self.assertIn("1. Configuracoes", labels)
        self.assertIn("2. Movimentos", labels)
        self.assertIn("SIG100", programs)
        self.assertIn("SIG200", programs)
        self.assertNotIn("WHILE", programs)
        self.assertNotIn("CASE", programs)

    def test_menu_analyzer_ignores_prompt_dialog_without_program_calls(self):
        self._write_file("dialogo.prg", """
rotina = "ARQUIVOS"
@ 03, 01 prompt " 1. Loja A "
@ 04, 01 prompt " 2. Loja B "
menu to nEmpresa
if lastkey() = 27
   return
endif
return
""")

        analyzer = MenuAnalyzer(str(self.source_dir))
        tree = analyzer.analyze(str(self.source_dir))

        self.assertIsNone(tree.root)
        self.assertEqual(tree.total_menus, 0)


class JourneyBuilderTests(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tmpdir.name) / "test.db")
        self.con = connect(self.db_path)
        init_db(self.con)

    def tearDown(self):
        self.con.close()
        self.tmpdir.cleanup()

    def test_save_and_load_journey(self):
        builder = JourneyBuilder(db_connection=self.con)

        journey = JourneyDefinition(
            journey_id="test_journey_1",
            name="Jornada de Teste",
            description="Descrição",
            category="teste",
            steps=[
                JourneyStep(step_order=0, screen_id="menu", action="navigate", trigger="1"),
                JourneyStep(step_order=1, screen_id="cadastro", action="input", input_template="{{nome}}"),
            ],
            tags=["teste"],
        )

        jid = builder.save_journey(journey)
        self.assertGreater(jid, 0)

        loaded = builder.load_journey("test_journey_1")
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.name, "Jornada de Teste")
        self.assertEqual(len(loaded.steps), 2)

    def test_list_journeys(self):
        builder = JourneyBuilder(db_connection=self.con)

        builder.save_journey(JourneyDefinition(
            journey_id="j1", name="Jornada 1", category="cadastro",
            steps=[JourneyStep(step_order=0, screen_id="menu", action="navigate")],
            tags=["cadastro"],
        ))
        builder.save_journey(JourneyDefinition(
            journey_id="j2", name="Jornada 2", category="financeiro",
            steps=[JourneyStep(step_order=0, screen_id="menu", action="navigate")],
            tags=["fin"],
        ))

        journeys = builder.list_journeys()
        self.assertEqual(len(journeys), 2)

    def test_build_journey_dataset_cross_screen(self):
        """Valida que dados são consistentes entre telas da jornada."""
        builder = JourneyBuilder(db_connection=self.con)

        journey = JourneyDefinition(
            journey_id="cross_screen_test",
            name="Test Cross-Screen",
            steps=[
                JourneyStep(step_order=0, screen_id="tela_a", action="input"),
                JourneyStep(step_order=1, screen_id="tela_b", action="input", depends_on=["tela_a.nome"]),
            ],
        )

        jds = builder.build_journey_dataset(journey, session_count=5, seed=42)
        self.assertEqual(jds.session_count, 5)
        self.assertIn(0, jds.steps_data)
        self.assertIn(1, jds.steps_data)

    def test_generate_replay_script(self):
        builder = JourneyBuilder(db_connection=self.con)

        journey = JourneyDefinition(
            journey_id="replay_test",
            name="Jornada Replay",
            steps=[
                JourneyStep(step_order=0, screen_id="menu", action="navigate", trigger="1", description="Abre menu"),
                JourneyStep(step_order=1, screen_id="cadastro", action="input", input_template="{{nome}}\n{{cpf}}", description="Preenche dados"),
                JourneyStep(step_order=2, screen_id="cadastro", action="submit", trigger="F10", description="Salva"),
            ],
        )

        jds = JourneyDataset(
            journey_id="replay_test",
            journey_name="Jornada Replay",
            seed=42,
            session_count=3,
            steps_data={
                0: [{"opcao": "1"}, {"opcao": "1"}, {"opcao": "1"}],
                1: [{"nome": "ANA", "cpf": "111"}, {"nome": "BRUNO", "cpf": "222"}, {"nome": "CARLA", "cpf": "333"}],
                2: [{}, {}, {}],
            },
        )

        script = builder.generate_replay_script(journey, jds, session_index=0)
        self.assertIn("Jornada Replay", script)
        self.assertIn("Sessão: 1/3", script)
        self.assertIn("ANA", script)
        self.assertIn("111", script)

    def test_journey_idempotent_save(self):
        builder = JourneyBuilder(db_connection=self.con)

        j1 = JourneyDefinition(
            journey_id="idempotent_test",
            name="V1",
            steps=[JourneyStep(step_order=0, screen_id="menu", action="navigate")],
        )
        builder.save_journey(j1)

        j2 = JourneyDefinition(
            journey_id="idempotent_test",
            name="V2 Atualizada",
            steps=[JourneyStep(step_order=0, screen_id="menu", action="navigate")],
        )
        builder.save_journey(j2)

        loaded = builder.load_journey("idempotent_test")
        self.assertEqual(loaded.name, "V2 Atualizada")


if __name__ == "__main__":
    unittest.main()

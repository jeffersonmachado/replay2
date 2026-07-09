#!/usr/bin/env python3
"""Testes para o ScreenEntityLinker — P2-A Synthetic Knowledge Base."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GATEWAY_DIR = ROOT / "gateway"
sys.path.insert(0, str(GATEWAY_DIR))

from dakota_gateway.source_analyzer.entity_catalog import (
    EntityDefinition,
    FieldDefinition,
    OperationDefinition,
    ScreenDefinition,
)
from dakota_gateway.source_analyzer.screen_entity_linker import (
    ScreenEntityBinding,
    ScreenEntityLinker,
)


class ScreenEntityLinkerUnitTests(unittest.TestCase):
    """Testes unitarios do ScreenEntityLinker."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.source_dir = Path(self.tmpdir.name)
        self.linker = ScreenEntityLinker(str(self.source_dir))

    def tearDown(self):
        self.tmpdir.cleanup()

    def _write_file(self, name: str, content: str) -> Path:
        path = self.source_dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    # ── Cenário 1: Cadastro de Clientes → create ──

    def test_cadastro_cliente_create(self):
        """Tela 'Cadastro de Clientes' com campos nome/cpf/email
        associada a entidade CLIENTES com operacao create."""
        self._write_file("cadcli.prg", """
TITLE "Cadastro de Clientes"
@ 1,1 SAY "Nome:"
@ 1,20 GET nome
@ 2,1 SAY "CPF:"
@ 2,20 GET cpf
@ 3,1 SAY "Email:"
@ 3,20 GET email
USE CLIENTES
APPEND BLANK
REPLACE nome WITH m.nome, cpf WITH m.cpf, email WITH m.email
""")
        screen = ScreenDefinition(
            screen_id="scr-cadcli",
            title="Cadastro de Clientes",
            program_name="cadcli",
            source_file=str(self.source_dir / "cadcli.prg"),
            fields=[
                FieldDefinition(name="nome", prompt="Nome:"),
                FieldDefinition(name="cpf", prompt="CPF:"),
                FieldDefinition(name="email", prompt="Email:"),
            ],
        )
        entities = [
            EntityDefinition(
                name="CLIENTES",
                storage_type="isam",
                source=str(self.source_dir / "cadcli.prg"),
                fields=[
                    FieldDefinition(name="NOME"),
                    FieldDefinition(name="CPF"),
                    FieldDefinition(name="EMAIL"),
                    FieldDefinition(name="TELEFONE"),
                ],
            ),
        ]

        bindings = self.linker.link([screen], entities)
        self.assertEqual(len(bindings), 1)

        b = bindings[0]
        self.assertEqual(b.entity_name, "CLIENTES")
        self.assertEqual(b.operation, "create")
        self.assertGreater(b.confidence, 0.6)
        self.assertIn("NOME", [f.upper() for f in b.matched_fields])
        self.assertIn("CPF", [f.upper() for f in b.matched_fields])
        self.assertIn("EMAIL", [f.upper() for f in b.matched_fields])
        self.assertGreaterEqual(len(b.evidence), 2)

    # ── Cenário 2: Consulta de Clientes → read ──

    def test_consulta_cliente_read(self):
        """Tela 'Consulta de Clientes' associada a entidade CLIENTES
        com operacao read."""
        self._write_file("concli.prg", """
TITLE "Consulta de Clientes"
@ 1,1 SAY "Nome:"
@ 1,20 GET m.nome
USE CLIENTES
SEEK m.nome
""")
        screen = ScreenDefinition(
            screen_id="scr-concli",
            title="Consulta de Clientes",
            program_name="concli",
            source_file=str(self.source_dir / "concli.prg"),
            fields=[
                FieldDefinition(name="m.nome", prompt="Nome:"),
            ],
        )
        entities = [
            EntityDefinition(
                name="CLIENTES",
                storage_type="isam",
                source=str(self.source_dir / "concli.prg"),
                fields=[
                    FieldDefinition(name="NOME"),
                    FieldDefinition(name="CPF"),
                    FieldDefinition(name="EMAIL"),
                ],
            ),
        ]

        bindings = self.linker.link([screen], entities)
        self.assertEqual(len(bindings), 1)

        b = bindings[0]
        self.assertEqual(b.entity_name, "CLIENTES")
        self.assertEqual(b.operation, "read")
        self.assertGreater(b.confidence, 0.4)

    # ── Cenário 3: Alteracao de Clientes → update ──

    def test_alteracao_cliente_update(self):
        """Tela 'Alteracao de Clientes' associada a entidade CLIENTES
        com operacao update."""
        self._write_file("altcli.prg", """
TITLE "Alteracao de Clientes"
USE CLIENTES
SEEK m.nome
@ 1,1 SAY "Nome:"
@ 1,20 GET nome
@ 2,1 SAY "Email:"
@ 2,20 GET email
REPLACE nome WITH m.nome, email WITH m.email
""")
        screen = ScreenDefinition(
            screen_id="scr-altcli",
            title="Alteracao de Clientes",
            program_name="altcli",
            source_file=str(self.source_dir / "altcli.prg"),
            fields=[
                FieldDefinition(name="nome", prompt="Nome:"),
                FieldDefinition(name="email", prompt="Email:"),
            ],
        )
        entities = [
            EntityDefinition(
                name="CLIENTES",
                storage_type="isam",
                source=str(self.source_dir / "altcli.prg"),
                fields=[
                    FieldDefinition(name="NOME"),
                    FieldDefinition(name="EMAIL"),
                ],
            ),
        ]

        bindings = self.linker.link([screen], entities)
        self.assertEqual(len(bindings), 1)

        b = bindings[0]
        self.assertEqual(b.entity_name, "CLIENTES")
        self.assertEqual(b.operation, "update")
        self.assertGreater(b.confidence, 0.5)

    # ── Cenário 4: Menu sem GET → menu ──

    def test_menu_sem_get(self):
        """Tela de menu numerado sem GET classificada como menu
        e sem entidade principal."""
        self._write_file("menu_principal.prg", """
TITLE "Menu Principal"
@ 1,1 SAY "1 - Cadastros"
@ 2,1 SAY "2 - Vendas"
@ 3,1 SAY "3 - Relatorios"
@ 4,1 SAY "0 - Sair"
""")
        screen = ScreenDefinition(
            screen_id="scr-menu",
            title="Menu Principal",
            program_name="menu_principal",
            source_file=str(self.source_dir / "menu_principal.prg"),
            fields=[],  # sem campos GET
        )
        entities: list[EntityDefinition] = []

        bindings = self.linker.link([screen], entities)
        self.assertEqual(len(bindings), 1)

        b = bindings[0]
        self.assertEqual(b.operation, "menu")
        self.assertEqual(b.entity_name, "")

    # ── Cenário 5: Tela ambígua → baixa confiança ──

    def test_tela_ambigua_baixa_confianca(self):
        """Tela ambigua deve retornar baixa confidence e evidencia suficiente."""
        self._write_file("rotina_x.prg", """
@ 1,1 SAY "XYZ:"
@ 1,20 GET xyz_abc
@ 2,1 SAY "LMN:"
@ 2,20 GET lmn_pqr
""")
        screen = ScreenDefinition(
            screen_id="scr-ambigua",
            title="Rotina X",
            program_name="rotina_x",
            source_file=str(self.source_dir / "rotina_x.prg"),
            fields=[
                FieldDefinition(name="xyz_abc", prompt="XYZ:"),
                FieldDefinition(name="lmn_pqr", prompt="LMN:"),
            ],
        )
        entities = [
            EntityDefinition(
                name="ENTIDADE_A",
                storage_type="sql",
                fields=[
                    FieldDefinition(name="CAMPO_A"),
                    FieldDefinition(name="CAMPO_B"),
                ],
            ),
        ]

        bindings = self.linker.link([screen], entities)
        self.assertEqual(len(bindings), 1)

        b = bindings[0]
        # Campos nao batem → confianca deve ser baixa
        self.assertLess(b.confidence, 0.40)
        self.assertGreaterEqual(len(b.evidence), 1)
        # Deve mencionar confianca baixa na evidencia
        ev_text = " ".join(b.evidence).lower()
        self.assertTrue(
            "baixa" in ev_text or "fraca" in ev_text or "baixo" in ev_text,
            f"Evidencia deveria mencionar baixa confianca: {b.evidence}"
        )

    # ── Cenário 6: Multiplas telas para mesma entidade ──

    def test_multiplas_telas_mesma_entidade(self):
        """Duas telas diferentes apontando para a mesma entidade."""
        self._write_file("cadcli.prg", """
TITLE "Cadastro de Clientes"
@ 1,1 SAY "Nome:"
@ 1,20 GET nome
USE CLIENTES
APPEND BLANK
""")
        self._write_file("concli.prg", """
TITLE "Consulta de Clientes"
@ 1,1 SAY "CPF:"
@ 1,20 GET cpf
USE CLIENTES
SEEK cpf
""")
        screens = [
            ScreenDefinition(
                screen_id="scr-cadcli",
                title="Cadastro de Clientes",
                program_name="cadcli",
                source_file=str(self.source_dir / "cadcli.prg"),
                fields=[FieldDefinition(name="nome", prompt="Nome:")],
            ),
            ScreenDefinition(
                screen_id="scr-concli",
                title="Consulta de Clientes",
                program_name="concli",
                source_file=str(self.source_dir / "concli.prg"),
                fields=[FieldDefinition(name="cpf", prompt="CPF:")],
            ),
        ]
        entities = [
            EntityDefinition(
                name="CLIENTES",
                storage_type="isam",
                fields=[
                    FieldDefinition(name="NOME"),
                    FieldDefinition(name="CPF"),
                ],
            ),
        ]

        bindings = self.linker.link(screens, entities)
        self.assertEqual(len(bindings), 2)

        cad = bindings[0]
        con = bindings[1]

        self.assertEqual(cad.entity_name, "CLIENTES")
        self.assertEqual(cad.operation, "create")
        self.assertEqual(con.entity_name, "CLIENTES")
        self.assertEqual(con.operation, "read")


class SourceParserBindingsTests(unittest.TestCase):
    """Testa integracao do ScreenEntityLinker no SourceParser."""

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

    def test_discovery_report_inclui_bindings(self):
        """SourceParser.discovery_report() deve incluir screen_entity_bindings."""
        from dakota_gateway.source_analyzer.parser import SourceParser

        self._write_file("cadcli.prg", """
TITLE "Cadastro de Clientes"
@ 1,1 SAY "Nome:"
@ 1,20 GET nome
@ 2,1 SAY "CPF:"
@ 2,20 GET cpf
INSERT INTO CLIENTES (NOME, CPF) VALUES ('X', 'Y')
""")

        parser = SourceParser(str(self.source_dir))
        parser.parse_all()

        report = parser.discovery_report()
        self.assertIsInstance(report, dict)

        # Deve conter a secao de bindings
        self.assertIn("screen_entity_bindings", report)
        bindings_section = report["screen_entity_bindings"]
        self.assertIsInstance(bindings_section, dict)

        # Campos esperados
        for key in (
            "total_bindings", "high_confidence", "medium_confidence",
            "low_confidence", "unbound_screens", "bindings_by_entity", "details"
        ):
            self.assertIn(key, bindings_section, f"Falta chave '{key}' no relatorio")

        self.assertGreaterEqual(bindings_section["total_bindings"], 1)

        # Verifica serializacao JSON
        json_str = json.dumps(report, ensure_ascii=False, default=str)
        parsed = json.loads(json_str)
        self.assertIn("screen_entity_bindings", parsed)

    def test_screen_entity_bindings_method(self):
        """screen_entity_bindings() retorna lista de ScreenEntityBinding."""
        from dakota_gateway.source_analyzer.parser import SourceParser

        self._write_file("cadcli.prg", """
TITLE "Cadastro de Clientes"
@ 1,1 SAY "Nome:"
@ 1,20 GET nome
INSERT INTO CLIENTES (NOME, CPF) VALUES ('X', 'Y')
""")

        parser = SourceParser(str(self.source_dir))
        parser.parse_all()

        bindings = parser.screen_entity_bindings()
        self.assertIsInstance(bindings, list)
        self.assertGreaterEqual(len(bindings), 1)

        b = bindings[0]
        self.assertIsInstance(b, ScreenEntityBinding)
        self.assertTrue(hasattr(b, "confidence"))
        self.assertTrue(hasattr(b, "evidence"))
        self.assertTrue(hasattr(b, "entity_name"))

    def test_discovery_report_serializavel_json(self):
        """Relatorio completo deve ser serializavel em JSON."""
        from dakota_gateway.source_analyzer.parser import SourceParser

        self._write_file("cadcli.prg", """
TITLE "Cadastro de Clientes"
@ 1,1 SAY "Nome:"
@ 1,20 GET nome
INSERT INTO CLIENTES (NOME, CPF) VALUES ('X', 'Y')
""")
        self._write_file("cadprod.prg", """
TITLE "Cadastro de Produtos"
@ 1,1 SAY "Descricao:"
@ 1,20 GET descricao
INSERT INTO PRODUTOS (DESCRICAO) VALUES ('Z')
""")

        parser = SourceParser(str(self.source_dir))
        parser.parse_all()
        report = parser.discovery_report()

        # Nao deve lancar excecao
        json_str = json.dumps(report, ensure_ascii=False, default=str)
        parsed = json.loads(json_str)

        # Verifica estrutura dos bindings
        b = parsed["screen_entity_bindings"]
        self.assertGreaterEqual(b["total_bindings"], 1)


if __name__ == "__main__":
    unittest.main()

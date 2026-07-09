#!/usr/bin/env python3
"""Testes para o Source Parser e Inferencer integrados."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GATEWAY_DIR = ROOT / "gateway"
sys.path.insert(0, str(GATEWAY_DIR))

from dakota_gateway.source_analyzer.parser import SourceParser
from dakota_gateway.synthetic.inferencer import SyntheticInferencer


class SourceParserTests(unittest.TestCase):

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

    def test_parse_sql_files(self):
        self._write_file("cadcli.prg", """
INSERT INTO CLIENTES (NOME, CPF, TELEFONE, EMAIL)
VALUES ('Teste', '123', '456', 'a@b.com')
""")
        parser = SourceParser(str(self.source_dir))
        entities, screens = parser.parse_all()
        self.assertGreaterEqual(len(entities), 1)
        cliente = next((e for e in entities if e.name == "CLIENTES"), None)
        self.assertIsNotNone(cliente)
        self.assertEqual(cliente.storage_type, "sql")
        field_names = {f.name for f in cliente.fields}
        self.assertIn("NOME", field_names)
        self.assertIn("CPF", field_names)

    def test_parse_isam_files(self):
        self._write_file("cadprod.prg", """
USE PRODUTOS
APPEND BLANK
REPLACE CODIGO WITH cCod, DESCRICAO WITH cDesc, PRECO WITH nPreco
""")
        parser = SourceParser(str(self.source_dir))
        entities, screens = parser.parse_all()
        produtos = next((e for e in entities if e.name == "PRODUTOS"), None)
        self.assertIsNotNone(produtos)
        self.assertEqual(produtos.storage_type, "isam")

    def test_parse_mixed_sql_and_isam(self):
        self._write_file("cliente.prg", """
INSERT INTO CLIENTES (NOME, CPF) VALUES ('A', '1')
""")
        self._write_file("contrato.prg", """
USE CONTRATOS
APPEND BLANK
REPLACE NUMERO WITH cNum, CLIENTE_ID WITH nCliId
""")
        parser = SourceParser(str(self.source_dir))
        entities, screens = parser.parse_all()
        names = {e.name for e in entities}
        self.assertIn("CLIENTES", names)
        self.assertIn("CONTRATOS", names)

    def test_merge_entities_across_files(self):
        self._write_file("a.prg", "INSERT INTO CLIENTES (NOME) VALUES ('X')")
        self._write_file("b.prg", "INSERT INTO CLIENTES (CPF) VALUES ('Y')")
        parser = SourceParser(str(self.source_dir))
        entities, _ = parser.parse_all()
        cliente = next(e for e in entities if e.name == "CLIENTES")
        field_names = {f.name for f in cliente.fields}
        self.assertIn("NOME", field_names)
        self.assertIn("CPF", field_names)
        # Deve ter 2 operacoes
        self.assertEqual(len(cliente.operations), 2)


class SyntheticInferencerTests(unittest.TestCase):

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

    def test_inferencer_generates_schemas_from_source(self):
        self._write_file("cadcli.prg", """
INSERT INTO CLIENTES (NOME, CPF, TELEFONE, EMAIL)
VALUES ('Teste', '123', '456', 'a@b.com')
""")
        inferencer = SyntheticInferencer()
        result = inferencer.analyze_source(str(self.source_dir))
        self.assertGreaterEqual(len(result.screens), 1)
        self.assertGreaterEqual(len(result.schemas), 1)

    def test_inferencer_maps_fields_with_types(self):
        self._write_file("cadcli.prg", """
INSERT INTO CLIENTES (NOME, CPF, TELEFONE, EMAIL, ENDERECO)
VALUES ('Teste', '123', '456', 'a@b.com', 'Rua X')
""")
        inferencer = SyntheticInferencer()
        result = inferencer.analyze_source(str(self.source_dir))
        cliente_schema = next(
            (s for s in result.screens if "CLIENTES" in s.title.upper()), None
        )
        self.assertIsNotNone(cliente_schema, "Deve encontrar schema para CLIENTES")
        field_types = {f.name: f.datatype for f in cliente_schema.fields}
        self.assertEqual(field_types.get("CPF"), "cpf")
        self.assertEqual(field_types.get("EMAIL"), "email")
        self.assertEqual(field_types.get("TELEFONE"), "phone")
        self.assertEqual(field_types.get("ENDERECO"), "address")
        self.assertEqual(field_types.get("NOME"), "person_name")


if __name__ == "__main__":
    unittest.main()

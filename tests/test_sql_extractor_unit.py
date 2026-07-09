#!/usr/bin/env python3
"""Testes para o SQL extractor."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GATEWAY_DIR = ROOT / "gateway"
sys.path.insert(0, str(GATEWAY_DIR))

from dakota_gateway.source_analyzer.sql_extractor import SQLExtractor


class SQLExtractorTests(unittest.TestCase):

    def test_detect_insert(self):
        content = "INSERT INTO CLIENTES (NOME, CPF, TELEFONE) VALUES ('Joao', '123', '456')"
        entities = SQLExtractor.extract(content)
        self.assertEqual(len(entities), 1)
        self.assertEqual(entities[0].name, "CLIENTES")
        self.assertEqual(entities[0].storage_type, "sql")
        field_names = {f.name for f in entities[0].fields}
        self.assertSetEqual(field_names, {"NOME", "CPF", "TELEFONE"})
        self.assertEqual(entities[0].operations[0].operation_type, "insert")

    def test_detect_update(self):
        content = "UPDATE CLIENTES SET NOME='Maria', TELEFONE='999' WHERE ID=1"
        entities = SQLExtractor.extract(content)
        self.assertEqual(len(entities), 1)
        self.assertEqual(entities[0].name, "CLIENTES")
        field_names = {f.name for f in entities[0].fields}
        self.assertIn("NOME", field_names)
        self.assertIn("TELEFONE", field_names)
        self.assertEqual(entities[0].operations[0].operation_type, "update")

    def test_detect_select(self):
        content = "SELECT NOME, CPF FROM CLIENTES WHERE ATIVO=1"
        entities = SQLExtractor.extract(content)
        self.assertEqual(len(entities), 1)
        self.assertEqual(entities[0].name, "CLIENTES")
        field_names = {f.name for f in entities[0].fields}
        self.assertIn("NOME", field_names)
        self.assertIn("CPF", field_names)
        self.assertEqual(entities[0].operations[0].operation_type, "select")

    def test_detect_delete(self):
        content = "DELETE FROM CLIENTES WHERE ID=99"
        entities = SQLExtractor.extract(content)
        self.assertEqual(len(entities), 1)
        self.assertEqual(entities[0].name, "CLIENTES")
        self.assertEqual(entities[0].operations[0].operation_type, "delete")

    def test_detect_join(self):
        content = "SELECT c.NOME, p.DESCRICAO FROM CLIENTES c JOIN PRODUTOS p ON c.ID=p.CLIENTE_ID"
        entities = SQLExtractor.extract(content)
        names = {e.name for e in entities}
        self.assertIn("CLIENTES", names)
        self.assertIn("PRODUTOS", names)

    def test_extract_fields_correctly(self):
        content = 'INSERT INTO CONTRATOS (NUMERO, CLIENTE_ID, DATA_INICIO, DATA_FIM) VALUES (1, 10, "2024-01-01", "2024-12-31")'
        entities = SQLExtractor.extract(content)
        field_names = {f.name for f in entities[0].fields}
        self.assertSetEqual(field_names, {"NUMERO", "CLIENTE_ID", "DATA_INICIO", "DATA_FIM"})

    def test_multiple_statements(self):
        content = """
        INSERT INTO CLIENTES (NOME, CPF) VALUES ('A', '1');
        INSERT INTO PRODUTOS (CODIGO, DESCRICAO) VALUES (1, 'Produto A');
        UPDATE CLIENTES SET TELEFONE='555' WHERE ID=1;
        """
        entities = SQLExtractor.extract(content)
        names = {e.name for e in entities}
        self.assertIn("CLIENTES", names)
        self.assertIn("PRODUTOS", names)


if __name__ == "__main__":
    unittest.main()

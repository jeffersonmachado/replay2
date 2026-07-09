#!/usr/bin/env python3
"""Testa extracao de CREATE TABLE no SQLExtractor."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GATEWAY_DIR = ROOT / "gateway"
sys.path.insert(0, str(GATEWAY_DIR))

from dakota_gateway.source_analyzer.sql_extractor import SQLExtractor


class SQLExtractorCreateTableTests(unittest.TestCase):

    def test_create_table_basic(self):
        """CREATE TABLE basico deve extrair campos."""
        content = """
CREATE TABLE CLIENTES (
ID INT PRIMARY KEY,
CPF VARCHAR(14),
NOME VARCHAR(60)
);
"""
        entities = SQLExtractor.extract(content, "schema.sql")
        cliente = next((e for e in entities if e.name == "CLIENTES"), None)
        self.assertIsNotNone(cliente)
        field_names = {f.name for f in cliente.fields}
        self.assertIn("ID", field_names)
        self.assertIn("CPF", field_names)
        self.assertIn("NOME", field_names)

    def test_create_table_if_not_exists(self):
        """CREATE TABLE IF NOT EXISTS deve funcionar."""
        content = "CREATE TABLE IF NOT EXISTS PRODUTOS (ID INT, DESCRICAO VARCHAR(80), VALOR DECIMAL(10,2));"
        entities = SQLExtractor.extract(content, "s.sql")
        prod = next((e for e in entities if e.name == "PRODUTOS"), None)
        self.assertIsNotNone(prod)
        names = {f.name for f in prod.fields}
        self.assertIn("ID", names)
        self.assertIn("DESCRICAO", names)
        self.assertIn("VALOR", names)

    def test_not_null_and_pk(self):
        """NOT NULL e PRIMARY KEY devem ser marcados."""
        content = """
CREATE TABLE PEDIDOS (
ID INT PRIMARY KEY,
CLIENTE_ID INT NOT NULL,
VALOR_TOTAL DECIMAL(10,2)
);
"""
        entities = SQLExtractor.extract(content, "s.sql")
        ped = next((e for e in entities if e.name == "PEDIDOS"), None)
        self.assertIsNotNone(ped)

        id_field = next((f for f in ped.fields if f.name == "ID"), None)
        self.assertTrue(id_field.unique_flag)

        cli_field = next((f for f in ped.fields if f.name == "CLIENTE_ID"), None)
        self.assertTrue(cli_field.required)

    def test_multiple_tables(self):
        """Multiplas CREATE TABLE no mesmo arquivo."""
        content = """
CREATE TABLE CLIENTES (ID INT, NOME VARCHAR(60));
CREATE TABLE PRODUTOS (ID INT, DESCRICAO VARCHAR(80));
"""
        entities = SQLExtractor.extract(content, "s.sql")
        names = {e.name for e in entities}
        self.assertIn("CLIENTES", names)
        self.assertIn("PRODUTOS", names)


if __name__ == "__main__":
    unittest.main()

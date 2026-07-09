#!/usr/bin/env python3
"""Testa SourceParser com CREATE TABLE em arquivos .sql."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GATEWAY_DIR = ROOT / "gateway"
sys.path.insert(0, str(GATEWAY_DIR))

from dakota_gateway.source_analyzer.parser import SourceParser


class SourceParserSQLDDLTests(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.sd = Path(self.tmpdir.name)

    def tearDown(self):
        self.tmpdir.cleanup()

    def _w(self, name: str, content: str) -> None:
        p = self.sd / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)

    def test_one_line_create_table(self):
        """CREATE TABLE em uma linha deve extrair todos os campos."""
        self._w("schema.sql", """
CREATE TABLE CLIENTES (ID INT PRIMARY KEY, CPF VARCHAR(14), NOME VARCHAR(60));
CREATE TABLE PRODUTOS (ID INT PRIMARY KEY, DESCRICAO VARCHAR(80), VALOR DECIMAL(10,2));
""")
        parser = SourceParser(str(self.sd))
        entities, _ = parser.parse_all()

        cli = next((e for e in entities if e.name == "CLIENTES"), None)
        self.assertIsNotNone(cli, "CLIENTES nao detectada")
        cli_fields = {f.name for f in cli.fields}
        self.assertIn("ID", cli_fields)
        self.assertIn("CPF", cli_fields)
        self.assertIn("NOME", cli_fields)

        prod = next((e for e in entities if e.name == "PRODUTOS"), None)
        self.assertIsNotNone(prod, "PRODUTOS nao detectada")
        prod_fields = {f.name for f in prod.fields}
        self.assertIn("DESCRICAO", prod_fields)
        self.assertIn("VALOR", prod_fields)

    def test_multiline_create_table(self):
        """CREATE TABLE multilinha deve extrair campos."""
        self._w("schema.sql", """
CREATE TABLE CLIENTES (
    ID INT PRIMARY KEY,
    CPF VARCHAR(14),
    NOME VARCHAR(60)
);
CREATE TABLE PRODUTOS (
    ID INT PRIMARY KEY,
    DESCRICAO VARCHAR(80),
    VALOR DECIMAL(10,2)
);
""")
        parser = SourceParser(str(self.sd))
        entities, _ = parser.parse_all()

        names = {e.name for e in entities}
        self.assertIn("CLIENTES", names)
        self.assertIn("PRODUTOS", names)

    def test_discovery_report_includes_sql_entities(self):
        """discovery_report deve incluir entidades de .sql."""
        self._w("schema.sql", "CREATE TABLE CLIENTES (ID INT, NOME VARCHAR(60));")
        parser = SourceParser(str(self.sd))
        parser.parse_all()
        report = parser.discovery_report()
        self.assertGreaterEqual(report["entities"], 1)


if __name__ == "__main__":
    unittest.main()

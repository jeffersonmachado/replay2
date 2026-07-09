#!/usr/bin/env python3
"""Testes para o ISAM/DBF/Recital extractor."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GATEWAY_DIR = ROOT / "gateway"
sys.path.insert(0, str(GATEWAY_DIR))

from dakota_gateway.source_analyzer.isam_extractor import ISAMExtractor


class ISAMExtractorTests(unittest.TestCase):

    def test_detect_use_clientes(self):
        content = "USE CLIENTES"
        entities = ISAMExtractor.extract(content)
        self.assertEqual(len(entities), 1)
        self.assertEqual(entities[0].name, "CLIENTES")
        self.assertEqual(entities[0].storage_type, "isam")
        self.assertEqual(entities[0].operations[0].operation_type, "use")

    def test_detect_use_with_alias(self):
        content = "USE PRODUTO ALIAS PROD"
        entities = ISAMExtractor.extract(content)
        self.assertEqual(len(entities), 1)
        self.assertEqual(entities[0].name, "PRODUTO")

    def test_detect_use_shared(self):
        content = "USE CONTRATO SHARED"
        entities = ISAMExtractor.extract(content)
        self.assertEqual(len(entities), 1)
        self.assertEqual(entities[0].name, "CONTRATO")

    def test_detect_append_blank_and_replace(self):
        content = """USE CLIENTES
APPEND BLANK
REPLACE NOME WITH cNome, CPF WITH cCpf"""
        entities = ISAMExtractor.extract(content)
        self.assertEqual(len(entities), 1)
        ent = entities[0]
        field_names = {f.name for f in ent.fields}
        self.assertIn("NOME", field_names)
        self.assertIn("CPF", field_names)
        ops = [o.operation_type for o in ent.operations]
        self.assertIn("use", ops)
        self.assertIn("append", ops)
        self.assertIn("insert", ops)

    def test_replace_without_append_is_update(self):
        content = """USE CLIENTES
REPLACE NOME WITH cNovoNome"""
        entities = ISAMExtractor.extract(content)
        ops = [o.operation_type for o in entities[0].operations]
        self.assertIn("update", ops)

    def test_detect_seek_and_set_order(self):
        content = """USE CLIENTES
SET ORDER TO CPF
SEEK cCpf"""
        entities = ISAMExtractor.extract(content)
        ent = entities[0]
        ops = [o.operation_type for o in ent.operations]
        self.assertIn("seek", ops)
        # Deve ter indice CPF
        self.assertTrue(any(idx.get("field") == "CPF" for idx in ent.indexes))

    def test_detect_locate_for(self):
        content = """USE CLIENTES
LOCATE FOR CPF = cCpf"""
        entities = ISAMExtractor.extract(content)
        ent = entities[0]
        ops = [o.operation_type for o in ent.operations]
        self.assertIn("locate", ops)
        locate_op = next(o for o in ent.operations if o.operation_type == "locate")
        self.assertIn("CPF", locate_op.fields)

    def test_detect_index_on(self):
        content = """USE CLIENTES
INDEX ON CPF TAG CPF"""
        entities = ISAMExtractor.extract(content)
        ent = entities[0]
        self.assertTrue(any(
            idx.get("field") == "CPF" and idx.get("index") == "CPF"
            for idx in ent.indexes
        ))

    def test_detect_dbseek(self):
        content = """USE CLIENTES
DBSEEK(cCpf)"""
        entities = ISAMExtractor.extract(content)
        ops = [o.operation_type for o in entities[0].operations]
        self.assertIn("dbseek", ops)

    def test_detect_fieldget_fieldput(self):
        content = """USE CLIENTES
FIELDGET(1)
FIELDPUT(1, cNome)"""
        entities = ISAMExtractor.extract(content)
        ops = [o.operation_type for o in entities[0].operations]
        self.assertIn("fieldget", ops)
        self.assertIn("fieldput", ops)

    def test_detect_scatter_gather(self):
        content = """USE CLIENTES
SCATTER MEMVAR
GATHER MEMVAR"""
        entities = ISAMExtractor.extract(content)
        ops = [o.operation_type for o in entities[0].operations]
        self.assertIn("scatter", ops)
        self.assertIn("gather", ops)

    def test_multiple_tables(self):
        content = """USE CLIENTES
APPEND BLANK
REPLACE NOME WITH cNome
USE PRODUTOS
REPLACE DESCRICAO WITH cDesc"""
        entities = ISAMExtractor.extract(content)
        names = {e.name for e in entities}
        self.assertIn("CLIENTES", names)
        self.assertIn("PRODUTOS", names)


if __name__ == "__main__":
    unittest.main()

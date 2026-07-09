#!/usr/bin/env python3
"""Testes para o Screen Registry."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GATEWAY_DIR = ROOT / "gateway"
sys.path.insert(0, str(GATEWAY_DIR))

from dakota_gateway.state_db import connect, init_db
from dakota_gateway.synthetic.screen_registry import ScreenRegistry
from dakota_gateway.synthetic.schema import FieldSchema, ScreenSchema


class ScreenRegistryTests(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tmpdir.name) / "test.db")
        self.con = connect(self.db_path)
        init_db(self.con)
        self.registry = ScreenRegistry(self.con)

    def tearDown(self):
        self.con.close()
        self.tmpdir.cleanup()

    def test_register_and_get_screen(self):
        sid = self.registry.register_screen(
            screen_signature="L=10 W=40",
            title="Cadastro de Clientes",
            program_name="CADCLI.PRG",
        )
        self.assertGreater(sid, 0)

        screen = self.registry.get_screen_by_id(sid)
        self.assertIsNotNone(screen)
        self.assertEqual(screen.title, "Cadastro de Clientes")
        self.assertEqual(screen.program_name, "CADCLI.PRG")

    def test_register_screen_idempotent(self):
        sid1 = self.registry.register_screen(
            screen_signature="L=5 W=30", title="Tela A", program_name="A.PRG"
        )
        sid2 = self.registry.register_screen(
            screen_signature="L=5 W=30", title="Tela A Atualizada", program_name="A.PRG"
        )
        self.assertEqual(sid1, sid2)
        screen = self.registry.get_screen_by_id(sid1)
        self.assertEqual(screen.title, "Tela A Atualizada")

    def test_get_screen_by_signature(self):
        self.registry.register_screen(
            screen_signature="SIG_UNICA", title="Tela Unica", program_name="UNICA.PRG"
        )
        screen = self.registry.get_screen_by_signature("SIG_UNICA")
        self.assertIsNotNone(screen)
        self.assertEqual(screen.title, "Tela Unica")

        screen_none = self.registry.get_screen_by_signature("INEXISTENTE")
        self.assertIsNone(screen_none)

    def test_register_fields_from_schema(self):
        sid = self.registry.register_screen(
            screen_signature="CAD_CLI", title="Cadastro de Clientes"
        )
        schema = ScreenSchema(
            screen_signature="CAD_CLI",
            title="Cadastro de Clientes",
            fields=[
                FieldSchema(name="nome", datatype="person_name", required=True),
                FieldSchema(name="cpf", datatype="cpf", unique=True),
                FieldSchema(name="telefone", datatype="phone"),
                FieldSchema(name="email", datatype="email"),
            ],
        )
        ids = self.registry.register_fields_from_schema(sid, schema)
        self.assertEqual(len(ids), 4)

        fields = self.registry.get_fields_by_screen(sid)
        self.assertEqual(len(fields), 4)
        field_names = {f.field_name for f in fields}
        self.assertSetEqual(field_names, {"nome", "cpf", "telefone", "email"})

    def test_get_screen_schema_full(self):
        sid = self.registry.register_screen(
            screen_signature="CAD_PROD", title="Cadastro de Produtos"
        )
        schema = ScreenSchema(
            screen_signature="CAD_PROD",
            title="Cadastro de Produtos",
            fields=[
                FieldSchema(name="codigo", datatype="code"),
                FieldSchema(name="descricao", datatype="text"),
                FieldSchema(name="preco", datatype="money"),
            ],
        )
        self.registry.register_fields_from_schema(sid, schema)

        loaded = self.registry.get_screen_schema(sid)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.title, "Cadastro de Produtos")
        self.assertEqual(len(loaded.fields), 3)

    def test_list_screens(self):
        self.registry.register_screen(screen_signature="S1", title="Tela 1")
        self.registry.register_screen(screen_signature="S2", title="Tela 2")
        self.registry.register_screen(screen_signature="S3", title="Tela 3")
        screens = self.registry.list_screens()
        self.assertEqual(len(screens), 3)

    def test_associate_screen_signature_to_screen_id(self):
        sid = self.registry.register_screen(
            screen_signature="MENU_PRINCIPAL",
            title="Menu Principal",
            program_name="MENU.PRG",
        )
        schema = ScreenSchema(
            screen_signature="MENU_PRINCIPAL",
            title="Menu Principal",
            fields=[FieldSchema(name="opcao", datatype="number")],
        )
        self.registry.register_fields_from_schema(sid, schema)

        screen = self.registry.get_screen_by_signature("MENU_PRINCIPAL")
        self.assertEqual(screen.id, sid)
        fields = self.registry.get_fields_by_screen(sid)
        self.assertEqual(len(fields), 1)
        self.assertEqual(fields[0].field_name, "opcao")


if __name__ == "__main__":
    unittest.main()

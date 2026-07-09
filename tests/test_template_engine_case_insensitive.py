#!/usr/bin/env python3
"""Testa TemplateEngine case-insensitive para placeholders."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GATEWAY_DIR = ROOT / "gateway"
sys.path.insert(0, str(GATEWAY_DIR))

from dakota_gateway.synthetic.template_engine import TemplateEngine


class TemplateEngineCaseInsensitiveTests(unittest.TestCase):

    def test_lowercase_field_resolves(self):
        """Campo 'cpf' minusculo deve resolver placeholder."""
        data = {"CLIENTES": {"cpf": "123.456.789-09"}}
        result = TemplateEngine.render("{{CLIENTES.cpf}}", data)
        self.assertEqual(result, "123.456.789-09")

    def test_uppercase_placeholder_resolves_lowercase_field(self):
        """Placeholder {{CLIENTES.CPF}} resolve campo 'cpf'."""
        data = {"CLIENTES": {"cpf": "123.456.789-09"}}
        result = TemplateEngine.render("{{CLIENTES.CPF}}", data)
        self.assertEqual(result, "123.456.789-09")

    def test_lowercase_entity_resolves(self):
        """Placeholder {{clientes.cpf}} resolve entidade CLIENTES."""
        data = {"CLIENTES": {"cpf": "123.456.789-09"}}
        result = TemplateEngine.render("{{clientes.cpf}}", data)
        self.assertEqual(result, "123.456.789-09")

    def test_mixed_case_resolves(self):
        """Placeholder {{Clientes.Cpf}} resolve."""
        data = {"CLIENTES": {"cpf": "123.456.789-09"}}
        result = TemplateEngine.render("{{Clientes.Cpf}}", data)
        self.assertEqual(result, "123.456.789-09")

    def test_unresolved_placeholder_kept(self):
        """Placeholder sem dado deve ser mantido."""
        data = {"CLIENTES": {"cpf": "123"}}
        result = TemplateEngine.render("{{CLIENTES.inexistente}}", data)
        self.assertIn("{{", result)

    def test_flat_key_resolves(self):
        """Chave plana {{campo}} resolve."""
        data = {"nome": "JOAO"}
        result = TemplateEngine.render("{{nome}}", data)
        self.assertEqual(result, "JOAO")


if __name__ == "__main__":
    unittest.main()

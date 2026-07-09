#!/usr/bin/env python3
"""Testes para o Synthetic Engine (providers, constraints, dataset builder, template engine)."""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GATEWAY_DIR = ROOT / "gateway"
sys.path.insert(0, str(GATEWAY_DIR))

from dakota_gateway.synthetic.providers import (
    CPFProvider,
    CNPJProvider,
    PhoneProvider,
    EmailProvider,
    DateProvider,
    ChoiceProvider,
    BooleanProvider,
    UUIDProvider,
    SequenceProvider,
    TextProvider,
    CodeProvider,
    MoneyProvider,
    PersonNameProvider,
)
from dakota_gateway.synthetic.constraints import ConstraintValidator, ConstraintRule
from dakota_gateway.synthetic.schema import FieldSchema, ScreenSchema, SyntheticSchema
from dakota_gateway.synthetic.dataset_builder import DatasetBuilder


class ProvidersTests(unittest.TestCase):

    def test_cpf_valid_format(self):
        provider = CPFProvider(seed=42)
        cpf = provider.generate()
        self.assertRegex(cpf, r"^\d{3}\.\d{3}\.\d{3}-\d{2}$")

    def test_cnpj_valid_format(self):
        provider = CNPJProvider(seed=42)
        cnpj = provider.generate()
        self.assertRegex(cnpj, r"^\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}$")

    def test_cpf_deterministic(self):
        p1 = CPFProvider(seed=123)
        p2 = CPFProvider(seed=123)
        self.assertEqual(p1.generate(), p2.generate())

    def test_phone_valid_format(self):
        provider = PhoneProvider(seed=42)
        phone = provider.generate()
        self.assertRegex(phone, r"^\(\d{2}\) \d{5}-\d{4}$")

    def test_email_contains_at(self):
        provider = EmailProvider(seed=42)
        email = provider.generate()
        self.assertIn("@", email)
        self.assertIn(".", email.split("@")[1])

    def test_date_valid_format(self):
        provider = DateProvider(seed=42)
        date_str = provider.generate()
        self.assertRegex(date_str, r"^\d{4}-\d{2}-\d{2}$")

    def test_choice_returns_from_options(self):
        provider = ChoiceProvider(seed=42)
        for _ in range(20):
            val = provider.generate(choices=["A", "I"])
            self.assertIn(val, ["A", "I"])

    def test_boolean(self):
        provider = BooleanProvider(seed=42)
        values = {provider.generate() for _ in range(50)}
        self.assertTrue(len(values) >= 1)

    def test_uuid_format(self):
        provider = UUIDProvider(seed=42)
        uuid_val = provider.generate()
        self.assertRegex(uuid_val, r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")

    def test_sequence_increments(self):
        provider = SequenceProvider(seed=0)
        self.assertEqual(provider.generate(start=100), 100)
        self.assertEqual(provider.generate(start=100), 101)
        self.assertEqual(provider.generate(start=100), 102)
        provider.reseed(0)
        self.assertEqual(provider.generate(start=100), 100)

    def test_text_respects_max_length(self):
        provider = TextProvider(seed=42)
        text = provider.generate(min_length=10, max_length=50)
        self.assertLessEqual(len(text), 50)
        self.assertGreaterEqual(len(text), 10)

    def test_code_with_prefix(self):
        provider = CodeProvider(seed=42)
        code = provider.generate(prefix="PRD", min=1, max=999, width=4)
        self.assertRegex(code, r"^PRD\d{4}$")

    def test_money_format(self):
        provider = MoneyProvider(seed=42)
        money = provider.generate(min=10, max=100)
        # MoneyProvider retorna formato como "67.55" ou "67,55" dependendo do locale
        self.assertRegex(money, r"^[\d.,]+$")

    def test_person_name_not_empty(self):
        provider = PersonNameProvider(seed=42)
        name = provider.generate()
        self.assertGreater(len(name), 3)
        self.assertIn(" ", name)


class ConstraintsTests(unittest.TestCase):

    def test_required_violation(self):
        rule = ConstraintRule(field="nome", required=True)
        violations = ConstraintValidator.validate("", rule)
        self.assertEqual(len(violations), 1)
        self.assertEqual(violations[0].rule, "required")

    def test_min_length_violation(self):
        rule = ConstraintRule(field="codigo", min_length=5)
        violations = ConstraintValidator.validate("AB", rule)
        self.assertEqual(len(violations), 1)
        self.assertEqual(violations[0].rule, "min_length")

    def test_max_length_violation(self):
        rule = ConstraintRule(field="sigla", max_length=2)
        violations = ConstraintValidator.validate("ABC", rule)
        self.assertEqual(len(violations), 1)
        self.assertEqual(violations[0].rule, "max_length")

    def test_choices_violation(self):
        rule = ConstraintRule(field="status", choices=["A", "I"])
        violations = ConstraintValidator.validate("X", rule)
        self.assertEqual(len(violations), 1)
        self.assertEqual(violations[0].rule, "choices")

    def test_min_value_violation(self):
        rule = ConstraintRule(field="idade", min_value=18)
        violations = ConstraintValidator.validate(15, rule)
        self.assertEqual(len(violations), 1)

    def test_max_value_violation(self):
        rule = ConstraintRule(field="nota", max_value=10)
        violations = ConstraintValidator.validate(11, rule)
        self.assertEqual(len(violations), 1)

    def test_valid_record_no_violations(self):
        rules = [
            ConstraintRule(field="nome", required=True, min_length=3),
            ConstraintRule(field="status", choices=["A", "I"]),
        ]
        record = {"nome": "Joao Silva", "status": "A"}
        violations = ConstraintValidator.validate_record(record, rules)
        self.assertEqual(len(violations), 0)


class DatasetBuilderTests(unittest.TestCase):

    def setUp(self):
        self.builder = DatasetBuilder()

    def test_generate_unique_values(self):
        schema = SyntheticSchema(
            screen=ScreenSchema(
                screen_id="test",
                screen_signature="cadastro_cliente",
                title="Cadastro de Clientes",
                fields=[
                    FieldSchema(name="codigo", datatype="code", required=True, unique=True),
                    FieldSchema(name="nome", datatype="person_name"),
                ],
            ),
            entity_name="cliente",
            quantity=50,
            seed=42,
        )
        dataset = self.builder.build(schema)
        self.assertEqual(len(dataset.records), 50)
        codigos = [r.data["codigo"] for r in dataset.records]
        self.assertEqual(len(codigos), len(set(codigos)))

    def test_respect_required(self):
        schema = SyntheticSchema(
            screen=ScreenSchema(
                screen_id="test",
                title="Test",
                fields=[
                    FieldSchema(name="nome", datatype="person_name", required=True),
                ],
            ),
            entity_name="test",
            quantity=10,
            seed=1,
        )
        dataset = self.builder.build(schema)
        for rec in dataset.records:
            self.assertIsNotNone(rec.data["nome"])
            self.assertNotEqual(rec.data["nome"], "")

    def test_respect_choices(self):
        schema = SyntheticSchema(
            screen=ScreenSchema(
                screen_id="test",
                title="Test",
                fields=[
                    FieldSchema(name="status", datatype="choice", choices=["A", "I"]),
                ],
            ),
            entity_name="test",
            quantity=30,
            seed=7,
        )
        dataset = self.builder.build(schema)
        for rec in dataset.records:
            self.assertIn(rec.data["status"], ["A", "I"])

    def test_respect_min_max(self):
        schema = SyntheticSchema(
            screen=ScreenSchema(
                screen_id="test",
                title="Test",
                fields=[
                    FieldSchema(name="idade", datatype="number", min_value=18, max_value=65),
                ],
            ),
            entity_name="test",
            quantity=20,
            seed=3,
        )
        dataset = self.builder.build(schema)
        for rec in dataset.records:
            self.assertGreaterEqual(rec.data["idade"], 18)
            self.assertLessEqual(rec.data["idade"], 65)

    def test_respect_max_length(self):
        schema = SyntheticSchema(
            screen=ScreenSchema(
                screen_id="test",
                title="Test",
                fields=[
                    FieldSchema(name="sigla", datatype="text", max_length=5),
                ],
            ),
            entity_name="test",
            quantity=10,
            seed=5,
        )
        dataset = self.builder.build(schema)
        for rec in dataset.records:
            self.assertLessEqual(len(rec.data["sigla"]), 5)

    def test_seed_determinism(self):
        schema = SyntheticSchema(
            screen=ScreenSchema(
                screen_id="test",
                title="Test",
                fields=[FieldSchema(name="nome", datatype="person_name")],
            ),
            entity_name="test",
            quantity=10,
            seed=99,
        )
        ds1 = self.builder.build(schema)
        ds2 = self.builder.build(schema)
        for r1, r2 in zip(ds1.records, ds2.records):
            self.assertEqual(r1.data["nome"], r2.data["nome"])


class TemplateEngineTests(unittest.TestCase):

    def test_substitute_placeholders(self):
        from dakota_gateway.synthetic.template_engine import TemplateEngine

        template = "{{cliente.nome}}"
        data = {"cliente": {"nome": "MARCOS SOUZA"}}
        result = TemplateEngine.render(template, data)
        self.assertEqual(result, "MARCOS SOUZA")

    def test_substitute_flat_placeholder(self):
        from dakota_gateway.synthetic.template_engine import TemplateEngine

        template = "{{valor}}"
        data = {"valor": "123.45"}
        result = TemplateEngine.render(template, data)
        self.assertEqual(result, "123.45")

    def test_multiple_placeholders(self):
        from dakota_gateway.synthetic.template_engine import TemplateEngine

        template = "{{cliente.nome}}\n{{cliente.cpf}}\n{{cliente.telefone}}"
        data = {"cliente": {"nome": "MARCOS", "cpf": "84753219004", "telefone": "11981234567"}}
        result = TemplateEngine.render(template, data)
        lines = result.split("\n")
        self.assertEqual(lines[0], "MARCOS")
        self.assertEqual(lines[1], "84753219004")
        self.assertEqual(lines[2], "11981234567")

    def test_detect_cpf_placeholder(self):
        from dakota_gateway.synthetic.template_engine import TemplateEngine

        inputs = ["12345678909"]
        placeholders = TemplateEngine.detect_placeholders(inputs)
        self.assertIn("{{cliente.cpf}}", placeholders)

    def test_detect_phone_placeholder(self):
        from dakota_gateway.synthetic.template_engine import TemplateEngine

        inputs = ["(11) 99999-8888"]
        placeholders = TemplateEngine.detect_placeholders(inputs)
        self.assertIn("{{cliente.telefone}}", placeholders)

    def test_detect_email_placeholder(self):
        from dakota_gateway.synthetic.template_engine import TemplateEngine

        inputs = ["joao@email.com.br"]
        placeholders = TemplateEngine.detect_placeholders(inputs)
        self.assertIn("{{cliente.email}}", placeholders)

    def test_render_batch_generates_different_values(self):
        from dakota_gateway.synthetic.template_engine import TemplateEngine

        templates = ["{{nome}}"]
        dataset = [{"nome": "ANA"}, {"nome": "BRUNO"}, {"nome": "CARLA"}]
        results = TemplateEngine.render_batch(templates, dataset)
        self.assertEqual(len(results), 3)
        self.assertEqual(results[0][0], "ANA")
        self.assertEqual(results[1][0], "BRUNO")
        self.assertEqual(results[2][0], "CARLA")

    def test_extract_entities(self):
        from dakota_gateway.synthetic.template_engine import TemplateEngine

        templates = ["{{cliente.nome}}", "{{cliente.cpf}}", "{{produto.codigo}}"]
        entities = TemplateEngine.extract_entities(templates)
        self.assertSetEqual(entities, {"cliente", "produto"})


if __name__ == "__main__":
    unittest.main()

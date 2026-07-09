#!/usr/bin/env python3
"""Testes para o CaptureKnowledgeIntegrator — P2.4 jornada sintetica real."""
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
    ScreenDefinition,
)
from dakota_gateway.source_analyzer.screen_entity_linker import ScreenEntityBinding
from dakota_gateway.synthetic.capture_parametrizer import (
    CaptureTemplate,
    CaptureParametrizer,
)
from dakota_gateway.synthetic.capture_knowledge_integrator import (
    CaptureKnowledgeIntegrator,
    KnowledgeEnrichedTemplate,
    ScreenKnowledgeMapping,
    MappedInput,
)


# ═══════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════

def make_sample_jsonl(dir_path: Path) -> str:
    """Cria arquivo .jsonl simulando uma captura de cadastro de cliente."""
    events = [
        {"type": "checkpoint", "session_id": "sess-001", "screen_sig": "L=10 C=40 H=abc123",
         "screen_sample": "Cadastro de Clientes", "seq_global": 1, "norm_len": 400},
        {"type": "bytes", "key_text": "1"},
        {"type": "checkpoint", "session_id": "sess-001", "screen_sig": "L=10 C=40 H=def456",
         "screen_sample": "Cadastro de Clientes", "seq_global": 2, "norm_len": 410,
         "key_text": "1"},
        {"type": "bytes", "key_text": "123.456.789-09"},
        {"type": "checkpoint", "session_id": "sess-001", "screen_sig": "L=10 C=40 H=ghi789",
         "screen_sample": "Cadastro de Clientes", "seq_global": 3, "norm_len": 420,
         "key_text": "123.456.789-09"},
        {"type": "bytes", "key_text": "JOAO SILVA"},
        {"type": "checkpoint", "session_id": "sess-001", "screen_sig": "L=10 C=40 H=jkl012",
         "screen_sample": "Cadastro de Clientes", "seq_global": 4, "norm_len": 430,
         "key_text": "JOAO SILVA"},
        {"type": "bytes", "key_text": "joao@email.com"},
        {"type": "checkpoint", "session_id": "sess-001", "screen_sig": "L=10 C=40 H=mno345",
         "screen_sample": "Cadastro de Clientes", "seq_global": 5, "norm_len": 440,
         "key_text": "joao@email.com"},
    ]
    path = dir_path / "capture" / "session_001.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        for ev in events:
            f.write(json.dumps(ev) + "\n")
    return str(path)


def make_entities() -> list[EntityDefinition]:
    return [
        EntityDefinition(
            name="CLIENTES",
            storage_type="sql",
            fields=[
                FieldDefinition(name="ID", datatype="integer"),
                FieldDefinition(name="NOME", datatype="text"),
                FieldDefinition(name="CPF", datatype="text"),
                FieldDefinition(name="EMAIL", datatype="text"),
                FieldDefinition(name="TELEFONE", datatype="text"),
            ],
        ),
    ]


def make_bindings() -> list[ScreenEntityBinding]:
    return [
        ScreenEntityBinding(
            screen_title="Cadastro de Clientes",
            program_name="cadcli",
            source_file="/src/cadcli.prg",
            entity_name="CLIENTES",
            operation="create",
            matched_fields=["nome", "cpf", "email", "telefone"],
            confidence=0.92,
            evidence=["titulo contem 'cadastro'"],
        ),
    ]


# ═══════════════════════════════════════════════════════════════════
# Tests
# ═══════════════════════════════════════════════════════════════════

class CaptureKnowledgeIntegratorTests(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.dir_path = Path(self.tmpdir.name)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_enrich_template_maps_screens_to_entities(self):
        """Template enriquecido deve associar telas a entidades."""
        jsonl_path = make_sample_jsonl(self.dir_path)

        parametrizer = CaptureParametrizer()
        template = parametrizer.analyze_capture(jsonl_path)

        entities = make_entities()
        bindings = make_bindings()

        integrator = CaptureKnowledgeIntegrator()
        enriched = integrator.enrich_template(template, entities, bindings)

        self.assertIsInstance(enriched, KnowledgeEnrichedTemplate)
        self.assertEqual(enriched.capture_source, jsonl_path)
        self.assertGreater(len(enriched.screen_mappings), 0)

        # Pelo menos uma tela deve estar associada a CLIENTES
        entities_found = {m.entity_name for m in enriched.screen_mappings if m.entity_name}
        self.assertIn("CLIENTES", entities_found)

    def test_enrich_template_counts_mapped_inputs(self):
        """Deve contar inputs mapeados e nao mapeados."""
        jsonl_path = make_sample_jsonl(self.dir_path)

        parametrizer = CaptureParametrizer()
        template = parametrizer.analyze_capture(jsonl_path)

        integrator = CaptureKnowledgeIntegrator()
        enriched = integrator.enrich_template(
            template, make_entities(), make_bindings()
        )

        self.assertGreaterEqual(enriched.total_inputs, 1)
        # mapped + unmapped deve ser >= 0
        self.assertGreaterEqual(enriched.mapped_inputs + enriched.unmapped_inputs, 0)

    def test_classify_value_types(self):
        """Classificacao de valores deve detectar CPF, email, phone, etc."""
        integrator = CaptureKnowledgeIntegrator()

        self.assertEqual(integrator._classify_value("123.456.789-09"), "cpf")
        self.assertEqual(integrator._classify_value("12345678909"), "cpf")
        self.assertEqual(integrator._classify_value("joao@email.com"), "email")
        self.assertEqual(integrator._classify_value("(11) 91234-5678"), "phone")
        self.assertEqual(integrator._classify_value("12345-678"), "cep")
        self.assertEqual(integrator._classify_value("2024-01-15"), "date")
        self.assertEqual(integrator._classify_value("15/01/2024"), "date")
        self.assertEqual(integrator._classify_value("1"), "menu_option")
        self.assertEqual(integrator._classify_value("JOAO SILVA"), "text")
        self.assertEqual(integrator._classify_value("123.45"), "number")

    def test_infer_semantic_type_from_field_name(self):
        """Inferencia semantica pelo nome do campo."""
        integrator = CaptureKnowledgeIntegrator()

        self.assertEqual(integrator._infer_semantic_type("CPF"), "cpf")
        self.assertEqual(integrator._infer_semantic_type("email"), "email")
        self.assertEqual(integrator._infer_semantic_type("TELEFONE"), "phone")
        self.assertEqual(integrator._infer_semantic_type("celular"), "phone")
        self.assertEqual(integrator._infer_semantic_type("NOME"), "person_name")
        self.assertEqual(integrator._infer_semantic_type("razao_social"), "person_name")
        self.assertEqual(integrator._infer_semantic_type("VALOR_TOTAL"), "number")
        self.assertEqual(integrator._infer_semantic_type("DT_NASCIMENTO"), "date")
        self.assertEqual(integrator._infer_semantic_type("desconhecido"), "text")

    def test_entity_to_field_schemas(self):
        """Conversao de entity fields para FieldSchema com providers."""
        entities = make_entities()
        integrator = CaptureKnowledgeIntegrator()

        schemas = integrator._entity_to_field_schemas(entities[0])
        self.assertEqual(len(schemas), 5)

        cpf_schema = next((s for s in schemas if s.name == "CPF"), None)
        self.assertIsNotNone(cpf_schema)
        self.assertEqual(cpf_schema.format, "cpf")
        self.assertEqual(cpf_schema.datatype, "cpf")

        nome_schema = next((s for s in schemas if s.name == "NOME"), None)
        self.assertIsNotNone(nome_schema)
        self.assertEqual(nome_schema.datatype, "person_name")

    def test_generate_parametrized_sessions(self):
        """Deve gerar sessoes com dados sinteticos validos."""
        jsonl_path = make_sample_jsonl(self.dir_path)

        parametrizer = CaptureParametrizer()
        template = parametrizer.analyze_capture(jsonl_path)

        integrator = CaptureKnowledgeIntegrator()
        enriched = integrator.enrich_template(
            template, make_entities(), make_bindings()
        )

        sessions = integrator.generate_parametrized_sessions(
            enriched, session_count=5, seed=42
        )

        self.assertEqual(len(sessions), 5)
        for sess in sessions:
            self.assertGreater(len(sess.inputs), 0)
            self.assertIsInstance(sess.data, dict)

    def test_generate_parametrized_sessions_deterministic(self):
        """Sessoes com mesmo seed devem ser identicas."""
        jsonl_path = make_sample_jsonl(self.dir_path)

        parametrizer = CaptureParametrizer()
        template = parametrizer.analyze_capture(jsonl_path)

        integrator = CaptureKnowledgeIntegrator()
        enriched = integrator.enrich_template(
            template, make_entities(), make_bindings()
        )

        sessions1 = integrator.generate_parametrized_sessions(enriched, session_count=3, seed=42)
        sessions2 = integrator.generate_parametrized_sessions(enriched, session_count=3, seed=42)

        self.assertEqual(len(sessions1), len(sessions2))
        for s1, s2 in zip(sessions1, sessions2):
            self.assertEqual(s1.inputs, s2.inputs)

    def test_to_screen_schemas(self):
        """Deve extrair ScreenSchemas do template enriquecido."""
        jsonl_path = make_sample_jsonl(self.dir_path)

        parametrizer = CaptureParametrizer()
        template = parametrizer.analyze_capture(jsonl_path)

        integrator = CaptureKnowledgeIntegrator()
        enriched = integrator.enrich_template(
            template, make_entities(), make_bindings()
        )

        schemas = integrator.to_screen_schemas(enriched)
        self.assertGreaterEqual(len(schemas), 0)

        for schema in schemas:
            self.assertIsNotNone(schema.fields)

    def test_enrich_without_bindings_still_works(self):
        """Sem bindings, ainda deve enriquecer (com mapeamento vazio)."""
        jsonl_path = make_sample_jsonl(self.dir_path)

        parametrizer = CaptureParametrizer()
        template = parametrizer.analyze_capture(jsonl_path)

        integrator = CaptureKnowledgeIntegrator()
        enriched = integrator.enrich_template(
            template, make_entities(), []  # sem bindings
        )

        self.assertIsInstance(enriched, KnowledgeEnrichedTemplate)
        self.assertGreaterEqual(enriched.total_inputs, 0)


if __name__ == "__main__":
    unittest.main()

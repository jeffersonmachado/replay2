#!/usr/bin/env python3
"""Testa relatorio detalhado de capture_knowledge."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GATEWAY_DIR = ROOT / "gateway"
sys.path.insert(0, str(GATEWAY_DIR))

from dakota_gateway.source_analyzer.entity_catalog import EntityDefinition, FieldDefinition
from dakota_gateway.source_analyzer.screen_entity_linker import ScreenEntityBinding
from dakota_gateway.synthetic.capture_parametrizer import CaptureParametrizer
from dakota_gateway.synthetic.capture_knowledge_integrator import CaptureKnowledgeIntegrator


class CaptureKnowledgeReportTests(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.d = Path(self.tmpdir.name)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_report_has_screen_mappings(self):
        """Relatorio deve conter screen_mappings com placeholders."""
        events = [
            {"type": "checkpoint", "session_id": "s1", "screen_sig": "S1",
             "screen_sample": "Cadastro de Clientes", "seq_global": 1, "norm_len": 400},
            {"type": "bytes", "key_text": "123.456.789-09"},
            {"type": "bytes", "key_text": "JOAO CLIENTE"},
            {"type": "checkpoint", "session_id": "s1", "screen_sig": "S2",
             "screen_sample": "Cadastro de Produtos", "seq_global": 2, "norm_len": 410},
            {"type": "bytes", "key_text": "PRODUTO TESTE"},
        ]
        p = self.d / "cap.jsonl"
        p.write_text("\n".join(json.dumps(e) for e in events))

        cp = CaptureParametrizer()
        template = cp.analyze_capture(str(p))

        entities = [
            EntityDefinition(name="CLIENTES", fields=[
                FieldDefinition(name="cpf"), FieldDefinition(name="nome"),
            ]),
            EntityDefinition(name="PRODUTOS", fields=[
                FieldDefinition(name="descricao"), FieldDefinition(name="valor"),
            ]),
        ]
        bindings = [
            ScreenEntityBinding(screen_title="Cadastro de Clientes", entity_name="CLIENTES",
                matched_fields=["cpf", "nome"], confidence=0.9, evidence=["test"]),
            ScreenEntityBinding(screen_title="Cadastro de Produtos", entity_name="PRODUTOS",
                matched_fields=["descricao", "valor"], confidence=0.9, evidence=["test"]),
        ]

        integrator = CaptureKnowledgeIntegrator()
        enriched = integrator.enrich_template(template, entities, bindings)

        # Verifica entidades envolvidas
        self.assertIn("CLIENTES", enriched.entities_involved)
        self.assertIn("PRODUTOS", enriched.entities_involved)

        # Verifica screen mappings
        self.assertEqual(len(enriched.screen_mappings), 2)
        self.assertEqual(enriched.screen_mappings[0].entity_name, "CLIENTES")
        self.assertEqual(enriched.screen_mappings[1].entity_name, "PRODUTOS")

    def test_report_entities_involved_multiple(self):
        """entities_involved deve incluir multiplas entidades."""
        events = [
            {"type": "checkpoint", "session_id": "s1", "screen_sig": "S1",
             "screen_sample": "Cadastro de Clientes", "seq_global": 1, "norm_len": 400},
            {"type": "bytes", "key_text": "123"},
            {"type": "checkpoint", "session_id": "s1", "screen_sig": "S2",
             "screen_sample": "Cadastro de Produtos", "seq_global": 2, "norm_len": 410},
            {"type": "bytes", "key_text": "ABC"},
        ]
        p = self.d / "cap2.jsonl"
        p.write_text("\n".join(json.dumps(e) for e in events))

        cp = CaptureParametrizer()
        template = cp.analyze_capture(str(p))

        entities = [
            EntityDefinition(name="CLIENTES", fields=[FieldDefinition(name="cpf")]),
            EntityDefinition(name="PRODUTOS", fields=[FieldDefinition(name="descricao")]),
        ]
        bindings = [
            ScreenEntityBinding(screen_title="Cadastro de Clientes", entity_name="CLIENTES",
                matched_fields=["cpf"], confidence=0.9, evidence=["test"]),
            ScreenEntityBinding(screen_title="Cadastro de Produtos", entity_name="PRODUTOS",
                matched_fields=["descricao"], confidence=0.9, evidence=["test"]),
        ]

        integrator = CaptureKnowledgeIntegrator()
        enriched = integrator.enrich_template(template, entities, bindings)

        self.assertEqual(len(enriched.entities_involved), 2)
        self.assertIn("CLIENTES", enriched.entities_involved)
        self.assertIn("PRODUTOS", enriched.entities_involved)

    # ── v0.2.2: Contadores de comandos ──

    def test_command_counters(self):
        """Comandos incrementam command_inputs, nao mapped_inputs nem unmapped_inputs."""
        events = [
            {"type": "checkpoint", "session_id": "s1", "screen_sig": "S1",
             "screen_sample": "Cadastro de Clientes", "seq_global": 1, "norm_len": 400},
            {"type": "bytes", "key_text": "{KEY:ENTER}"},
            {"type": "bytes", "key_text": "123.456.789-09"},
            {"type": "bytes", "key_text": "JOAO CLIENTE"},
        ]
        p = self.d / "cap.jsonl"
        p.write_text("\n".join(json.dumps(e) for e in events))

        cp = CaptureParametrizer()
        template = cp.analyze_capture(str(p))

        entities = [EntityDefinition(name="CLIENTES", storage_type="sql",
            fields=[FieldDefinition(name="CPF"), FieldDefinition(name="NOME")])]
        bindings = [ScreenEntityBinding(
            screen_title="Cadastro de Clientes", entity_name="CLIENTES",
            matched_fields=["CPF", "NOME"], confidence=0.9, evidence=["test"])]

        integrator = CaptureKnowledgeIntegrator()
        enriched = integrator.enrich_template(template, entities, bindings)

        # total_inputs inclui comando
        self.assertEqual(enriched.total_inputs, 3)
        # mapped_inputs nao inclui comando
        self.assertEqual(enriched.mapped_inputs, 2)
        # command_inputs inclui comando
        self.assertEqual(enriched.command_inputs, 1)
        # unmapped_inputs nao inclui comando
        self.assertEqual(enriched.unmapped_inputs, 0)

        # screen_mappings inputs incluem comando com method="command"
        sm = enriched.screen_mappings[0]
        cmd_inputs = [mi for mi in sm.mapped_inputs if mi.method == "command"]
        self.assertEqual(len(cmd_inputs), 1)

        # Contadores por tela
        self.assertEqual(sm.total_inputs, 3, "total_inputs da tela")
        self.assertEqual(sm.mapped_count, 2, "mapped_count da tela")
        self.assertEqual(sm.command_count, 1, "command_count da tela")
        self.assertEqual(sm.unmapped_count, 0, "unmapped_count da tela")

        # Comando: field_name=None, placeholder=None, confidence=1.0
        cmd = cmd_inputs[0]
        self.assertEqual(cmd.method, "command")
        self.assertEqual(cmd.field_name, "")
        self.assertEqual(cmd.placeholder, "")
        self.assertEqual(cmd.confidence, 1.0)

    def test_per_screen_counters_with_command(self):
        """Contadores por tela: comando + 3 campos mapeados."""
        events = [
            {"type": "checkpoint", "session_id": "s1", "screen_sig": "S1",
             "screen_sample": "Cadastro de Clientes", "seq_global": 1, "norm_len": 400},
            {"type": "bytes", "key_text": "{KEY:ENTER}"},
            {"type": "bytes", "key_text": "123.456.789-09"},
            {"type": "bytes", "key_text": "JOAO CLIENTE"},
            {"type": "bytes", "key_text": "RUA TESTE"},
        ]
        p = self.d / "cap4.jsonl"
        p.write_text("\n".join(json.dumps(e) for e in events))

        cp = CaptureParametrizer()
        template = cp.analyze_capture(str(p))

        entities = [EntityDefinition(name="CLIENTES", storage_type="sql",
            fields=[FieldDefinition(name="CPF"), FieldDefinition(name="NOME"),
                    FieldDefinition(name="ENDERECO")])]
        bindings = [ScreenEntityBinding(
            screen_title="Cadastro de Clientes", entity_name="CLIENTES",
            matched_fields=["CPF", "NOME", "ENDERECO"], confidence=0.9, evidence=["test"])]

        integrator = CaptureKnowledgeIntegrator()
        enriched = integrator.enrich_template(template, entities, bindings)

        # Contadores globais
        self.assertEqual(enriched.total_inputs, 4)
        self.assertEqual(enriched.mapped_inputs, 3)
        self.assertEqual(enriched.command_inputs, 1)
        self.assertEqual(enriched.unmapped_inputs, 0)

        # Contadores por tela
        sm = enriched.screen_mappings[0]
        self.assertEqual(sm.total_inputs, 4, "total_inputs da tela")
        self.assertEqual(sm.mapped_count, 3, "mapped_count da tela")
        self.assertEqual(sm.command_count, 1, "command_count da tela")
        self.assertEqual(sm.unmapped_count, 0, "unmapped_count da tela")

        # Comando tem method=command, field_name vazio, placeholder vazio
        cmd = [mi for mi in sm.mapped_inputs if mi.method == "command"]
        self.assertEqual(len(cmd), 1)
        self.assertEqual(cmd[0].field_name, "")
        self.assertEqual(cmd[0].placeholder, "")
        self.assertEqual(cmd[0].confidence, 1.0)


if __name__ == "__main__":
    unittest.main()

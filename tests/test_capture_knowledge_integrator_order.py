#!/usr/bin/env python3
"""Testes de ordem e preservacao no CaptureKnowledgeIntegrator."""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GATEWAY_DIR = ROOT / "gateway"
sys.path.insert(0, str(GATEWAY_DIR))

from dakota_gateway.source_analyzer.entity_catalog import (
    EntityDefinition, FieldDefinition,
)
from dakota_gateway.source_analyzer.screen_entity_linker import ScreenEntityBinding
from dakota_gateway.synthetic.capture_parametrizer import CaptureParametrizer
from dakota_gateway.synthetic.capture_knowledge_integrator import (
    CaptureKnowledgeIntegrator, KnowledgeEnrichedTemplate, MappedInput,
)


class CaptureKnowledgeIntegratorOrderTests(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.d = Path(self.tmpdir.name)

    def tearDown(self):
        self.tmpdir.cleanup()

    def _make_jsonl(self, inputs: list[str], screen_title: str = "Cadastro de Clientes") -> str:
        events = []
        for i, inp in enumerate(inputs):
            events.append({
                "type": "checkpoint", "session_id": "sess-001",
                "screen_sig": f"SIG_{i}", "screen_sample": screen_title,
                "seq_global": i + 1, "norm_len": 400 + i,
            })
            events.append({"type": "bytes", "key_text": inp})
        p = self.d / "cap.jsonl"
        p.write_text("\n".join(json.dumps(e) for e in events))
        return str(p)

    def test_preserve_input_order(self):
        """Ordem original dos inputs deve ser preservada."""
        original = ["1", "123.456.789-09", "JOAO SILVA", "joao@email.com", "F10"]
        jsonl = self._make_jsonl(original)

        cp = CaptureParametrizer()
        template = cp.analyze_capture(jsonl)

        entities = [EntityDefinition(
            name="CLIENTES", storage_type="sql",
            fields=[
                FieldDefinition(name="CPF"), FieldDefinition(name="NOME"),
                FieldDefinition(name="EMAIL"),
            ],
        )]
        bindings = [ScreenEntityBinding(
            screen_title="Cadastro de Clientes", entity_name="CLIENTES",
            matched_fields=["cpf", "nome", "email"], confidence=0.9,
            evidence=["test"],
        )]

        integrator = CaptureKnowledgeIntegrator()
        enriched = integrator.enrich_template(template, entities, bindings)

        sessions = integrator.generate_parametrized_sessions(enriched, session_count=1, seed=42)
        self.assertEqual(len(sessions), 1)

        # "1" (menu) e "F10" (tecla) devem ser mantidos como estavam
        # CPF, nome, email devem ser substituidos
        result = sessions[0].inputs
        self.assertEqual(len(result), len(original),
                         f"Numero de inputs diferente: {len(result)} vs {len(original)}")

        # Menu option e F10 devem ser preservados (nao substituidos)
        # CPF deve ser diferente do original
        self.assertNotEqual(result[1], original[1], "CPF deveria ser substituido")

    def test_menu_options_preserved(self):
        """Opcoes de menu (1, 2, 3...) e ENTER/F10 devem ser mantidos."""
        original = ["1", "F10"]
        jsonl = self._make_jsonl(original)

        cp = CaptureParametrizer()
        template = cp.analyze_capture(jsonl)

        entities = [EntityDefinition(name="MENU", storage_type="sql",
            fields=[FieldDefinition(name="OPCAO")])]
        bindings = []

        integrator = CaptureKnowledgeIntegrator()
        enriched = integrator.enrich_template(template, entities, bindings)

        sessions = integrator.generate_parametrized_sessions(enriched, session_count=2, seed=42)
        self.assertEqual(len(sessions), 2)

    def test_seed_deterministic_across_processes(self):
        """Seed deve ser deterministico entre execucoes (usando hashlib, nao hash())."""
        entities = [EntityDefinition(
            name="CLIENTES", storage_type="sql",
            fields=[FieldDefinition(name="NOME"), FieldDefinition(name="CPF")],
        )]
        bindings = [ScreenEntityBinding(
            screen_title="Tela", entity_name="CLIENTES",
            matched_fields=["nome", "cpf"], confidence=0.9, evidence=["test"],
        )]

        integrator1 = CaptureKnowledgeIntegrator()
        integrator2 = CaptureKnowledgeIntegrator()

        # Criar template minimo
        jsonl = self._make_jsonl(["JOAO SILVA", "123.456.789-09"])
        cp = CaptureParametrizer()
        template = cp.analyze_capture(jsonl)

        enriched1 = integrator1.enrich_template(template, entities, bindings)
        enriched2 = integrator2.enrich_template(template, entities, bindings)

        s1 = integrator1.generate_parametrized_sessions(enriched1, session_count=3, seed=42)
        s2 = integrator2.generate_parametrized_sessions(enriched2, session_count=3, seed=42)

        # Mesmos inputs devem ser gerados (deterministico)
        for i in range(3):
            self.assertEqual(s1[i].inputs, s2[i].inputs,
                             f"Sessao {i} difere entre execucoes")

    def test_only_mapped_fields_become_placeholders(self):
        """Apenas campos mapeados devem virar placeholders — menu e teclas nao."""
        integrator = CaptureKnowledgeIntegrator()

        # Verifica que "1" (menu_option) e "F10" (text nao match) nao viram placeholder
        mi_menu = integrator._map_input_to_field(
            0, "1",
            EntityDefinition(name="E", fields=[FieldDefinition(name="CAMPO")]),
            {"CAMPO": FieldDefinition(name="CAMPO")},
        )
        self.assertEqual(mi_menu.field_name, "",
                         "Opcao de menu nao deve ser mapeada a campo")

        # "123.456.789-09" deve ser mapeado se houver campo CPF
        mi_cpf = integrator._map_input_to_field(
            1, "123.456.789-09",
            EntityDefinition(name="CLIENTES", fields=[
                FieldDefinition(name="NOME"), FieldDefinition(name="CPF"),
            ]),
            {"NOME": FieldDefinition(name="NOME"), "CPF": FieldDefinition(name="CPF")},
        )
        self.assertEqual(mi_cpf.field_name, "CPF",
                         "CPF deveria ser mapeado ao campo CPF")

    # ── v0.2.1: Multi-entidade e preservação de teclas ──

    def test_two_screens_two_entities(self):
        """Captura com duas telas deve ter duas entidades envolvidas."""
        events = [
            {"type": "checkpoint", "session_id": "s-1", "screen_sig": "S1",
             "screen_sample": "Cadastro de Clientes", "seq_global": 1, "norm_len": 400},
            {"type": "bytes", "key_text": "123.456.789-09"},
            {"type": "checkpoint", "session_id": "s-1", "screen_sig": "S2",
             "screen_sample": "Cadastro de Produtos", "seq_global": 2, "norm_len": 410},
            {"type": "bytes", "key_text": "PRODUTO TESTE"},
        ]
        p = self.d / "multi.jsonl"
        p.write_text("\n".join(json.dumps(e) for e in events))

        cp = CaptureParametrizer()
        template = cp.analyze_capture(str(p))

        entities = [
            EntityDefinition(name="CLIENTES", storage_type="sql",
                fields=[FieldDefinition(name="CPF"), FieldDefinition(name="NOME")]),
            EntityDefinition(name="PRODUTOS", storage_type="sql",
                fields=[FieldDefinition(name="DESCRICAO")]),
        ]
        bindings = [
            ScreenEntityBinding(screen_title="Cadastro de Clientes", entity_name="CLIENTES",
                matched_fields=["cpf"], confidence=0.9, evidence=["test"]),
            ScreenEntityBinding(screen_title="Cadastro de Produtos", entity_name="PRODUTOS",
                matched_fields=["descricao"], confidence=0.9, evidence=["test"]),
        ]

        integrator = CaptureKnowledgeIntegrator()
        enriched = integrator.enrich_template(template, entities, bindings)

        self.assertGreaterEqual(len(enriched.entities_involved), 1)
        self.assertGreaterEqual(enriched.total_inputs, 1)

    def test_preserve_enter_tab_esc_f10(self):
        """ENTER, TAB, ESC, F10 devem ser preservados."""
        original = ["1", "\r", "12345678901", "\t", "JOAO", "\x1b", "F10"]
        jsonl = self._make_jsonl(original)

        cp = CaptureParametrizer()
        template = cp.analyze_capture(jsonl)

        entities = [EntityDefinition(name="CLIENTES", storage_type="sql",
            fields=[FieldDefinition(name="CPF"), FieldDefinition(name="NOME")])]
        bindings = [ScreenEntityBinding(screen_title="Cadastro de Clientes", entity_name="CLIENTES",
            matched_fields=["cpf", "nome"], confidence=0.9, evidence=["test"])]

        integrator = CaptureKnowledgeIntegrator()
        enriched = integrator.enrich_template(template, entities, bindings)
        sessions = integrator.generate_parametrized_sessions(enriched, session_count=1, seed=42)
        self.assertEqual(len(sessions), 1)
        result = sessions[0].inputs
        self.assertEqual(len(result), len(original),
                         f"Perdeu inputs: {len(result)} vs {len(original)}")

    # ── v0.2.2: Command-aware data_field_index ──

    def test_command_does_not_shift_matched_fields(self):
        """Comando nao consome posicao de matched_fields."""
        # Todos os inputs na mesma tela (um unico checkpoint)
        events = [
            {"type": "checkpoint", "session_id": "sess-001",
             "screen_sig": "SIG_0", "screen_sample": "Cadastro de Clientes",
             "seq_global": 1, "norm_len": 400},
            {"type": "bytes", "key_text": "\r"},
            {"type": "bytes", "key_text": "JOAO CLIENTE"},
            {"type": "bytes", "key_text": "RUA TESTE"},
        ]
        p = self.d / "cap_cmd.jsonl"
        p.write_text("\n".join(json.dumps(e) for e in events))
        jsonl = str(p)

        cp = CaptureParametrizer()
        template = cp.analyze_capture(jsonl)

        entities = [EntityDefinition(name="CLIENTES", storage_type="sql",
            fields=[FieldDefinition(name="NOME"), FieldDefinition(name="ENDERECO")])]
        bindings = [ScreenEntityBinding(
            screen_title="Cadastro de Clientes", entity_name="CLIENTES",
            matched_fields=["NOME", "ENDERECO"], confidence=0.9, evidence=["test"])]

        integrator = CaptureKnowledgeIntegrator()
        enriched = integrator.enrich_template(template, entities, bindings)

        self.assertEqual(len(enriched.screen_mappings), 1)
        sm = enriched.screen_mappings[0]

        # 3 inputs: comando + 2 campos
        self.assertEqual(len(sm.mapped_inputs), 3)
        self.assertEqual(enriched.total_inputs, 3)

        # Comando
        self.assertEqual(sm.mapped_inputs[0].method, "command")
        self.assertEqual(sm.mapped_inputs[0].field_name, "")

        # ENTER nao consome posicao → JOAO CLIENTE → NOME (pos 0)
        self.assertEqual(sm.mapped_inputs[1].field_name, "NOME")

        # RUA TESTE → ENDERECO (pos 1)
        self.assertEqual(sm.mapped_inputs[2].field_name, "ENDERECO")

        # Contadores: comando nao conta como mapped nem unmapped
        self.assertEqual(enriched.mapped_inputs, 2)
        self.assertEqual(enriched.command_inputs, 1)
        self.assertEqual(enriched.unmapped_inputs, 0)

    def test_placeholder_preserves_field_name_case(self):
        """Placeholder deve preservar FieldDefinition.name (minusculas ou maiusculas)."""
        # Todos os inputs na mesma tela
        events = [
            {"type": "checkpoint", "session_id": "sess-001",
             "screen_sig": "SIG_0", "screen_sample": "Cadastro de Clientes",
             "seq_global": 1, "norm_len": 400},
            {"type": "bytes", "key_text": "123.456.789-09"},
            {"type": "bytes", "key_text": "JOAO CLIENTE"},
            {"type": "bytes", "key_text": "RUA TESTE"},
        ]
        p = self.d / "cap_ph.jsonl"
        p.write_text("\n".join(json.dumps(e) for e in events))
        jsonl = str(p)

        cp = CaptureParametrizer()
        template = cp.analyze_capture(jsonl)

        entities = [EntityDefinition(name="CLIENTES", storage_type="sql",
            fields=[
                FieldDefinition(name="cpf"),
                FieldDefinition(name="nome"),
                FieldDefinition(name="endereco"),
            ])]
        bindings = [ScreenEntityBinding(
            screen_title="Cadastro de Clientes", entity_name="CLIENTES",
            matched_fields=["cpf", "nome", "endereco"], confidence=0.9, evidence=["test"])]

        integrator = CaptureKnowledgeIntegrator()
        enriched = integrator.enrich_template(template, entities, bindings)

        sm = enriched.screen_mappings[0]

        # Verifica que os placeholders usam field.name original (minusculo)
        cpf_inputs = [mi for mi in sm.mapped_inputs if mi.field_name == "cpf"]
        self.assertGreaterEqual(len(cpf_inputs), 1)
        self.assertEqual(cpf_inputs[0].placeholder, "{{CLIENTES.cpf}}")

        nome_inputs = [mi for mi in sm.mapped_inputs if mi.field_name == "nome"]
        self.assertGreaterEqual(len(nome_inputs), 1)
        self.assertEqual(nome_inputs[0].placeholder, "{{CLIENTES.nome}}")

        end_inputs = [mi for mi in sm.mapped_inputs if mi.field_name == "endereco"]
        self.assertGreaterEqual(len(end_inputs), 1)
        self.assertEqual(end_inputs[0].placeholder, "{{CLIENTES.endereco}}")


if __name__ == "__main__":
    unittest.main()

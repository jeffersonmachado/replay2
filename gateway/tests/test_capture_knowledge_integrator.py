"""Testes para CaptureKnowledgeIntegrator — data_field_index, comandos, multi-campo, minúsculas."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

GATEWAY_DIR = str(Path(__file__).resolve().parents[1])
if GATEWAY_DIR not in sys.path:
    sys.path.insert(0, GATEWAY_DIR)

from dakota_gateway.synthetic.capture_knowledge_integrator import (
    CaptureKnowledgeIntegrator,
    MappedInput,
)
from dakota_gateway.synthetic.capture_parametrizer import CaptureTemplate
from dakota_gateway.source_analyzer.screen_entity_linker import ScreenEntityBinding
from dakota_gateway.source_analyzer.entity_catalog import EntityDefinition, FieldDefinition


# ── Fixtures ──

@pytest.fixture
def integrator() -> CaptureKnowledgeIntegrator:
    return CaptureKnowledgeIntegrator()


@pytest.fixture
def entity_cliente() -> EntityDefinition:
    return EntityDefinition(
        name="CLIENTE",
        storage_type="sql",
        fields=[
            FieldDefinition(name="NOME", datatype="varchar", required=True),
            FieldDefinition(name="CPF", datatype="varchar", required=True),
            FieldDefinition(name="EMAIL", datatype="varchar"),
            FieldDefinition(name="ENDERECO", datatype="varchar"),
        ],
    )


@pytest.fixture
def binding_cliente() -> ScreenEntityBinding:
    return ScreenEntityBinding(
        program_name="cadcli.prg",
        entity_name="CLIENTE",
        operation="cadastro",
        confidence=0.85,
        matched_fields=["NOME", "CPF", "EMAIL", "ENDERECO"],
        screen_title="Cadastro de Clientes",
    )


@pytest.fixture
def capture_simple() -> CaptureTemplate:
    return CaptureTemplate(
        capture_source="test.jsonl",
        session_id="sessao-001",
        screen_sequence=["abc123"],
        input_templates=["João Silva", "123.456.789-00", "{KEY:ENTER}"],  # 3 campos + 1 comando
        screen_contexts=[{"screen_signature": "abc123", "inputs": ["João Silva", "123.456.789-00", "{KEY:ENTER}"]}],
    )


@pytest.fixture
def capture_multi_screen() -> CaptureTemplate:
    return CaptureTemplate(
        capture_source="test.jsonl",
        session_id="sessao-002",
        screen_sequence=["sig1", "sig2"],
        input_templates=["João Silva", "123.456.789-00", {KEY:ENTER}, "joao@email.com", "Rua A, 123"],
        screen_contexts=[
            {"screen_signature": "sig1", "inputs": ["João Silva", "123.456.789-00", "{KEY:ENTER}"]},
            {"screen_signature": "sig2", "inputs": ["joao@email.com", "Rua A, 123"]},
        ],
    )


@pytest.fixture
def capture_lowercase_fields() -> CaptureTemplate:
    return CaptureTemplate(
        capture_source="test.jsonl",
        session_id="sessao-003",
        screen_sequence=["sig1"],
        input_templates=["joao silva", "cpf_value"],
        screen_contexts=[{"screen_signature": "sig1", "inputs": ["joao silva", "cpf_value"]}],
    )


class TestIsCommand:
    def test_key_enter(self, integrator):
        assert integrator._is_command("{KEY:ENTER}") is True

    def test_key_f1(self, integrator):
        assert integrator._is_command("{KEY:F1}") is True

    def test_f_key_bare(self, integrator):
        assert integrator._is_command("F5") is True

    def test_escape_sequence(self, integrator):
        assert integrator._is_command("\x1b[A") is True  # cursor up

    def test_plain_text_is_not_command(self, integrator):
        assert integrator._is_command("João Silva") is False

    def test_number_is_not_command(self, integrator):
        assert integrator._is_command("12345") is False

    def test_cr_is_command(self, integrator):
        assert integrator._is_command("\r") is True


class TestDataFieldIndex:
    """data_field_index: comandos não consomem posição no matched_fields."""

    def test_command_skips_position(
        self, integrator, entity_cliente, binding_cliente
    ):
        """Se input[0] é comando, matched_fields[0] deve ser usado pelo primeiro campo de dados."""
        capture = CaptureTemplate(
            capture_source="test.jsonl",
            session_id="sessao-004",
            screen_sequence=["sig1"],
            input_templates=["{KEY:ENTER}", "João Silva", "123.456.789-00"],
            screen_contexts=[{
                "screen_signature": "sig1",
                "inputs": ["{KEY:ENTER}", "João Silva", "123.456.789-00"],
            }],
        )

        enriched = integrator.enrich_template(
            capture, [entity_cliente], [binding_cliente]
        )

        assert len(enriched.screen_mappings) == 1
        sm = enriched.screen_mappings[0]
        assert len(sm.mapped_inputs) == 3

        # Comando deve ser command, método="command"
        assert sm.mapped_inputs[0].method == "command"
        assert sm.mapped_inputs[0].original_type == "command"

        # João Silva → NOME (matched_fields[0] pois data_field_index=0)
        assert sm.mapped_inputs[1].field_name == "NOME"
        assert sm.mapped_inputs[1].method == "by_matched_fields"

        # CPF → CPF (matched_fields[1] pois data_field_index=1)
        assert sm.mapped_inputs[2].field_name == "CPF"
        assert sm.mapped_inputs[2].method == "by_semantic_type" or sm.mapped_inputs[2].method == "by_matched_fields"

    def test_multiple_commands_between_fields(
        self, integrator, entity_cliente, binding_cliente
    ):
        """Vários comandos entre campos não consomem posições extras."""
        capture = CaptureTemplate(
            capture_source="test.jsonl",
            session_id="sessao-005",
            screen_sequence=["sig1"],
            input_templates=["{KEY:ENTER}", "{KEY:F5}", "João Silva"],
            screen_contexts=[{
                "screen_signature": "sig1",
                "inputs": ["{KEY:ENTER}", "{KEY:F5}", "João Silva"],
            }],
        )

        enriched = integrator.enrich_template(
            capture, [entity_cliente], [binding_cliente]
        )

        sm = enriched.screen_mappings[0]
        # Primeiros 2 são comandos
        assert sm.mapped_inputs[0].method == "command"
        assert sm.mapped_inputs[1].method == "command"
        # Terceiro → NOME (data_field_index=0)
        assert sm.mapped_inputs[2].field_name == "NOME"

    def test_data_field_index_advances_after_unmapped(self, integrator, entity_cliente, binding_cliente):
        """Campo não mapeado ainda consome posição no data_field_index.
        
        Usamos binding sem matched_fields para forçar screen_order.
        O primeiro campo (pos 0) da entidade é NOME (texto), então 'XYZ' mapeia como NOME.
        Mas o segundo '123.456.789-00' deve ir para CPF via semântica, não para NOME novamente.
        """
        # Binding sem matched_fields: força screen_order em vez de matched_fields
        binding_no_match = ScreenEntityBinding(
            program_name="cadcli.prg",
            entity_name="CLIENTE",
            operation="cadastro",
            confidence=0.85,
            matched_fields=[],  # sem matched_fields
            screen_title="Cadastro de Clientes",
        )

        capture = CaptureTemplate(
            capture_source="test.jsonl",
            session_id="sessao-006",
            screen_sequence=["sig1"],
            input_templates=["XYZ_UNKNOWN", "123.456.789-00"],
            screen_contexts=[{
                "screen_signature": "sig1",
                "inputs": ["XYZ_UNKNOWN", "123.456.789-00"],
            }],
        )

        enriched = integrator.enrich_template(
            capture, [entity_cliente], [binding_no_match]
        )

        sm = enriched.screen_mappings[0]
        # XYZ_UNKNOWN → NOME via screen_order (pos 0), mas confiança pode ser baixa
        # CPF → CPF via semântica (reconhece formato)
        assert sm.mapped_inputs[1].field_name == "CPF"


class TestMultiFieldDedup:
    def test_fields_not_reused(self, integrator, entity_cliente, binding_cliente):
        """Campos já usados não são reutilizados."""
        capture = CaptureTemplate(
            capture_source="test.jsonl",
            session_id="sessao-007",
            screen_sequence=["sig1"],
            input_templates=["João Silva", "Maria Silva"],
            screen_contexts=[{
                "screen_signature": "sig1",
                "inputs": ["João Silva", "Maria Silva"],
            }],
        )

        enriched = integrator.enrich_template(
            capture, [entity_cliente], [binding_cliente]
        )

        sm = enriched.screen_mappings[0]
        # primeiro → NOME, segundo não pode ser NOME de novo
        names_used = [mi.field_name for mi in sm.mapped_inputs if mi.field_name == "NOME"]
        assert len(names_used) <= 1  # NOME não pode aparecer 2x
        # Pelo menos um mapeado
        assert any(mi.field_name for mi in sm.mapped_inputs)


class TestEnumrichTemplateStructure:
    def test_enriched_capture_structure(self, integrator, entity_cliente, binding_cliente, capture_simple):
        enriched = integrator.enrich_template(
            capture_simple, [entity_cliente], [binding_cliente]
        )

        assert enriched.capture_source == "test.jsonl"
        assert enriched.session_id == "sessao-001"
        assert enriched.total_inputs == 3  # 2 campos + 1 comando
        assert len(enriched.screen_mappings) >= 1
        assert "CLIENTE" in enriched.entities_involved

    def test_screen_mapping_fields(self, integrator, entity_cliente, binding_cliente, capture_simple):
        enriched = integrator.enrich_template(
            capture_simple, [entity_cliente], [binding_cliente]
        )

        sm = enriched.screen_mappings[0]
        assert sm.entity_name == "CLIENTE"
        assert sm.operation == "cadastro"
        assert sm.binding_confidence == 0.85
        assert len(sm.mapped_inputs) == 3
        # matched_fields disponíveis
        assert "NOME" in sm.matched_fields
        assert "CPF" in sm.matched_fields

    def test_no_entity_capture(self, integrator):
        """Captura sem entidade vinculada: todos inputs = unmapped."""
        entity_empty = EntityDefinition(
            name="VAZIO",
            storage_type="sql",
            fields=[],
        )
        binding = ScreenEntityBinding(
            program_name="vazio.prg",
            entity_name="VAZIO",
            operation="consulta",
            confidence=0.30,
            matched_fields=[],
        )
        capture = CaptureTemplate(
            capture_source="test.jsonl",
            session_id="sessao-008",
            screen_sequence=["sig1"],
            input_templates=["valor1", "valor2"],
            screen_contexts=[{"screen_signature": "sig1", "inputs": ["valor1", "valor2"]}],
        )

        enriched = integrator.enrich_template(
            capture, [entity_empty], [binding]
        )

        sm = enriched.screen_mappings[0]
        assert sm.entity_name == "VAZIO"
        assert len(sm.mapped_inputs) == 2
        assert enriched.unmapped_inputs == 2


class TestLowercaseFields:
    """Verifica que o template engine é case-insensitive."""
    def test_lowercase_matches_uppercase_entity(self, integrator, entity_cliente, binding_cliente):
        """Entidade CLIENTE tem campo NOME; input 'nome' deve ser reconhecido."""
        # Entidade com campos maiúsculos
        capture = CaptureTemplate(
            capture_source="test.jsonl",
            session_id="sessao-009",
            screen_sequence=["sig1"],
            input_templates=["joao silva"],
            screen_contexts=[{"screen_signature": "sig1", "inputs": ["joao silva"]}],
        )

        enriched = integrator.enrich_template(
            capture, [entity_cliente], [binding_cliente]
        )

        sm = enriched.screen_mappings[0]
        # Pelo menos um input mapeado via matched_fields ou screen_order
        assert any(mi.field_name for mi in sm.mapped_inputs)

    def test_entity_lookup_case_insensitive(self, integrator):
        """entity_index usa .upper() para lookup."""
        entity = EntityDefinition(
            name="cliente",  # minúsculo
            storage_type="sql",
            fields=[FieldDefinition(name="nome", datatype="varchar", required=True)],
        )
        binding = ScreenEntityBinding(
            program_name="test.prg",
            entity_name="cliente",
            operation="cadastro",
            confidence=0.80,
            matched_fields=["nome"],
        )

        capture = CaptureTemplate(
            capture_source="test.jsonl",
            session_id="sessao-010",
            screen_sequence=["sig1"],
            input_templates=["João"],
            screen_contexts=[{"screen_signature": "sig1", "inputs": ["João"]}],
        )

        enriched = integrator.enrich_template(capture, [entity], [binding])
        sm = enriched.screen_mappings[0]
        assert sm.entity_name == "cliente"


class TestIsTextCompatible:
    def test_text_to_person_name(self, integrator):
        assert integrator._is_text_compatible("text", "person_name", "varchar") is True

    def test_text_to_name(self, integrator):
        assert integrator._is_text_compatible("text", "nome", "varchar") is True

    def test_number_to_money(self, integrator):
        assert integrator._is_text_compatible("number", "money", "decimal") is True

    def test_number_to_text_field(self, integrator):
        assert integrator._is_text_compatible("number", "person_name", "varchar") is False

    def test_text_to_number_field(self, integrator):
        assert integrator._is_text_compatible("text", "money", "decimal") is False

#!/usr/bin/env python3
"""Testes unitários da detecção automática de campos-âncora (generalista).

Um campo-âncora é chave de consulta da entidade (índice, operação de busca
ou campo único). Substituir uma âncora por valor sintético inexistente faz a
consulta não achar o registro e desvia o fluxo gravado (ex.: cai no cadastro
em vez de seguir a jornada). Por isso âncoras são mantidas com o valor
original da captura por default — sem o usuário precisar conhecer o domínio.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

GATEWAY_DIR = Path(__file__).resolve().parents[1] / "gateway"
sys.path.insert(0, str(GATEWAY_DIR))
sys.path.insert(0, str(GATEWAY_DIR / "control"))

from dakota_gateway.source_analyzer.entity_catalog import (
    EntityDefinition,
    FieldDefinition,
    OperationDefinition,
)
from control.services.capture_synthesis_service import suggest_key_fields


def _entity_clientes() -> EntityDefinition:
    return EntityDefinition(
        name="CLIENTES",
        storage_type="isam",
        fields=[
            FieldDefinition(name="CPF", datatype="cpf", unique_flag=True),
            FieldDefinition(name="NOME", datatype="person_name"),
            FieldDefinition(name="EMAIL", datatype="email"),
        ],
        indexes=[{"field": "CPF", "index": "CPF"}],
        operations=[
            OperationDefinition(operation_type="seek", entity_name="CLIENTES", fields=["CPF"]),
            OperationDefinition(operation_type="replace", entity_name="CLIENTES", fields=["NOME"]),
        ],
    )


class SuggestKeyFieldsTests(unittest.TestCase):
    def test_campo_unique_e_sugerido(self):
        mappings = [{
            "entity_name": "CLIENTES",
            "inputs": [{"original": "00109829069", "field_name": "cpf", "placeholder": "{{clientes.cpf}}"}],
        }]
        self.assertEqual(suggest_key_fields(mappings, [_entity_clientes()]), ["cpf"])

    def test_campo_de_indice_e_sugerido_mesmo_sem_unique(self):
        entity = EntityDefinition(
            name="PRODUTOS",
            fields=[FieldDefinition(name="EAN"), FieldDefinition(name="DESCRICAO")],
            indexes=[{"field": "EAN", "index": "EAN"}],
        )
        mappings = [{
            "entity_name": "PRODUTOS",
            "inputs": [{"original": "7909521971642", "field_name": "ean", "placeholder": "{{produtos.ean}}"}],
        }]
        self.assertEqual(suggest_key_fields(mappings, [entity]), ["ean"])

    def test_campo_de_operacao_seek_e_sugerido(self):
        entity = EntityDefinition(
            name="PEDIDOS",
            fields=[FieldDefinition(name="NUMERO"), FieldDefinition(name="VALOR")],
            operations=[OperationDefinition(operation_type="locate", entity_name="PEDIDOS", fields=["NUMERO"])],
        )
        mappings = [{
            "entity_name": "PEDIDOS",
            "inputs": [{"original": "D00011128", "field_name": "numero", "placeholder": "{{pedidos.numero}}"}],
        }]
        self.assertEqual(suggest_key_fields(mappings, [entity]), ["numero"])

    def test_campo_identificador_por_datatype_e_sugerido(self):
        """Tipo identificador de registro (cpf/cnpj/...) — âncora mesmo sem
        unique_flag (a KB persistida do AIX não popula unique nem índices)."""
        entity = EntityDefinition(
            name="arq",
            fields=[
                FieldDefinition(name="cpf", datatype="cpf", unique_flag=False),
                FieldDefinition(name="nome", datatype="person_name"),
            ],
        )
        mappings = [{
            "entity_name": "arq",
            "inputs": [{"original": "00109829069", "field_name": "cpf", "placeholder": "{{arq.cpf}}"}],
        }]
        self.assertEqual(suggest_key_fields(mappings, [entity]), ["cpf"])

    def test_campo_com_lookup_table_e_sugerido(self):
        """Campo com lookup_table é FK: o valor precisa existir na entidade
        referenciada — substituir por inexistente desvia o fluxo."""
        entity = EntityDefinition(
            name="PEDIDOS",
            fields=[
                FieldDefinition(name="EAN_PRODUTO", datatype="text", lookup_table="PRODUTOS"),
                FieldDefinition(name="QUANTIDADE", datatype="number"),
            ],
        )
        mappings = [{
            "entity_name": "PEDIDOS",
            "inputs": [
                {"original": "7909521971642", "field_name": "ean_produto", "placeholder": "{{pedidos.ean_produto}}"},
                {"original": "1", "field_name": "quantidade", "placeholder": "{{pedidos.quantidade}}"},
            ],
        }]
        self.assertEqual(suggest_key_fields(mappings, [entity]), ["ean_produto"])

    def test_identifies_record_modulo_central(self):
        """A propriedade 'identifica registro' é declarativa e centralizada."""
        from dakota_gateway.source_analyzer.semantic_types import identifies_record

        self.assertTrue(identifies_record("cpf"))
        self.assertTrue(identifies_record("CNPJ"))
        self.assertFalse(identifies_record("email"))
        self.assertFalse(identifies_record(""))
        self.assertFalse(identifies_record(None))

    def test_campo_comum_nao_e_sugerido(self):
        mappings = [{
            "entity_name": "CLIENTES",
            "inputs": [
                {"original": "JOAO", "field_name": "nome", "placeholder": "{{clientes.nome}}"},
                {"original": "229,90", "field_name": "valor", "placeholder": "{{clientes.valor}}"},
            ],
        }]
        self.assertEqual(suggest_key_fields(mappings, [_entity_clientes()]), [])

    def test_entidade_ausente_ou_sem_mapping_nao_quebra(self):
        self.assertEqual(suggest_key_fields([], [_entity_clientes()]), [])
        self.assertEqual(suggest_key_fields([{"entity_name": "INEXISTENTE", "inputs": [{"field_name": "cpf", "original": "1"}]}], [_entity_clientes()]), [])
        self.assertEqual(suggest_key_fields(None, None), [])

    def test_dedup_preserva_ordem_da_captura(self):
        mappings = [
            {"entity_name": "CLIENTES", "inputs": [{"original": "1", "field_name": "cpf", "placeholder": "{{clientes.cpf}}"}]},
            {"entity_name": "CLIENTES", "inputs": [{"original": "1", "field_name": "cpf", "placeholder": "{{clientes.cpf}}"}]},
        ]
        self.assertEqual(suggest_key_fields(mappings, [_entity_clientes()]), ["cpf"])

    def test_sugestao_alimenta_extract_substitutions_como_identidade(self):
        """Âncora detectada vira substituição identidade sem skip explícito."""
        from control.services.capture_synthesis_service import _extract_substitutions

        mappings = [{
            "entity_name": "CLIENTES",
            "inputs": [
                {"original": "00109829069", "field_name": "cpf", "placeholder": "{{clientes.cpf}}"},
                {"original": "JOAO", "field_name": "nome", "placeholder": "{{clientes.nome}}"},
            ],
        }]
        keys = suggest_key_fields(mappings, [_entity_clientes()])
        subs = _extract_substitutions(
            mappings, {"cpf": "185.032.574-08", "nome": "MARIA"}, skip_fields=set(keys))
        self.assertEqual(subs[0], ("00109829069", "00109829069"))  # âncora mantida
        self.assertEqual(subs[1], ("JOAO", "MARIA"))  # dado comum substituído


if __name__ == "__main__":
    unittest.main()

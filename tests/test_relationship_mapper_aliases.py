#!/usr/bin/env python3
"""Testes de aliases e normalizacao no RelationshipMapper."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GATEWAY_DIR = ROOT / "gateway"
sys.path.insert(0, str(GATEWAY_DIR))

from dakota_gateway.source_analyzer.entity_catalog import EntityDefinition, FieldDefinition
from dakota_gateway.source_analyzer.relationship_mapper import RelationshipMapper


class RelationshipMapperAliasTests(unittest.TestCase):

    def setUp(self):
        self.mapper = RelationshipMapper()

    def test_cliente_id_maps_to_clientes(self):
        """CLIENTE_ID deve apontar para CLIENTES."""
        entities = [
            EntityDefinition(name="PEDIDOS", storage_type="sql", source="/src/ped.prg",
                fields=[FieldDefinition(name="CLIENTE_ID")]),
            EntityDefinition(name="CLIENTES", storage_type="sql", source="/src/cli.prg",
                fields=[FieldDefinition(name="NOME")]),
        ]
        result = self.mapper.map(entities)
        fks = [r for r in result.relationships if r.relationship_type == "foreign_key"]
        self.assertTrue(any(
            r.source_entity == "PEDIDOS" and r.target_entity == "CLIENTES"
            for r in fks
        ), f"CLIENTE_ID → CLIENTES nao encontrado. FKs: {[(r.source_entity, r.target_entity) for r in fks]}")

    def test_codcli_maps_to_clientes(self):
        """CODCLI deve apontar para CLIENTES."""
        entities = [
            EntityDefinition(name="PEDIDOS", storage_type="sql", source="/src/ped.prg",
                fields=[FieldDefinition(name="CODCLI")]),
            EntityDefinition(name="CLIENTES", storage_type="sql", source="/src/cli.prg",
                fields=[FieldDefinition(name="NOME")]),
        ]
        result = self.mapper.map(entities)
        fks = [r for r in result.relationships if r.relationship_type == "foreign_key"]
        self.assertTrue(any(
            r.source_entity == "PEDIDOS" and r.target_entity == "CLIENTES"
            for r in fks
        ), f"CODCLI → CLIENTES nao encontrado")

    def test_produto_id_maps_to_produtos(self):
        """PRODUTO_ID deve apontar para PRODUTOS."""
        entities = [
            EntityDefinition(name="ITENS_PEDIDO", storage_type="sql", source="/src/itens.prg",
                fields=[FieldDefinition(name="PRODUTO_ID")]),
            EntityDefinition(name="PRODUTOS", storage_type="sql", source="/src/prod.prg",
                fields=[FieldDefinition(name="DESCRICAO")]),
        ]
        result = self.mapper.map(entities)
        fks = [r for r in result.relationships if r.relationship_type == "foreign_key"]
        self.assertTrue(any(
            r.source_entity == "ITENS_PEDIDO" and r.target_entity == "PRODUTOS"
            for r in fks
        ), f"PRODUTO_ID → PRODUTOS nao encontrado")

    def test_pedido_id_maps_to_pedidos(self):
        """PEDIDO_ID deve apontar para PEDIDOS."""
        entities = [
            EntityDefinition(name="ITENS_PEDIDO", storage_type="sql", source="/src/itens.prg",
                fields=[FieldDefinition(name="PEDIDO_ID")]),
            EntityDefinition(name="PEDIDOS", storage_type="sql", source="/src/ped.prg",
                fields=[FieldDefinition(name="VALOR_TOTAL")]),
        ]
        result = self.mapper.map(entities)
        fks = [r for r in result.relationships if r.relationship_type == "foreign_key"]
        self.assertTrue(any(
            r.source_entity == "ITENS_PEDIDO" and r.target_entity == "PEDIDOS"
            for r in fks
        ), f"PEDIDO_ID → PEDIDOS nao encontrado")

    def test_id_cliente_maps_to_clientes(self):
        """ID_CLIENTE deve apontar para CLIENTES."""
        entities = [
            EntityDefinition(name="PEDIDOS", storage_type="sql", source="/src/ped.prg",
                fields=[FieldDefinition(name="ID_CLIENTE")]),
            EntityDefinition(name="CLIENTES", storage_type="sql", source="/src/cli.prg",
                fields=[FieldDefinition(name="NOME")]),
        ]
        result = self.mapper.map(entities)
        fks = [r for r in result.relationships if r.relationship_type == "foreign_key"]
        self.assertTrue(any(
            r.source_entity == "PEDIDOS" and r.target_entity == "CLIENTES"
            for r in fks
        ), f"ID_CLIENTE → CLIENTES nao encontrado")

    # ── v0.2.1: Falso-positivo ──

    def test_forma_pgto_nao_aponta_para_fornecedores(self):
        """FORMA_PGTO nao deve apontar para FORNECEDORES."""
        entities = [
            EntityDefinition(name="PAGAMENTOS", source="/src/pag.prg",
                fields=[FieldDefinition(name="FORMA_PGTO")]),
            EntityDefinition(name="FORNECEDORES", source="/src/for.prg",
                fields=[FieldDefinition(name="NOME")]),
        ]
        result = self.mapper.map(entities)
        fks = [r for r in result.relationships if r.relationship_type == "foreign_key"]
        # Nao deve ter FK de PAGAMENTOS para FORNECEDORES via FORMA_PGTO
        bad = [r for r in fks if r.source_entity == "PAGAMENTOS" and r.target_entity == "FORNECEDORES"]
        self.assertEqual(len(bad), 0, f"FORMA_PGTO nao deveria gerar FK para FORNECEDORES: {bad}")

    def test_informacao_nao_aponta_para_fornecedores(self):
        """INFORMACAO nao deve apontar para FORNECEDORES."""
        entities = [
            EntityDefinition(name="CADASTRO", source="/src/cad.prg",
                fields=[FieldDefinition(name="INFORMACAO")]),
            EntityDefinition(name="FORNECEDORES", source="/src/for.prg",
                fields=[FieldDefinition(name="NOME")]),
        ]
        result = self.mapper.map(entities)
        fks = [r for r in result.relationships if r.relationship_type == "foreign_key"]
        bad = [r for r in fks if r.source_entity == "CADASTRO" and r.target_entity == "FORNECEDORES"]
        self.assertEqual(len(bad), 0)

    def test_codfor_aponta_para_fornecedores(self):
        """CODFOR deve apontar para FORNECEDORES."""
        entities = [
            EntityDefinition(name="PEDIDOS", source="/src/ped.prg",
                fields=[FieldDefinition(name="CODFOR")]),
            EntityDefinition(name="FORNECEDORES", source="/src/for.prg",
                fields=[FieldDefinition(name="NOME")]),
        ]
        result = self.mapper.map(entities)
        fks = [r for r in result.relationships if r.relationship_type == "foreign_key"]
        self.assertTrue(any(
            r.source_entity == "PEDIDOS" and r.target_entity == "FORNECEDORES"
            for r in fks
        ), "CODFOR → FORNECEDORES nao encontrado")

    def test_cd_for_aponta_para_fornecedores(self):
        """CD_FOR deve apontar para FORNECEDORES."""
        entities = [
            EntityDefinition(name="PEDIDOS", source="/src/ped.prg",
                fields=[FieldDefinition(name="CD_FOR")]),
            EntityDefinition(name="FORNECEDORES", source="/src/for.prg",
                fields=[FieldDefinition(name="NOME")]),
        ]
        result = self.mapper.map(entities)
        fks = [r for r in result.relationships if r.relationship_type == "foreign_key"]
        self.assertTrue(any(
            r.source_entity == "PEDIDOS" and r.target_entity == "FORNECEDORES"
            for r in fks
        ), "CD_FOR → FORNECEDORES nao encontrado")

    # ── v0.2.1: VENDEDORES vs VENDAS ──

    def test_codven_maps_to_vendedores(self):
        """CODVEN deve apontar para VENDEDORES quando existe."""
        entities = [
            EntityDefinition(name="PEDIDOS", source="/src/ped.prg",
                fields=[FieldDefinition(name="CODVEN")]),
            EntityDefinition(name="VENDEDORES", source="/src/ven.prg",
                fields=[FieldDefinition(name="NOME")]),
            EntityDefinition(name="VENDAS", source="/src/venda.prg",
                fields=[FieldDefinition(name="VALOR")]),
        ]
        result = self.mapper.map(entities)
        fks = [r for r in result.relationships if r.relationship_type == "foreign_key"]
        self.assertTrue(any(
            r.source_entity == "PEDIDOS" and r.target_entity == "VENDEDORES"
            for r in fks
        ), "CODVEN → VENDEDORES nao encontrado")

    def test_vendedor_id_maps_to_vendedores(self):
        """VENDEDOR_ID deve apontar para VENDEDORES."""
        entities = [
            EntityDefinition(name="PEDIDOS", source="/src/ped.prg",
                fields=[FieldDefinition(name="VENDEDOR_ID")]),
            EntityDefinition(name="VENDEDORES", source="/src/ven.prg",
                fields=[FieldDefinition(name="NOME")]),
        ]
        result = self.mapper.map(entities)
        fks = [r for r in result.relationships if r.relationship_type == "foreign_key"]
        self.assertTrue(any(
            r.source_entity == "PEDIDOS" and r.target_entity == "VENDEDORES"
            for r in fks
        ), "VENDEDOR_ID → VENDEDORES nao encontrado")

    def test_venda_id_maps_to_vendas(self):
        """VENDA_ID deve apontar para VENDAS quando existe."""
        entities = [
            EntityDefinition(name="ITENS", source="/src/itens.prg",
                fields=[FieldDefinition(name="VENDA_ID")]),
            EntityDefinition(name="VENDAS", source="/src/venda.prg",
                fields=[FieldDefinition(name="VALOR")]),
        ]
        result = self.mapper.map(entities)
        fks = [r for r in result.relationships if r.relationship_type == "foreign_key"]
        self.assertTrue(any(
            r.source_entity == "ITENS" and r.target_entity == "VENDAS"
            for r in fks
        ), "VENDA_ID → VENDAS nao encontrado")


if __name__ == "__main__":
    unittest.main()

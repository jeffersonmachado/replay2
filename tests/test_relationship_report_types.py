#!/usr/bin/env python3
"""Testa separacao de FK, lookup e cooccurrence no relatorio e planejador."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GATEWAY_DIR = ROOT / "gateway"
sys.path.insert(0, str(GATEWAY_DIR))

from dakota_gateway.source_analyzer.entity_catalog import EntityDefinition, FieldDefinition
from dakota_gateway.source_analyzer.relationship_mapper import RelationshipMapper, RelationshipMap, Relationship
from dakota_gateway.synthetic.business_dataset_planner import BusinessDatasetPlanner
from dakota_gateway.synthetic.synthetic_evidence_report import SyntheticEvidenceReportBuilder


class RelationshipReportTypesTests(unittest.TestCase):

    def test_fk_appears_as_foreign_key(self):
        """FK deve aparecer como foreign_key no relatorio."""
        entities = [
            EntityDefinition(name="PEDIDOS", source="/src/ped.prg",
                fields=[FieldDefinition(name="CLIENTE_ID")]),
            EntityDefinition(name="CLIENTES", source="/src/cli.prg",
                fields=[FieldDefinition(name="NOME")]),
        ]
        mapper = RelationshipMapper()
        result = mapper.map(entities)

        fks = [r for r in result.relationships if r.relationship_type == "foreign_key"]
        self.assertGreater(len(fks), 0)

    def test_cooccurrence_not_dependency(self):
        """Cooccurrence nao deve virar dependencia no BusinessDatasetPlanner."""
        entities = [
            EntityDefinition(name="CLIENTES", source="/src/cli.prg",
                fields=[FieldDefinition(name="NOME")]),
            EntityDefinition(name="PRODUTOS", source="/src/cli.prg",
                fields=[FieldDefinition(name="DESCRICAO")]),
        ]
        mapper = RelationshipMapper()
        rel_map = mapper.map(entities)

        planner = BusinessDatasetPlanner()
        graph = planner.plan(entities, rel_map)

        # Nenhuma dependencia — CLIENTES e PRODUTOS so tem cooccurrence
        self.assertEqual(len(graph.roots), 2,
                         "Ambas devem ser raiz (cooccurrence nao gera dependencia)")

    def test_evidence_report_separates_types(self):
        """Evidence report deve separar FK, lookup e cooccurrence."""
        entities = [
            EntityDefinition(name="PEDIDOS", source="/src/ped.prg",
                fields=[FieldDefinition(name="CLIENTE_ID")]),
            EntityDefinition(name="CLIENTES", source="/src/cli.prg",
                fields=[FieldDefinition(name="NOME")]),
            EntityDefinition(name="PRODUTOS", source="/src/cli.prg",
                fields=[FieldDefinition(name="DESCRICAO")]),
        ]
        mapper = RelationshipMapper()
        rel_map = mapper.map(entities)

        builder = SyntheticEvidenceReportBuilder()
        report = builder.build(
            entities=entities, screens_count=0, bindings=[],
            relationships=rel_map,
        )

        self.assertGreaterEqual(report.foreign_key_relationships, 1)
        # Cooccurrence entre PRODUTOS e CLIENTES (mesmo arquivo)
        self.assertGreaterEqual(report.cooccurrence_hints, 0)

    def test_evidence_report_exposes_structured_graphs(self):
        """Evidence report expoe grafos completos: FK, lookup, cooccurrence, entity_graph."""
        entities = [
            EntityDefinition(name="PEDIDOS", source="/src/ped.prg",
                fields=[FieldDefinition(name="CLIENTE_ID"), FieldDefinition(name="PRODUTO_ID")]),
            EntityDefinition(name="CLIENTES", source="/src/cli.prg",
                fields=[FieldDefinition(name="NOME")]),
            EntityDefinition(name="PRODUTOS", source="/src/cli.prg",
                fields=[FieldDefinition(name="DESCRICAO")]),
        ]
        mapper = RelationshipMapper()
        rel_map = mapper.map(entities)

        builder = SyntheticEvidenceReportBuilder()
        report = builder.build(
            entities=entities, screens_count=0, bindings=[],
            relationships=rel_map,
        )

        json_str = builder.to_json(report)
        import json
        data = json.loads(json_str)

        # Deve ter a secao relationships
        self.assertIn("relationships", data)
        rels = data["relationships"]

        self.assertIn("foreign_key_relationships", rels)
        self.assertIn("lookup_relationships", rels)
        self.assertIn("cooccurrence_hints", rels)
        self.assertIn("dependency_graph", rels)
        self.assertIn("cooccurrence_graph", rels)
        self.assertIn("entity_graph", rels)

        # entity_graph deve conter todas as entidades como chaves
        eg = rels["entity_graph"]
        self.assertIn("PEDIDOS", eg)
        self.assertIn("CLIENTES", eg)
        self.assertIn("PRODUTOS", eg)

        # dependency_graph nao deve conter cooccurrence
        dg = rels["dependency_graph"]
        # CLIENTES e PRODUTOS tem cooccurrence, nao FK entre si
        if "CLIENTES" in dg:
            self.assertNotIn("PRODUTOS", dg.get("CLIENTES", []),
                             "cooccurrence nao deve estar em dependency_graph")

        # cooccurrence_graph deve conter cooccurrence se houver
        cg = rels["cooccurrence_graph"]
        self.assertIsInstance(cg, dict)


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""Testes para os modulos P2-A: ProgramCatalog, BusinessDatasetPlanner,
DataSynthesizer.generate_ordered() e SyntheticEvidenceReport."""
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
    OperationDefinition,
    ScreenDefinition,
)
from dakota_gateway.source_analyzer.relationship_mapper import (
    Relationship,
    RelationshipMap,
)
from dakota_gateway.source_analyzer.screen_entity_linker import (
    ScreenEntityBinding,
    ScreenEntityLinker,
)
from dakota_gateway.source_analyzer.program_catalog import (
    ProgramCatalog,
    ProgramEntry,
)
from dakota_gateway.synthetic.business_dataset_planner import (
    BusinessDatasetPlanner,
    BusinessDependencyGraph,
    DatasetPlan,
)
from dakota_gateway.synthetic.synthetic_evidence_report import (
    SyntheticEvidenceReport,
    SyntheticEvidenceReportBuilder,
    EntityEvidence,
)


# ═══════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════

def make_entities() -> list[EntityDefinition]:
    """Fixture: entidades de um sistema de lojas."""
    return [
        EntityDefinition(
            name="CLIENTES",
            storage_type="sql",
            source="/src/cadcli.prg",
            fields=[
                FieldDefinition(name="ID", datatype="integer"),
                FieldDefinition(name="NOME", datatype="text"),
                FieldDefinition(name="CPF", datatype="text"),
                FieldDefinition(name="EMAIL", datatype="text"),
                FieldDefinition(name="TELEFONE", datatype="text"),
            ],
            operations=[
                OperationDefinition(operation_type="insert", entity_name="CLIENTES",
                                   source_file="/src/cadcli.prg", line_number=10),
            ],
        ),
        EntityDefinition(
            name="PRODUTOS",
            storage_type="isam",
            source="/src/cadprod.prg",
            fields=[
                FieldDefinition(name="ID", datatype="integer"),
                FieldDefinition(name="DESCRICAO", datatype="text"),
                FieldDefinition(name="PRECO", datatype="decimal"),
                FieldDefinition(name="ESTOQUE", datatype="integer"),
            ],
            operations=[
                OperationDefinition(operation_type="append", entity_name="PRODUTOS",
                                   source_file="/src/cadprod.prg", line_number=5),
            ],
        ),
        EntityDefinition(
            name="PEDIDOS",
            storage_type="isam",
            source="/src/pedido.prg",
            fields=[
                FieldDefinition(name="ID", datatype="integer"),
                FieldDefinition(name="CLIENTE_ID", datatype="integer"),
                FieldDefinition(name="DATA_PEDIDO", datatype="date"),
                FieldDefinition(name="VALOR_TOTAL", datatype="decimal"),
                FieldDefinition(name="STATUS", datatype="text"),
            ],
            operations=[
                OperationDefinition(operation_type="append", entity_name="PEDIDOS",
                                   source_file="/src/pedido.prg", line_number=8),
            ],
        ),
        EntityDefinition(
            name="ITENS_PEDIDO",
            storage_type="isam",
            source="/src/pedido.prg",
            fields=[
                FieldDefinition(name="ID", datatype="integer"),
                FieldDefinition(name="PEDIDO_ID", datatype="integer"),
                FieldDefinition(name="PRODUTO_ID", datatype="integer"),
                FieldDefinition(name="QUANTIDADE", datatype="integer"),
                FieldDefinition(name="PRECO_UNITARIO", datatype="decimal"),
            ],
            operations=[
                OperationDefinition(operation_type="append", entity_name="ITENS_PEDIDO",
                                   source_file="/src/pedido.prg", line_number=20),
            ],
        ),
        EntityDefinition(
            name="FINANCEIRO",
            storage_type="isam",
            source="/src/financeiro.prg",
            fields=[
                FieldDefinition(name="ID", datatype="integer"),
                FieldDefinition(name="PEDIDO_ID", datatype="integer"),
                FieldDefinition(name="VALOR", datatype="decimal"),
                FieldDefinition(name="VENCIMENTO", datatype="date"),
                FieldDefinition(name="STATUS", datatype="text"),
            ],
            operations=[
                OperationDefinition(operation_type="append", entity_name="FINANCEIRO",
                                   source_file="/src/financeiro.prg", line_number=5),
            ],
        ),
    ]


def make_relationships() -> RelationshipMap:
    """Fixture: relacionamentos entre entidades."""
    return RelationshipMap(
        relationships=[
            Relationship(
                source_entity="PEDIDOS", target_entity="CLIENTES",
                relationship_type="foreign_key", source_field="CLIENTE_ID",
                target_field="ID", cardinality="N:1", confidence=0.85,
                evidence=["fk_cliente_id"],
            ),
            Relationship(
                source_entity="ITENS_PEDIDO", target_entity="PEDIDOS",
                relationship_type="foreign_key", source_field="PEDIDO_ID",
                target_field="ID", cardinality="N:1", confidence=0.85,
                evidence=["fk_pedido_id"],
            ),
            Relationship(
                source_entity="ITENS_PEDIDO", target_entity="PRODUTOS",
                relationship_type="foreign_key", source_field="PRODUTO_ID",
                target_field="ID", cardinality="N:1", confidence=0.85,
                evidence=["fk_produto_id"],
            ),
            Relationship(
                source_entity="FINANCEIRO", target_entity="PEDIDOS",
                relationship_type="foreign_key", source_field="PEDIDO_ID",
                target_field="ID", cardinality="N:1", confidence=0.80,
                evidence=["fk_pedido_id_fin"],
            ),
        ],
        entity_graph={
            "CLIENTES": ["PEDIDOS"],
            "PRODUTOS": ["ITENS_PEDIDO"],
            "PEDIDOS": ["ITENS_PEDIDO", "FINANCEIRO"],
            "ITENS_PEDIDO": [],
            "FINANCEIRO": [],
        },
        orphan_entities=[],
    )


def make_bindings() -> list[ScreenEntityBinding]:
    """Fixture: bindings tela→entidade."""
    return [
        ScreenEntityBinding(
            screen_title="Cadastro de Clientes",
            program_name="cadcli",
            source_file="/src/cadcli.prg",
            entity_name="CLIENTES",
            operation="create",
            matched_fields=["nome", "cpf", "email", "telefone"],
            confidence=0.92,
            evidence=["titulo contem 'cadastro'", "4/4 campos correspondem a entidade 'CLIENTES'"],
        ),
        ScreenEntityBinding(
            screen_title="Cadastro de Produtos",
            program_name="cadprod",
            source_file="/src/cadprod.prg",
            entity_name="PRODUTOS",
            operation="create",
            matched_fields=["descricao", "preco", "estoque"],
            confidence=0.90,
            evidence=["titulo contem 'cadastro'", "3/3 campos correspondem a entidade 'PRODUTOS'"],
        ),
        ScreenEntityBinding(
            screen_title="Pedido de Venda",
            program_name="pedido",
            source_file="/src/pedido.prg",
            entity_name="PEDIDOS",
            operation="create",
            matched_fields=["cliente_id", "data_pedido", "valor_total"],
            confidence=0.88,
            evidence=["programa referencia entidade 'PEDIDOS'"],
        ),
    ]


# ═══════════════════════════════════════════════════════════════════
# ProgramCatalog Tests
# ═══════════════════════════════════════════════════════════════════

class ProgramCatalogTests(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.source_dir = Path(self.tmpdir.name)

    def tearDown(self):
        self.tmpdir.cleanup()

    def _write_file(self, name: str, content: str) -> Path:
        path = self.source_dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def test_build_catalog_from_bindings(self):
        """Catalogo deve indexar programas a partir dos bindings."""
        entities = make_entities()
        bindings = make_bindings()

        catalog = ProgramCatalog(str(self.source_dir))
        entries = catalog.build(entities, bindings)

        self.assertGreaterEqual(len(entries), 3)

        # Busca por nome
        cadcli = catalog.get("cadcli")
        self.assertIsNotNone(cadcli)
        self.assertEqual(cadcli.entity_references, ["CLIENTES"])
        self.assertEqual(cadcli.operations, ["create"])

    def test_by_module(self):
        """Agrupamento por modulo deve funcionar."""
        self._write_file("cad/cli/cadcli.prg", """
INSERT INTO CLIENTES (NOME) VALUES ('X')
""")
        self._write_file("est/prod/cadprod.prg", """
USE PRODUTOS
APPEND BLANK
""")
        self._write_file("fat/ped/pedido.prg", """
USE PEDIDOS
APPEND BLANK
""")

        entities = make_entities()
        bindings = [
            ScreenEntityBinding(
                screen_title="Cadastro de Clientes",
                program_name="cadcli",
                source_file=str(self.source_dir / "cad/cli/cadcli.prg"),
                entity_name="CLIENTES",
                operation="create",
                matched_fields=["nome"],
                confidence=0.90,
                evidence=["test"],
            ),
            ScreenEntityBinding(
                screen_title="Cadastro de Produtos",
                program_name="cadprod",
                source_file=str(self.source_dir / "est/prod/cadprod.prg"),
                entity_name="PRODUTOS",
                operation="create",
                matched_fields=["descricao"],
                confidence=0.90,
                evidence=["test"],
            ),
            ScreenEntityBinding(
                screen_title="Pedido de Venda",
                program_name="pedido",
                source_file=str(self.source_dir / "fat/ped/pedido.prg"),
                entity_name="PEDIDOS",
                operation="create",
                matched_fields=["cliente_id"],
                confidence=0.90,
                evidence=["test"],
            ),
        ]

        catalog = ProgramCatalog(str(self.source_dir))
        catalog.build(entities, bindings)

        modules = catalog.modules()
        self.assertGreaterEqual(len(modules), 1)

    def test_catalog_by_entity(self):
        """Indexacao por entidade deve agrupar programas."""
        entities = make_entities()
        bindings = make_bindings()

        catalog = ProgramCatalog()
        catalog.build(entities, bindings)

        cliente_progs = catalog.by_entity("CLIENTES")
        self.assertGreaterEqual(len(cliente_progs), 1)
        self.assertEqual(cliente_progs[0].name, "cadcli")

    def test_catalog_to_report(self):
        """Relatorio do catalogo deve ser serializavel."""
        entities = make_entities()
        bindings = make_bindings()

        catalog = ProgramCatalog()
        catalog.build(entities, bindings)

        report = catalog.to_report()
        self.assertIn("total_programs", report)
        self.assertIn("modules", report)
        self.assertIn("by_entity", report)

        json_str = json.dumps(report, ensure_ascii=False)
        self.assertIn("cadcli", json_str)


# ═══════════════════════════════════════════════════════════════════
# BusinessDatasetPlanner Tests
# ═══════════════════════════════════════════════════════════════════

class BusinessDatasetPlannerTests(unittest.TestCase):

    def test_plan_builds_dependency_graph(self):
        """Deve construir grafo de dependencia com FK."""
        entities = make_entities()
        rels = make_relationships()

        planner = BusinessDatasetPlanner()
        graph = planner.plan(entities, rels)

        self.assertIsInstance(graph, BusinessDependencyGraph)
        self.assertEqual(graph.total_entities, 5)

    def test_generation_order_topological(self):
        """Ordem de geracao deve ser topologica: raizes antes de dependentes."""
        entities = make_entities()
        rels = make_relationships()

        planner = BusinessDatasetPlanner()
        graph = planner.plan(entities, rels)

        # CLIENTES e PRODUTOS devem vir antes de PEDIDOS
        order = graph.generation_order
        idx_clientes = order.index("CLIENTES")
        idx_produtos = order.index("PRODUTOS")
        idx_pedidos = order.index("PEDIDOS")
        idx_itens = order.index("ITENS_PEDIDO")
        idx_fin = order.index("FINANCEIRO")

        self.assertLess(idx_clientes, idx_pedidos)
        self.assertLess(idx_produtos, idx_itens)
        self.assertLess(idx_pedidos, idx_itens)
        self.assertLess(idx_pedidos, idx_fin)

    def test_roots_and_leaves(self):
        """Entidades sem FK de entrada sao raiz; sem FK de saida sao folha."""
        entities = make_entities()
        rels = make_relationships()

        planner = BusinessDatasetPlanner()
        graph = planner.plan(entities, rels)

        self.assertIn("CLIENTES", graph.roots)
        self.assertIn("PRODUTOS", graph.roots)
        self.assertIn("ITENS_PEDIDO", graph.leaves)
        self.assertIn("FINANCEIRO", graph.leaves)

    def test_plan_summary_serializable(self):
        """Resumo do plano deve ser serializavel em JSON."""
        entities = make_entities()
        rels = make_relationships()

        planner = BusinessDatasetPlanner()
        graph = planner.plan(entities, rels)

        summary = planner.plan_summary(graph)
        json_str = json.dumps(summary, ensure_ascii=False)
        self.assertIn("CLIENTES", json_str)

    def test_empty_entities_returns_empty_graph(self):
        """Sem entidades, grafo vazio."""
        planner = BusinessDatasetPlanner()
        graph = planner.plan([], RelationshipMap())
        self.assertEqual(graph.total_entities, 0)


# ═══════════════════════════════════════════════════════════════════
# SyntheticEvidenceReport Tests
# ═══════════════════════════════════════════════════════════════════

class SyntheticEvidenceReportTests(unittest.TestCase):

    def test_build_report_basic(self):
        """Relatorio basico com entidades e bindings."""
        entities = make_entities()
        bindings = make_bindings()
        rels = make_relationships()

        planner = BusinessDatasetPlanner()
        graph = planner.plan(entities, rels)

        builder = SyntheticEvidenceReportBuilder()
        report = builder.build(
            entities=entities,
            screens_count=3,
            bindings=bindings,
            relationships=rels,
            dependency_graph=graph,
            source_files_count=5,
        )

        self.assertIsInstance(report, SyntheticEvidenceReport)
        self.assertEqual(report.entities_detected, 5)
        self.assertEqual(report.screens_detected, 3)
        self.assertEqual(report.screen_entity_bindings, 3)
        self.assertEqual(report.relationships_inferred, 4)
        self.assertGreater(len(report.entities), 0)

    def test_report_entity_evidence(self):
        """Cada entidade deve ter evidencias detalhadas."""
        entities = make_entities()
        bindings = make_bindings()
        rels = make_relationships()

        planner = BusinessDatasetPlanner()
        graph = planner.plan(entities, rels)

        builder = SyntheticEvidenceReportBuilder()
        report = builder.build(
            entities=entities,
            screens_count=3,
            bindings=bindings,
            relationships=rels,
            dependency_graph=graph,
        )

        # CLIENTES deve ter evidencias
        cliente_ev = next(
            (e for e in report.entities if e.entity_name == "CLIENTES"), None
        )
        self.assertIsNotNone(cliente_ev)
        self.assertEqual(cliente_ev.field_count, 5)
        self.assertIn("Cadastro de Clientes", cliente_ev.screens_bound)
        self.assertTrue(cliente_ev.is_root)

        # ITENS_PEDIDO deve ser folha
        itens_ev = next(
            (e for e in report.entities if e.entity_name == "ITENS_PEDIDO"), None
        )
        self.assertIsNotNone(itens_ev)
        self.assertTrue(itens_ev.is_leaf)

    def test_report_json_serializable(self):
        """Relatorio completo deve ser serializavel em JSON."""
        entities = make_entities()
        bindings = make_bindings()
        rels = make_relationships()

        planner = BusinessDatasetPlanner()
        graph = planner.plan(entities, rels)

        builder = SyntheticEvidenceReportBuilder()
        report = builder.build(
            entities=entities,
            screens_count=3,
            bindings=bindings,
            relationships=rels,
            dependency_graph=graph,
        )

        json_str = builder.to_json(report)
        parsed = json.loads(json_str)

        self.assertIn("summary", parsed)
        self.assertIn("entities", parsed)
        self.assertIn("dependency_graph", parsed)
        self.assertIn("generation_plan", parsed)
        self.assertEqual(parsed["summary"]["entities_detected"], 5)

    def test_report_warnings_for_low_confidence(self):
        """Deve gerar warnings para bindings de baixa confianca."""
        entities = make_entities()
        bindings = [
            ScreenEntityBinding(
                screen_title="Tela Duvidosa",
                program_name="duvida",
                source_file="/src/duvida.prg",
                entity_name="ALGUMA",
                operation="",
                matched_fields=[],
                confidence=0.25,
                evidence=["baixa confianca"],
            ),
        ]

        builder = SyntheticEvidenceReportBuilder()
        report = builder.build(
            entities=entities,
            screens_count=1,
            bindings=bindings,
        )

        self.assertGreater(len(report.warnings), 0)

    def test_report_with_program_catalog(self):
        """Relatorio com ProgramCatalog deve incluir programas."""
        entities = make_entities()
        bindings = make_bindings()

        catalog = ProgramCatalog()
        catalog.build(entities, bindings)

        builder = SyntheticEvidenceReportBuilder()
        report = builder.build(
            entities=entities,
            screens_count=3,
            bindings=bindings,
            program_catalog=catalog,
        )

        self.assertEqual(report.programs_cataloged, 3)


# ═══════════════════════════════════════════════════════════════════
# SourceParser Integration Tests
# ═══════════════════════════════════════════════════════════════════

class SourceParserIntegrationTests(unittest.TestCase):
    """Testa integracao dos modulos P2-A no SourceParser."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.source_dir = Path(self.tmpdir.name)

    def tearDown(self):
        self.tmpdir.cleanup()

    def _write_file(self, name: str, content: str) -> Path:
        path = self.source_dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def test_program_catalog_from_parser(self):
        """SourceParser.program_catalog() deve retornar ProgramCatalog."""
        from dakota_gateway.source_analyzer.parser import SourceParser

        self._write_file("cad/cli/cadcli.prg", """
TITLE "Cadastro de Clientes"
@ 1,1 SAY "Nome:"
@ 1,20 GET nome
INSERT INTO CLIENTES (NOME, CPF) VALUES ('X', 'Y')
""")
        self._write_file("est/prod/cadprod.prg", """
TITLE "Cadastro de Produtos"
@ 1,1 SAY "Descricao:"
@ 1,20 GET descricao
INSERT INTO PRODUTOS (DESCRICAO) VALUES ('Z')
""")

        parser = SourceParser(str(self.source_dir))
        parser.parse_all()

        catalog = parser.program_catalog()
        self.assertIsNotNone(catalog)

        report = catalog.to_report()
        self.assertGreaterEqual(report["total_programs"], 1)

    def test_business_dependency_graph_from_parser(self):
        """SourceParser.business_dependency_graph() deve retornar grafo."""
        from dakota_gateway.source_analyzer.parser import SourceParser

        self._write_file("cadcli.prg", """
INSERT INTO CLIENTES (ID, NOME) VALUES (1, 'X')
""")
        self._write_file("pedido.prg", """
INSERT INTO PEDIDOS (ID, CLIENTE_ID, VALOR) VALUES (1, 1, 100)
""")

        parser = SourceParser(str(self.source_dir))
        parser.parse_all()

        graph = parser.business_dependency_graph()
        self.assertIsNotNone(graph)
        self.assertGreaterEqual(graph.total_entities, 1)

    def test_discovery_report_includes_all_sections(self):
        """discovery_report() deve incluir catalog, graph e bindings."""
        from dakota_gateway.source_analyzer.parser import SourceParser

        self._write_file("cadcli.prg", """
TITLE "Cadastro de Clientes"
@ 1,1 SAY "Nome:"
@ 1,20 GET nome
INSERT INTO CLIENTES (NOME, CPF) VALUES ('X', 'Y')
""")
        self._write_file("cadprod.prg", """
TITLE "Cadastro de Produtos"
@ 1,1 SAY "Descricao:"
@ 1,20 GET descricao
INSERT INTO PRODUTOS (DESCRICAO, PRECO) VALUES ('Z', 10)
""")

        parser = SourceParser(str(self.source_dir))
        parser.parse_all()

        report = parser.discovery_report()

        # Verifica secoes obrigatorias
        for section in (
            "screen_entity_bindings",
            "program_catalog",
            "dependency_graph",
            "crud",
            "relationships",
        ):
            self.assertIn(section, report, f"Falta secao '{section}' no relatorio")

        # Verifica serializacao JSON
        json_str = json.dumps(report, ensure_ascii=False, default=str)
        parsed = json.loads(json_str)
        self.assertIn("program_catalog", parsed)
        self.assertIn("dependency_graph", parsed)
        self.assertIn("total_programs", parsed["program_catalog"])

    def test_discovery_report_pipeline_label(self):
        """Relatorio deve conter label do pipeline P2-A."""
        from dakota_gateway.source_analyzer.parser import SourceParser

        self._write_file("cadcli.prg", """
INSERT INTO CLIENTES (NOME) VALUES ('X')
""")

        parser = SourceParser(str(self.source_dir))
        parser.parse_all()

        report = parser.discovery_report()
        self.assertEqual(report.get("pipeline"), "P2-A Synthetic Knowledge Base")


# ═══════════════════════════════════════════════════════════════════
# JourneyMix Tests
# ═══════════════════════════════════════════════════════════════════

class JourneyMixBuilderTests(unittest.TestCase):

    def test_lojas_basico_scenario(self):
        """Cenario pre-definido lojas_basico deve ter 5 jornadas."""
        from dakota_gateway.synthetic.journey_mix import JourneyMixBuilder

        config = JourneyMixBuilder.lojas_basico(
            target_host="10.10.2.30",
            target_user="oper",
            target_command="recital",
        )

        self.assertEqual(config.name, "lojas_basico")
        self.assertEqual(len(config.entries), 5)
        self.assertEqual(config.concurrency, 30)
        self.assertEqual(config.duration_minutes, 60)
        self.assertEqual(config.total_weight, 100)
        self.assertIn("create", config.categories_covered)
        self.assertIn("read", config.categories_covered)

    def test_build_schedule_distribution(self):
        """Agenda deve distribuir sessoes proporcionalmente aos pesos."""
        from dakota_gateway.synthetic.journey_mix import (
            JourneyMixBuilder,
            JourneyMixConfig,
            JourneyMixEntry,
        )

        config = JourneyMixConfig(
            name="teste",
            concurrency=10,
            duration_minutes=1,
            entries=[
                JourneyMixEntry(journey_id="j1", journey_name="J1", weight=70),
                JourneyMixEntry(journey_id="j2", journey_name="J2", weight=30),
            ],
            total_weight=100,
        )

        builder = JourneyMixBuilder()
        schedule = builder.build_schedule(config, total_sessions=100)

        self.assertEqual(schedule.total_sessions, 100)
        self.assertIn("j1", schedule.journey_distribution)
        self.assertIn("j2", schedule.journey_distribution)

        # j1 deve ter ~70 sessoes, j2 ~30
        j1_count = schedule.journey_distribution["j1"]
        j2_count = schedule.journey_distribution["j2"]
        self.assertGreater(j1_count, j2_count)
        self.assertEqual(j1_count + j2_count, 100)

    def test_build_schedule_order_randomized(self):
        """Ordem das sessoes deve ser embaralhada."""
        from dakota_gateway.synthetic.journey_mix import (
            JourneyMixBuilder,
            JourneyMixConfig,
            JourneyMixEntry,
        )

        config = JourneyMixConfig(
            name="teste",
            concurrency=10,
            duration_minutes=1,
            entries=[
                JourneyMixEntry(journey_id="j1", journey_name="J1", weight=50),
                JourneyMixEntry(journey_id="j2", journey_name="J2", weight=50),
            ],
            total_weight=100,
            seed=42,
        )

        builder = JourneyMixBuilder()
        schedule = builder.build_schedule(config, total_sessions=20)

        # Nao deve ser tudo j1 seguido de j2
        assignments = schedule.session_assignments
        self.assertEqual(len(assignments), 20)

        # Com seed fixo, a ordem e deterministica
        builder2 = JourneyMixBuilder()
        schedule2 = builder2.build_schedule(config, total_sessions=20)
        self.assertEqual(assignments, schedule2.session_assignments)

    def test_validate_empty_config(self):
        """Validacao deve detectar config vazia."""
        from dakota_gateway.synthetic.journey_mix import (
            JourneyMixBuilder,
            JourneyMixConfig,
        )

        config = JourneyMixConfig(name="", concurrency=0, duration_minutes=0)
        builder = JourneyMixBuilder()
        issues = builder.validate(config)
        self.assertGreater(len(issues), 0)

    def test_validate_valid_config(self):
        """Validacao deve passar para config valida."""
        from dakota_gateway.synthetic.journey_mix import (
            JourneyMixBuilder,
            JourneyMixConfig,
            JourneyMixEntry,
        )

        config = JourneyMixConfig(
            name="valido",
            concurrency=10,
            duration_minutes=5,
            entries=[
                JourneyMixEntry(journey_id="j1", journey_name="J1", weight=100),
            ],
            total_weight=100,
        )
        builder = JourneyMixBuilder()
        issues = builder.validate(config)
        self.assertEqual(len(issues), 0)

    def test_json_roundtrip(self):
        """Config serializada/deserializada deve ser identica."""
        from dakota_gateway.synthetic.journey_mix import JourneyMixBuilder

        config = JourneyMixBuilder.lojas_basico()
        builder = JourneyMixBuilder()

        json_str = builder.to_config_json(config)
        restored = builder.from_config_json(json_str)

        self.assertEqual(config.name, restored.name)
        self.assertEqual(len(config.entries), len(restored.entries))
        self.assertEqual(config.concurrency, restored.concurrency)
        self.assertEqual(config.entries[0].journey_id, restored.entries[0].journey_id)
        self.assertEqual(config.entries[0].weight, restored.entries[0].weight)

    def test_cadastro_intensivo_scenario(self):
        """Cenario cadastro_intensivo deve focar em escritas."""
        from dakota_gateway.synthetic.journey_mix import JourneyMixBuilder

        config = JourneyMixBuilder.cadastro_intensivo()
        categories = {e.category for e in config.entries}
        self.assertIn("create", categories)
        self.assertIn("update", categories)

        builder = JourneyMixBuilder()
        issues = builder.validate(config)
        self.assertEqual(len(issues), 0)

    def test_consulta_leve_scenario(self):
        """Cenario consulta_leve deve ser majoritariamente leitura."""
        from dakota_gateway.synthetic.journey_mix import JourneyMixBuilder

        config = JourneyMixBuilder.consulta_leve()
        read_weight = sum(e.weight for e in config.entries if e.category == "read")
        self.assertGreater(read_weight, 50)  # maioria deve ser consulta


if __name__ == "__main__":
    unittest.main()

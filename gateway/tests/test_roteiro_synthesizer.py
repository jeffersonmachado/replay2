"""Testes para o RoteiroSynthesizer.

Cobre:
- Serialização/desserialização dos dataclasses (to_dict/from_dict)
- Síntese com jornada vazia (fallback)
- Síntese com jornada + fluxo + regras
- Formatação Markdown
- Comparação com roteiro de referência
- Detecção de fases semânticas
- Classificação de tipo de passo
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

# allow running tests from repo root
GATEWAY_DIR = str(Path(__file__).resolve().parents[1])
if GATEWAY_DIR not in sys.path:
    sys.path.insert(0, GATEWAY_DIR)

import pytest

from dakota_gateway.synthetic.roteiro_model import (
    InferredRoute,
    InferredRoutePhase,
    InferredRouteStep,
    MenuEvidence,
    ProgramEvidence,
    ReferenceRouteSummary,
)
from dakota_gateway.synthetic.roteiro_synthesizer import (
    RoteiroSynthesizer,
    _extract_module,
    _classify_step_type,
    _humanize_action,
    _map_entity_to_friendly,
)
from dakota_gateway.synthetic.journey import JourneyDefinition, JourneyStep
from dakota_gateway.synthetic.flow_inferencer import (
    InferredBusinessModel,
    InferredFlow,
    InferredRule,
)
from dakota_gateway.source_analyzer.entity_catalog import (
    EntityDefinition,
    FieldDefinition,
    OperationDefinition,
)


# ── Fixtures ──


@dataclass
class FakeMenuNode:
    label: str = ""
    program_name: str = ""
    key: str = ""
    source_file: str = ""
    source_line: int = 0


@pytest.fixture
def sample_journey() -> JourneyDefinition:
    """Jornada simulada de inclusão de pedido de venda."""
    return JourneyDefinition(
        journey_id="j-pedido-01",
        name="Inclusão de Pedido de Venda",
        description="Fluxo completo de inclusão de pedido no balcão: "
                    "cliente, itens, valores e fechamento.",
        category="venda",
        entry_screen="est361",
        steps=[
            JourneyStep(
                step_order=1, screen_id="menu_principal",
                action="select", trigger="1",
                description="Selecionar opção Pedido de Venda no menu principal",
                screen_title="Menu Principal",
            ),
            JourneyStep(
                step_order=2, screen_id="est361",
                action="navigate", trigger="",
                description="Acessar rotina est361 — Inclusão de Pedido",
                screen_title="Inclusão de Pedido de Venda",
            ),
            JourneyStep(
                step_order=3, screen_id="est361",
                action="input", trigger="",
                input_template="Informar código do cliente",
                description="Informar código do cliente",
            ),
            JourneyStep(
                step_order=4, screen_id="est361",
                action="input", trigger="",
                input_template="Informar condição de pagamento",
                description="Informar condição de pagamento",
            ),
            JourneyStep(
                step_order=5, screen_id="est361",
                action="input", trigger="",
                input_template="Incluir item: produto, quantidade, preço",
                description="Incluir itens do pedido",
            ),
            JourneyStep(
                step_order=6, screen_id="est361",
                action="submit", trigger="F2",
                description="Confirmar gravação do pedido",
            ),
        ],
        tags=["venda", "pedido", "balcao"],
    )


@pytest.fixture
def sample_business_model() -> InferredBusinessModel:
    """Modelo de negócio inferido para fluxo de venda."""
    return InferredBusinessModel(
        rules=[
            InferredRule(
                rule_id="INFER-001",
                description="'pedido' referencia/depende de 'cliente'",
                depends_on="cliente", enables="pedido",
                dependency_type="requires", is_critical=True,
            ),
            InferredRule(
                rule_id="INFER-002",
                description="'pedido' referencia/depende de 'produto'",
                depends_on="produto", enables="pedido",
                dependency_type="requires", is_critical=True,
            ),
        ],
        flows=[
            InferredFlow(
                flow_id="FLOW-INFER-001",
                flow_name="cliente → pedido → nota",
                description="Fluxo de venda inferido do grafo.",
                steps=[
                    {"entity": "cliente", "operation": "create",
                     "why": "Cadastrar o cliente — sem cliente não há pedido"},
                    {"entity": "produto", "operation": "create",
                     "why": "Cadastrar o produto — sem produto não há item"},
                    {"entity": "pedido", "operation": "create",
                     "why": "Abrir o pedido vinculando cliente + itens"},
                    {"entity": "nota", "operation": "create",
                     "why": "Faturar o pedido — gera nota fiscal"},
                ],
                entities_in_flow=["cliente", "produto", "pedido", "nota"],
            ),
        ],
        entity_graph={
            "pedido": ["cliente", "produto"],
            "nota": ["pedido"],
        },
        roots=["cliente", "produto"],
        leaves=["nota"],
    )


@pytest.fixture
def sample_entities() -> list:
    """Entidades simuladas do source_analyzer."""
    return [
        EntityDefinition(
            name="est361", storage_type="recital",
            source="/dakota/prg/ped/est361.prg",
        ),
        EntityDefinition(
            name="cad110", storage_type="recital",
            source="/dakota/prg/cad/cad110.prg",
        ),
        EntityDefinition(
            name="epp010", storage_type="recital",
            source="/dakota/prg/epp/epp010.prg",
        ),
        EntityDefinition(
            name="fat210", storage_type="recital",
            source="/dakota/prg/fat/fat210.prg",
        ),
    ]


@pytest.fixture
def sample_business_eval() -> dict:
    """Resultado simulado da avaliação do BusinessRuleEngine."""
    return {
        "rules_evaluated": 11,
        "rules_ok": 9,
        "rules_broken": 2,
        "gaps": [],
        "flows_coverage": [
            {
                "flow_id": "FLOW-VENDA",
                "flow_name": "Venda Completa",
                "coverage_pct": 80.0,
                "entities_covered": ["cliente", "produto", "pedido", "nota"],
                "entities_missing": ["duplicata"],
            }
        ],
        "recommendation": "2 regras quebradas. Prioridade: verificar fluxo de venda.",
    }


@pytest.fixture
def sample_menu_nodes() -> list:
    return [
        FakeMenuNode(
            label="3.6.1 Inclusão de Pedido de Venda",
            program_name="est361",
            key="3.6.1",
            source_file="/dakota/prg/est/menu_est.prg",
            source_line=42,
        )
    ]


@pytest.fixture
def sample_reference() -> dict:
    """Roteiro de referência simulado."""
    return {
        "name": "Inclusão de Pedido de Venda — Referência Oficial",
        "source": "documentação Dakota",
        "phases": [
            {
                "title": "Inicialização e Cliente",
                "steps": [
                    {"action": "Acessar a rotina de Inclusão de Pedido (est361)"},
                    {"action": "Informar o código do cliente"},
                ],
            },
            {
                "title": "Valores e Logística",
                "steps": [
                    {"action": "Informar a condição de pagamento"},
                ],
            },
            {
                "title": "Itens do Pedido",
                "steps": [
                    {"action": "Incluir um item/produto no pedido"},
                    {"action": "Informar a quantidade do item"},
                ],
            },
            {
                "title": "Pagamento e Fechamento",
                "steps": [
                    {"action": "Confirmar a gravação do pedido"},
                ],
            },
        ],
    }


# ── Testes de dataclasses ──


class TestProgramEvidence:
    def test_to_dict_and_from_dict(self):
        pe = ProgramEvidence(
            program_name="est361",
            program_path="/dakota/prg/ped/est361.prg",
            module="ped",
            program_type="main",
            title="Inclusão de Pedido",
            operations=["DO cad110", "DO epp010"],
        )
        d = pe.to_dict()
        pe2 = ProgramEvidence.from_dict(d)
        assert pe2.program_name == "est361"
        assert pe2.module == "ped"
        assert pe2.title == "Inclusão de Pedido"
        assert pe2.operations == ["DO cad110", "DO epp010"]


class TestMenuEvidence:
    def test_to_dict_and_from_dict(self):
        me = MenuEvidence(
            menu_name="Menu Principal",
            menu_path="/dakota/prg/menu/menu_main.prg",
            option_label="1. Inclusão de Pedido de Venda",
            option_key="1",
            target_program="est361",
            source_line=42,
        )
        d = me.to_dict()
        me2 = MenuEvidence.from_dict(d)
        assert me2.menu_name == "Menu Principal"
        assert me2.option_key == "1"
        assert me2.target_program == "est361"


class TestInferredRouteStep:
    def test_to_dict_and_from_dict_with_mixed_evidence(self):
        pe = ProgramEvidence(program_name="est361", module="ped", program_type="main")
        me = MenuEvidence(menu_name="Menu Principal", option_label="1. Pedido", target_program="est361")
        step = InferredRouteStep(
            order=1,
            action="Acessar a rotina de Inclusão de Pedido",
            type="navigate",
            program="est361",
            confidence=0.9,
            evidence=[pe, me],
        )
        d = step.to_dict()
        step2 = InferredRouteStep.from_dict(d)
        assert step2.order == 1
        assert step2.confidence == 0.9
        assert len(step2.evidence) == 2
        assert isinstance(step2.evidence[0], ProgramEvidence)
        assert isinstance(step2.evidence[1], MenuEvidence)


class TestInferredRoutePhase:
    def test_to_dict_and_from_dict(self):
        phase = InferredRoutePhase(
            phase_id="phase-init",
            title="Inicialização e Cliente",
            objective="Identificar o cliente da venda.",
            steps=[
                InferredRouteStep(order=1, action="Informar código do cliente", type="input", confidence=0.8),
            ],
            confidence=0.8,
        )
        d = phase.to_dict()
        phase2 = InferredRoutePhase.from_dict(d)
        assert phase2.phase_id == "phase-init"
        assert phase2.title == "Inicialização e Cliente"
        assert len(phase2.steps) == 1


class TestInferredRoute:
    def test_to_dict_and_from_dict_full(self, sample_journey, sample_business_model, sample_entities, sample_business_eval):
        synth = RoteiroSynthesizer()
        route = synth.synthesize(
            journey=sample_journey,
            business_model=sample_business_model,
            business_eval=sample_business_eval,
            entities=sample_entities,
        )
        d = route.to_dict()
        route2 = InferredRoute.from_dict(d)
        assert route2.title == route.title
        assert route2.primary_program == route.primary_program
        assert len(route2.phases) == len(route.phases)

    def test_to_dict_includes_reference_comparison(self):
        ref = ReferenceRouteSummary(
            reference_name="Ref",
            coverage_pct=75.0,
            steps_matched=3,
            steps_total=4,
        )
        route = InferredRoute(
            route_id="route-test",
            title="Teste",
            summary="Resumo",
            reference_comparison=ref,
        )
        d = route.to_dict()
        assert d["reference_comparison"]["coverage_pct"] == 75.0

    def test_to_dict_without_reference_comparison(self):
        route = InferredRoute(route_id="route-test", title="Teste", summary="Resumo")
        d = route.to_dict()
        assert "reference_comparison" not in d


class TestReferenceRouteSummary:
    def test_to_dict_and_from_dict(self):
        ref = ReferenceRouteSummary(
            reference_name="Ref Oficial",
            reference_source="doc.pdf",
            phases_matched=3,
            phases_total=4,
            steps_matched=12,
            steps_total=15,
            coverage_pct=80.0,
            notes=["Boa cobertura."],
        )
        d = ref.to_dict()
        ref2 = ReferenceRouteSummary.from_dict(d)
        assert ref2.reference_name == "Ref Oficial"
        assert ref2.coverage_pct == 80.0
        assert ref2.notes == ["Boa cobertura."]


# ── Testes do RoteiroSynthesizer ──


class TestSynthesize:
    def test_empty_synthesis(self):
        """Síntese sem entradas — deve retornar roteiro mínimo."""
        synth = RoteiroSynthesizer()
        route = synth.synthesize()
        assert isinstance(route, InferredRoute)
        assert route.route_id != ""
        assert route.title != ""
        assert route.phases == []

    def test_synthesis_with_journey_only(self, sample_journey):
        """Síntese apenas com jornada — deve gerar fases."""
        synth = RoteiroSynthesizer()
        route = synth.synthesize(journey=sample_journey)
        assert route.primary_program == "est361"
        assert len(route.phases) > 0

    def test_synthesis_full(
        self, sample_journey, sample_business_model,
        sample_entities, sample_business_eval, sample_menu_nodes,
    ):
        """Síntese completa com todas as fontes."""
        synth = RoteiroSynthesizer()
        route = synth.synthesize(
            journey=sample_journey,
            business_model=sample_business_model,
            business_eval=sample_business_eval,
            entities=sample_entities,
            menu_nodes=sample_menu_nodes,
        )
        assert route.route_id.startswith("route-est361-")
        assert route.primary_program == "est361"
        assert route.title == "Inclusão de Pedido de Venda"
        assert len(route.supporting_programs) > 0
        assert len(route.phases) > 0

        # Todas as fases devem ter passos
        for phase in route.phases:
            assert phase.phase_id != ""
            assert phase.title != ""
            assert len(phase.steps) > 0

    def test_synthesis_with_reference(
        self, sample_journey, sample_business_model,
        sample_entities, sample_business_eval, sample_reference, sample_menu_nodes,
    ):
        """Síntese com comparação de referência."""
        synth = RoteiroSynthesizer()
        route = synth.synthesize(
            journey=sample_journey,
            business_model=sample_business_model,
            business_eval=sample_business_eval,
            entities=sample_entities,
            menu_nodes=sample_menu_nodes,
            reference_route=sample_reference,
        )
        assert route.reference_comparison is not None
        ref = route.reference_comparison
        assert ref.reference_name == "Inclusão de Pedido de Venda — Referência Oficial"
        assert ref.steps_total > 0
        assert ref.coverage_pct > 0

    def test_summary_includes_business_eval(
        self, sample_journey, sample_business_model,
        sample_entities, sample_business_eval, sample_menu_nodes,
    ):
        synth = RoteiroSynthesizer()
        route = synth.synthesize(
            journey=sample_journey,
            business_model=sample_business_model,
            business_eval=sample_business_eval,
            entities=sample_entities,
            menu_nodes=sample_menu_nodes,
        )
        assert "9/11" in route.summary

    def test_supporting_programs_detected(self, sample_journey, sample_business_model, sample_entities, sample_business_eval, sample_menu_nodes):
        synth = RoteiroSynthesizer()
        route = synth.synthesize(
            journey=sample_journey,
            business_model=sample_business_model,
            business_eval=sample_business_eval,
            entities=sample_entities,
            menu_nodes=sample_menu_nodes,
        )
        supp_names = {p.program_name for p in route.supporting_programs}
        assert {"cad110", "epp010", "fat210"} <= supp_names

    def test_primary_program_ignores_menu_entry_screen(self, sample_business_model, sample_entities, sample_business_eval, sample_menu_nodes):
        synth = RoteiroSynthesizer()
        journey = JourneyDefinition(
            journey_id="j-menu-est",
            name="Inclusão de Pedido de Venda",
            description="Fluxo navegando via menu de estoque",
            category="venda",
            entry_screen="menu_est",
            steps=[
                JourneyStep(step_order=1, screen_id="menu_est", action="navigate", description="Acessar menu de estoque"),
                JourneyStep(step_order=2, screen_id="est361", action="navigate", description="Acessar rotina est361"),
            ],
        )
        route = synth.synthesize(
            journey=journey,
            business_model=sample_business_model,
            business_eval=sample_business_eval,
            entities=sample_entities,
            menu_nodes=sample_menu_nodes,
        )
        assert route.primary_program == "est361"
        assert route.primary_menu == "3.6.1 Inclusão de Pedido de Venda"

    def test_route_id_is_deterministic(self, sample_journey, sample_business_model, sample_entities, sample_business_eval, sample_menu_nodes):
        synth = RoteiroSynthesizer()
        route1 = synth.synthesize(
            journey=sample_journey,
            business_model=sample_business_model,
            business_eval=sample_business_eval,
            entities=sample_entities,
            menu_nodes=sample_menu_nodes,
        )
        route2 = synth.synthesize(
            journey=sample_journey,
            business_model=sample_business_model,
            business_eval=sample_business_eval,
            entities=sample_entities,
            menu_nodes=sample_menu_nodes,
        )
        assert route1.route_id == route2.route_id


class TestMarkdownOutput:
    def test_to_markdown_contains_expected_sections(
        self, sample_journey, sample_business_model,
        sample_entities, sample_business_eval, sample_menu_nodes,
    ):
        synth = RoteiroSynthesizer()
        route = synth.synthesize(
            journey=sample_journey,
            business_model=sample_business_model,
            business_eval=sample_business_eval,
            entities=sample_entities,
            menu_nodes=sample_menu_nodes,
        )
        md = route.to_markdown()
        assert "# Inclusão de Pedido de Venda" in md
        assert "## Metadados de Rastreabilidade" in md
        assert "## Resumo" in md
        assert "## Fases do Processo" in md
        assert "est361" in md

    def test_to_markdown_with_reference(self, sample_journey, sample_business_model, sample_entities, sample_business_eval, sample_reference, sample_menu_nodes):
        synth = RoteiroSynthesizer()
        route = synth.synthesize(
            journey=sample_journey,
            business_model=sample_business_model,
            business_eval=sample_business_eval,
            entities=sample_entities,
            menu_nodes=sample_menu_nodes,
            reference_route=sample_reference,
        )
        md = route.to_markdown()
        assert "## Comparação com Roteiro de Referência" in md

    def test_to_markdown_shows_menu_when_available(self, sample_journey, sample_business_model, sample_entities, sample_business_eval, sample_menu_nodes):
        synth = RoteiroSynthesizer()
        route = synth.synthesize(
            journey=sample_journey,
            business_model=sample_business_model,
            business_eval=sample_business_eval,
            entities=sample_entities,
            menu_nodes=sample_menu_nodes,
        )
        md = route.to_markdown()
        assert "3.6.1 Inclusão de Pedido de Venda" in md

    def test_to_markdown_minimal_route(self):
        route = InferredRoute(
            route_id="route-min",
            title="Roteiro Mínimo",
            summary="Resumo mínimo.",
        )
        md = route.to_markdown()
        assert "# Roteiro Mínimo" in md
        assert "## Resumo" in md
        assert "Resumo mínimo." in md
        # Não deve ter seções vazias com "## Programas de Apoio" sem itens
        assert "## Comparação com Roteiro de Referência" not in md


# ── Testes de funções auxiliares ──


class TestExtractModule:
    def test_standard_prg(self):
        assert _extract_module("est361") == "est"
        assert _extract_module("cad110") == "cad"
        assert _extract_module("fat210") == "fat"

    def test_with_path(self):
        assert _extract_module("/dakota/prg/ped/est361.prg") == "est"

    def test_short_name(self):
        assert _extract_module("a1") == "a1"

    def test_empty(self):
        assert _extract_module("") == ""


class TestClassifyStepType:
    def test_navigate(self):
        assert _classify_step_type("acessar rotina") == "navigate"

    def test_input(self):
        assert _classify_step_type("informar código") == "input"
        assert _classify_step_type("preencher campo") == "input"
        assert _classify_step_type("incluir item") == "input"

    def test_select(self):
        assert _classify_step_type("selecionar opção") == "select"
        assert _classify_step_type("pesquisar cliente") == "select"

    def test_submit(self):
        assert _classify_step_type("confirmar gravação") == "submit"
        assert _classify_step_type("gravar pedido") == "submit"
        assert _classify_step_type("finalizar") == "submit"

    def test_verify(self):
        assert _classify_step_type("validar dados") == "verify"
        assert _classify_step_type("verificar estoque") == "verify"

    def test_default(self):
        assert _classify_step_type("xyz desconhecido") == "navigate"


class TestHumanizeAction:
    def test_navigate_with_label(self):
        result = _humanize_action(1, "navigate to est361", {"program": "est361", "label": "Inclusão de Pedido"})
        assert "Acessar a rotina" in result
        assert "Inclusão de Pedido" in result

    def test_menu_selection(self):
        result = _humanize_action(1, "selecionar opção", {"option": "1. Pedido de Venda"})
        assert "Selecionar a opção" in result
        assert "Pedido de Venda" in result

    def test_cliente_input(self):
        result = _humanize_action(2, "input codigo_cliente", {})
        assert result == "Informar o código do cliente"

    def test_item_input(self):
        result = _humanize_action(3, "incluir item produto", {})
        assert "Incluir um item/produto" in result

    def test_confirm(self):
        result = _humanize_action(4, "gravar", {})
        assert "Confirmar a gravação" in result

    def test_fallback(self):
        result = _humanize_action(5, "operacao_especial", {})
        assert "Operacao especial" in result


class TestMapEntityToFriendly:
    def test_known_entities(self):
        assert _map_entity_to_friendly("cliente") == "Cliente"
        assert _map_entity_to_friendly("produto") == "Produto"
        assert _map_entity_to_friendly("pedido") == "Pedido"
        assert _map_entity_to_friendly("nota") == "Nota Fiscal"

    def test_normalization(self):
        assert _map_entity_to_friendly("cad110") == "Cliente"
        assert _map_entity_to_friendly("forn_sul") == "Fornecedor"

    def test_unknown(self):
        assert _map_entity_to_friendly("xyz_desconhecido") == "Xyz_desconhecido"


# ── Testes de fase semântica ──


class TestPhaseDetection:
    def test_phases_have_unique_ids(self, sample_journey, sample_business_model, sample_entities, sample_business_eval, sample_menu_nodes):
        synth = RoteiroSynthesizer()
        route = synth.synthesize(
            journey=sample_journey,
            business_model=sample_business_model,
            business_eval=sample_business_eval,
            entities=sample_entities,
            menu_nodes=sample_menu_nodes,
        )
        phase_ids = [p.phase_id for p in route.phases]
        assert len(phase_ids) == len(set(phase_ids))

    def test_phases_have_confidence(self, sample_journey, sample_business_model, sample_entities, sample_business_eval, sample_menu_nodes):
        synth = RoteiroSynthesizer()
        route = synth.synthesize(
            journey=sample_journey,
            business_model=sample_business_model,
            business_eval=sample_business_eval,
            entities=sample_entities,
            menu_nodes=sample_menu_nodes,
        )
        for phase in route.phases:
            assert 0 <= phase.confidence <= 1.0
        assert route.phases[0].menu_context


# ── Testes de serialização JSON ──


class TestJSONRoundtrip:
    def test_full_route_json_roundtrip(
        self, sample_journey, sample_business_model,
        sample_entities, sample_business_eval, sample_menu_nodes,
    ):
        synth = RoteiroSynthesizer()
        route = synth.synthesize(
            journey=sample_journey,
            business_model=sample_business_model,
            business_eval=sample_business_eval,
            entities=sample_entities,
            menu_nodes=sample_menu_nodes,
        )
        json_str = json.dumps(route.to_dict(), indent=2, ensure_ascii=False, default=str)
        d = json.loads(json_str)
        route2 = InferredRoute.from_dict(d)
        assert route2.title == route.title
        assert len(route2.phases) == len(route.phases)

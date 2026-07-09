"""Sintetizador de Roteiro de Processo — gera roteiro textual humano a partir de inferências.

Combina as saídas de:
- JourneyInferencer (jornadas de navegação)
- FlowInferencer (fluxos de negócio por dependência)
- BusinessRuleEngine (regras semânticas de negócio)
- SourceAnalyzer (evidências de código: PRGs, menus, telas)

Pipeline de síntese:
1. Extrair programa principal e menu da jornada
2. Detectar PRGs auxiliares por dependências e regras de negócio
3. Agrupar passos em fases semânticas por palavras-chave e contexto
4. Gerar passos com descrições textuais, confiança e evidências
5. Opcionalmente comparar com roteiro de referência
"""
from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from .roteiro_model import (
    InferredRoute,
    InferredRoutePhase,
    InferredRouteStep,
    MenuEvidence,
    ProgramEvidence,
    ReferenceRouteSummary,
)
from .journey import JourneyDefinition, JourneyStep
from .flow_inferencer import InferredBusinessModel, InferredFlow
from .business_rule_engine import BusinessRuleEngine, _normalize_entity_name, _ENTITY_ALIAS_MAP


# ── Palavras-chave para detecção de fase semântica ──
# Cada fase tem: (phase_id, título, objetivo, palavras-chave para match)
_PHASE_TEMPLATES: List[Tuple[str, str, str, List[str]]] = [
    (
        "phase-init",
        "Inicialização e Cliente",
        "Acessar a rotina principal, identificar ou cadastrar o cliente, "
        "verificar situação cadastral e limites.",
        [
            "cliente", "clie", "cadastro", "inicial", "abertura",
            "identific", "selecion", "consulta", "pesquisa",
            "codigo", "nome", "cgc", "cpf", "cnpj", "limit",
            "situacao", "bloque", "inadimp",
            "cliente_desde", "cliente_desde", "cadpes",
        ],
    ),
    (
        "phase-values",
        "Valores e Logística",
        "Definir condições comerciais: tabela de preço, prazo de entrega, "
        "tipo de frete, transportadora, desconto e acréscimo geral.",
        [
            "condicao", "pagamento", "prazo", "entrega", "frete",
            "transport", "tabela", "preco", "desconto", "acrescimo",
            "comissao", "vendedor", "ven", "natop", "cfop",
            "logistic", "exped", "tipo_cobr", "cond_pagto",
            "dt_entrega", "dt_prev", "vlr_frete",
        ],
    ),
    (
        "phase-items",
        "Itens do Pedido",
        "Incluir, alterar e validar os produtos/itens do pedido: "
        "código, quantidade, preço unitário, desconto por item, "
        "totalização e verificação de estoque.",
        [
            "item", "produto", "prod", "ean", "referencia",
            "quantidade", "qtd", "qtde", "preco", "unitario",
            "desconto", "total", "subtotal", "estoque",
            "grade", "tamanho", "cor", "incluir", "alterar",
            "excluir", "remover", "grid", "browse",
        ],
    ),
    (
        "phase-payment",
        "Pagamento e Fechamento",
        "Definir forma de pagamento, calcular totais, gerar parcela(s), "
        "confirmar e gravar o pedido.",
        [
            "pagamento", "parcela", "total", "final",
            "fechamento", "gravar", "confirmar", "salvar",
            "dinheiro", "cartao", "boleto", "cheque", "pix",
            "forma_pagto", "tipo_pagto", "vlr_total",
            "vlr_parcela", "num_parc", "troco", "finalizar",
        ],
    ),
]

# ── Palavras-chave de ação → tipo de step ──
_ACTION_TYPE_MAP: Dict[str, str] = {
    "acess": "navigate",
    "selecion": "select",
    "inform": "input",
    "digitar": "input",
    "preencher": "input",
    "incluir": "input",
    "alterar": "input",
    "excluir": "select",
    "confirmar": "submit",
    "gravar": "submit",
    "salvar": "submit",
    "finalizar": "submit",
    "validar": "verify",
    "verificar": "verify",
    "consultar": "verify",
    "pesquisar": "select",
    "aguardar": "wait",
}

# ── Módulo → nome amigável ──
_MODULE_FRIENDLY_NAMES: Dict[str, str] = {
    "ped": "Pedido",
    "cad": "Cadastro",
    "fat": "Faturamento",
    "est": "Estoque",
    "cre": "Contas a Receber",
    "cmp": "Compras",
    "forn": "Fornecedor",
    "ven": "Vendas",
    "ban": "Financeiro",
    "trib": "Fiscal",
    "rel": "Relatórios",
    "utl": "Utilitários",
}


def _extract_module(program_name: str) -> str:
    """Extrai prefixo de módulo (2-3 letras iniciais) de um nome de PRG."""
    if not program_name:
        return ""
    name = program_name.lower()
    # Remove caminho, pega só o nome do arquivo
    name = name.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    # Remove extensão
    name = re.sub(r"\.(prg|src|dbo)$", "", name)
    m = re.match(r"^([a-z]{2,4})", name)
    return m.group(1) if m else name[:3]


def _looks_like_program(program_name: str) -> bool:
    """Heurística simples para diferenciar PRG real de screen/menu lógico."""
    if not program_name:
        return False
    name = program_name.lower().strip()
    if name == "menu_principal" or name.startswith("menu_") or name.startswith("menuoption_"):
        return False
    name = name.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    name = re.sub(r"\.(prg|src|dbo)$", "", name)
    return bool(re.match(r"^[a-z]{2,4}\d+[a-z0-9_]*$", name))


def _tokenize_for_phase(text: str) -> Set[str]:
    """Tokeniza texto para matching de fase: lowercase, split por não-alfanumérico."""
    tokens = re.split(r"[^a-z0-9]+", text.lower())
    return {t for t in tokens if len(t) >= 2}


def _classify_step_type(action_text: str) -> str:
    """Classifica o tipo de passo (navigate, input, select, submit, verify, wait)."""
    low = action_text.lower()
    for keyword, step_type in _ACTION_TYPE_MAP.items():
        if keyword in low:
            return step_type
    return "navigate"


def _humanize_action(step_order: int, action_text: str, context: Dict[str, str]) -> str:
    """Transforma uma ação técnica em frase descritiva para leitura humana.

    Exemplos:
        "navigate to est361" → "Acessar a rotina de Inclusão de Pedido (est361)"
        "input codigo_cliente" → "Informar o código do cliente"
        "submit gravar" → "Confirmar a gravação do pedido"
    """
    low = action_text.lower()

    # Padrões de transformação
    if "navigate" in low or "acess" in low:
        prog = context.get("program", "")
        label = context.get("label", "")
        if label:
            return f"Acessar a rotina \"{label}\"{' (' + prog + ')' if prog else ''}"
        return f"Acessar a rotina{(' ' + prog) if prog else ''}"

    if "menu" in low or "selecion" in low:
        option = context.get("option", context.get("label", ""))
        if option:
            return f"Selecionar a opção \"{option}\" no menu"
        return f"Selecionar a opção no menu principal"

    if "cliente" in low or "codigo" in low:
        return "Informar o código do cliente"

    if "condicao" in low or "pagamento" in low:
        return "Informar a condição de pagamento"

    if "tabela" in low or "preco" in low:
        return "Selecionar a tabela de preço"

    if "item" in low or "produto" in low:
        if "incluir" in low:
            return "Incluir um item/produto no pedido"
        if "alterar" in low:
            return "Alterar dados do item no pedido"
        if "excluir" in low:
            return "Excluir item do pedido"
        return "Informar os dados do item/produto"

    if "quantidade" in low:
        return "Informar a quantidade do item"

    if "desconto" in low:
        return "Informar o desconto"

    if "gravar" in low or "confirmar" in low or "salvar" in low:
        return "Confirmar a gravação do pedido"

    if "finalizar" in low:
        return "Finalizar o pedido"

    # Fallback: capitalizar a ação
    entity = context.get("entity", "")
    if entity and low.startswith("create "):
        return f"Cadastrar {entity}"
    if entity and low.startswith("update "):
        return f"Atualizar dados de {entity}"

    parts = action_text.replace("_", " ").split()
    return " ".join(parts).capitalize()


def _map_entity_to_friendly(entity_name: str) -> str:
    """Mapeia nome de entidade para nome amigável em português."""
    normalized = _normalize_entity_name(entity_name)
    friendly_map = {
        "cliente": "Cliente",
        "produto": "Produto",
        "pedido": "Pedido",
        "nota": "Nota Fiscal",
        "duplicata": "Duplicata",
        "vendedor": "Vendedor",
        "fornecedor": "Fornecedor",
        "compra": "Ordem de Compra",
        "estoque": "Estoque",
        "empresa": "Empresa",
        "filial": "Filial",
        "funcionario": "Funcionário",
        "contapagar": "Contas a Pagar",
        "banco": "Banco",
        "tributacao": "Tributação",
    }
    return friendly_map.get(normalized, normalized.capitalize())


@dataclass
class _SynthesisContext:
    """Contexto interno para a síntese — reúne todos os insumos processados."""

    journey: Optional[JourneyDefinition] = None
    business_model: Optional[InferredBusinessModel] = None
    business_eval: Optional[Dict[str, Any]] = None
    entity_map: Dict[str, Any] = field(default_factory=dict)
    menu_options: List[MenuEvidence] = field(default_factory=list)
    program_evidence_map: Dict[str, ProgramEvidence] = field(default_factory=dict)
    primary_program: str = ""
    primary_menu: str = ""


class RoteiroSynthesizer:
    """Sintetiza um roteiro textual de processo de negócio.

    Combina múltiplas fontes de inferência (jornada, fluxo, regras de negócio,
    evidências de código) para gerar um documento estruturado similar a um
    roteiro de referência como "Inclusão de Pedido de Venda".

    Uso básico:
        synth = RoteiroSynthesizer()
        route = synth.synthesize(
            journey=journey_def,
            business_model=flow_model,
            business_eval=engine_eval,
            entities=entity_defs,
            menu_nodes=menu_nodes_list,
            db_connection=sqlite3_conn,  # para simulação
        )
        print(route.to_markdown())
    """

    def __init__(self, db_connection: Any = None):
        self._db = db_connection

    def synthesize(
        self,
        *,
        journey: Optional[JourneyDefinition] = None,
        business_model: Optional[InferredBusinessModel] = None,
        business_eval: Optional[Dict[str, Any]] = None,
        entities: Optional[List[Any]] = None,
        menu_nodes: Optional[List[Any]] = None,
        reference_route: Optional[Dict[str, Any]] = None,
    ) -> InferredRoute:
        """Sintetiza um roteiro completo a partir das fontes de inferência.

        Args:
            journey: Jornada inferida (JourneyDefinition)
            business_model: Modelo de negócio inferido (InferredBusinessModel)
            business_eval: Resultado da avaliação do BusinessRuleEngine
            entities: Lista de EntityDefinition do source_analyzer
            menu_nodes: Lista de MenuNode do menu_analyzer
            reference_route: Dicionário com dados de roteiro de referência para comparação

        Returns:
            InferredRoute com fases, passos, evidências e metadados
        """
        ctx = _SynthesisContext(
            journey=journey,
            business_model=business_model,
            business_eval=business_eval,
        )

        # 1. Extrair programa principal e menu da jornada
        self._extract_primary_context(ctx)

        # 2. Coletar evidências de programas
        self._collect_program_evidence(ctx, entities, menu_nodes)

        # 3. Detectar PRGs auxiliares
        supporting = self._detect_supporting_programs(ctx, business_model)

        # 4. Extrair opções de menu relevantes
        self._extract_menu_evidence(ctx, menu_nodes)

        # 5. Gerar título e resumo
        title = self._generate_title(ctx)
        summary = self._generate_summary(ctx, supporting)

        # 6. Criar fases semânticas
        phases = self._build_phases(ctx, business_model)

        # 7. Construir roteiro
        route = InferredRoute(
            route_id=self._generate_route_id(ctx),
            title=title,
            summary=summary,
            primary_menu=ctx.primary_menu,
            primary_program=ctx.primary_program,
            supporting_programs=supporting,
            phases=phases,
        )

        # 8. Simular com dados sintéticos (validação do roteiro)
        if ctx.journey and self._db:
            route.simulation = self._simulate(ctx.journey)

        # 9. Comparar com referência se fornecida
        if reference_route:
            route.reference_comparison = self._compare_with_reference(
                route, reference_route
            )

        return route

    # ── helpers privados ──

    def _extract_primary_context(self, ctx: _SynthesisContext) -> None:
        """Extrai programa principal e menu da jornada."""
        if ctx.journey:
            ctx.primary_program = self._infer_primary_program_from_journey(ctx.journey)
            if not ctx.primary_program and _looks_like_program(ctx.journey.entry_screen):
                ctx.primary_program = ctx.journey.entry_screen
            # Tenta extrair menu do nome da jornada ou descrição
            desc = ctx.journey.description or ""
            menu_match = re.search(r"\bmenu\s*:\s*([^\s,;]+)", desc, re.IGNORECASE)
            if menu_match:
                ctx.primary_menu = menu_match.group(1)

    def _infer_primary_program_from_journey(self, journey: JourneyDefinition) -> str:
        """Infere o PRG principal ignorando entry screens de menu lógico."""
        for step in sorted(journey.steps, key=lambda s: s.step_order):
            if _looks_like_program(step.screen_id):
                return step.screen_id
        return ""

    def _collect_program_evidence(
        self,
        ctx: _SynthesisContext,
        entities: Optional[List[Any]],
        menu_nodes: Optional[List[Any]],
    ) -> None:
        """Coleta evidências de programas a partir das entidades."""
        if not entities:
            return

        for ent in entities:
            name = getattr(ent, "name", "")
            source = getattr(ent, "source", "")
            storage = getattr(ent, "storage_type", "unknown")
            if not name:
                continue

            module = _extract_module(name)
            # Tenta extrair título de fonte
            title = ""
            if hasattr(ent, "operations"):
                for op in getattr(ent, "operations", []):
                    if hasattr(op, "source_file") and op.source_file:
                        source = source or op.source_file
                    break

            ctx.program_evidence_map[name] = ProgramEvidence(
                program_name=name,
                program_path=source,
                module=module,
                program_type="main" if name.lower() == ctx.primary_program.lower() else "support",
                title=title,
                operations=[],
            )

            # Associa entidade ao mapa
            ctx.entity_map[name] = ent

    def _detect_supporting_programs(
        self,
        ctx: _SynthesisContext,
        business_model: Optional[InferredBusinessModel],
    ) -> List[ProgramEvidence]:
        """Detecta PRGs auxiliares por dependências e regras de negócio."""
        supporting: List[ProgramEvidence] = []
        seen: Set[str] = {ctx.primary_program.lower()} if ctx.primary_program else set()

        # Via fluxo de negócio: entidades do mesmo componente conectado
        if business_model and business_model.flows:
            for flow in business_model.flows:
                for entity_name in flow.entities_in_flow:
                    for pe in self._resolve_program_candidates_for_entity(entity_name, ctx):
                        if pe.program_name.lower() in seen:
                            continue
                        seen.add(pe.program_name.lower())
                        pe.program_type = "support"
                        supporting.append(pe)

        return supporting

    def _resolve_program_candidates_for_entity(
        self,
        entity_name: str,
        ctx: _SynthesisContext,
    ) -> List[ProgramEvidence]:
        """Mapeia uma entidade de negócio para PRGs reais associados."""
        normalized = _normalize_entity_name(entity_name)
        candidates: List[ProgramEvidence] = []

        for pe in ctx.program_evidence_map.values():
            if pe.program_name.lower() == ctx.primary_program.lower():
                continue
            candidate_norm = _normalize_entity_name(pe.program_name)
            if candidate_norm != normalized:
                continue
            candidates.append(pe)

        def _score(pe: ProgramEvidence) -> tuple[int, int, str]:
            path_score = 1 if pe.program_path else 0
            numeric_score = 1 if _looks_like_program(pe.program_name) else 0
            return (path_score, numeric_score, pe.program_name)

        return sorted(candidates, key=_score, reverse=True)

    def _extract_menu_evidence(
        self,
        ctx: _SynthesisContext,
        menu_nodes: Optional[List[Any]],
    ) -> None:
        """Extrai evidências de menu relevantes ao programa principal."""
        if not menu_nodes:
            return

        for node in menu_nodes:
            program = getattr(node, "program_name", "")
            if program and program.lower() == ctx.primary_program.lower():
                ctx.menu_options.append(MenuEvidence(
                    menu_name=getattr(node, "label", ""),
                    menu_path=getattr(node, "source_file", ""),
                    option_label=getattr(node, "label", ""),
                    option_key=getattr(node, "key", ""),
                    target_program=program,
                    source_line=getattr(node, "source_line", 0),
                ))
                if not ctx.primary_menu:
                    ctx.primary_menu = getattr(node, "label", "")

    def _generate_title(self, ctx: _SynthesisContext) -> str:
        """Gera título do roteiro baseado no contexto."""
        if ctx.journey and ctx.journey.name:
            return ctx.journey.name

        module = _extract_module(ctx.primary_program)
        friendly = _MODULE_FRIENDLY_NAMES.get(module, module.upper())
        return f"Roteiro de {friendly} — {ctx.primary_program}"

    def _generate_summary(
        self,
        ctx: _SynthesisContext,
        supporting: List[ProgramEvidence],
    ) -> str:
        """Gera resumo textual do roteiro."""
        parts: List[str] = []

        if ctx.journey and ctx.journey.description:
            parts.append(ctx.journey.description)
        else:
            module = _extract_module(ctx.primary_program)
            friendly = _MODULE_FRIENDLY_NAMES.get(module, "Processo")
            parts.append(
                f"Roteiro automático inferido para o processo de {friendly}, "
                f"gerado a partir da análise de código-fonte, jornadas de "
                f"navegação e regras de negócio do sistema Dakota."
            )

        if supporting:
            names = [p.program_name for p in supporting[:5]]
            parts.append(
                f"Programas auxiliares detectados: {', '.join(f'`{n}`' for n in names)}."
            )

        if ctx.business_eval:
            rules_ok = ctx.business_eval.get("rules_ok", 0)
            rules_total = ctx.business_eval.get("rules_evaluated", 0)
            if rules_total:
                parts.append(
                    f"Cobertura de regras de negócio: {rules_ok}/{rules_total}."
                )

        return " ".join(parts)

    def _generate_route_id(self, ctx: _SynthesisContext) -> str:
        """Gera ID único para o roteiro."""
        prog = ctx.primary_program or "unknown"
        parts: List[str] = [prog]
        if ctx.journey:
            parts.extend(f"{step.step_order}:{step.screen_id}:{step.action}:{step.description}" for step in ctx.journey.steps)
        raw = "|".join(parts)
        short_id = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:8]
        return f"route-{prog}-{short_id}"

    def _build_phases(
        self,
        ctx: _SynthesisContext,
        business_model: Optional[InferredBusinessModel],
    ) -> List[InferredRoutePhase]:
        """Constrói fases semânticas agrupando passos da jornada e do fluxo."""

        # Coleta todos os passos brutos (da jornada + fluxo)
        raw_steps: List[Tuple[str, str, float, List[Any]]] = []  # (action, type, confidence, evidence)

        # Passos da jornada
        if ctx.journey and ctx.journey.steps:
            first_menu_ev = ctx.menu_options[0] if ctx.menu_options else None
            for index, step in enumerate(sorted(ctx.journey.steps, key=lambda s: s.step_order), start=1):
                action = step.description or step.action
                step_type = step.action if step.action in _ACTION_TYPE_MAP.values() else _classify_step_type(action)
                # Evidência do step
                evidence: List[Any] = []
                if step.screen_id:
                    pe = ctx.program_evidence_map.get(step.screen_id)
                    if pe:
                        evidence.append(pe)
                if first_menu_ev and index <= 2 and step.action in ("select", "navigate"):
                    evidence.append(first_menu_ev)
                raw_steps.append((action, step_type, 0.8, evidence))

        # Passos do fluxo de negócio
        if business_model and business_model.flows:
            for flow in business_model.flows:
                for step_dict in flow.steps:
                    entity = step_dict.get("entity", "")
                    operation = step_dict.get("operation", "create")
                    why = step_dict.get("why", "")
                    friendly = _map_entity_to_friendly(entity)
                    action = f"{operation} {friendly}"
                    step_type = "input" if operation == "create" else "submit"
                    evidence = self._resolve_program_candidates_for_entity(entity, ctx)[:1]
                    raw_steps.append((action, step_type, 0.6, evidence))

        # Agrupa passos por fase semântica
        phase_buckets: Dict[int, List[Tuple[str, str, float, List[Any]]]] = defaultdict(list)
        unassigned: List[Tuple[str, str, float, List[Any]]] = []

        for action, step_type, confidence, evidence in raw_steps:
            assigned = False
            for phase_idx, (_, _, _, keywords) in enumerate(_PHASE_TEMPLATES):
                tokens = _tokenize_for_phase(action)
                if tokens & {kw.lower() for kw in keywords}:
                    phase_buckets[phase_idx].append((action, step_type, confidence, evidence))
                    assigned = True
                    break
            if not assigned:
                unassigned.append((action, step_type, confidence, evidence))

        # Distribui não-assinalados para a fase mais próxima ou cria fase genérica
        if unassigned:
            # Tenta fase "Itens" como fallback para maioria dos casos
            phase_buckets[2].extend(unassigned)

        # Constroi InferredRoutePhase para cada bucket
        phases: List[InferredRoutePhase] = []
        for phase_idx, (phase_id, title, objective, _) in enumerate(_PHASE_TEMPLATES):
            bucket = phase_buckets.get(phase_idx, [])
            if not bucket:
                continue

            steps: List[InferredRouteStep] = []
            for order, (action, step_type, confidence, evidence) in enumerate(bucket, start=1):
                # Detecta programa associado
                prog = ""
                for ev in evidence:
                    if isinstance(ev, ProgramEvidence):
                        prog = ev.program_name
                        break

                menu_opt = ""
                for ev in evidence:
                    if isinstance(ev, MenuEvidence):
                        menu_opt = ev.option_label
                        break

                human_context = {
                    "program": prog,
                    "label": "",
                    "option": menu_opt,
                    "entity": "",
                }
                if step_type == "input" and action.lower().startswith(("create ", "update ")):
                    entity_label = action.split(" ", 1)[1] if " " in action else action
                    human_context["entity"] = entity_label.lower()

                # Humaniza a ação
                human_action = _humanize_action(order, action, human_context)

                steps.append(InferredRouteStep(
                    order=order,
                    action=human_action,
                    type=step_type,
                    menu_option=menu_opt,
                    program=prog,
                    depends_on=[],
                    confidence=round(confidence, 2),
                    evidence=evidence,
                ))

            # Confiança da fase: média das confianças dos passos
            phase_confidence = (
                sum(s.confidence for s in steps) / len(steps) if steps else 0.0
            )

            # Contexto da fase: evidências de menu e programa
            menu_context: List[MenuEvidence] = []
            program_context: List[ProgramEvidence] = []
            for step in steps:
                for ev in step.evidence:
                    if isinstance(ev, MenuEvidence) and ev not in menu_context:
                        menu_context.append(ev)
                    if isinstance(ev, ProgramEvidence) and ev not in program_context:
                        program_context.append(ev)
            if phase_idx == 0:
                for ev in ctx.menu_options:
                    if ev not in menu_context:
                        menu_context.append(ev)

            phases.append(InferredRoutePhase(
                phase_id=phase_id,
                title=title,
                objective=objective,
                menu_context=menu_context,
                program_context=program_context,
                steps=steps,
                confidence=round(phase_confidence, 2),
            ))

        return phases

    def _compare_with_reference(
        self,
        route: InferredRoute,
        reference: Dict[str, Any],
    ) -> ReferenceRouteSummary:
        """Compara o roteiro inferido com um roteiro de referência."""
        ref_phases = reference.get("phases", [])
        ref_steps_total = sum(len(p.get("steps", [])) for p in ref_phases)
        ref_phase_titles = {p.get("title", "").lower() for p in ref_phases}

        # Quantas fases inferidas batem com fases de referência
        matched_phases = 0
        for phase in route.phases:
            if phase.title.lower() in ref_phase_titles:
                matched_phases += 1

        # Quantos passos inferidos batem (por similaridade de ação)
        ref_actions: Set[str] = set()
        for p in ref_phases:
            for s in p.get("steps", []):
                action = s.get("action", s.get("description", ""))
                ref_actions.add(action.lower())

        matched_steps = 0
        infer_steps_total = sum(len(p.steps) for p in route.phases)
        for phase in route.phases:
            for step in phase.steps:
                if step.action.lower() in ref_actions:
                    matched_steps += 1

        coverage = (matched_steps / max(ref_steps_total, 1)) * 100

        notes: List[str] = []
        if coverage >= 80:
            notes.append("Alta cobertura: o roteiro inferido cobre a maioria dos passos da referência.")
        elif coverage >= 50:
            notes.append("Cobertura média: alguns passos da referência não foram detectados automaticamente.")
        else:
            notes.append("Baixa cobertura: o código-fonte pode não conter evidências suficientes para todos os passos.")

        if route.phases and ref_phases:
            infer_phase_count = len(route.phases)
            ref_phase_count = len(ref_phases)
            if infer_phase_count < ref_phase_count:
                notes.append(
                    f"O roteiro inferido tem {infer_phase_count} fases "
                    f"contra {ref_phase_count} na referência."
                )

        return ReferenceRouteSummary(
            reference_name=reference.get("name", "Roteiro de Referência"),
            reference_source=reference.get("source", ""),
            phases_matched=matched_phases,
            phases_total=len(ref_phases),
            steps_matched=matched_steps,
            steps_total=ref_steps_total,
            coverage_pct=round(coverage, 1),
            notes=notes,
        )

    def _simulate(self, journey: JourneyDefinition) -> SimulationResult:
        """Gera dados sintéticos para validar a jornada.

        Tenta gerar um dataset para a jornada e diagnostica:
        - Passos sem input_template (não geram dados realistas)
        - Passos com placeholder não resolvido
        - Total de campos gerados por sessão
        """
        from .roteiro_model import SimulationResult
        from .journey_builder import JourneyBuilder
        import sqlite3

        warnings: List[str] = []
        sessions_data: List[Dict[str, Any]] = []
        total_fields = 0
        ok = True

        # Diagnostica templates faltantes
        missing_templates = []
        for step in journey.steps:
            if step.action in ("input", "select") and not (
                step.input_template and step.input_template.strip()
            ):
                missing_templates.append(
                    f"Passo #{step.step_order} ({step.action}): sem input_template "
                    f"— dados gerados serão genéricos. "
                    f"Descrição: {step.description[:60]}"
                )

        if missing_templates:
            warnings.append(
                f"{len(missing_templates)} passo(s) sem template de entrada. "
                f"Adicione placeholders como {{{{entidade.campo}}}} para dados realistas."
            )
            warnings.extend(missing_templates[:5])  # limita a 5
            if len(missing_templates) > 5:
                warnings.append(f"  ... +{len(missing_templates)-5} passos")

        # Gera dataset
        try:
            if not self._db:
                raise RuntimeError("sem conexão com banco")

            self._db.row_factory = sqlite3.Row
            builder = JourneyBuilder(db_connection=self._db)
            dataset = builder.build_journey_dataset(
                journey, session_count=3, seed=42
            )
            total_fields = len(dataset.get_session_inputs(0))

            # Amostra por sessão
            for si in range(min(3, dataset.session_count)):
                inputs = dataset.get_session_inputs(si)
                # Pega primeiros 8 valores como amostra
                sample = [str(v)[:80] for v in inputs[:8]]
                sessions_data.append({
                    "session": si + 1,
                    "total_campos": len(inputs),
                    "amostra": sample,
                })

        except Exception as e:
            warnings.append(f"Erro na geração de dados: {e}")
            ok = False

        if missing_templates:
            ok = False

        return SimulationResult(
            session_count=3,
            seed=42,
            total_fields=total_fields,
            sessions=sessions_data,
            warnings=warnings,
            ok=ok,
        )

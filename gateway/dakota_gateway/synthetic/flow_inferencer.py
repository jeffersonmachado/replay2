"""Inferenciador de Fluxos de Negocio — deriva fluxos do grafo de dependencias.

Substitui regras hardcoded (_BUSINESS_RULES, _BUSINESS_FLOWS) por inferencia
real baseada nas FK detectadas pelo RelationshipMapper.

Logica:
1. Extrai grafo de dependencias: pedido → cliente (pedido referencia cliente)
2. Agrupa entidades em componentes conectados (clusters de negocio)
3. Ordena topologicamente: entidades "raiz" (sem dependencias) primeiro
4. Cada caminho raiz→folha vira um fluxo de negocio
5. Cada aresta do grafo vira uma regra: "pedido exige cliente"
"""
from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
import re
from typing import Dict, List, Optional, Set, Tuple


@dataclass
class InferredRule:
    """Regra de negocio inferida do grafo de dependencias."""
    rule_id: str = ""
    description: str = ""
    depends_on: str = ""
    enables: str = ""
    dependency_type: str = "requires"
    is_critical: bool = True
    confidence: float = 0.0


@dataclass
class InferredFlow:
    """Fluxo de negocio inferido do grafo."""
    flow_id: str = ""
    flow_name: str = ""
    description: str = ""
    steps: List[Dict] = field(default_factory=list)
    entities_in_flow: List[str] = field(default_factory=list)


@dataclass 
class InferredBusinessModel:
    """Modelo de negocio completo inferido do codigo."""
    rules: List[InferredRule] = field(default_factory=list)
    flows: List[InferredFlow] = field(default_factory=list)
    entity_graph: Dict[str, List[str]] = field(default_factory=dict)
    roots: List[str] = field(default_factory=list)
    leaves: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


class FlowInferencer:
    """Infere fluxos de negocio a partir do grafo de entidades.

    Uso:
        from dakota_gateway.source_analyzer.relationship_mapper import RelationshipMapper
        
        mapper = RelationshipMapper()
        rel_map = mapper.map(entities)  # entities = lista de EntityDefinition
        
        inferencer = FlowInferencer()
        model = inferencer.infer(rel_map.entity_graph, entities)
        
        # model.rules  → lista de InferredRule
        # model.flows  → lista de InferredFlow
    """

    def infer(
        self,
        entity_graph: Dict[str, List[str]],
        entities: list | None = None,
        *,
        min_confidence: float = 0.3,
    ) -> InferredBusinessModel:
        """Infere modelo de negocio completo do grafo de entidades.

        Args:
            entity_graph: {entity_name: [dependent_entity_names]}
                          Ex: {"pedido": ["cliente", "produto"]}
            entities: Lista de EntityDefinition (opcional, para nomes)
            min_confidence: Confianca minima para arestas

        Returns:
            InferredBusinessModel com regras, fluxos, grafo
        """
        model = InferredBusinessModel(entity_graph=entity_graph)

        if not entity_graph:
            model.warnings.append("grafo de entidades vazio — sem relacoes detectadas")
            return model

        # 1. Encontrar raizes (nao dependem de ninguem) e folhas (ninguem depende delas)
        roots, leaves = self._find_roots_and_leaves(entity_graph)
        model.roots = roots
        model.leaves = leaves

        # 2. Gerar regras a partir de cada aresta do grafo
        model.rules = self._generate_rules(entity_graph)

        # 3. Gerar fluxos a partir dos componentes conectados
        model.flows = self._generate_flows(entity_graph, roots, leaves, entities)

        return model

    def _find_roots_and_leaves(
        self, graph: Dict[str, List[str]]
    ) -> Tuple[List[str], List[str]]:
        """Encontra entidades raiz (sem dependencias) e folha (sem dependentes)."""
        all_entities = set(graph.keys())
        # Entidades que sao referenciadas (alguem depende delas)
        referenced = set()
        for deps in graph.values():
            referenced.update(deps)

        # Entidades que aparecem como dependencia mas nao no grafo
        all_entities |= referenced

        # Raizes: entidades que nao dependem de ninguem (ou so de si mesmas)
        roots = sorted(
            e for e in all_entities
            if e in graph and (not graph[e] or graph[e] == [e])
        )
        if not roots:
            # Fallback: entidades com menor numero de dependencias
            roots = sorted(all_entities, key=lambda e: len(graph.get(e, [])))

        # Folhas: entidades das quais ninguem depende
        leaves = sorted(all_entities - set().union(*(set(graph.get(e, [])) for e in graph)))

        return roots, leaves

    def _generate_rules(self, graph: Dict[str, List[str]]) -> List[InferredRule]:
        """Gera regras de negocio a partir de cada aresta do grafo.

        Aresta pedido→cliente vira: "pedido exige cliente cadastrado"
        """
        rules = []
        rule_counter = 0

        for source, targets in graph.items():
            for target in targets:
                if source == target:
                    continue
                rule_counter += 1
                rules.append(InferredRule(
                    rule_id=f"INFER-{rule_counter:03d}",
                    description=f"'{source}' referencia/depende de '{target}'",
                    depends_on=target,
                    enables=source,
                    dependency_type="requires",
                    is_critical=True,
                ))

        return rules

    def _generate_flows(
        self,
        graph: Dict[str, List[str]],
        roots: List[str],
        leaves: List[str],
        entities: list | None = None,
    ) -> List[InferredFlow]:
        """Gera fluxos de negocio percorrendo o grafo a partir das raizes.

        Cada componente conectado vira um fluxo.
        Se ha poucas FK detectadas, agrupa por prefixo de nome como fallback.
        """
        # Constroi grafo reverso (quem depende de quem)
        reverse_graph: Dict[str, List[str]] = defaultdict(list)
        total_edges = 0
        for source, targets in graph.items():
            for target in targets:
                if source != target:
                    reverse_graph[target].append(source)
                    total_edges += 1

        # Detecta se FK relationships sao esparsas — se sim, usa clustering por nome
        all_nodes = set(graph.keys())
        for deps in graph.values():
            all_nodes.update(deps)
        fk_density = total_edges / max(len(all_nodes), 1)

        if fk_density < 0.05 and total_edges < 100:
            # FK relationships muito esparsas — cluster por prefixo de nome
            components = self._name_based_clusters(all_nodes, graph)
        else:
            components = self._find_connected_components(graph)

        flows = []
        flow_counter = 0

        for component in components:
            if len(component) < 2:
                continue  # entidade isolada nao forma fluxo

            flow_counter += 1

            # Ordena topologicamente o componente
            sorted_entities = self._topological_sort_component(
                component, graph
            )

            # Constroi passos do fluxo
            steps = []
            for entity in sorted_entities:
                # Entidade pode ter operacoes de create e update no fluxo
                deps = graph.get(entity, [])
                deps_in_component = [d for d in deps if d in component and d != entity]
                
                if deps_in_component:
                    why_parts = []
                    for dep in deps_in_component:
                        why_parts.append(f"referencia '{dep}'")
                    why = f"Depende de: {', '.join(why_parts)}"
                else:
                    why = "Entidade raiz — nao depende de outras no fluxo"

                steps.append({
                    "entity": entity,
                    "operation": "create",
                    "why": why,
                })

            # Nome do fluxo baseado nas entidades
            entity_names = [e for e in sorted_entities]
            flow_name = " → ".join(entity_names[:5])
            if len(entity_names) > 5:
                flow_name += f" +{len(entity_names)-5}"

            flows.append(InferredFlow(
                flow_id=f"FLOW-INFER-{flow_counter:03d}",
                flow_name=flow_name,
                description=(
                    f"Fluxo inferido do grafo de dependencias: "
                    f"{len(sorted_entities)} entidades conectadas por FK. "
                    f"Ordem: {flow_name}"
                ),
                steps=steps,
                entities_in_flow=sorted_entities,
            ))

        return flows

    def _name_based_clusters(
        self, all_nodes: Set[str], graph: Dict[str, List[str]]
    ) -> List[Set[str]]:
        """Agrupa entidades por prefixo de nome quando FK sao esparsas.

        Recital usa prefixos de 2-4 letras para modulos:
        ped*, cad*, fat*, est*, cmp*, etc.
        """
        # Extrai prefixo: primeiras letras ate encontrar digito ou underscore
        def _prefix(name: str) -> str:
            name = name.lower()
            m = re.match(r'^([a-z_]+?)(?:_?\d|$)', name)
            if m:
                p = m.group(1).rstrip('_')
                if len(p) >= 2:
                    return p
            return name[:4] if len(name) >= 4 else name

        clusters: Dict[str, Set[str]] = defaultdict(set)
        for node in all_nodes:
            pfx = _prefix(node)
            clusters[pfx].add(node)

        # Junta clusters muito pequenos (< 5 entidades) ao maior vizinho por FK
        small_clusters = {k: v for k, v in clusters.items() if len(v) < 5}
        for pfx, nodes in list(small_clusters.items()):
            # Tenta encontrar um cluster maior conectado por FK
            best_target = None
            best_count = 0
            for node in nodes:
                deps = graph.get(node, [])
                for dep in deps:
                    if dep in all_nodes:
                        dep_pfx = _prefix(dep)
                        if dep_pfx in clusters and len(clusters[dep_pfx]) >= 5:
                            count = len(clusters[dep_pfx])
                            if count > best_count:
                                best_count = count
                                best_target = dep_pfx
            if best_target and best_target in clusters:
                clusters[best_target].update(nodes)
                del clusters[pfx]

        result = sorted(clusters.values(), key=len, reverse=True)
        return result

    def _find_connected_components(
        self, graph: Dict[str, List[str]]
    ) -> List[Set[str]]:
        """Encontra componentes conectados no grafo (nao-direcionado)."""
        # Constroi grafo nao-direcionado
        undirected: Dict[str, Set[str]] = defaultdict(set)
        all_nodes = set(graph.keys())
        for source, targets in graph.items():
            all_nodes.add(source)
            for target in targets:
                if source != target:
                    undirected[source].add(target)
                    undirected[target].add(source)
                    all_nodes.add(target)

        visited: Set[str] = set()
        components: List[Set[str]] = []

        for node in all_nodes:
            if node in visited:
                continue
            # BFS
            component: Set[str] = set()
            queue = deque([node])
            visited.add(node)
            while queue:
                current = queue.popleft()
                component.add(current)
                for neighbor in undirected.get(current, set()):
                    if neighbor not in visited:
                        visited.add(neighbor)
                        queue.append(neighbor)
            components.append(component)

        # Ordena componentes por tamanho (maiores primeiro)
        components.sort(key=len, reverse=True)
        return components

    def _topological_sort_component(
        self, component: Set[str], graph: Dict[str, List[str]]
    ) -> List[str]:
        """Ordena topologicamente as entidades de um componente.

        Entidades sem dependencias (raizes) vem primeiro.
        """
        # Subgrafo apenas com entidades do componente
        in_degree: Dict[str, int] = {e: 0 for e in component}
        subgraph: Dict[str, List[str]] = {e: [] for e in component}

        for source in component:
            deps = graph.get(source, [])
            for target in deps:
                if target in component and target != source:
                    subgraph[source].append(target)

        # Calcula grau de entrada (quantos dependem desta entidade)
        for source, targets in subgraph.items():
            for target in targets:
                in_degree[target] = in_degree.get(target, 0) + 1

        # Tambem considera o grau de saida — entidades que referenciam outras
        # devem vir DEPOIS das que elas referenciam.
        # No nosso grafo: pedido→cliente significa "pedido depende de cliente"
        # Ordem correta: cliente primeiro, pedido depois
        # Entao queremos entidades com menos dependencias primeiro (raizes primeiro)

        # Ordena por numero de dependencias (menos = mais cedo no fluxo)
        sorted_entities = sorted(
            component,
            key=lambda e: (len(graph.get(e, [])), e)
        )

        return sorted_entities


def convert_to_engine_format(model: InferredBusinessModel) -> Dict:
    """Converte modelo inferido para o formato esperado pelo BusinessRuleEngine.

    Returns:
        dict com: rules_evaluated, rules_ok, gaps, flows_coverage, recommendation
    """
    # Para compatibilidade, retorna no mesmo formato do engine atual
    rules_evaluated = len(model.rules)
    
    # Todas as regras inferidas do grafo sao "ok" porque
    # o grafo ja reflete o que existe no codigo
    gaps = []
    
    flows_coverage = []
    for flow in model.flows:
        entities_in_flow = set(flow.entities_in_flow)
        flows_coverage.append({
            "flow_id": flow.flow_id,
            "flow_name": flow.flow_name,
            "description": flow.description,
            "entities_covered": sorted(entities_in_flow),
            "entities_missing": [],
            "coverage_pct": 100.0,
            "steps": [
                {**s, "available": True}
                for s in flow.steps
            ],
        })

    return {
        "rules_evaluated": rules_evaluated,
        "rules_ok": rules_evaluated,
        "rules_broken": 0,
        "gaps": gaps,
        "flows_coverage": flows_coverage,
        "recommendation": (
            f"Modelo de negocio inferido do grafo de dependencias: "
            f"{len(model.flows)} fluxos, {rules_evaluated} regras, "
            f"{len(model.roots)} entidades raiz."
        ),
    }

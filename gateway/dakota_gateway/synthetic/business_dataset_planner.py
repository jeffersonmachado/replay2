"""Planejador de dataset por grafo de dependencia de negocio.

Faz parte da entrega P2-A — Synthetic Knowledge Base.

Garante que os dados sinteticos sejam gerados na ordem correta:
1. Entidades raiz (sem dependencias): CLIENTES, PRODUTOS, FORNECEDORES
2. Entidades dependentes (com FK): PEDIDOS, ITENS, FINANCEIRO

Usa topological sort baseado no grafo de relacionamentos.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any, Optional

from ..source_analyzer.entity_catalog import EntityDefinition
from ..source_analyzer.relationship_mapper import RelationshipMap, Relationship
from ..source_analyzer.screen_entity_linker import ScreenEntityBinding


@dataclass
class DatasetPlan:
    """Plano de geracao para uma entidade no grafo de negocio."""
    entity_name: str = ""
    storage_type: str = ""
    dependencies: list[str] = field(default_factory=list)    # entidades das quais depende
    dependents: list[str] = field(default_factory=list)       # entidades que dependem dela
    fields: list[str] = field(default_factory=list)
    foreign_keys: list[dict] = field(default_factory=list)    # [{field, target_entity, target_field}]
    generation_order: int = 0
    is_root: bool = False
    is_leaf: bool = False
    suggested_quantity: int = 100
    metadata: dict = field(default_factory=dict)


@dataclass
class BusinessDependencyGraph:
    """Grafo completo de dependencias de negocio entre entidades."""
    plans: list[DatasetPlan] = field(default_factory=list)
    generation_order: list[str] = field(default_factory=list)   # ordem topologica
    roots: list[str] = field(default_factory=list)              # entidades sem dependencia
    leaves: list[str] = field(default_factory=list)             # entidades sem dependentes
    cycles: list[list[str]] = field(default_factory=list)       # ciclos detectados
    total_entities: int = 0
    max_depth: int = 0


class BusinessDatasetPlanner:
    """Planeja geracao de datasets respeitando o grafo de dependencia.

    Fluxo:
    1. Recebe entidades e relacionamentos do SourceParser
    2. Constroi grafo de dependencia (FK → entidade pai)
    3. Ordena topologicamente
    4. Gera planos com FK resolvidas, quantidades sugeridas e metadados
    """

    def __init__(self):
        self._entity_index: dict[str, EntityDefinition] = {}

    def plan(
        self,
        entities: list[EntityDefinition],
        relationship_map: RelationshipMap,
        bindings: Optional[list[ScreenEntityBinding]] = None,
    ) -> BusinessDependencyGraph:
        """Planeja geracao de datasets para todas as entidades."""
        self._entity_index = {e.name.upper(): e.name for e in entities}
        entity_map = {e.name.upper(): e for e in entities}

        # Constroi grafo de dependencia
        # dep_graph[A] = [B, C] significa que A depende de B e C
        dep_graph: dict[str, set[str]] = {}
        reverse_dep: dict[str, set[str]] = {}  # B → [A] (quem depende de B)

        for rel in relationship_map.relationships:
            src = rel.source_entity.upper()
            tgt = rel.target_entity.upper()

            if src not in entity_map or tgt not in entity_map:
                continue

            # So foreign_key e lookup influenciam dependencia de geracao
            if rel.relationship_type in ("foreign_key", "lookup"):
                dep_graph.setdefault(src, set()).add(tgt)
                reverse_dep.setdefault(tgt, set()).add(src)

        # Garante que todas as entidades estejam no grafo
        for ename in entity_map:
            dep_graph.setdefault(ename, set())
            reverse_dep.setdefault(ename, set())

        # Ordenacao topologica
        order, cycles = self._topological_sort(dep_graph)

        # Profundidade de cada entidade (distancia da raiz)
        depth: dict[str, int] = {}
        for ename in order:
            deps = dep_graph.get(ename, set())
            if not deps:
                depth[ename] = 0
            else:
                depth[ename] = 1 + max((depth.get(d, 0) for d in deps), default=0)

        # Constroi planos
        plans: list[DatasetPlan] = []
        for idx, ename in enumerate(order):
            entity = entity_map.get(ename)
            if not entity:
                continue

            deps = sorted(dep_graph.get(ename, set()))
            revs = sorted(reverse_dep.get(ename, set()))

            # FKs deste plano
            fks: list[dict] = []
            for rel in relationship_map.relationships:
                if (rel.source_entity.upper() == ename
                        and rel.relationship_type == "foreign_key"):
                    fks.append({
                        "field": rel.source_field,
                        "target_entity": rel.target_entity,
                        "target_field": rel.target_field or "id",
                        "cardinality": rel.cardinality,
                    })

            # Quantidade sugerida: entidades raiz geram mais registros
            is_root = len(deps) == 0
            suggested_qty = 200 if is_root else 100

            # Ajusta quantidade para entidades transacionais (pedidos, notas, etc.)
            # que tipicamente tem mais registros que entidades mestre
            if entity.storage_type in ("isam", "recital") and not is_root:
                suggested_qty = 150

            plan = DatasetPlan(
                entity_name=ename,
                storage_type=entity.storage_type,
                dependencies=deps,
                dependents=revs,
                fields=[f.name for f in entity.fields],
                foreign_keys=fks,
                generation_order=idx,
                is_root=is_root,
                is_leaf=len(revs) == 0,
                suggested_quantity=suggested_qty,
                metadata={
                    "depth": depth.get(ename, 0),
                    "field_count": len(entity.fields),
                    "index_count": len(entity.indexes),
                    "operation_count": len(entity.operations),
                },
            )
            plans.append(plan)

        roots = [p.entity_name for p in plans if p.is_root]
        leaves = [p.entity_name for p in plans if p.is_leaf]
        max_depth = max((depth.get(e, 0) for e in order), default=0)

        return BusinessDependencyGraph(
            plans=plans,
            generation_order=[p.entity_name for p in plans],
            roots=roots,
            leaves=leaves,
            cycles=cycles,
            total_entities=len(plans),
            max_depth=max_depth,
        )

    @staticmethod
    def _topological_sort(
        dep_graph: dict[str, set[str]],
    ) -> tuple[list[str], list[list[str]]]:
        """Ordenacao topologica (algoritmo de Kahn).

        dep_graph[A] = {B, C} significa: A depende de B e C.
        Portanto B e C devem ser processados antes de A.
        """
        # Conta quantas dependencias cada noh tem (out-degree do dep_graph)
        pending_deps: dict[str, int] = {
            n: len(targets) for n, targets in dep_graph.items()
        }

        # Fila: nós sem dependencias pendentes (raizes)
        queue: deque[str] = deque(
            n for n, d in pending_deps.items() if d == 0
        )
        order: list[str] = []

        while queue:
            node = queue.popleft()
            order.append(node)

            # Para cada entidade que depende de 'node', reduz pending_deps
            for dependent, targets in dep_graph.items():
                if node in targets:
                    pending_deps[dependent] -= 1
                    if pending_deps[dependent] == 0:
                        queue.append(dependent)

        # Detecta ciclos: nós com dependencias ainda pendentes
        remaining = [n for n, d in pending_deps.items() if d > 0]
        cycles: list[list[str]] = []
        if remaining:
            cycles.append(remaining)

        # Adiciona entidades em ciclo ao final
        order.extend(remaining)

        return order, cycles

    def plan_summary(self, graph: BusinessDependencyGraph) -> dict:
        """Resumo do plano de geracao, serializavel."""
        return {
            "total_entities": graph.total_entities,
            "generation_order": graph.generation_order,
            "roots": graph.roots,
            "leaves": graph.leaves,
            "max_depth": graph.max_depth,
            "cycles_detected": len(graph.cycles) > 0,
            "cycles": graph.cycles,
            "plans": [
                {
                    "entity_name": p.entity_name,
                    "storage_type": p.storage_type,
                    "dependencies": p.dependencies,
                    "dependents": p.dependents,
                    "foreign_keys": p.foreign_keys,
                    "generation_order": p.generation_order,
                    "is_root": p.is_root,
                    "is_leaf": p.is_leaf,
                    "suggested_quantity": p.suggested_quantity,
                    "depth": p.metadata.get("depth", 0),
                    "field_count": p.metadata.get("field_count", 0),
                }
                for p in graph.plans
            ],
        }

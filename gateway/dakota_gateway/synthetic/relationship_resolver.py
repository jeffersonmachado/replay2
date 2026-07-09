"""Resolvedor de relacionamentos: gera dados cross-entity consistentes (FK, lookup)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from .schema import FieldSchema
from .dataset_builder import Dataset
from ..source_analyzer.relationship_mapper import Relationship, RelationshipMap


@dataclass
class ResolvedForeignKey:
    """FK resolvida: campo → valores validos do dataset pai."""
    source_entity: str = ""
    source_field: str = ""
    target_entity: str = ""
    target_field: str = ""
    values: list[Any] = field(default_factory=list)


@dataclass
class ResolvedLookup:
    """Lookup table resolvida: campo → valores de dominio."""
    field_name: str = ""
    entity_name: str = ""
    values: list[str] = field(default_factory=list)


class RelationshipResolver:
    """Resolve relacionamentos entre entidades para geracao de dados consistentes.

    Fluxo:
    1. Identifica campos FK
    2. Obtem valores do dataset pai
    3. Distribui entre os registros filhos
    """

    def __init__(self):
        self._fk_cache: dict[str, ResolvedForeignKey] = {}
        self._lookup_cache: dict[str, ResolvedLookup] = {}

    def resolve_fk_values(
        self,
        source_entity: str,
        source_field: str,
        parent_dataset: Dataset,
        parent_field: str = "",
        quantity: int = 0,
    ) -> list[Any]:
        """Retorna valores do dataset pai para usar como FK."""
        cache_key = f"{source_entity}.{source_field}"
        if cache_key in self._fk_cache and self._fk_cache[cache_key].values:
            return self._fk_cache[cache_key].values[:quantity] if quantity else self._fk_cache[cache_key].values

        # Extrair valores do dataset pai
        pk_field = parent_field or self._guess_pk(parent_dataset)
        values = [r.data.get(pk_field, r.record_index) for r in parent_dataset.records]

        self._fk_cache[cache_key] = ResolvedForeignKey(
            source_entity=source_entity,
            source_field=source_field,
            target_entity=parent_dataset.entity_name,
            target_field=pk_field,
            values=values,
        )
        return values[:quantity] if quantity else values

    def resolve_lookup_values(
        self,
        field: FieldSchema,
        entity_name: str = "",
    ) -> list[str]:
        """Retorna valores de dominio para um campo com lookup."""
        cache_key = f"{entity_name}.{field.name}"
        if cache_key in self._lookup_cache:
            return self._lookup_cache[cache_key].values

        values: list[str] = []

        # Choices explicitos no field schema
        if field.choices:
            values = list(field.choices)

        # Lookup table referenciada
        if field.lookup:
            values = [field.lookup]  # placeholder - sera populado pelo dataset da tabela lookup

        # Valores default por tipo de campo
        if not values:
            values = self._default_domain_values(field)

        self._lookup_cache[cache_key] = ResolvedLookup(
            field_name=field.name,
            entity_name=entity_name,
            values=values,
        )
        return values

    def build_dependency_graph(
        self,
        relationship_map: RelationshipMap,
    ) -> dict[str, list[str]]:
        """Constroi grafo de dependencias para ordenar geracao de datasets.

        Entidade A depende de B se A tem FK para B.
        Retorna: {entidade: [dependencias]}
        """
        deps: dict[str, set[str]] = {}

        for rel in relationship_map.relationships:
            if rel.relationship_type == "foreign_key":
                deps.setdefault(rel.source_entity, set()).add(rel.target_entity)

        return {k: sorted(v) for k, v in deps.items()}

    def topological_order(
        self,
        entities: list[str],
        dependency_graph: dict[str, list[str]],
    ) -> list[str]:
        """Ordena entidades para geracao respeitando dependencias FK."""
        visited: set[str] = set()
        temp: set[str] = set()
        order: list[str] = []

        def visit(entity: str) -> None:
            if entity in temp:
                return  # ciclo detectado - ignora
            if entity in visited:
                return
            temp.add(entity)
            for dep in dependency_graph.get(entity, []):
                if dep in entities:
                    visit(dep)
            temp.discard(entity)
            visited.add(entity)
            order.append(entity)

        for entity in entities:
            if entity not in visited:
                visit(entity)

        return order

    @staticmethod
    def _guess_pk(dataset: Dataset) -> str:
        """Tenta adivinhar qual campo e a PK do dataset."""
        if not dataset.records:
            return "id"
        first = dataset.records[0].data
        for candidate in ("id", "codigo", "cod", "chave", "pk"):
            if candidate in first:
                return candidate
        # Primeiro campo
        return list(first.keys())[0] if first else "id"

    @staticmethod
    def _default_domain_values(field: FieldSchema) -> list[str]:
        """Valores de dominio default baseados no tipo do campo."""
        name = (field.name or "").lower()

        if "status" in name or "situacao" in name:
            return ["ATIVO", "INATIVO", "PENDENTE", "CANCELADO"]
        if "tipo" in name:
            return ["NORMAL", "ESPECIAL", "VIP"]
        if "uf" in name or "estado" in name:
            return ["SP", "RJ", "MG", "RS", "PR", "BA", "PE", "CE", "SC", "DF"]
        if "sexo" in name:
            return ["M", "F"]
        if "sim_nao" in name or "flag" in name:
            return ["S", "N"]

        return []

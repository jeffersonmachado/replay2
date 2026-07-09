"""Mapeia relacionamentos entre entidades por inferencia IA.

Substitui regex fixa de FK por inferencia baseada em:
1. Tokenizacao de nomes (snake_case, CamelCase) → match parcial
2. Similaridade de nome entre campo e entidade alvo
3. Prefixos indicadores de FK (id_, cod_, cd_, fk_, ref_)
4. Contexto de modulo (entidades do mesmo modulo tem mais chance de relacionamento)
5. Scoring de confianca por multiplas evidencias
6. Deteccao bidirecional e cardinalidade inferida
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from .entity_catalog import EntityDefinition, FieldDefinition


@dataclass
class Relationship:
    source_entity: str = ""
    target_entity: str = ""
    relationship_type: str = ""
    source_field: str = ""
    target_field: str = ""
    cardinality: str = ""
    source_file: str = ""
    confidence: float = 0.0
    evidence: list[str] = field(default_factory=list)


@dataclass
class RelationshipMap:
    relationships: list[Relationship] = field(default_factory=list)
    entity_graph: dict[str, list[str]] = field(default_factory=dict)
    dependency_graph: dict[str, list[str]] = field(default_factory=dict)
    cooccurrence_graph: dict[str, list[str]] = field(default_factory=dict)
    orphan_entities: list[str] = field(default_factory=list)


# Prefixos que indicam FK
_FK_PREFIXES = {"id", "cod", "cd", "fk", "ref", "chave", "num", "nr",
                "id_", "cod_", "cd_", "fk_", "ref_", "chave_", "num_", "nr_"}

# ── Normalizacao singular/plural + aliases ──

_ENTITY_NORMALIZE: dict[str, str] = {
    # Singular → Plural
    "CLIENTE": "CLIENTES", "PRODUTO": "PRODUTOS", "PEDIDO": "PEDIDOS",
    "FORNECEDOR": "FORNECEDORES", "VENDA": "VENDAS", "NOTA": "NOTAS",
    "ITEM": "ITENS", "ITENS_PEDIDO": "ITENS_PEDIDO",
    "VENDEDOR": "VENDEDORES", "ESTOQUE": "ESTOQUE",
    "FINANCEIRO": "FINANCEIRO", "CONTABIL": "CONTABIL",
}

# Aliases de FK → entidade canonica (apenas para padroes FK, nao substring solta)
_ALIAS_TO_ENTITY: dict[str, str] = {
    "CLI": "CLIENTES", "CLIE": "CLIENTES", "CLIENT": "CLIENTES",
    "PROD": "PRODUTOS", "PRODU": "PRODUTOS",
    "FORN": "FORNECEDORES", "FORNEC": "FORNECEDORES", "FOR": "FORNECEDORES",
    "PED": "PEDIDOS", "PEDI": "PEDIDOS",
    "VEN": "VENDEDORES", "VEND": "VENDEDORES",
    "FIN": "FINANCEIRO",
    "NFE": "NOTAS", "NF": "NOTAS",
    "DUP": "FINANCEIRO", "DUPL": "FINANCEIRO",
    "CTA": "CONTAS", "CTB": "CONTABIL",
}

# Padroes de VENDEDOR (CODVEN, CD_VEN, VENDEDOR_ID...)
_VENDEDOR_PATTERNS = {
    "CODVEN", "COD_VEN", "CD_VEN", "ID_VEN", "VEN_ID",
    "CODVEND", "COD_VEND", "VENDEDOR_ID", "ID_VENDEDOR", "COD_VENDEDOR",
}

# Padroes de VENDA (VENDA_ID, NUM_VENDA...)
_VENDA_PATTERNS = {
    "VENDA_ID", "ID_VENDA", "NUM_VENDA", "NR_VENDA", "COD_VENDA", "VENDA_NUM",
}

# Alias CURTOS (2-3 chars) — so casam como FK, nunca como substring solta
_SHORT_ALIASES = {"for", "cli", "ven", "ped", "nf", "fin", "dup", "cta"}

# Padroes seguros de FK: prefixo_FK + alias
# CODFOR, CD_FOR, ID_FOR, FOR_ID, FORNECEDOR_ID, ID_FORNECEDOR, etc.
_SAFE_FK_PATTERNS = [
    r"(?:COD|CD|ID|NUM|NR|FK|REF)_?{alias}$",       # COD_CLI, CDCLI, ID_FOR
    r"^{alias}_(?:ID|COD|CD|NUM)$",                   # CLI_ID, FOR_COD
    r"(?:COD|CD|ID|NUM|NR)_{alias}$",                 # COD_cliente
    r"^{alias}(?:ID|COD|CD|NUM)$",                     # clienteID
]


def _tokenize(name: str) -> List[str]:
    """Tokeniza nome em partes significativas."""
    parts = re.split(r"[_\-\s]+", name.lower())
    result = []
    for p in parts:
        if not p:
            continue
        sub = re.findall(r'[a-z]+|[0-9]+', p)
        result.extend(s for s in sub if len(s) >= 2)
    return result or [name.lower()]


def _similarity(tokens1: List[str], tokens2: List[str]) -> float:
    """Similaridade de Jaccard entre conjuntos de tokens."""
    if not tokens1 or not tokens2:
        return 0.0
    s1, s2 = set(tokens1), set(tokens2)
    return len(s1 & s2) / len(s1 | s2)


def _is_fk_field(field_name: str) -> Tuple[bool, str]:
    """Verifica se campo parece FK e extrai possivel entidade alvo."""
    tokens = _tokenize(field_name)
    if not tokens:
        return False, ""

    # Remove prefixos FK conhecidos
    cleaned = [t for t in tokens if t.lower() not in _FK_PREFIXES]
    if len(cleaned) < len(tokens):  # Tinha prefixo FK
        return True, "_".join(cleaned) if cleaned else ""
    return False, ""


class RelationshipMapper:
    """Mapeia relacionamentos com inferencia inteligente."""

    def map(self, entities: List[EntityDefinition], source_dir: str = "") -> RelationshipMap:
        result = RelationshipMap()
        entity_names = {e.name.upper(): e.name for e in entities}
        entity_name_list = [e.name for e in entities]

        # ── Co-ocorrencia de entidades no mesmo arquivo fonte ──
        file_entity_map: dict[str, set[str]] = {}
        for e in entities:
            src = getattr(e, 'source', '') or ''
            if src:
                file_entity_map.setdefault(src, set()).add(e.name)

        # ── Cache de conteudo de arquivos ──
        source_contents: dict[str, str] = {}
        all_files = set(file_entity_map.keys())
        if source_dir:
            import os as _os
            for root, dirs, files in _os.walk(source_dir):
                for f in files:
                    if f.endswith('.prg'):
                        all_files.add(_os.path.join(root, f))
        for src in all_files:
            try:
                source_contents[src] = Path(src).read_text(encoding="utf-8", errors="replace")
            except Exception:
                pass

        for entity in entities:
            entity_rels: List[str] = []

            # ── FK por SET RELATION TO (padrao Recital) ──
            src = getattr(entity, 'source', '') or ''
            content = source_contents.get(src, '')
            if content:
                recital_fks = self._detect_recital_fk(content, entity.name, entity_names)
                for fk_target in recital_fks:
                    result.relationships.append(Relationship(
                        source_entity=entity.name, target_entity=fk_target,
                        relationship_type="foreign_key", source_field="",
                        cardinality="N:1", confidence=0.70,
                        evidence=["recital_set_relation_to"],
                    ))
                    entity_rels.append(fk_target)

            for field in entity.fields:
                rels = self._infer_relationships(field, entity, entities, entity_names)
                for rel in rels:
                    result.relationships.append(rel)
                    entity_rels.append(rel.target_entity)

            result.entity_graph[entity.name.upper()] = sorted({rel.upper() for rel in entity_rels})

        # ── Popula dependency_graph (FK+lookup) e cooccurrence_graph ──
        for rel in result.relationships:
            src = rel.source_entity.upper()
            tgt = rel.target_entity.upper()
            if rel.relationship_type in ("foreign_key", "lookup"):
                result.dependency_graph.setdefault(src, []).append(tgt)
            elif rel.relationship_type == "cooccurrence":
                result.cooccurrence_graph.setdefault(src, []).append(tgt)
                result.cooccurrence_graph.setdefault(tgt, []).append(src)

        # entity_graph: todas entidades como chaves. Dependencias de FK+lookup.
        # Entidades sem dependencias de saida aparecem com lista vazia.
        all_entity_names = sorted({e.name.upper() for e in entities})
        result.entity_graph = {name: [] for name in all_entity_names}
        for src, tgts in result.dependency_graph.items():
            result.entity_graph[src] = sorted(set(tgts))

        for g in (result.dependency_graph, result.cooccurrence_graph):
            for k in list(g.keys()):
                g[k] = sorted(set(g[k]))

        # ── Co-ocorrencia: entidades no mesmo arquivo fonte ──
        cooccur_pairs: dict[tuple, int] = {}
        for src, ents in file_entity_map.items():
            ent_list = sorted(ents)
            for i in range(len(ent_list)):
                for j in range(i + 1, len(ent_list)):
                    a, b = ent_list[i], ent_list[j]
                    if a == b:
                        continue
                    key = (min(a, b), max(a, b))
                    cooccur_pairs[key] = cooccur_pairs.get(key, 0) + 1

        for (a, b), count in cooccur_pairs.items():
            if count >= 1:
                # Adiciona arestas bidirecionais com confianca proporcional a co-ocorrencias
                conf = min(0.85, 0.40 + count * 0.05)
                result.relationships.append(Relationship(
                    source_entity=a, target_entity=b,
                    relationship_type="cooccurrence", source_field="",
                    cardinality="N:M", confidence=conf,
                    evidence=[f"cooccur_{count}_files"],
                ))
                result.relationships.append(Relationship(
                    source_entity=b, target_entity=a,
                    relationship_type="cooccurrence", source_field="",
                    cardinality="N:M", confidence=conf,
                    evidence=[f"cooccur_{count}_files"],
                ))
                a_up, b_up = a.upper(), b.upper()
                # Cooccurrence vai apenas para cooccurrence_graph,
                # nao polui entity_graph (que eh so FK+lookup)
                result.cooccurrence_graph.setdefault(a_up, []).append(b_up)
                result.cooccurrence_graph.setdefault(b_up, []).append(a_up)

        result.orphan_entities = sorted(
            n for n, r in result.entity_graph.items() if not r
        )
        return result

    @staticmethod
    def _detect_recital_fk(content: str, entity_name: str, all_entity_names: dict[str, str]) -> list[str]:
        """Detecta SET RELATION TO <key> INTO <alias> e mapeia alias → entidade.

        Ex: SET RELATION TO cliente_id INTO CLI → FK para entidade que contenha "cli" ou "cliente"
        """
        targets: list[str] = []
        entity_upper = entity_name.upper()
        for m in re.finditer(
            r'set\s+relation\s+to\s+\w+\s+into\s+(\w+)',
            content, re.IGNORECASE
        ):
            alias = m.group(1).upper()
            # Tenta casar o alias com nomes de entidades conhecidas
            best_match = ""
            best_score = 0
            for ename_upper, ename_orig in all_entity_names.items():
                if ename_upper == entity_upper:
                    continue
                # Match exato do alias
                if ename_upper == alias:
                    best_match = ename_orig
                    best_score = 1.0
                    break
                # Match parcial: alias é prefixo do nome da entidade ou vice-versa
                if alias.startswith(ename_upper[:3]) or ename_upper.startswith(alias[:3]):
                    score = len(set(alias) & set(ename_upper)) / max(len(alias), len(ename_upper))
                    if score > best_score:
                        best_score = score
                        best_match = ename_orig
            if best_match and best_match.upper() != entity_upper:
                targets.append(best_match)
        return targets

    def _infer_relationships(
        self, field: FieldDefinition, entity: EntityDefinition,
        all_entities: List[EntityDefinition], entity_names: Dict[str, str],
    ) -> List[Relationship]:
        rels: List[Relationship] = []
        field_lower = field.name.lower()
        field_upper = field.name.upper()

        # ── Lookup table explicita ──
        if field.lookup_table:
            rels.append(Relationship(
                source_entity=entity.name, target_entity=field.lookup_table,
                relationship_type="lookup", source_field=field.name,
                confidence=0.95, evidence=["lookup_table_explicit"],
            ))
            return rels

        # ── FK por alias: CLIENTE_ID, CODCLI, CD_CLI, PRODUTO_ID, ID_PEDIDO ──
        target_entity = self._resolve_fk_alias(field_upper)
        if target_entity and target_entity.upper() in entity_names:
            if target_entity.upper() != entity.name.upper():
                rels.append(Relationship(
                    source_entity=entity.name, target_entity=target_entity,
                    relationship_type="foreign_key", source_field=field.name,
                    cardinality="N:1", confidence=0.85,
                    evidence=[f"fk_alias={target_entity}"],
                ))
                return rels

        # ── FK por prefixo (id_cliente, cod_empresa) ──
        is_fk, target_hint = _is_fk_field(field.name)
        if is_fk and target_hint:
            # Tenta resolver hint via aliases primeiro
            resolved = self._resolve_fk_alias(target_hint.upper())
            if resolved and resolved.upper() in entity_names and resolved.upper() != entity.name.upper():
                rels.append(Relationship(
                    source_entity=entity.name, target_entity=resolved,
                    relationship_type="foreign_key", source_field=field.name,
                    cardinality="N:1", confidence=0.80,
                    evidence=[f"fk_prefix+alias={resolved}"],
                ))
                return rels

            target_tokens = _tokenize(target_hint)
            best_match = ""
            best_score = 0.0
            for other in all_entities:
                if other.name.upper() == entity.name.upper():
                    continue
                other_tokens = _tokenize(other.name)
                sim = _similarity(target_tokens, other_tokens)
                if sim > best_score and sim > 0.3:
                    best_score = sim
                    best_match = other.name
            if best_match:
                conf = min(0.95, 0.60 + best_score * 0.4)
                rels.append(Relationship(
                    source_entity=entity.name, target_entity=best_match,
                    relationship_type="foreign_key", source_field=field.name,
                    cardinality="N:1", confidence=round(conf, 3),
                    evidence=[f"fk_prefix+token_sim={best_score:.2f}"],
                ))

        # ── Nome do campo = nome de outra entidade (exato ou parcial) ──
        for other in all_entities:
            if other.name.upper() == entity.name.upper():
                continue
            if other.name.upper() == field_upper:
                rels.append(Relationship(
                    source_entity=entity.name, target_entity=other.name,
                    relationship_type="foreign_key", source_field=field.name,
                    cardinality="N:1", confidence=0.75,
                    evidence=["exact_name_match"],
                ))
            elif field_upper.startswith(other.name.upper()[:4]) and len(other.name) >= 3:
                other_tokens = _tokenize(other.name)
                field_tokens = _tokenize(field.name)
                sim = _similarity(field_tokens, other_tokens)
                if sim > 0.4:
                    rels.append(Relationship(
                        source_entity=entity.name, target_entity=other.name,
                        relationship_type="foreign_key", source_field=field.name,
                        cardinality="N:1", confidence=round(0.55 + sim * 0.35, 3),
                        evidence=[f"partial_name_match_sim={sim:.2f}"],
                    ))

        return rels

    @staticmethod
    def _resolve_fk_alias(field_name_upper: str) -> str:
        """Resolve nome de campo FK para entidade canonica via padroes seguros.

        CLIENTE_ID → CLIENTES, CODCLI → CLIENTES, PRODUTO_ID → PRODUTOS.
        NAO casa em: INFORMACAO, CONFORME, FORMA_PGTO, CODFORMA.
        """
        clean = field_name_upper
        # Remove prefixos FK com underscore
        for prefix in ("ID_", "COD_", "CD_", "NUM_", "NR_", "FK_", "REF_"):
            if clean.startswith(prefix):
                clean = clean[len(prefix):]
        # Remove prefixos FK sem underscore (CODCLI→CLI, CDPROD→PROD)
        for prefix in ("COD", "CD", "ID", "NUM", "NR", "FK", "REF"):
            if clean.startswith(prefix) and len(clean) > len(prefix) + 1:
                candidate = clean[len(prefix):]
                if candidate.upper() in _ALIAS_TO_ENTITY or len(candidate) >= 2:
                    clean = candidate
                    break
        for suffix in ("_ID", "_COD", "_CD", "_NUM", "_NR", "_FK", "_REF"):
            if clean.endswith(suffix):
                clean = clean[:-len(suffix)]
        clean = clean.strip("_")

        if not clean:
            return ""

        # ── VENDEDOR vs VENDA: desambiguacao ──
        if field_name_upper in _VENDEDOR_PATTERNS:
            return "VENDEDORES"
        if field_name_upper in _VENDA_PATTERNS:
            return "VENDAS"

        # ── Alias curto: so casa via padrao FK seguro ──
        if clean.lower() in _SHORT_ALIASES:
            # Verifica se o campo original casa com algum padrao FK seguro
            for pattern in _SAFE_FK_PATTERNS:
                pat = pattern.replace("{alias}", re.escape(clean.lower()))
                if re.search(pat, field_name_upper, re.IGNORECASE):
                    return _ALIAS_TO_ENTITY.get(clean.upper(), "")

        # ── Alias longo (>= 4 chars): match direto ──
        if clean in _ALIAS_TO_ENTITY:
            return _ALIAS_TO_ENTITY[clean]

        # ── Singular → plural ──
        if clean in _ENTITY_NORMALIZE:
            return _ENTITY_NORMALIZE[clean]

        # ── Alias contido no nome do campo (apenas para alias >= 4 chars) ──
        for alias, entity in _ALIAS_TO_ENTITY.items():
            if len(alias) >= 4 and alias in clean and alias not in _SHORT_ALIASES:
                return entity

        return ""

    @staticmethod
    def to_adjacency_list(rmap: RelationshipMap) -> dict[str, list[dict]]:
        result: dict[str, list[dict]] = {}
        for rel in rmap.relationships:
            result.setdefault(rel.source_entity, []).append({
                "target": rel.target_entity,
                "type": rel.relationship_type,
                "confidence": rel.confidence,
                "evidence": rel.evidence,
            })
        return result

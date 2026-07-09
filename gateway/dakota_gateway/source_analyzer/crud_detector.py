"""Detecta cobertura CRUD por entidade com inferencia de completude.

Melhorias sobre versao anterior:
1. Scoring de completude (0-100%) em vez de booleano
2. Inferencia de operacoes faltantes por contexto (ex: lookup tables nao precisam DELETE)
3. Peso por tipo de operacao (CREATE/DELETE valem mais que READ)
4. Confianca baseada na quantidade e diversidade de operacoes
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .entity_catalog import EntityDefinition, OperationDefinition


@dataclass
class CRUDCoverage:
    entity_name: str = ""
    has_create: bool = False
    has_read: bool = False
    has_update: bool = False
    has_delete: bool = False
    is_complete: bool = False

    create_programs: list[str] = field(default_factory=list)
    read_programs: list[str] = field(default_factory=list)
    update_programs: list[str] = field(default_factory=list)
    delete_programs: list[str] = field(default_factory=list)

    missing_operations: list[str] = field(default_factory=list)
    total_operations: int = 0
    completeness_score: float = 0.0  # 0-100, inferido
    inferred_ops: list[str] = field(default_factory=list)  # ops inferidas por contexto


# Pesos para cada tipo CRUD no score de completude
_CRUD_WEIGHTS = {"create": 30, "read": 20, "update": 25, "delete": 25}

# Entidades que tipicamente nao precisam de CRUD completo
_READONLY_PATTERNS = {"log", "audit", "histor", "backup", "temp", "tmp", "cache", "config"}
_LOOKUP_PATTERNS = {"tipo", "status", "categoria", "unidade", "uf", "estado", "cidade", "param"}


def _infer_entity_type(entity_name: str) -> str:
    """Infere o tipo da entidade para ajustar expectativas de CRUD."""
    low = entity_name.lower()
    for p in _READONLY_PATTERNS:
        if p in low:
            return "readonly"
    for p in _LOOKUP_PATTERNS:
        if p in low:
            return "lookup"
    return "transactional"


class CRUDDetector:
    """Detecta cobertura CRUD com inferencia de completude."""

    _CREATE_OPS = {"insert", "append", "create"}
    _READ_OPS = {"select", "seek", "locate", "scatter", "find", "open", "reference"}
    _UPDATE_OPS = {"update", "replace", "gather"}
    _DELETE_OPS = {"delete", "pack", "zap", "erase"}

    @classmethod
    def detect(cls, entity: EntityDefinition) -> CRUDCoverage:
        coverage = CRUDCoverage(entity_name=entity.name)
        source_files: set = set()

        for op in entity.operations:
            op_type = str(op.operation_type or "").strip().lower()
            source_files.add(op.source_file or "")

            if op_type in cls._CREATE_OPS:
                coverage.has_create = True
                if op.source_file and op.source_file not in coverage.create_programs:
                    coverage.create_programs.append(op.source_file)
            elif op_type in cls._READ_OPS:
                coverage.has_read = True
                if op.source_file and op.source_file not in coverage.read_programs:
                    coverage.read_programs.append(op.source_file)
            elif op_type in cls._UPDATE_OPS:
                coverage.has_update = True
                if op.source_file and op.source_file not in coverage.update_programs:
                    coverage.update_programs.append(op.source_file)
            elif op_type in cls._DELETE_OPS:
                coverage.has_delete = True
                if op.source_file and op.source_file not in coverage.delete_programs:
                    coverage.delete_programs.append(op.source_file)

        coverage.total_operations = len(entity.operations)

        # ── Inferencia de completude ──
        cls._infer_completeness(coverage)

        # Missing operations
        missing = []
        if not coverage.has_create:
            missing.append("create")
        if not coverage.has_read:
            missing.append("read")
        if not coverage.has_update:
            missing.append("update")
        if not coverage.has_delete:
            missing.append("delete")
        coverage.missing_operations = missing

        return coverage

    @classmethod
    def _infer_completeness(cls, coverage: CRUDCoverage) -> None:
        """Calcula score de completude com inferencia contextual."""
        entity_type = _infer_entity_type(coverage.entity_name)
        score = 0.0
        inferred = []

        # CREATE
        if coverage.has_create:
            score += _CRUD_WEIGHTS["create"]
        elif entity_type == "lookup":
            score += _CRUD_WEIGHTS["create"] * 0.8  # lookups provavelmente tem create implicito
            inferred.append("create(inferred:lookup)")

        # READ
        if coverage.has_read:
            score += _CRUD_WEIGHTS["read"]
        elif coverage.total_operations > 0:
            score += _CRUD_WEIGHTS["read"] * 0.5  # se tem ops, read eh provavel
            inferred.append("read(inferred:has_other_ops)")

        # UPDATE
        if coverage.has_update:
            score += _CRUD_WEIGHTS["update"]
        elif coverage.has_create and coverage.has_read:
            score += _CRUD_WEIGHTS["update"] * 0.3  # se tem C+R, U eh provavel
            inferred.append("update(inferred:has_create_read)")

        # DELETE
        if coverage.has_delete:
            score += _CRUD_WEIGHTS["delete"]
        elif entity_type == "readonly":
            score += _CRUD_WEIGHTS["delete"] * 0.9  # readonly nao precisa delete
            inferred.append("delete(inferred:readonly_entity)")

        coverage.completeness_score = round(score, 1)
        coverage.inferred_ops = inferred
        coverage.is_complete = score >= 80

    @classmethod
    def detect_all(cls, entities: list[EntityDefinition]) -> list[CRUDCoverage]:
        return [cls.detect(e) for e in entities]

    @classmethod
    def summary(cls, coverages: list[CRUDCoverage]) -> dict:
        total = len(coverages)
        complete = sum(1 for c in coverages if c.is_complete)
        partial = total - complete

        by_missing: Dict[str, int] = {}
        for c in coverages:
            for m in c.missing_operations:
                by_missing[m] = by_missing.get(m, 0) + 1

        # Score medio de completude (inferido)
        avg_score = sum(c.completeness_score for c in coverages) / max(1, total)

        return {
            "total_entities": total,
            "complete_crud": complete,
            "partial_crud": partial,
            "completeness_pct": round(complete / max(1, total) * 100, 1),
            "avg_completeness_score": round(avg_score, 1),
            "missing_operations": by_missing,
            "entities_without_create": sum(1 for c in coverages if not c.has_create),
            "entities_without_read": sum(1 for c in coverages if not c.has_read),
            "entities_without_update": sum(1 for c in coverages if not c.has_update),
            "entities_without_delete": sum(1 for c in coverages if not c.has_delete),
        }

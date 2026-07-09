"""Relatorio de evidencias da geracao sintetica.

Faz parte da entrega P2-A — Synthetic Knowledge Base.

Gera um relatorio auditavel que responde:
- Quais entidades foram detectadas?
- Quais campos foram classificados e com qual confianca?
- Quais telas estao associadas a quais entidades?
- Qual a ordem de geracao (grafo de dependencia)?
- Quais amostras de dados foram geradas?
- Ha violacoes de restricao? Ciclos? Entidades orfas?
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Optional

from ..source_analyzer.entity_catalog import EntityDefinition, FieldDefinition
from ..source_analyzer.field_classifier import FieldClassification
from ..source_analyzer.relationship_mapper import RelationshipMap
from ..source_analyzer.screen_entity_linker import ScreenEntityBinding
from ..source_analyzer.program_catalog import ProgramCatalog, ProgramEntry
from .business_dataset_planner import BusinessDependencyGraph, DatasetPlan
from .data_synthesizer import BulkGenerationResult
from .dataset_builder import Dataset


@dataclass
class EntityEvidence:
    """Evidencia de uma entidade no relatorio."""
    entity_name: str = ""
    storage_type: str = ""
    field_count: int = 0
    fields: list[dict] = field(default_factory=list)
    operations_detected: list[str] = field(default_factory=list)
    screens_bound: list[str] = field(default_factory=list)
    programs_referencing: list[str] = field(default_factory=list)
    related_entities: list[str] = field(default_factory=list)
    dependency_depth: int = -1
    generation_order: int = -1
    is_root: bool = False
    is_leaf: bool = False


@dataclass
class SyntheticEvidenceReport:
    """Relatorio completo de evidencias da base de conhecimento sintetica."""

    # ── Resumo ──
    source_files_analyzed: int = 0
    entities_detected: int = 0
    screens_detected: int = 0
    screen_entity_bindings: int = 0
    relationships_inferred: int = 0
    foreign_key_relationships: int = 0
    lookup_relationships: int = 0
    cooccurrence_hints: int = 0
    fields_classified: int = 0
    programs_cataloged: int = 0
    modules_detected: int = 0

    # ── Detalhamento ──
    entities: list[EntityEvidence] = field(default_factory=list)
    bindings_summary: dict = field(default_factory=dict)
    dependency_graph_summary: dict = field(default_factory=dict)
    generation_plan: list[dict] = field(default_factory=list)
    samples: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)

    # ── Relationships detail (v0.2.2) ──
    relationships_detail: Optional[RelationshipMap] = None

    # ── Metadata ──
    generated_at: str = ""
    pipeline_version: str = "P2-A"


class SyntheticEvidenceReportBuilder:
    """Construtor do relatorio de evidencias a partir dos modulos P2-A."""

    def __init__(self):
        self._warnings: list[str] = []
        self._recommendations: list[str] = []

    def build(
        self,
        *,
        entities: list[EntityDefinition],
        screens_count: int,
        bindings: list[ScreenEntityBinding],
        classifications: Optional[dict[str, list[FieldClassification]]] = None,
        relationships: Optional[RelationshipMap] = None,
        dependency_graph: Optional[BusinessDependencyGraph] = None,
        program_catalog: Optional[ProgramCatalog] = None,
        generation_results: Optional[list[BulkGenerationResult]] = None,
        source_files_count: int = 0,
    ) -> SyntheticEvidenceReport:
        """Constroi o relatorio completo."""
        from datetime import datetime

        report = SyntheticEvidenceReport(
            generated_at=datetime.now().isoformat(),
            source_files_analyzed=source_files_count,
            entities_detected=len(entities),
            screens_detected=screens_count,
            screen_entity_bindings=len(bindings),
            relationships_inferred=len(relationships.relationships) if relationships else 0,
            fields_classified=sum(len(v) for v in (classifications or {}).values()),
        )

        # ── Evidencias por entidade ──
        entity_index = {e.name.upper(): e for e in entities}
        binding_by_entity: dict[str, list[ScreenEntityBinding]] = {}
        for b in bindings:
            if b.entity_name:
                binding_by_entity.setdefault(b.entity_name.upper(), []).append(b)

        for entity in entities:
            ename_upper = entity.name.upper()
            ev = self._build_entity_evidence(
                entity,
                bindings=binding_by_entity.get(ename_upper, []),
                classifications=classifications,
                relationships=relationships,
                dependency_graph=dependency_graph,
                program_catalog=program_catalog,
            )
            report.entities.append(ev)

        # ── Bindings summary ──
        if bindings:
            high = [b for b in bindings if b.confidence >= 0.75]
            medium = [b for b in bindings if 0.40 <= b.confidence < 0.75]
            low = [b for b in bindings if b.confidence < 0.40]
            unbound = [b for b in bindings if not b.entity_name]
            report.bindings_summary = {
                "total": len(bindings),
                "high_confidence": len(high),
                "medium_confidence": len(medium),
                "low_confidence": len(low),
                "unbound": len(unbound),
            }

        # ── Relacionamentos por tipo ──
        if relationships:
            fk_rels = [r for r in relationships.relationships if r.relationship_type == "foreign_key"]
            lookup_rels = [r for r in relationships.relationships if r.relationship_type == "lookup"]
            cooccur_rels = [r for r in relationships.relationships if r.relationship_type == "cooccurrence"]
            report.relationships_inferred = len(relationships.relationships)
            report.foreign_key_relationships = len(fk_rels)
            report.lookup_relationships = len(lookup_rels)
            report.cooccurrence_hints = len(cooccur_rels)
            report.relationships_detail = relationships
        if dependency_graph:
            report.dependency_graph_summary = {
                "total_entities": dependency_graph.total_entities,
                "roots": dependency_graph.roots,
                "leaves": dependency_graph.leaves,
                "max_depth": dependency_graph.max_depth,
                "generation_order": dependency_graph.generation_order,
                "cycles_detected": len(dependency_graph.cycles) > 0,
                "cycles": dependency_graph.cycles,
            }
            report.generation_plan = [
                {
                    "entity": p.entity_name,
                    "order": p.generation_order,
                    "dependencies": p.dependencies,
                    "suggested_quantity": p.suggested_quantity,
                    "is_root": p.is_root,
                    "is_leaf": p.is_leaf,
                }
                for p in dependency_graph.plans
            ]

        # ── Program catalog ──
        if program_catalog:
            report.programs_cataloged = len(program_catalog._entries)
            report.modules_detected = len(program_catalog._by_module)

        # ── Samples ──
        if generation_results:
            for gr in generation_results:
                if gr.dataset and gr.dataset.records:
                    sample_records = [
                        r.data for r in gr.dataset.records[:3]
                    ]
                    report.samples.append({
                        "plan_id": gr.plan_id,
                        "entity": gr.dataset.entity_name,
                        "generated": gr.generated_count,
                        "blocked": gr.blocked,
                        "sample_records": sample_records,
                    })

        # ── Warnings e recomendacoes ──
        self._generate_warnings(report, dependency_graph, bindings)
        self._generate_recommendations(report, dependency_graph, bindings)

        report.warnings = self._warnings
        report.recommendations = self._recommendations

        return report

    def _build_entity_evidence(
        self,
        entity: EntityDefinition,
        bindings: list[ScreenEntityBinding],
        classifications: Optional[dict[str, list[FieldClassification]]],
        relationships: Optional[RelationshipMap],
        dependency_graph: Optional[BusinessDependencyGraph],
        program_catalog: Optional[ProgramCatalog],
    ) -> EntityEvidence:
        """Constroi evidencia para uma entidade."""

        # Campos com classificacao
        entity_classifications = (classifications or {}).get(entity.name, [])
        field_dicts: list[dict] = []
        for f in entity.fields:
            fd = {
                "name": f.name,
                "datatype": f.datatype,
                "required": f.required,
                "unique": f.unique_flag,
            }
            # Encontra classificacao correspondente
            for fc in entity_classifications:
                if fc.field_name.upper() == f.name.upper():
                    fd["semantic_type"] = fc.semantic_category
                    fd["format"] = fc.format_mask
                    fd["classification_confidence"] = fc.confidence
                    break
            field_dicts.append(fd)

        # Operacoes detectadas
        operations = list(dict.fromkeys(
            op.operation_type for op in entity.operations
        ))

        # Telas associadas
        screens = [b.screen_title or b.program_name for b in bindings]

        # Programas que referenciam
        programs: list[str] = []
        if program_catalog:
            for entry in program_catalog._entries.values():
                if entity.name.upper() in {r.upper() for r in entry.entity_references}:
                    programs.append(entry.name)

        # Entidades relacionadas
        related: list[str] = []
        if relationships:
            for rel in relationships.relationships:
                if rel.source_entity.upper() == entity.name.upper():
                    if rel.target_entity not in related:
                        related.append(rel.target_entity)
                elif rel.target_entity.upper() == entity.name.upper():
                    if rel.source_entity not in related:
                        related.append(rel.source_entity)

        # Profundidade e ordem no grafo
        depth = -1
        order = -1
        is_root = False
        is_leaf = False
        if dependency_graph:
            for plan in dependency_graph.plans:
                if plan.entity_name.upper() == entity.name.upper():
                    depth = plan.metadata.get("depth", -1)
                    order = plan.generation_order
                    is_root = plan.is_root
                    is_leaf = plan.is_leaf
                    break

        return EntityEvidence(
            entity_name=entity.name,
            storage_type=entity.storage_type,
            field_count=len(entity.fields),
            fields=field_dicts,
            operations_detected=operations,
            screens_bound=screens,
            programs_referencing=programs,
            related_entities=related,
            dependency_depth=depth,
            generation_order=order,
            is_root=is_root,
            is_leaf=is_leaf,
        )

    def _generate_warnings(
        self,
        report: SyntheticEvidenceReport,
        graph: Optional[BusinessDependencyGraph],
        bindings: list[ScreenEntityBinding],
    ) -> None:
        if report.entities_detected == 0:
            self._warnings.append("Nenhuma entidade detectada no codigo-fonte")
        if report.screens_detected == 0:
            self._warnings.append("Nenhuma tela detectada no codigo-fonte")
        if report.screen_entity_bindings == 0:
            self._warnings.append("Nenhum binding tela→entidade gerado")

        low_bindings = [b for b in bindings if 0 < b.confidence < 0.40 and b.entity_name]
        if low_bindings:
            self._warnings.append(
                f"{len(low_bindings)} bindings com confianca baixa — "
                f"revisar associacoes: {', '.join(b.screen_title or b.program_name for b in low_bindings[:5])}"
            )

        unbound = [b for b in bindings if not b.entity_name]
        if unbound:
            self._warnings.append(
                f"{len(unbound)} telas sem entidade associada"
            )

        if graph and graph.cycles:
            self._warnings.append(
                f"Ciclos detectados no grafo de dependencia: {graph.cycles}"
            )

        orphan_entities = [
            e.entity_name for e in report.entities
            if not e.related_entities and not e.screens_bound
        ]
        if orphan_entities:
            self._warnings.append(
                f"{len(orphan_entities)} entidades sem relacionamentos nem telas: "
                f"{', '.join(orphan_entities[:10])}"
            )

    def _generate_recommendations(
        self,
        report: SyntheticEvidenceReport,
        graph: Optional[BusinessDependencyGraph],
        bindings: list[ScreenEntityBinding],
    ) -> None:
        if report.entities_detected > 0 and report.screen_entity_bindings == 0:
            self._recommendations.append(
                "Executar screen_entity_linker para associar telas a entidades"
            )

        if graph and graph.cycles:
            self._recommendations.append(
                "Resolver ciclos no grafo de dependencia antes da geracao ordenada"
            )

        if graph and graph.max_depth == 0 and report.entities_detected > 1:
            self._recommendations.append(
                "Grafo de dependencia raso — verificar deteccao de FKs e relacionamentos"
            )

        entities_without_ops = [
            e.entity_name for e in report.entities
            if not e.operations_detected
        ]
        if entities_without_ops and len(entities_without_ops) < len(report.entities):
            self._recommendations.append(
                f"{len(entities_without_ops)} entidades sem operacoes CRUD detectadas"
            )

    def to_json(self, report: SyntheticEvidenceReport) -> str:
        """Serializa o relatorio em JSON."""
        result: dict[str, Any] = {
            "generated_at": report.generated_at,
            "pipeline_version": report.pipeline_version,
            "summary": {
                "source_files_analyzed": report.source_files_analyzed,
                "entities_detected": report.entities_detected,
                "screens_detected": report.screens_detected,
                "screen_entity_bindings": report.screen_entity_bindings,
                "relationships_inferred": report.relationships_inferred,
                "foreign_key_relationships": report.foreign_key_relationships,
                "lookup_relationships": report.lookup_relationships,
                "cooccurrence_hints": report.cooccurrence_hints,
                "fields_classified": report.fields_classified,
                "programs_cataloged": report.programs_cataloged,
                "modules_detected": report.modules_detected,
            },
            "bindings_summary": report.bindings_summary,
            "dependency_graph": report.dependency_graph_summary,
            "generation_plan": report.generation_plan,
            "entities": [
                {
                    "entity_name": e.entity_name,
                    "storage_type": e.storage_type,
                    "field_count": e.field_count,
                    "fields": e.fields,
                    "operations_detected": e.operations_detected,
                    "screens_bound": e.screens_bound,
                    "programs_referencing": e.programs_referencing,
                    "related_entities": e.related_entities,
                    "dependency_depth": e.dependency_depth,
                    "generation_order": e.generation_order,
                    "is_root": e.is_root,
                    "is_leaf": e.is_leaf,
                }
                for e in report.entities
            ],
            "samples": report.samples,
            "warnings": report.warnings,
            "recommendations": report.recommendations,
        }

        # ── Relationships detail (v0.2.2) ──
        if report.relationships_detail:
            rm = report.relationships_detail
            result["relationships"] = {
                "foreign_key_relationships": [
                    {
                        "source_entity": r.source_entity,
                        "target_entity": r.target_entity,
                        "source_field": r.source_field,
                        "target_field": r.target_field,
                        "relationship_type": r.relationship_type,
                        "cardinality": r.cardinality,
                        "confidence": r.confidence,
                        "is_dependency": True,
                        "evidence": r.evidence,
                    }
                    for r in rm.relationships
                    if r.relationship_type == "foreign_key"
                ],
                "lookup_relationships": [
                    {
                        "source_entity": r.source_entity,
                        "target_entity": r.target_entity,
                        "source_field": r.source_field,
                        "relationship_type": r.relationship_type,
                        "confidence": r.confidence,
                        "is_dependency": True,
                        "evidence": r.evidence,
                    }
                    for r in rm.relationships
                    if r.relationship_type == "lookup"
                ],
                "cooccurrence_hints": [
                    {
                        "source_entity": r.source_entity,
                        "target_entity": r.target_entity,
                        "relationship_type": r.relationship_type,
                        "confidence": r.confidence,
                        "is_dependency": False,
                        "evidence": r.evidence,
                    }
                    for r in rm.relationships
                    if r.relationship_type == "cooccurrence"
                ],
                "dependency_graph": rm.dependency_graph,
                "cooccurrence_graph": rm.cooccurrence_graph,
                "entity_graph": rm.entity_graph,
            }

        return json.dumps(result, ensure_ascii=False, indent=2, default=str)

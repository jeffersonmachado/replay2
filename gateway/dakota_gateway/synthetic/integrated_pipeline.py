"""Pipeline integrado: Discovery → Journey → Synthetic em uma unica chamada."""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from ..source_analyzer.parser import SourceParser
from ..source_analyzer.source_inventory import collect_preferred_source_files
from ..source_analyzer.crud_detector import CRUDDetector
from ..source_analyzer.field_classifier import FieldClassifier
from ..source_analyzer.relationship_mapper import RelationshipMapper
from ..source_analyzer.menu_analyzer import MenuAnalyzer
from ..source_analyzer.screen_entity_linker import ScreenEntityLinker
from ..source_analyzer.program_catalog import ProgramCatalog
from .business_dataset_planner import BusinessDatasetPlanner, BusinessDependencyGraph
from .synthetic_evidence_report import SyntheticEvidenceReportBuilder
from .journey_mix import JourneyMixBuilder, JourneyMixConfig
from .capture_knowledge_integrator import CaptureKnowledgeIntegrator, KnowledgeEnrichedTemplate
from .crud_journey_generator import CRUDJourneyGenerator, CRUDJourneyConfig
from .ddl_parser import DDLParser
from .journey_validator import JourneyValidator
from .smart_provider_router import SmartProviderRouter
from .relationship_resolver import RelationshipResolver
from .dataset_builder import DatasetBuilder, Dataset
from .journey_builder import JourneyBuilder
from .journey import JourneyDefinition
from .journey_report import JourneyReport
from .schema import ScreenSchema


@dataclass
class PipelineResult:
    """Resultado completo do pipeline."""
    source_dir: str = ""
    # Discovery
    entities_count: int = 0
    screens_count: int = 0
    crud_summary: dict = field(default_factory=dict)
    relationships_count: int = 0
    menu_programs: int = 0
    # P2-A: Knowledge Base
    bindings_count: int = 0
    programs_cataloged: int = 0
    modules_detected: int = 0
    dependency_graph: dict = field(default_factory=dict)
    evidence_report: dict = field(default_factory=dict)
    # Journey
    journeys_generated: int = 0
    journeys_saved: int = 0
    skipped_entities: list = field(default_factory=list)
    business_eval: dict = field(default_factory=dict)
    # Journey Mix (P2.5)
    journey_mix_configs: list[dict] = field(default_factory=list)
    # Synthetic
    datasets_generated: int = 0
    # Capture Knowledge (P2.4)
    capture_templates_enriched: int = 0
    # Validation
    validation_summary: dict = field(default_factory=dict)
    # Timing
    duration_ms: int = 0
    # Warnings
    warnings: list[str] = field(default_factory=list)
    # Detail
    detail: dict = field(default_factory=dict)


class IntegratedPipeline:
    """Orquestrador do pipeline completo de validacao."""

    def __init__(self, db_connection: Optional[sqlite3.Connection] = None):
        self.con = db_connection
        self.router = SmartProviderRouter()
        self.resolver = RelationshipResolver()
        self.validator = JourneyValidator()

    def run(
        self,
        source_dir: str,
        *,
        save_to_db: bool = True,
        session_count: int = 10,
        seed: int = 0,
        progress_callback: Optional[Callable[[str, str, int, dict], None]] = None,
    ) -> PipelineResult:
        """Executa o pipeline completo."""
        import time
        start_ms = int(time.time() * 1000)

        result = PipelineResult(source_dir=source_dir)

        def _progress(phase: str, step: str, pct: int, extra: Optional[dict] = None):
            if progress_callback:
                progress_callback(phase, step, pct, extra or {})

        source_files = collect_preferred_source_files(source_dir, {".prg", ".dbo", ".sql"})
        _progress("discovery", "analisando arquivos fonte", 3,
                  {"total_files": len(source_files)})

        # ------------------------------------------------------------------
        # Fase 1: Discovery
        # ------------------------------------------------------------------
        parser = SourceParser(source_dir)
        entities, screens = parser.parse_all(progress_cb=_progress if progress_callback else None)

        _progress("discovery", f"{len(entities)} entidades, {len(screens)} telas", 30,
                  {"entities": len(entities), "screens": len(screens)})

        if not entities:
            result.warnings.append("nenhuma entidade encontrada no codigo fonte")
            # Tenta DDL
            ddl_parser = DDLParser()
            ddl_result = ddl_parser.parse_directory(source_dir)
            if ddl_result.entities:
                entities = ddl_result.entities
                screens_schemas = ddl_result.screen_schemas
                result.warnings.append(f"entidades extraidas apenas de DDL: {len(entities)}")

        result.entities_count = len(entities)
        result.screens_count = len(screens)

        # Persiste entidades na tabela source_entities (antes ficava só no business_evals JSON)
        if save_to_db and self.con and entities:
            import datetime as _dt
            now = _dt.datetime.now().isoformat()
            for ent in entities:
                try:
                    meta = {}
                    if hasattr(ent, 'storage_type'):
                        meta['storage_type'] = ent.storage_type
                    if hasattr(ent, 'source'):
                        meta['source'] = ent.source
                    cur = self.con.execute(
                        """INSERT OR IGNORE INTO source_entities (name, storage_type, source, metadata_json, created_at)
                           VALUES (?, ?, ?, ?, ?)""",
                        (ent.name, getattr(ent, 'storage_type', 'unknown'),
                         getattr(ent, 'source', source_dir),
                         json.dumps(meta, ensure_ascii=False), now),
                    )
                    entity_id = cur.lastrowid or 0
                    if entity_id and hasattr(ent, 'fields'):
                        for ef in ent.fields:
                            constraints = getattr(ef, 'constraints_json', None) or json.dumps(
                                {"required": getattr(ef, 'required', False),
                                 "unique": getattr(ef, 'unique_flag', False)},
                                ensure_ascii=False)
                            self.con.execute(
                                """INSERT OR IGNORE INTO source_entity_fields
                                   (entity_id, field_name, datatype, required, unique_flag, constraints_json)
                                   VALUES (?, ?, ?, ?, ?, ?)""",
                                (entity_id, getattr(ef, 'name', ''),
                                 getattr(ef, 'datatype', 'text'),
                                 1 if getattr(ef, 'required', False) else 0,
                                 1 if getattr(ef, 'unique_flag', False) else 0,
                                 constraints),
                            )
                except Exception:
                    pass
            try:
                self.con.commit()
            except Exception:
                pass

        # CRUD
        coverages = CRUDDetector.detect_all(entities)
        crud_summary = CRUDDetector.summary(coverages)
        result.crud_summary = crud_summary

        # Classify fields
        classifications: dict[str, list] = {}
        for entity in entities:
            classifications[entity.name] = FieldClassifier.classify_all(entity.fields)

        _progress("discovery", "classificando campos e relacionamentos", 40)

        # Relationships
        rels = parser.relationships()
        result.relationships_count = len(rels.relationships)

        # Menu
        menu = parser.menu_tree()
        result.menu_programs = menu.total_programs

        # ── P2-A: Synthetic Knowledge Base ──
        _progress("knowledge", "construindo base de conhecimento P2-A", 45)

        # Screen-entity bindings
        bindings = parser.screen_entity_bindings()
        result.bindings_count = len(bindings)

        # Program catalog
        catalog = parser.program_catalog()
        result.programs_cataloged = len(catalog._entries)
        result.modules_detected = len(catalog._by_module)

        # Business dependency graph
        graph = parser.business_dependency_graph()
        planner = BusinessDatasetPlanner()
        result.dependency_graph = planner.plan_summary(graph)

        # Evidence report
        evidence_builder = SyntheticEvidenceReportBuilder()
        evidence = evidence_builder.build(
            entities=entities,
            screens_count=len(screens),
            bindings=bindings,
            relationships=rels,
            dependency_graph=graph,
            program_catalog=catalog,
            source_files_count=len(source_files),
        )
        result.evidence_report = json.loads(evidence_builder.to_json(evidence))

        # Journey Mix pre-definidos
        mix_builder = JourneyMixBuilder()
        result.journey_mix_configs = [
            json.loads(mix_builder.to_config_json(
                JourneyMixBuilder.lojas_basico()
            )),
        ]

        # Capture Knowledge (se houver captures disponiveis)
        capture_dir = Path(source_dir) / "captures" if Path(source_dir).is_dir() else None
        if capture_dir and capture_dir.exists():
            try:
                from .capture_parametrizer import CaptureParametrizer
                cp = CaptureParametrizer()
                templates = cp.analyze_capture_dir(str(capture_dir))
                if templates:
                    integrator = CaptureKnowledgeIntegrator()
                    for tmpl in templates[:5]:  # Limite de 5 capturas
                        enriched = integrator.enrich_template(tmpl, entities, bindings)
                        result.capture_templates_enriched += 1
            except Exception as e:
                result.warnings.append(f"capture knowledge: {e}")

        _progress("journey", "gerando jornadas CRUD", 55)

        # ------------------------------------------------------------------
        # Fase 2: Journey Generation
        # ------------------------------------------------------------------
        gen = CRUDJourneyGenerator()
        journeys, reports, skipped = gen.generate_all_with_reports(entities, coverages, classifications)
        result.journeys_generated = len(journeys)
        result.skipped_entities = skipped

        saved = 0
        for journey, report in zip(journeys, reports):
            if save_to_db and self.con:
                try:
                    entity_name = journey.journey_id.replace("crud_", "", 1)
                    import json as _json
                    steps_json = _json.dumps([s.__dict__ if hasattr(s, '__dict__') else s for s in journey.steps], default=str)
                    self.con.execute(
                        """INSERT OR REPLACE INTO entity_tests (entity_name, name, description, steps_json, tags_csv, created_at, updated_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?)""",
                        (entity_name, journey.name, journey.description,
                         steps_json, ",".join(journey.tags) if journey.tags else "",
                         report.generated_at, report.generated_at),
                    )
                    self.con.commit()
                    saved += 1
                except Exception:
                    pass

        result.journeys_saved = saved

        _progress("business", "inferindo regras de negocio", 70,
                  {"journeys": len(journeys)})

        # Fase 2b: Inferencia de regras de negocio (baseada no grafo de dependencias)
        from dakota_gateway.source_analyzer.relationship_mapper import RelationshipMapper
        from dakota_gateway.synthetic.flow_inferencer import FlowInferencer, convert_to_engine_format

        mapper = RelationshipMapper()
        rel_map = mapper.map(entities, source_dir=source_dir)
        # Garante que todas as entidades estao no grafo (mesmo sem relacoes)
        full_graph = dict(rel_map.entity_graph)
        for e in entities:
            if e.name not in full_graph:
                full_graph[e.name] = []
        inferencer = FlowInferencer()
        model = inferencer.infer(full_graph, entities)
        discovered = {e.name for e in entities}
        result.business_eval = convert_to_engine_format(model) if model.flows else {}

        # Persiste business_eval no banco
        if save_to_db and self.con and result.business_eval:
            import hashlib, json as _json
            try:
                source_hash = hashlib.sha256(source_dir.encode()).hexdigest()[:16]
                self.con.execute(
                    """INSERT INTO business_evals 
                       (source_hash, source_dir, rules_evaluated, rules_ok, rules_broken, 
                        gaps_json, flows_coverage_json, recommendation, entities_normalized_json, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        source_hash,
                        source_dir,
                        result.business_eval.get("rules_evaluated", 0),
                        result.business_eval.get("rules_ok", 0),
                        result.business_eval.get("rules_broken", 0),
                        _json.dumps(result.business_eval.get("gaps", []), ensure_ascii=False),
                        _json.dumps(result.business_eval.get("flows_coverage", []), ensure_ascii=False),
                        result.business_eval.get("recommendation", ""),
                        _json.dumps(list(discovered), ensure_ascii=False),
                        result.business_eval.get("created_at", ""),
                    ),
                )
                self.con.commit()
            except Exception:
                pass

            # Persiste cada gap individualmente para auditoria
            for gap in result.business_eval.get("gaps", []):
                try:
                    existing = self.con.execute(
                        "SELECT id FROM business_gaps WHERE gap_id = ?", (gap.get("gap_id", ""),)
                    ).fetchone()
                    if not existing:
                        # Sugere arquivos provaveis onde a entidade deveria estar
                        missing = gap.get("missing_entity", "")
                        suggested_files = []
                        import os as _os
                        if missing and source_dir:
                            for root, dirs, files in _os.walk(source_dir):
                                for f in files:
                                    # Busca arquivos que mencionam a entidade (qualquer extensao)
                                    if missing.lower() in f.lower():
                                        suggested_files.append(_os.path.relpath(_os.path.join(root, f), source_dir))
                                if len(suggested_files) >= 5:
                                    break
                            # Se nao achou, sugere criar stub
                            if not suggested_files:
                                suggested_files.append("(criar) cadastro/{}.prg".format(missing.lower()))
                                suggested_files.append("(criar) includes/{}.dbo".format(missing.lower()))
                                suggested_files.append("(criar) menus/menu_{}.prg".format(missing.lower()))
                        self.con.execute(
                            """INSERT INTO business_gaps 
                               (gap_id, severity, description, missing_entity, affected_flow,
                                impact, recommendation, suggested_files, status, created_at)
                               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'open', ?)""",
                            (
                                gap.get("gap_id", ""),
                                gap.get("severity", "high"),
                                gap.get("description", ""),
                                missing,
                                gap.get("affected_flow", ""),
                                gap.get("impact", ""),
                                gap.get("recommendation", ""),
                                _json.dumps(suggested_files, ensure_ascii=False),
                                result.business_eval.get("created_at", ""),
                            ),
                        )
                except Exception:
                    pass
            if save_to_db and self.con:
                try:
                    self.con.commit()
                except Exception:
                    pass

        _progress("business", "regras avaliadas", 82,
                  {"rules": result.business_eval.get("rules_evaluated", 0)})

        # ------------------------------------------------------------------
        # Fase 3: Synthetic Data
        # ------------------------------------------------------------------
        _progress("synthetic", "gerando dados sinteticos", 90)
        dataset_builder = DatasetBuilder() if self.con else None
        datasets_count = 0

        if save_to_db and self.con and dataset_builder:
            for journey in journeys:
                try:
                    jds = journey_builder.build_journey_dataset(
                        journey, session_count=session_count, seed=seed
                    )
                    datasets_count += 1
                except Exception:
                    pass

        result.datasets_generated = datasets_count

        _progress("validation", "validando jornadas", 96)

        # ------------------------------------------------------------------
        # Fase 4: Validation
        # ------------------------------------------------------------------
        entity_map = {e.name: e for e in entities}
        validations = self.validator.validate_all(journeys, entity_map)
        result.validation_summary = self.validator.summary(validations)

        # Detail
        result.detail = {
            "entities": [e.name for e in entities[:20]],
            "complete_crud_entities": [
                c.entity_name for c in coverages if c.is_complete
            ],
            "journey_ids": [j.journey_id for j in journeys[:20]],
            "top_issues": result.validation_summary.get("top_issues", []),
        }

        result.duration_ms = int(time.time() * 1000) - start_ms

        _progress("completed", "pipeline concluido", 100,
                  {"entities": result.entities_count, "journeys": result.journeys_generated,
                   "datasets": result.datasets_generated, "duration_ms": result.duration_ms})

        return result

    def run_and_report(self, source_dir: str, **kwargs) -> dict:
        """Executa o pipeline e retorna relatorio em dicionario."""
        progress_cb = kwargs.pop("progress_callback", None)
        result = self.run(source_dir, progress_callback=progress_cb, **kwargs)
        return {
            "source_dir": result.source_dir,
            "duration_ms": result.duration_ms,
            "pipeline": "P2-A Synthetic Knowledge Base",
            "discovery": {
                "entities": result.entities_count,
                "screens": result.screens_count,
                "crud": result.crud_summary,
                "relationships": result.relationships_count,
                "menu_programs": result.menu_programs,
            },
            "knowledge_base": {
                "screen_entity_bindings": result.bindings_count,
                "programs_cataloged": result.programs_cataloged,
                "modules_detected": result.modules_detected,
                "dependency_graph": result.dependency_graph,
            },
            "evidence_report": result.evidence_report,
            "journeys": {
                "generated": result.journeys_generated,
                "saved": result.journeys_saved,
            },
            "journey_mix": result.journey_mix_configs,
            "synthetic": {
                "datasets_generated": result.datasets_generated,
            },
            "capture_knowledge": {
                "templates_enriched": result.capture_templates_enriched,
            },
            "validation": result.validation_summary,
            "warnings": result.warnings,
            "detail": result.detail,
            "business_eval": result.business_eval,
        }

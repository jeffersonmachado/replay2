from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from .entity_catalog import EntityDefinition, FieldDefinition, OperationDefinition, ScreenDefinition


from .sql_extractor import SQLExtractor
from .isam_extractor import ISAMExtractor
from .dbf_extractor import DBFExtractor
from .recital_extractor import RecitalExtractor
from .validation_extractor import ValidationExtractor
from .screen_extractor import ScreenExtractor
from .crud_detector import CRUDDetector, CRUDCoverage
from .field_classifier import FieldClassifier, FieldClassification
from .relationship_mapper import RelationshipMapper, RelationshipMap
from .menu_analyzer import MenuAnalyzer, MenuTree
from .screen_entity_linker import ScreenEntityLinker, ScreenEntityBinding
from .program_catalog import ProgramCatalog, ProgramEntry
from .source_inventory import collect_preferred_source_files
from ..synthetic.ddl_parser import DDLParser
from ..synthetic.business_dataset_planner import (
    BusinessDatasetPlanner,
    BusinessDependencyGraph,
)


_ENTITY_STOPWORDS = {
    "if", "or", "on", "in", "is", "as", "by", "to", "do", "go",
    "no", "ok", "id", "of", "at", "be", "he", "it", "we",
    "set", "for", "end", "use", "new", "old", "all", "and",
    "not", "the", "are", "has", "had", "was", "see", "off",
}


class SourceParser:
    """Orquestrador de analise de codigo-fonte: SQL, ISAM, DBF, Recital, validacoes e telas."""

    def __init__(self, source_dir: str):
        self.source_dir = Path(source_dir)
        self._entities: dict[str, EntityDefinition] = {}
        self._screens: list[ScreenDefinition] = []

    # ------------------------------------------------------------------
    # API publica
    # ------------------------------------------------------------------

    def parse_all(self, progress_cb=None) -> tuple[list[EntityDefinition], list[ScreenDefinition]]:
        files = self._collect_source_files()
        total = len(files)
        for i, file_path in enumerate(files):
            content = file_path.read_text(encoding="utf-8", errors="replace")
            self._parse_file(file_path, content)
            if progress_cb and total > 0 and (i % max(1, total // 20) == 0 or i == total - 1):
                pct = 5 + int((i / max(total, 1)) * 20)
                progress_cb("discovery", f"parse: {i+1}/{total} arquivos", pct,
                           {"files_parsed": i+1, "total_files": total, "entities": len(self._entities)})

        return (list(self._entities.values()), self._screens)

    def entities(self) -> list[EntityDefinition]:
        return list(self._entities.values())

    def screens(self) -> list[ScreenDefinition]:
        return self._screens

    # ------------------------------------------------------------------
    # Discovery Engine — Sprint 2
    # ------------------------------------------------------------------

    def crud_coverage(self) -> list[CRUDCoverage]:
        """Analisa cobertura CRUD de todas as entidades."""
        return CRUDDetector.detect_all(list(self._entities.values()))

    def crud_summary(self) -> dict:
        """Resumo estatistico da cobertura CRUD."""
        return CRUDDetector.summary(self.crud_coverage())

    def classify_fields(self) -> dict[str, list[FieldClassification]]:
        """Classifica campos de todas as entidades."""
        result: dict[str, list[FieldClassification]] = {}
        for entity in self._entities.values():
            result[entity.name] = FieldClassifier.classify_all(entity.fields)
        return result

    def relationships(self) -> RelationshipMap:
        """Mapeia relacionamentos entre entidades."""
        mapper = RelationshipMapper()
        return mapper.map(list(self._entities.values()))

    def menu_tree(self) -> MenuTree:
        """Constroi arvore de navegacao."""
        analyzer = MenuAnalyzer(str(self.source_dir))
        return analyzer.analyze(str(self.source_dir))

    def screen_entity_bindings(self) -> list[ScreenEntityBinding]:
        """Gera bindings tela→entidade com evidencias e confianca.

        Faz parte da entrega P2-A — Synthetic Knowledge Base.
        """
        linker = ScreenEntityLinker(str(self.source_dir))
        return linker.link(self._screens, list(self._entities.values()))

    def program_catalog(self) -> ProgramCatalog:
        """Constroi catalogo de programas com metadados semanticos.

        Faz parte da entrega P2-A — Synthetic Knowledge Base.
        """
        bindings = self.screen_entity_bindings()
        catalog = ProgramCatalog(str(self.source_dir))
        catalog.build(list(self._entities.values()), bindings)
        return catalog

    def business_dependency_graph(self) -> BusinessDependencyGraph:
        """Constroi grafo de dependencia de negocio entre entidades.

        Faz parte da entrega P2-A — Synthetic Knowledge Base.
        """
        rels = self.relationships()
        planner = BusinessDatasetPlanner()
        return planner.plan(list(self._entities.values()), rels)

    def discovery_report(self) -> dict:
        """Relatorio completo de discovery — P2-A Synthetic Knowledge Base."""
        crud = self.crud_summary()
        rels = self.relationships()
        menu = self.menu_tree()
        bindings = self.screen_entity_bindings()
        catalog = self.program_catalog()
        graph = self.business_dependency_graph()

        # ── Bindings ──
        high = [b for b in bindings if b.confidence >= 0.75]
        medium = [b for b in bindings if 0.40 <= b.confidence < 0.75]
        low = [b for b in bindings if b.confidence < 0.40]
        unbound = [b for b in bindings if not b.entity_name]

        by_entity: dict[str, list[dict]] = {}
        for b in bindings:
            if b.entity_name:
                by_entity.setdefault(b.entity_name, []).append({
                    "screen_title": b.screen_title,
                    "program_name": b.program_name,
                    "operation": b.operation,
                    "confidence": b.confidence,
                    "matched_fields": b.matched_fields,
                })

        # ── Program catalog ──
        catalog_report = catalog.to_report()

        # ── Dependency graph ──
        planner = BusinessDatasetPlanner()
        graph_summary = planner.plan_summary(graph)

        # ── Classificacao de campos ──
        classifications = self.classify_fields()
        total_classified = sum(len(v) for v in classifications.values())

        # ── Contagem de arquivos ──
        source_files_count = len(self._collect_source_files())

        return {
            "pipeline": "P2-A Synthetic Knowledge Base",
            "entities": len(self._entities),
            "screens": len(self._screens),
            "source_files": source_files_count,
            "fields_classified": total_classified,
            "crud": crud,
            "relationships": {
                "total": len(rels.relationships),
                "orphan_entities": rels.orphan_entities,
                "adjacency": RelationshipMapper.to_adjacency_list(rels),
            },
            "menu": {
                "total_menus": menu.total_menus,
                "total_programs": menu.total_programs,
                "max_depth": menu.max_depth,
                "orphan_programs": menu.orphan_programs,
                "tree": MenuAnalyzer.to_dict(menu.root) if menu.root else None,
            },
            "screen_entity_bindings": {
                "total_bindings": len(bindings),
                "high_confidence": len(high),
                "medium_confidence": len(medium),
                "low_confidence": len(low),
                "unbound_screens": len(unbound),
                "bindings_by_entity": by_entity,
                "details": [
                    {
                        "screen_title": b.screen_title,
                        "program_name": b.program_name,
                        "source_file": b.source_file,
                        "entity_name": b.entity_name,
                        "operation": b.operation,
                        "matched_fields": b.matched_fields,
                        "unmatched_screen_fields": b.unmatched_screen_fields,
                        "confidence": b.confidence,
                        "evidence": b.evidence,
                    }
                    for b in bindings
                ],
            },
            "program_catalog": catalog_report,
            "dependency_graph": graph_summary,
        }

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _collect_source_files(self) -> list[Path]:
        # Prefere .prg quando existe par .dbo do mesmo programa.
        return collect_preferred_source_files(self.source_dir, {".prg", ".sql", ".dbo"})

    def _parse_file(self, file_path: Path, content: str) -> None:
        if file_path.suffix.lower() == ".sql":
            # Usa SQLExtractor melhorado (com CREATE TABLE) + DDLParser como fallback
            sql_entities = SQLExtractor.extract(content, str(file_path))
            ddl_result = DDLParser().parse(content, source=str(file_path))
            for ent in sql_entities:
                self._merge_entity(ent)
            for ent in ddl_result.entities:
                self._merge_entity(ent)
            return

        sql_entities = SQLExtractor.extract(content, str(file_path))
        isam_entities = ISAMExtractor.extract(content, str(file_path))
        dbf_entities = DBFExtractor.extract(content, str(file_path))
        recital_entities = RecitalExtractor.extract(content, str(file_path))

        screens = ScreenExtractor.extract(content, str(file_path))
        self._screens.extend(screens)

        for ent_list in (sql_entities, isam_entities, dbf_entities, recital_entities):
            for ent in ent_list:
                merged = self._merge_entity(ent)
                ValidationExtractor.enrich(merged, content, str(file_path))

    def _merge_entity(self, incoming: EntityDefinition) -> EntityDefinition:
        key = incoming.name.upper()
        if not self._is_valid_entity_name(incoming.name):
            return incoming
        if key in self._entities:
            existing = self._entities[key]
            existing_fields = {f.name.upper() for f in existing.fields}
            for f in incoming.fields:
                if f.name.upper() not in existing_fields:
                    existing.fields.append(f)
            existing.operations.extend(incoming.operations)
            existing.indexes.extend(incoming.indexes)
            if self._storage_priority(incoming.storage_type) > self._storage_priority(existing.storage_type):
                existing.storage_type = incoming.storage_type
            return existing
        else:
            self._entities[key] = incoming
            return incoming

    @staticmethod
    def _storage_priority(storage_type: str) -> int:
        priority = {
            "unknown": 0,
            "recital": 1,
            "dbf": 2,
            "isam": 3,
            "sql": 4,
        }
        return priority.get(str(storage_type or "unknown").strip().lower(), 0)

    @staticmethod
    def _is_valid_entity_name(name: str) -> bool:
        clean = str(name or "").strip()
        low = clean.lower()

        if len(clean) < 3:
            return False
        if low in _ENTITY_STOPWORDS:
            return False
        if not re.search(r"[a-zA-Z]", clean):
            return False
        if re.search(r'[&"\'()+]', clean):
            return False
        if clean.count("{") or clean.count("}") or clean.count("[") or clean.count("]"):
            return False
        if clean.startswith((".", ",", ";", ":", "/", "\\")):
            return False
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{2,31}", clean):
            return False
        if low.startswith(("tmp_", "var_", "arg_", "param_")):
            return False
        if clean.startswith("&(") or clean.startswith("+") or clean.endswith("+"):
            return False
        if "->" in clean or ".." in clean:
            return False
        if any(token in low for token in ("+l", "+p", "cdir", "ldir")) and any(ch in clean for ch in '&+"'):
            return False
        return True

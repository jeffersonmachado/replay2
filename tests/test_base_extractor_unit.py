"""Testes do contrato BaseExtractor (dívida G3).

Garante que os extractors de entidade/tela respeitam a interface comum:
herdam de BaseExtractor, têm ``name`` único, ``extract`` estático com a
assinatura oficial, e que o parser aplica os extractors de entidade na
ordem do registro (SQL → ISAM → DBF → Recital).
"""
from __future__ import annotations

import inspect
import unittest

from dakota_gateway.source_analyzer.base_extractor import (
    BaseExtractor,
    entity_extractors,
)
from dakota_gateway.source_analyzer.sql_extractor import SQLExtractor
from dakota_gateway.source_analyzer.isam_extractor import ISAMExtractor
from dakota_gateway.source_analyzer.dbf_extractor import DBFExtractor
from dakota_gateway.source_analyzer.recital_extractor import RecitalExtractor
from dakota_gateway.source_analyzer.screen_extractor import ScreenExtractor
from dakota_gateway.source_analyzer.parser import SourceParser

_ENTITY_EXTRACTORS = (SQLExtractor, ISAMExtractor, DBFExtractor, RecitalExtractor)


class BaseExtractorContractTests(unittest.TestCase):
    def test_all_extractors_inherit_base(self):
        for cls in (*_ENTITY_EXTRACTORS, ScreenExtractor):
            self.assertTrue(issubclass(cls, BaseExtractor), cls.__name__)

    def test_names_are_non_empty_and_unique(self):
        names = [cls.name for cls in (*_ENTITY_EXTRACTORS, ScreenExtractor)]
        self.assertTrue(all(names))
        self.assertEqual(len(names), len(set(names)), names)

    def test_extract_is_static_with_official_signature(self):
        for cls in (*_ENTITY_EXTRACTORS, ScreenExtractor):
            # staticmethod: acessível na classe sem instância e sem self
            raw = inspect.getattr_static(cls, "extract")
            self.assertIsInstance(raw, staticmethod, cls.__name__)
            sig = inspect.signature(cls.extract)
            params = list(sig.parameters.values())
            self.assertEqual([p.name for p in params][:2], ["content", "source_file"], cls.__name__)
            self.assertEqual(params[1].default, "", cls.__name__)

    def test_base_extractor_is_abstract(self):
        with self.assertRaises(TypeError):
            BaseExtractor()  # type: ignore[abstract]

    def test_entity_extractors_registry_order(self):
        self.assertEqual(entity_extractors(), _ENTITY_EXTRACTORS)

    def test_registry_matches_parser_import(self):
        """O parser deve consumir o registro (não chamar extractors nominalmente)."""
        source = inspect.getsource(SourceParser._parse_file)
        self.assertIn("entity_extractors()", source)
        self.assertNotIn("ISAMExtractor.extract", source)
        self.assertNotIn("DBFExtractor.extract", source)
        self.assertNotIn("RecitalExtractor.extract", source)


class BaseExtractorBehaviorTests(unittest.TestCase):
    """Regressão funcional: extractors continuam extraindo após a ABC."""

    def test_sql_extractor_returns_entities(self):
        entities = SQLExtractor.extract(
            "INSERT INTO clientes (nome, cpf) VALUES ('A', '1')", "prog.prg"
        )
        self.assertIsInstance(entities, list)
        self.assertTrue(any(e.name.upper() == "CLIENTES" for e in entities))

    def test_isam_extractor_returns_entities(self):
        entities = ISAMExtractor.extract("USE clientes\nAPPEND BLANK", "prog.prg")
        self.assertIsInstance(entities, list)
        self.assertTrue(any(e.name.upper() == "CLIENTES" for e in entities))

    def test_screen_extractor_returns_screens(self):
        screens = ScreenExtractor.extract(
            '@ 1, 1 SAY "Nome:" GET wnome\nREAD', "prog.prg"
        )
        self.assertIsInstance(screens, list)

    def test_extractors_accept_empty_source_file(self):
        for cls in _ENTITY_EXTRACTORS:
            result = cls.extract("SELECT * FROM t")
            self.assertIsInstance(result, list, cls.__name__)


if __name__ == "__main__":
    unittest.main()

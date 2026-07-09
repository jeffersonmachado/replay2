from __future__ import annotations

from importlib import import_module


_EXPORTS = {
    "SourceParser": ".parser",
    "SQLExtractor": ".sql_extractor",
    "ISAMExtractor": ".isam_extractor",
    "DBFExtractor": ".dbf_extractor",
    "RecitalExtractor": ".recital_extractor",
    "EntityDefinition": ".entity_catalog",
    "FieldDefinition": ".entity_catalog",
    "OperationDefinition": ".entity_catalog",
    "ScreenDefinition": ".entity_catalog",
    "ValidationExtractor": ".validation_extractor",
    "ScreenExtractor": ".screen_extractor",
    "CRUDDetector": ".crud_detector",
    "CRUDCoverage": ".crud_detector",
    "MenuAnalyzer": ".menu_analyzer",
    "MenuTree": ".menu_analyzer",
    "MenuNode": ".menu_analyzer",
    "FieldClassifier": ".field_classifier",
    "FieldClassification": ".field_classifier",
    "RelationshipMapper": ".relationship_mapper",
    "Relationship": ".relationship_mapper",
    "RelationshipMap": ".relationship_mapper",
}

__all__ = list(_EXPORTS)


def __getattr__(name: str):
    module_name = _EXPORTS.get(name)
    if not module_name:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = import_module(module_name, __name__)
    value = getattr(module, name)
    globals()[name] = value
    return value

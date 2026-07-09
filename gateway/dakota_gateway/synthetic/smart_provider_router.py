"""Roteador inteligente: field → DataProvider.

Delega classificacao semantica ao FieldClassifier (inferencia por tokens)
e mapeia categoria→provider. Nao duplica regex — usa a mesma inferencia.
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Dict, List, Optional, Tuple

from .providers import DataProvider, ProviderRegistry, default_registry
from .schema import FieldSchema
from ..source_analyzer.field_classifier import FieldClassification, FieldClassifier, _TOKEN_MAP, _CONTEXT_MAP

# ── Categoria semantica → provider ──
_CATEGORY_TO_PROVIDER: Dict[str, str] = {
    "cpf": "cpf",
    "cnpj": "cnpj",
    "rg": "rg",
    "cep": "cep",
    "email": "email",
    "phone": "phone",
    "name": "person_name",
    "address": "address",
    "neighborhood": "text",
    "city": "text",
    "state": "text",
    "date": "date",
    "time": "datetime",
    "code": "code",
    "product_code": "code",
    "order_number": "number",
    "invoice_number": "number",
    "duplicate_number": "text",
    "money": "money",
    "quantity": "number",
    "measure": "decimal",
    "enum": "choice",
    "boolean": "boolean",
    "text": "text",
    "foreign_key": "code",
}

# ── Tipo de dados → provider (fallback) ──
_TYPE_TO_PROVIDER: Dict[str, str] = {
    "integer": "number", "int": "number", "number": "number",
    "decimal": "decimal", "float": "decimal", "double": "decimal",
    "money": "money", "date": "date", "datetime": "datetime",
    "timestamp": "datetime", "boolean": "boolean", "bool": "boolean",
    "text": "text", "varchar": "text", "char": "text",
}


class SmartProviderRouter:
    """Roteia campos para providers usando inferencia do FieldClassifier.

    Estrategia (ordem de prioridade):
    1. Classificacao semantica (FieldClassifier) → categoria → provider
    2. Formato/PICTURE → provider
    3. Tipo de dados → provider
    4. Contexto da entidade → provider
    5. Choices/domain → choice provider
    6. Fallback → text provider
    """

    def __init__(self, registry: Optional[ProviderRegistry] = None):
        self.registry = registry or default_registry()

    def resolve(
        self,
        field: FieldSchema,
        entity_name: str = "",
        classification: Optional[FieldClassification] = None,
    ) -> DataProvider:
        """Resolve o melhor provider por inferencia."""
        field_name = (field.name or "").lower().strip()
        best_provider = "text"
        best_confidence = 0.0

        # ── 1. Classificacao semantica (inferencia por tokens) ──
        if classification and classification.semantic_category:
            cat = classification.semantic_category
            provider = _CATEGORY_TO_PROVIDER.get(cat)
            if provider:
                best_provider = provider
                best_confidence = classification.confidence

        # Se nao tem classification, faz inferencia rapida
        if best_confidence < 0.5:
            inferred = self._quick_infer(field_name, entity_name)
            if inferred and inferred[1] > best_confidence:
                best_provider = inferred[0]
                best_confidence = inferred[1]

        # ── 2. Formato/PICTURE ──
        if best_confidence < 0.9 and field.format:
            fmt_provider = self._resolve_by_format(str(field.format))
            if fmt_provider and 0.95 > best_confidence:
                best_provider = fmt_provider
                best_confidence = 0.95

        # ── 3. Tipo de dados ──
        if best_confidence < 0.7:
            dtype = (field.datatype or (classification.inferred_datatype if classification else "") or "text").lower()
            type_provider = _TYPE_TO_PROVIDER.get(dtype)
            if type_provider and 0.6 > best_confidence:
                best_provider = type_provider
                best_confidence = 0.6

        # ── 4. Contexto entidade + campo ──
        if best_confidence < 0.85 and entity_name and field_name:
            ctx_provider, ctx_conf = self._resolve_by_context(field_name, entity_name)
            if ctx_provider and ctx_conf > best_confidence:
                best_provider = ctx_provider
                best_confidence = ctx_conf

        # ── 5. Choices definidos ──
        if field.choices:
            best_provider = "choice"
            best_confidence = 1.0

        return self.registry.get(best_provider)

    def resolve_all(
        self,
        fields: List[FieldSchema],
        entity_name: str = "",
        classifications: Optional[List[FieldClassification]] = None,
    ) -> List[DataProvider]:
        """Resolve providers para todos os campos de uma entidade."""
        providers: List[DataProvider] = []
        for i, field in enumerate(fields):
            cls_result = classifications[i] if classifications and i < len(classifications) else None
            providers.append(self.resolve(field, entity_name, cls_result))
        return providers

    @staticmethod
    def _quick_infer(field_name: str, entity_name: str = "") -> Optional[Tuple[str, float]]:
        """Inferencia rapida por token (sem dependencia do FieldClassifier)."""
        tokens = re.split(r"[_\-\s]+", field_name.lower())
        tokens = [t for t in tokens if t]
        best_provider = ""
        best_conf = 0.0

        for token in tokens:
            # Contexto
            for ctx_key, ctx_rules in _CONTEXT_MAP.items():
                if ctx_key in entity_name.lower() and token in ctx_rules:
                    _, cat, conf = ctx_rules[token]
                    provider = _CATEGORY_TO_PROVIDER.get(cat, "")
                    if provider and conf > best_conf:
                        best_provider = provider
                        best_conf = conf

            # Dicionario
            if token in _TOKEN_MAP:
                for _, cat, conf in _TOKEN_MAP[token]:
                    provider = _CATEGORY_TO_PROVIDER.get(cat, "")
                    if provider and conf > best_conf:
                        best_provider = provider
                        best_conf = conf

        return (best_provider, best_conf) if best_provider else None

    @staticmethod
    def _resolve_by_context(field_name: str, entity_name: str) -> Tuple[str, float]:
        """Resolve provider por contexto entidade+campo."""
        entity_lower = entity_name.lower()
        field_lower = field_name.lower()
        for ctx_entity, field_map in _CONTEXT_MAP.items():
            if ctx_entity in entity_lower or entity_lower in ctx_entity:
                for ctx_field, (_, cat, conf) in field_map.items():
                    if ctx_field in field_lower:
                        provider = _CATEGORY_TO_PROVIDER.get(cat, "")
                        return (provider, conf)
        return ("", 0.0)

    @staticmethod
    def _resolve_by_format(fmt: str) -> Optional[str]:
        """Resolve provider a partir do formato/mascara."""
        fmt = fmt.strip()
        if re.match(r"^\d{3}\.\d{3}\.\d{3}-\d{2}$", fmt):
            return "cpf"
        if re.match(r"^\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}$", fmt):
            return "cnpj"
        if re.match(r"^\d{5}-\d{3}$", fmt):
            return "cep"
        if re.match(r"^\(?\d{2}\)?\s?\d{4,5}-?\d{4}$", fmt):
            return "phone"
        if re.match(r"^\d{2}/\d{2}/\d{4}$", fmt) or re.match(r"^\d{4}-\d{2}-\d{2}$", fmt):
            return "date"
        if "@" in fmt:
            return "email"
        return None

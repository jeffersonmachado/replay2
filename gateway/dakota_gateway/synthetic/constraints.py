from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class ConstraintRule:
    field: str
    required: bool = False
    unique: bool = False
    min_value: Optional[float] = None
    max_value: Optional[float] = None
    min_length: Optional[int] = None
    max_length: Optional[int] = None
    choices: Optional[list[str]] = None
    format: Optional[str] = None
    country: str = "BR"

    @staticmethod
    def from_field_schema(field_schema) -> ConstraintRule:
        """Cria ConstraintRule a partir de um FieldSchema."""
        return ConstraintRule(
            field=field_schema.name,
            required=field_schema.required,
            unique=field_schema.unique,
            min_value=field_schema.min_value,
            max_value=field_schema.max_value,
            min_length=field_schema.min_length,
            max_length=field_schema.max_length,
            choices=field_schema.choices,
            format=field_schema.format,
            country=field_schema.country if hasattr(field_schema, "country") else "BR",
        )


@dataclass
class ConstraintViolation:
    field: str
    rule: str
    value: Any
    message: str


class ConstraintValidator:
    """Valida valores gerados contra constraints definidas."""

    @staticmethod
    def validate(value: Any, rule: ConstraintRule) -> list[ConstraintViolation]:
        violations: list[ConstraintViolation] = []

        if rule.required and (value is None or (isinstance(value, str) and value.strip() == "")):
            violations.append(
                ConstraintViolation(
                    field=rule.field,
                    rule="required",
                    value=value,
                    message=f"Campo '{rule.field}' é obrigatório mas está vazio",
                )
            )

        if isinstance(value, str) and value.strip():
            if rule.min_length is not None and len(value) < rule.min_length:
                violations.append(
                    ConstraintViolation(
                        field=rule.field,
                        rule="min_length",
                        value=value,
                        message=f"'{rule.field}' tem {len(value)} caracteres, mínimo {rule.min_length}",
                    )
                )
            if rule.max_length is not None and len(value) > rule.max_length:
                violations.append(
                    ConstraintViolation(
                        field=rule.field,
                        rule="max_length",
                        value=value,
                        message=f"'{rule.field}' tem {len(value)} caracteres, máximo {rule.max_length}",
                    )
                )
            if rule.choices and value not in rule.choices:
                violations.append(
                    ConstraintViolation(
                        field=rule.field,
                        rule="choices",
                        value=value,
                        message=f"'{rule.field}' valor '{value}' não está em {rule.choices}",
                    )
                )

        if isinstance(value, (int, float)):
            if rule.min_value is not None and value < rule.min_value:
                violations.append(
                    ConstraintViolation(
                        field=rule.field,
                        rule="min",
                        value=value,
                        message=f"'{rule.field}'={value} < mínimo {rule.min_value}",
                    )
                )
            if rule.max_value is not None and value > rule.max_value:
                violations.append(
                    ConstraintViolation(
                        field=rule.field,
                        rule="max",
                        value=value,
                        message=f"'{rule.field}'={value} > máximo {rule.max_value}",
                    )
                )

        return violations

    @staticmethod
    def validate_record(record: dict[str, Any], rules: list[ConstraintRule]) -> list[ConstraintViolation]:
        all_violations: list[ConstraintViolation] = []
        for rule in rules:
            value = record.get(rule.field)
            all_violations.extend(ConstraintValidator.validate(value, rule))
        return all_violations

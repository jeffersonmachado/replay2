from __future__ import annotations

import json
import random
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

from .schema import SyntheticSchema, FieldSchema, ScreenSchema
from .providers import ProviderRegistry, default_registry, DataProvider
from .constraints import ConstraintValidator, ConstraintRule


@dataclass
class DatasetRecord:
    record_index: int
    data: dict[str, Any]
    created_at: str = ""


@dataclass
class Dataset:
    name: str
    screen_id: str = ""
    entity_name: str = ""
    quantity: int = 0
    seed: int = 0
    records: list[DatasetRecord] = field(default_factory=list)
    params_json: Optional[str] = None
    created_at: str = ""

    def to_json(self) -> str:
        return json.dumps(
            {
                "name": self.name,
                "screen_id": self.screen_id,
                "entity_name": self.entity_name,
                "quantity": self.quantity,
                "seed": self.seed,
                "params": self.params_json,
                "records": [r.data for r in self.records],
            },
            ensure_ascii=False,
            indent=2,
        )


class DatasetBuilder:
    """Constroi datasets sinteticos a partir de schemas e providers."""

    def __init__(self, registry: Optional[ProviderRegistry] = None):
        self.registry = registry or default_registry()
        self._generated: dict[str, set] = {}  # field_name -> set de valores gerados (unicidade)

    def build(
        self,
        schema: SyntheticSchema,
        lookup_values: Optional[dict[str, list[Any]]] = None,
    ) -> Dataset:
        """Gera um dataset completo a partir de um SyntheticSchema."""
        self._generated = {}
        lookup_values = lookup_values or {}

        # Re-seed providers com o seed do schema
        seed = schema.seed
        for provider in self.registry._providers.values():
            provider.reseed(seed)

        ds = Dataset(
            name=f"{schema.entity_name}_{schema.quantity}",
            screen_id=schema.screen.screen_id,
            entity_name=schema.entity_name,
            quantity=schema.quantity,
            seed=schema.seed,
            params_json=json.dumps(schema.params, ensure_ascii=False),
            created_at=datetime.now().isoformat(),
        )

        for i in range(schema.quantity):
            record_data: dict[str, Any] = {}
            for field_schema in schema.screen.fields:
                record_data[field_schema.name] = self._generate_field(
                    field_schema, i, seed, lookup_values
                )

            ds.records.append(
                DatasetRecord(
                    record_index=i,
                    data=record_data,
                    created_at=datetime.now().isoformat(),
                )
            )

        return ds

    def _generate_field(
        self,
        field: FieldSchema,
        index: int,
        seed: int,
        lookup_values: dict[str, list[Any]],
    ) -> Any:
        provider_name = field.inferred_provider_name()
        provider = self.registry.get(provider_name)

        if not provider:
            provider = self.registry.get("text")

        # Lookup: se o campo referencia outra entidade, usa valores pre-gerados
        if field.lookup and field.lookup in lookup_values:
            lookup_list = lookup_values[field.lookup]
            if index < len(lookup_list):
                return lookup_list[index]
            return random.Random(seed + index).choice(lookup_list)

        kwargs: dict = {}
        if field.choices:
            kwargs["choices"] = field.choices
        if field.min_value is not None:
            kwargs["min"] = field.min_value
        if field.max_value is not None:
            kwargs["max"] = field.max_value
        if field.min_length is not None:
            kwargs["min_length"] = field.min_length
        if field.max_length is not None:
            kwargs["max_length"] = field.max_length

        value = provider.generate(**kwargs)

        # Garantir unicidade se necessario
        if field.unique:
            attempts = 0
            while attempts < 100:
                if field.name not in self._generated:
                    self._generated[field.name] = set()
                key = str(value)
                if key not in self._generated[field.name]:
                    self._generated[field.name].add(key)
                    break
                value = provider.generate(**kwargs)
                attempts += 1

        return value

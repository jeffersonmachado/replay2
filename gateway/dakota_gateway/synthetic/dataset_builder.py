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
        lookup_groups: Optional[dict[str, Any]] = None,
    ) -> Dataset:
        """Gera um dataset completo a partir de um SyntheticSchema.

        ``lookup_groups``: variação em par — ``{"fields_map": {campo:
        (group_key, pos)}, "groups": {group_key: {"fields": [...],
        "tuples": [(v1, v2), ...]}}}``. Os campos do grupo saem da MESMA
        tupla (registro real do cadastro) em cada registro gerado.
        """
        self._generated = {}
        lookup_values = lookup_values or {}
        lookup_groups = lookup_groups or {}
        group_fields_map: dict[str, tuple] = lookup_groups.get("fields_map") or {}
        groups: dict[str, dict] = lookup_groups.get("groups") or {}

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
            # Variação em par: escolhe UMA tupla por grupo neste registro —
            # os campos do grupo recebem valores do mesmo registro real.
            chosen: dict[str, tuple] = {}
            schema_field_names = {f.name.lower() for f in schema.screen.fields}
            for group_key, group in groups.items():
                present = [
                    f for f in (group.get("fields") or [])
                    if str(f).lower() in schema_field_names
                ]
                tuples = group.get("tuples") or []
                if len(present) < 2 or not tuples:
                    continue
                if i < len(tuples):
                    chosen[group_key] = tuples[i]
                else:
                    chosen[group_key] = random.Random(
                        f"{seed}:{i}:{group_key}").choice(tuples)
            for field_schema in schema.screen.fields:
                member = group_fields_map.get(field_schema.name.lower())
                if member and member[0] in chosen:
                    record_data[field_schema.name] = chosen[member[0]][member[1]]
                    continue
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
        # Lookup: se o campo referencia outra entidade (ou tem valores reais
        # observados para o campo — chave field:<nome>), sorteia da lista de
        # valores reais. Vem ANTES do formato pattern: um código real do
        # cadastro sempre vence um valor sintético no formato certo — o ERP
        # valida a existência ("Codigo nao cadastrado"), não o formato.
        if field.lookup and field.lookup in lookup_values:
            lookup_list = lookup_values[field.lookup]
            # O valor precisa caber no campo da tela (max_length da PICTURE):
            # código mais longo que o GET transborda para o campo seguinte e
            # desalinha a navegação do replay. Se nenhum couber, usa a lista
            # integral (largura desconhecida/ausente não pode bloquear).
            if field.max_length:
                fitting = [v for v in lookup_list if len(str(v).strip()) <= field.max_length]
                if fitting:
                    lookup_list = fitting
            if index < len(lookup_list):
                return lookup_list[index]
            return random.Random(seed + index).choice(lookup_list)

        # Formato "pattern:<molde>" — preserva o formato do valor original
        # (letra→letra aleatória, dígito→dígito, demais chars intactos). Usado
        # em células de grade com PICTURE de função ("@"), onde o provider por
        # nome geraria texto livre fora da largura real da coluna.
        fmt = field.format or ""
        if fmt.startswith("pattern:"):
            molde = fmt[len("pattern:"):]
            rng = random.Random(f"{seed}:{index}:{field.name}")
            return "".join(
                rng.choice("0123456789") if c.isdigit()
                else rng.choice("abcdefghijklmnopqrstuvwxyz") if c.islower()
                else rng.choice("ABCDEFGHIJKLMNOPQRSTUVWXYZ") if c.isupper()
                else c
                for c in molde
            )

        provider_name = field.inferred_provider_name()
        provider = self.registry.get(provider_name)

        if not provider:
            provider = self.registry.get("text")

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

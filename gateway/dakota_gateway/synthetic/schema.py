from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class FieldSchema:
    name: str
    datatype: str = "text"
    required: bool = False
    unique: bool = False
    prompt: Optional[str] = None
    lookup: Optional[str] = None  # entidade referenciada
    min_length: Optional[int] = None
    max_length: Optional[int] = None
    min_value: Optional[float] = None
    max_value: Optional[float] = None
    choices: Optional[list[str]] = None
    format: Optional[str] = None  # cpf, cnpj, cep, email, phone, date...
    country: str = "BR"
    validation_rules: list[str] = field(default_factory=list)
    constraints_json: Optional[str] = None

    @staticmethod
    def from_dict(data: dict) -> FieldSchema:
        return FieldSchema(
            name=data.get("name", ""),
            datatype=data.get("datatype", "text"),
            required=data.get("required", False),
            unique=data.get("unique", False) or data.get("unique_flag", False),
            prompt=data.get("prompt"),
            lookup=data.get("lookup") or data.get("lookup_table"),
            min_length=data.get("min_length"),
            max_length=data.get("max_length"),
            min_value=data.get("min"),
            max_value=data.get("max"),
            choices=data.get("choices"),
            format=data.get("format"),
            country=data.get("country", "BR"),
            validation_rules=data.get("validation_rules", []),
            constraints_json=data.get("constraints_json"),
        )

    def inferred_provider_name(self) -> str:
        """Retorna o nome do provider inferido a partir do datatype/format."""
        if self.format:
            format_map = {
                "cpf": "cpf",
                "cnpj": "cnpj",
                "rg": "rg",
                "cep": "cep",
                "email": "email",
                "phone": "phone",
                "date": "date",
                "datetime": "datetime",
            }
            if self.format in format_map:
                return format_map[self.format]
        if self.choices:
            return "choice"
        type_map = {
            "person_name": "person_name",
            "company_name": "company_name",
            "phone": "phone",
            "email": "email",
            "address": "address",
            "cep": "cep",
            "date": "date",
            "datetime": "datetime",
            "number": "number",
            "integer": "number",
            "decimal": "decimal",
            "money": "money",
            "boolean": "boolean",
            "uuid": "uuid",
            "code": "code",
            "text": "text",
        }
        return type_map.get(self.datatype, "text")


@dataclass
class ScreenSchema:
    screen_id: str = ""
    screen_signature: str = ""
    title: str = ""
    program_name: str = ""
    fields: list[FieldSchema] = field(default_factory=list)

    @staticmethod
    def from_dict(data: dict) -> ScreenSchema:
        fields_data = data.get("fields", [])
        return ScreenSchema(
            screen_id=data.get("screen_id", data.get("id", "")),
            screen_signature=data.get("screen_signature", ""),
            title=data.get("title", ""),
            program_name=data.get("program_name", ""),
            fields=[FieldSchema.from_dict(f) for f in fields_data],
        )


@dataclass
class SyntheticSchema:
    """Schema sintetico completo associando tela a campos e providers."""
    screen: ScreenSchema
    entity_name: str = ""
    quantity: int = 100
    seed: int = 0
    params: dict = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(
            {
                "screen_id": self.screen.screen_id,
                "screen_signature": self.screen.screen_signature,
                "entity_name": self.entity_name,
                "quantity": self.quantity,
                "seed": self.seed,
                "params": self.params,
                "fields": [
                    {
                        "name": f.name,
                        "datatype": f.datatype,
                        "required": f.required,
                        "unique": f.unique,
                        "lookup": f.lookup,
                        "min_length": f.min_length,
                        "max_length": f.max_length,
                        "min_value": f.min_value,
                        "max_value": f.max_value,
                        "choices": f.choices,
                        "format": f.format,
                    }
                    for f in self.screen.fields
                ],
            },
            ensure_ascii=False,
            indent=2,
        )

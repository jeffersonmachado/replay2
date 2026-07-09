from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Optional

from ..source_analyzer.entity_catalog import (
    EntityDefinition as SourceEntity,
    FieldDefinition as SourceField,
    ScreenDefinition as SourceScreen,
)
from ..source_analyzer.parser import SourceParser
from .schema import FieldSchema, ScreenSchema, SyntheticSchema


@dataclass
class InferenceResult:
    screens: list[ScreenSchema] = field(default_factory=list)
    schemas: list[SyntheticSchema] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class SyntheticInferencer:
    """Analisa codigo-fonte, capturas, SQL, comandos USE, e infere schemas sinteticos.

    Fluxo:
    1. Analisar codigo-fonte (via SourceParser)
    2. Mapear entidades -> ScreenSchema
    3. Mapear campos de captura -> FieldSchema
    4. Gerar SyntheticSchema por tela
    """

    def __init__(self):
        self._parser: Optional[SourceParser] = None

    def analyze_source(self, source_dir: str) -> InferenceResult:
        """Analisa diretorio de codigo-fonte e infere schemas."""
        self._parser = SourceParser(source_dir)
        entities, screens = self._parser.parse_all()

        result = InferenceResult()

        for src_screen in screens:
            screen_schema = self._screen_to_schema(src_screen, entities)
            if screen_schema.fields:
                result.screens.append(screen_schema)
                result.schemas.append(
                    SyntheticSchema(
                        screen=screen_schema,
                        entity_name=src_screen.program_name or src_screen.title or "unknown",
                    )
                )

        # Telas inferidas a partir de entidades (se nao tiver screen explícita)
        for entity in entities:
            if not any(s.title.upper() == entity.name.upper() for s in result.screens):
                screen_schema = self._entity_to_screen(entity)
                if screen_schema.fields:
                    result.screens.append(screen_schema)
                    result.schemas.append(
                        SyntheticSchema(
                            screen=screen_schema,
                            entity_name=entity.name,
                        )
                    )

        return result

    def _screen_to_schema(
        self, src_screen: SourceScreen, entities: list[SourceEntity]
    ) -> ScreenSchema:
        fields: list[FieldSchema] = []

        for sf in src_screen.fields:
            # Infere tipo pelo nome se datatype for generico
            inferred = self._infer_type_from_name(sf.name)
            datatype = sf.datatype if sf.datatype and sf.datatype != "text" else inferred
            fs = FieldSchema(
                name=sf.name,
                datatype=datatype,
                required=sf.required,
                unique=sf.unique_flag,
                prompt=sf.prompt or sf.name,
                lookup=sf.lookup_table,
                min_length=sf.min_length,
                max_length=sf.max_length,
            )
            # Enriquecer constraints via JSON
            if sf.constraints_json:
                try:
                    constraints = json.loads(sf.constraints_json)
                    if "format" in constraints:
                        fs.format = constraints["format"]
                    if "min_length" in constraints:
                        fs.min_length = constraints["min_length"]
                    if "max_length" in constraints:
                        fs.max_length = constraints["max_length"]
                except (json.JSONDecodeError, TypeError):
                    pass

            fields.append(fs)

        return ScreenSchema(
            screen_signature=src_screen.screen_signature,
            title=src_screen.title or src_screen.program_name,
            program_name=src_screen.program_name,
            fields=fields,
        )

    def _entity_to_screen(self, entity: SourceEntity) -> ScreenSchema:
        """Converte uma entidade em um ScreenSchema basico."""
        fields: list[FieldSchema] = []
        for ef in entity.fields:
            inferred = self._infer_type_from_name(ef.name)
            datatype = ef.datatype if ef.datatype and ef.datatype != "text" else inferred
            fields.append(
                FieldSchema(
                    name=ef.name,
                    datatype=datatype,
                    required=ef.required,
                    unique=ef.unique_flag,
                    lookup=ef.lookup_table,
                    min_length=ef.min_length,
                    max_length=ef.max_length,
                )
            )

        # Adicionar campos de operacoes como indices de busca
        if entity.indexes:
            for idx in entity.indexes:
                field_name = idx.get("field", "")
                if field_name and field_name.upper() not in {f.name.upper() for f in fields}:
                    fields.append(FieldSchema(name=field_name, datatype="text"))

        return ScreenSchema(
            title=entity.name,
            program_name=entity.name,
            fields=fields,
        )

    @staticmethod
    def _infer_type_from_name(name: str) -> str:
        name_upper = name.upper()
        hints = {
            "CPF": "cpf", "CNPJ": "cnpj",
            "NOME": "person_name", "RAZAO": "company_name",
            "EMAIL": "email", "E_MAIL": "email",
            "TELEFONE": "phone", "FONE": "phone", "CELULAR": "phone",
            "CEP": "cep", "ENDERECO": "address",
            "DATA": "date", "DT_": "date",
            "VALOR": "money", "PRECO": "money", "TOTAL": "money",
            "CODIGO": "code", "COD_": "code",
            "QTD": "integer", "QTDE": "integer", "QUANTIDADE": "integer",
            "ESTOQUE": "integer", "VOLUME": "integer",
        }
        for hint, dtype in hints.items():
            if hint in name_upper:
                # RG so deve casar se for palavra exata RG ou RG_
                if hint == "RG" and name_upper != "RG" and not name_upper.startswith("RG_"):
                    continue
                # CARGO nao deve ser tratado como RG
                if "CARGO" in name_upper and hint == "RG":
                    continue
                return dtype
        return "text"

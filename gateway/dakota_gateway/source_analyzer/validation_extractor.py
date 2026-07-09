from __future__ import annotations

import re

from .entity_catalog import EntityDefinition, FieldDefinition

# Padroes de validacao comuns em sistemas legados
_VALIDATION_PATTERNS = [
    (re.compile(r"(?:IF|WHEN)\s+.+\s*(?:EMPTY|ISBLANK|LEN\s*\(\s*ALLTRIM\s*\(\s*(\w+)\s*\)\s*\)\s*=\s*0)", re.IGNORECASE), "required"),
    (re.compile(r"(?:VALID|PICTURE|PICT)\s+.+['\"]@!?[A-Z0-9#]+['\"]", re.IGNORECASE), "format"),
    (re.compile(r"(\w+)\s*(?:>=|=>)\s*(\d+)\s*\.AND\.\s*\1\s*(?:<=|=<)\s*(\d+)", re.IGNORECASE), "range"),
    (re.compile(r"LEN\s*\(\s*(?:ALLTRIM\s*\(\s*)?(\w+)\s*\)?\s*\)\s*<\s*(\d+)", re.IGNORECASE), "min_length"),
    (re.compile(r"LEN\s*\(\s*(?:ALLTRIM\s*\(\s*)?(\w+)\s*\)?\s*\)\s*>\s*(\d+)", re.IGNORECASE), "max_length"),
    (re.compile(r"(\w+)\s*\$?\s*(?:INLIST|IN)\s*\(\s*([^)]+)\)", re.IGNORECASE), "choices"),
    (re.compile(r"BETWEEN\s*\(\s*(\w+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)", re.IGNORECASE), "between"),
]

# Nomes de campo que indicam tipo especifico
_FIELD_TYPE_HINTS = {
    "CPF": "cpf",
    "CNPJ": "cnpj",
    "RG": "rg",
    "IE": "text",
    "CARGO": "text",
    "NOME": "person_name",
    "RAZAO": "company_name",
    "FANTASIA": "company_name",
    "EMAIL": "email",
    "E_MAIL": "email",
    "TELEFONE": "phone",
    "FONE": "phone",
    "CELULAR": "phone",
    "CEP": "cep",
    "ENDERECO": "address",
    "LOGRADOURO": "address",
    "CIDADE": "city",
    "BAIRRO": "neighborhood",
    "UF": "state",
    "ESTADO": "state",
    "DATA": "date",
    "DT_": "date",
    "VALOR": "money",
    "PRECO": "money",
    "TOTAL": "money",
    "SALDO": "money",
    "CODIGO": "code",
    "COD_": "code",
    "DESCRICAO": "text",
    "OBS": "text",
    "OBSERVACAO": "text",
    "SENHA": "password",
    "PASSWORD": "password",
}


class ValidationExtractor:
    """Infere validacoes e tipos a partir de nomes de campo e expressoes condicionais."""

    @staticmethod
    def enrich(entity: EntityDefinition, content: str, source_file: str = "") -> None:
        """Enriquece os campos da entidade com validacoes inferidas."""
        for field in entity.fields:
            ValidationExtractor._infer_datatype_from_name(field)
            ValidationExtractor._infer_constraints_from_name(field)

    @staticmethod
    def _infer_datatype_from_name(field: FieldDefinition) -> None:
        name_upper = field.name.upper()
        for hint, dtype in _FIELD_TYPE_HINTS.items():
            if hint in name_upper:
                # Evitar falsos positivos: CARGO contem "RG" mas nao eh RG
                if hint == "RG" and "CARGO" in name_upper:
                    continue
                if field.datatype in ("text", "", None):
                    field.datatype = dtype
                break

    @staticmethod
    def _infer_constraints_from_name(field: FieldDefinition) -> None:
        import json

        name_upper = field.name.upper()
        constraints: dict = {}

        if "CPF" in name_upper:
            constraints["format"] = "cpf"
            constraints["min_length"] = 11
            constraints["max_length"] = 11
        elif "CNPJ" in name_upper:
            constraints["format"] = "cnpj"
            constraints["min_length"] = 14
            constraints["max_length"] = 14
        elif "CEP" in name_upper:
            constraints["format"] = "cep"
            constraints["min_length"] = 8
            constraints["max_length"] = 8
        elif "EMAIL" in name_upper or "E_MAIL" in name_upper:
            constraints["format"] = "email"
        elif "TELEFONE" in name_upper or "FONE" in name_upper or "CELULAR" in name_upper:
            constraints["format"] = "phone"

        if constraints:
            field.constraints_json = json.dumps(constraints, ensure_ascii=False)
            if "min_length" in constraints:
                field.min_length = constraints["min_length"]
            if "max_length" in constraints:
                field.max_length = constraints["max_length"]

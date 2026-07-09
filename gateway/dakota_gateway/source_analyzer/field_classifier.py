"""Classifica campos por inferencia — tokenizacao, contexto, frequencia.

Substitui regex fixas por inferencia inteligente:
1. Tokenizacao do nome do campo (snake_case, CamelCase)
2. Matching por token individual + contexto da entidade
3. Dicionario de tokens conhecidos com pesos
4. Confianca calculada pela forca das evidencias
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .entity_catalog import FieldDefinition


@dataclass
class FieldClassification:
    field_name: str = ""
    original_datatype: str = ""
    inferred_datatype: str = ""
    is_required: bool = False
    has_unique_constraint: bool = False
    format_mask: str = ""
    domain_values: list[str] = field(default_factory=list)
    min_length: int = 0
    max_length: int = 0
    min_value: float = 0.0
    max_value: float = 0.0
    lookup_entity: str = ""
    semantic_category: str = ""
    confidence: float = 0.0


# ── Dicionario de tokens → categoria (com peso de confianca) ──
_TOKEN_MAP: Dict[str, List[Tuple[str, str, float]]] = {
    # token → [(datatype, category, weight)]
    "cpf":       [("text", "cpf", 0.95)],
    "cnpj":      [("text", "cnpj", 0.95)],
    "rg":        [("text", "rg", 0.90)],
    "identidade":[("text", "rg", 0.85)],
    "cep":       [("text", "cep", 0.90)],
    "postal":    [("text", "cep", 0.85)],
    "zip":       [("text", "cep", 0.80)],
    "email":     [("text", "email", 0.90)],
    "mail":      [("text", "email", 0.85)],
    "telefone":  [("text", "phone", 0.90)],
    "fone":      [("text", "phone", 0.90)],
    "tel":       [("text", "phone", 0.85)],
    "phone":     [("text", "phone", 0.85)],
    "celular":   [("text", "phone", 0.90)],
    "cel":       [("text", "phone", 0.85)],
    "nome":      [("text", "name", 0.85)],
    "name":      [("text", "name", 0.80)],
    "razao":     [("text", "name", 0.85)],
    "fantasia":  [("text", "name", 0.85)],
    "endereco":  [("text", "address", 0.85)],
    "end":       [("text", "address", 0.75)],
    "logradouro":[("text", "address", 0.80)],
    "rua":       [("text", "address", 0.75)],
    "bairro":    [("text", "neighborhood", 0.80)],
    "cidade":    [("text", "city", 0.80)],
    "city":      [("text", "city", 0.75)],
    "municipio": [("text", "city", 0.80)],
    "uf":        [("text", "state", 0.85)],
    "estado":    [("text", "state", 0.85)],
    "state":     [("text", "state", 0.80)],
    "data":      [("date", "date", 0.85)],
    "dt":        [("date", "date", 0.85)],
    "date":      [("date", "date", 0.80)],
    "dta":       [("date", "date", 0.80)],
    "dat":       [("date", "date", 0.80)],
    "hora":      [("text", "time", 0.80)],
    "codigo":    [("integer", "code", 0.85)],
    "cod":       [("integer", "code", 0.85)],
    "cd":        [("integer", "code", 0.80)],
    "id":        [("integer", "code", 0.80)],
    "chave":     [("integer", "code", 0.80)],
    "valor":     [("decimal", "money", 0.80)],
    "vlr":       [("decimal", "money", 0.80)],
    "preco":     [("decimal", "money", 0.80)],
    "total":     [("decimal", "money", 0.80)],
    "saldo":     [("decimal", "money", 0.80)],
    "quantidade":[("integer", "quantity", 0.80)],
    "qtd":       [("integer", "quantity", 0.80)],
    "qtde":      [("integer", "quantity", 0.80)],
    "estoque":   [("integer", "quantity", 0.80)],
    "peso":      [("decimal", "measure", 0.75)],
    "altura":    [("decimal", "measure", 0.70)],
    "volume":    [("decimal", "measure", 0.70)],
    "metros":    [("decimal", "measure", 0.65)],
    "kg":        [("decimal", "measure", 0.65)],
    "status":    [("text", "enum", 0.70)],
    "situacao":  [("text", "enum", 0.70)],
    "tipo":      [("text", "enum", 0.65)],
    "obs":       [("text", "text", 0.70)],
    "observacao":[("text", "text", 0.70)],
    "descricao": [("text", "text", 0.70)],
    "desc":      [("text", "text", 0.65)],
    "complemento":[("text", "text", 0.65)],
    "flag":      [("boolean", "boolean", 0.80)],
    "flg":       [("boolean", "boolean", 0.80)],
    "indicador": [("boolean", "boolean", 0.75)],
    "ind":       [("boolean", "boolean", 0.70)],
    "ativo":     [("boolean", "boolean", 0.80)],
    "inativo":   [("boolean", "boolean", 0.80)],
    "numero":    [("text", "code", 0.60)],
    "num":       [("text", "code", 0.55)],
}

# ── Contexto: entidade influencia classificacao ──
_CONTEXT_MAP: Dict[str, Dict[str, Tuple[str, str, float]]] = {
    # entidade_contem → {token → (datatype, category, confidence)}
    "cliente": {
        "documento": ("text", "cpf", 0.85),
        "doc": ("text", "cpf", 0.80),
        "numero": ("text", "cpf", 0.70),
    },
    "fornecedor": {
        "documento": ("text", "cnpj", 0.85),
        "doc": ("text", "cnpj", 0.80),
    },
    "empresa": {
        "documento": ("text", "cnpj", 0.90),
        "doc": ("text", "cnpj", 0.85),
    },
    "produto": {
        "codigo": ("integer", "product_code", 0.85),
        "cod": ("integer", "product_code", 0.85),
    },
    "pedido": {
        "numero": ("integer", "order_number", 0.85),
        "num": ("integer", "order_number", 0.80),
    },
    "nota": {
        "numero": ("integer", "invoice_number", 0.85),
        "num": ("integer", "invoice_number", 0.80),
    },
    "duplicata": {
        "numero": ("text", "duplicate_number", 0.85),
        "num": ("text", "duplicate_number", 0.80),
    },
}


class FieldClassifier:
    """Classifica campos usando inferencia por token + contexto."""

    _RE_PICTURE_NUMERIC = re.compile(r"[9Z#\*\$\.\-,]+", re.IGNORECASE)
    _RE_PICTURE_DATE = re.compile(r"@[DE]\s", re.IGNORECASE)

    @classmethod
    def classify(cls, field: FieldDefinition,
                 entity_name: str = "", all_fields: Optional[List[FieldDefinition]] = None) -> FieldClassification:
        """Classifica um campo com inferencia contextual."""
        fc = FieldClassification(
            field_name=field.name,
            original_datatype=field.datatype or "text",
            is_required=field.required,
            has_unique_constraint=field.unique_flag,
            min_length=field.min_length or 0,
            max_length=field.max_length or 0,
            lookup_entity=field.lookup_table or "",
        )

        # ── Inferencia por tokens ──
        cls._infer_by_tokens(fc, entity_name)

        # ── Constraints do PICTURE/VALID ──
        if field.constraints_json:
            cls._parse_constraints(fc, field.constraints_json)

        # ── Validacao ──
        cls._parse_validation_rules(fc, field.validation_rules)

        # ── Resolucao final ──
        fc.inferred_datatype = cls._resolve_datatype(fc)

        return fc

    @classmethod
    def classify_batch(cls, fields: List[FieldDefinition],
                       entity_name: str = "") -> List[FieldClassification]:
        """Classifica lote de campos com analise de frequencia entre campos."""
        results = []
        for field in fields:
            results.append(cls.classify(field, entity_name, fields))
        # Refina com analise de co-ocorrencia
        cls._refine_by_cooccurrence(results, entity_name)
        return results

    @classmethod
    def classify_all(cls, fields, entity_name=""):
        """Alias para classify_batch."""
        return cls.classify_batch(fields, entity_name)

    @classmethod
    def _tokenize(cls, name: str) -> List[str]:
        """Quebra nome em tokens: snake_case, CamelCase, numeros."""
        # Remove underscores e split
        tokens = re.split(r"[_\-\s]+", name.lower())
        # Split CamelCase
        result = []
        for tok in tokens:
            if not tok:
                continue
            parts = re.findall(r'[a-z]+|[0-9]+', tok)
            result.extend(p for p in parts if len(p) >= 1)
        return result

    @classmethod
    def _infer_by_tokens(cls, fc: FieldClassification, entity_name: str = "") -> None:
        """Inferencia: cada token do nome vota em uma categoria."""
        tokens = cls._tokenize(fc.field_name)
        entity_lower = entity_name.lower()
        votes: Counter = Counter()
        best = ("text", "", 0.0)

        for token in tokens:
            # 1. Contexto da entidade primeiro (mais especifico)
            for ctx_key, ctx_rules in _CONTEXT_MAP.items():
                if ctx_key in entity_lower and token in ctx_rules:
                    dt, cat, conf = ctx_rules[token]
                    votes[(dt, cat)] += conf * 1.2  # bonus de contexto
                    if conf * 1.2 > best[2]:
                        best = (dt, cat, conf * 1.2)

            # 2. Dicionario de tokens
            if token in _TOKEN_MAP:
                for dt, cat, conf in _TOKEN_MAP[token]:
                    votes[(dt, cat)] += conf
                    if conf > best[2] and conf >= fc.confidence:
                        best = (dt, cat, conf)

        # Aplica resultado
        if best[2] > 0 and best[2] >= fc.confidence:
            fc.semantic_category = best[1]
            fc.confidence = min(best[2], 1.0)
            if best[0] and fc.original_datatype in ("text", ""):
                fc.inferred_datatype = best[0]

    @classmethod
    def _refine_by_cooccurrence(cls, classifications: List[FieldClassification],
                                entity_name: str = "") -> None:
        """Refina classificacao: se varios campos tem prefixo comum, inferir relacao."""
        if len(classifications) < 2:
            return
        # Se ha campo 'cod_empresa' e entidade contem 'empresa', inferir FK
        entity_lower = entity_name.lower()
        for fc in classifications:
            tokens = cls._tokenize(fc.field_name)
            for token in tokens:
                if token in _CONTEXT_MAP:
                    fc.lookup_entity = token
                    if fc.semantic_category == "":
                        fc.semantic_category = "foreign_key"
                        fc.confidence = max(fc.confidence, 0.60)

    @classmethod
    def _parse_constraints(cls, fc: FieldClassification, constraints_json: str) -> None:
        import json
        try:
            constraints = json.loads(constraints_json)
        except (json.JSONDecodeError, TypeError):
            return
        picture = str(constraints.get("picture") or constraints.get("format") or "")
        if picture:
            fc.format_mask = picture
            if cls._RE_PICTURE_DATE.search(picture):
                fc.inferred_datatype = "date"
            elif cls._RE_PICTURE_NUMERIC.search(picture):
                fc.inferred_datatype = "decimal" if "." in picture or "," in picture else "integer"
        if "min" in constraints:
            fc.min_value = float(constraints["min"])
        if "max" in constraints:
            fc.max_value = float(constraints["max"])
        valid_values = constraints.get("valid") or constraints.get("choices") or []
        if isinstance(valid_values, list):
            fc.domain_values = [str(v) for v in valid_values]
        elif isinstance(valid_values, str):
            fc.domain_values = [v.strip() for v in valid_values.split(",") if v.strip()]

    @classmethod
    def _parse_validation_rules(cls, fc: FieldClassification, rules: list[str]) -> None:
        for rule in rules:
            r = rule.lower()
            if "not null" in r or "required" in r or "obrigat" in r:
                fc.is_required = True
            if "unique" in r or "unico" in r or "primary key" in r:
                fc.has_unique_constraint = True

    @classmethod
    def _resolve_datatype(cls, fc: FieldClassification) -> str:
        if fc.inferred_datatype:
            return fc.inferred_datatype
        type_map = {
            "integer": "integer", "int": "integer", "number": "integer",
            "numeric": "decimal", "decimal": "decimal", "float": "decimal",
            "double": "decimal", "real": "decimal", "money": "decimal",
            "date": "date", "datetime": "datetime", "timestamp": "datetime",
            "boolean": "boolean", "bool": "boolean", "logical": "boolean",
            "text": "text", "varchar": "text", "char": "text",
            "character": "text", "string": "text", "memo": "text",
            "blob": "text", "binary": "text",
        }
        return type_map.get((fc.original_datatype or "text").lower(), "text")

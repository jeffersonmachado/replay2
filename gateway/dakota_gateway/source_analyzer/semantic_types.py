"""Propriedades declarativas dos tipos semânticos conhecidos.

Ponto central de extensão: quando um novo tipo semântico identificador de
registro surgir (ex.: "rg", "matricula"), basta marcá-lo aqui — todas as
regras que dependem da propriedade (âncora de replay sintético, geração de
dados, validação) passam a valer automaticamente.
"""
from __future__ import annotations

# Tipos que identificam um registro cadastral por natureza: o valor digitado
# precisa existir no cadastro para a consulta achar o registro e o fluxo
# seguir (um valor sintético novo desvia — ex.: cai no cadastro).
IDENTIFIER_TYPES = frozenset({"cpf", "cnpj"})


def identifies_record(semantic_or_datatype: str | None) -> bool:
    """True quando o tipo semântico identifica um registro por natureza."""
    return str(semantic_or_datatype or "").strip().lower() in IDENTIFIER_TYPES

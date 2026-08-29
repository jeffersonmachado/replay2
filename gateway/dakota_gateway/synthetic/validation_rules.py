"""Tradutor de expressões VALID do fonte legado para constraints de geração.

O ``ScreenExtractor`` extrai a expressão VALID do GET (``field.valid_expr``)
e o ``screen_layout`` a captura na linha do ``@ row,col GET`` — mas ela
morria como texto, sem virar constraint executável. Este módulo traduz os
padrões simples e seguros das VALIDs xBase/Recital:

- comparações numéricas: ``valor > 0``, ``vQtd >= 1``, ``preco <= 9999.99``
  → ``min_value``/``max_value`` (``>``/``<`` aplicam passo de 1 em inteiros
  e 0,01 em decimais);
- obrigatoriedade: ``!empty(campo)`` / ``.not. empty(campo)`` → required;
- domínio fechado: ``inlist(campo, "A", "B")`` ou ``campo $ "SN"`` →
  ``choices``.

Expressões não reconhecidas (chamadas de função, subconsultas, ``.or.``
entre condições) são parcialmente aproveitadas ou ignoradas — nunca
bloqueiam a geração. A aplicação em um ``FieldSchema`` é por INTERSEÇÃO: o
VALID só endurece limites já definidos (PICTURE, magnitude do original),
nunca os relaxa — super-restringir é seguro para síntese (o valor gerado
continua válido), sub-restringir não.
"""
from __future__ import annotations

import re
from typing import Any

# Comparação numérica simples: "> 0", ">= 1", "<= 9999.99" — o operando à
# esquerda é o próprio campo (implícito na VALID do GET), então basta achar
# operador + literal. Formas invertidas ("0 < valor") não casam (o literal
# ficaria à esquerda) e são ignoradas.
_RE_CMP = re.compile(r"(>=|<=|>|<)\s*(-?\d+(?:[.,]\d+)?)")

# !empty(campo) / .not. empty(campo) → campo obrigatório.
_RE_NOT_EMPTY = re.compile(r"(?:!|\.not\.)\s*empty\s*\(", re.IGNORECASE)

# inlist(campo, "A", "B") → domínio fechado.
_RE_INLIST = re.compile(r"inlist\s*\([^,)]+,([^)]*)\)", re.IGNORECASE)

# campo $ "SN" → contenção de caractere: domínio = caracteres da string.
_RE_CONTAINS = re.compile(r"[\w.()\[\]]+\s*\$\s*\"([^\"]+)\"")

_RE_QUOTED = re.compile(r"\"([^\"]*)\"|'([^']*)'")

# Passo aplicado por comparações estritas conforme o tipo do campo.
_STEP_INT = 1
_STEP_DEC = 0.01


def _to_number(text: str) -> float | None:
    """Converte literal numérico do fonte (decimal com ponto ou vírgula)."""
    try:
        return float(text.replace(",", "."))
    except ValueError:
        return None


def parse_valid_expr(expr: str) -> dict[str, Any]:
    """Traduz uma expressão VALID em constraints de geração.

    Retorna dict com qualquer subconjunto de::

        {"min_value": float, "min_exclusive": bool,
         "max_value": float, "max_exclusive": bool,
         "required": True, "choices": [str, ...]}

    Expressão vazia ou sem padrão reconhecido retorna ``{}``.
    """
    out: dict[str, Any] = {}
    text = (expr or "").strip()
    if not text:
        return out

    for op, raw in _RE_CMP.findall(text):
        num = _to_number(raw)
        if num is None:
            continue
        if op == ">":
            if "min_value" not in out or num > out["min_value"]:
                out["min_value"] = num
                out["min_exclusive"] = True
        elif op == ">=":
            if "min_value" not in out or num > out["min_value"]:
                out["min_value"] = num
                out["min_exclusive"] = False
        elif op == "<":
            if "max_value" not in out or num < out["max_value"]:
                out["max_value"] = num
                out["max_exclusive"] = True
        elif op == "<=":
            if "max_value" not in out or num < out["max_value"]:
                out["max_value"] = num
                out["max_exclusive"] = False

    if _RE_NOT_EMPTY.search(text):
        out["required"] = True

    m = _RE_INLIST.search(text)
    if m:
        choices = [g1 or g2 for g1, g2 in _RE_QUOTED.findall(m.group(1))]
        if choices:
            out["choices"] = choices

    m = _RE_CONTAINS.search(text)
    if m and "choices" not in out:
        chars = list(dict.fromkeys(m.group(1)))
        if chars:
            out["choices"] = chars

    return out


def apply_valid_constraints(fs: Any, constraints: dict[str, Any]) -> bool:
    """Aplica constraints de VALID a um ``FieldSchema``, por interseção.

    ``min``/``max`` só endurecem limites existentes (nunca relaxam PICTURE ou
    clamp de magnitude); ``choices`` só é definido se o campo ainda não tem
    domínio. Comparações estritas ganham o passo do tipo do campo
    (``number`` → ±1, demais → ±0,01). Retorna True se algo foi aplicado.
    """
    if not constraints:
        return False
    applied = False

    if constraints.get("required") and not getattr(fs, "required", False):
        fs.required = True
        applied = True

    is_int = str(getattr(fs, "datatype", "") or "") == "number"
    step = _STEP_INT if is_int else _STEP_DEC

    if "min_value" in constraints:
        mn = constraints["min_value"] + (
            step if constraints.get("min_exclusive") else 0)
        if is_int:
            mn = int(mn)
        current = getattr(fs, "min_value", None)
        maxv = getattr(fs, "max_value", None)
        if (current is None or current < mn) \
                and (maxv is None or mn <= maxv):
            fs.min_value = mn
            applied = True

    if "max_value" in constraints:
        mx = constraints["max_value"] - (
            step if constraints.get("max_exclusive") else 0)
        if is_int:
            mx = int(mx)
        current = getattr(fs, "max_value", None)
        minv = getattr(fs, "min_value", None)
        if (current is None or current > mx) \
                and (minv is None or mx >= minv):
            fs.max_value = mx
            applied = True

    choices = constraints.get("choices")
    if choices and not getattr(fs, "choices", None):
        fs.choices = list(choices)
        applied = True

    return applied

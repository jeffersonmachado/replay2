"""Helpers compartilhados entre todos os módulos de rotas."""
from __future__ import annotations

import json
import logging
import os
from typing import Any

log = logging.getLogger("replay2")


def write_json(handler, status_code: int, payload: Any) -> None:
    """Escreve resposta JSON padronizada."""
    handler.send_response(status_code)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.end_headers()
    handler.wfile.write(json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8"))


def parse_int(value: Any, default: int = 0, *, min_value: int | None = None, max_value: int | None = None) -> int:
    """Converte valor para int de forma tolerante, com default e clamp.

    Evita 500 por parsing de query strings/parâmetros inválidos.
    """
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        parsed = default
    if min_value is not None:
        parsed = max(min_value, parsed)
    if max_value is not None:
        parsed = min(max_value, parsed)
    return parsed


def is_production() -> bool:
    """Verifica se o modo produção está ativo (DAKOTA_ENV=production)."""
    return os.environ.get("DAKOTA_ENV", "").strip().lower() == "production"


def public_error_message(exc: Exception, *, fallback: str = "erro interno") -> str:
    """Mensagem de erro segura para o cliente.

    Em produção não expõe str(exc) (pode conter paths, SQL e dados internos);
    o detalhe fica no log do servidor.
    """
    if is_production():
        log.warning("erro interno ocultado do cliente: %s", exc)
        return fallback
    return str(exc)

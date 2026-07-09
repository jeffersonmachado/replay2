"""Helpers compartilhados entre todos os módulos de rotas."""
from __future__ import annotations

import json
from typing import Any


def write_json(handler, status_code: int, payload: Any) -> None:
    """Escreve resposta JSON padronizada."""
    handler.send_response(status_code)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.end_headers()
    handler.wfile.write(json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8"))

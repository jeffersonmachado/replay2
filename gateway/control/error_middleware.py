"""Middleware de erro global para o Control Server.

Fornece um decorator que captura excecoes nao tratadas nos handlers HTTP
e retorna 500 com payload JSON padronizado, logando o traceback no console.
"""
from __future__ import annotations

import json
import traceback
from typing import Callable


CLIENT_DISCONNECT_ERRORS = (BrokenPipeError, ConnectionResetError)


def error_guard(method: Callable) -> Callable:
    """Decorator que envolve um metodo handler com try/except global.

    Em caso de excecao nao capturada:
    - Loga o traceback no stderr
    - Retorna HTTP 500 com {"error": "internal_error", "detail": "<mensagem>"}
      (em producao, "detail" e generico para nao vazar informacao interna)
    """

    def wrapper(self, *args, **kwargs):
        try:
            return method(self, *args, **kwargs)
        except CLIENT_DISCONNECT_ERRORS:
            # Cliente fechou a conexao antes da resposta terminar.
            return None
        except Exception as exc:
            traceback.print_exc()
            # Em produção não vaza str(exc) ao cliente (pode conter paths,
            # SQL e dados internos); o detalhe fica no log do servidor.
            from control.routes.route_helpers import is_production
            detail = "erro interno" if is_production() else str(exc)
            try:
                self.send_response(500)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                payload = json.dumps(
                    {"error": "internal_error", "detail": detail},
                    ensure_ascii=False,
                )
                self.wfile.write(payload.encode("utf-8"))
            except CLIENT_DISCONNECT_ERRORS:
                return None
            except Exception:
                pass  # conexao pode ja estar fechada

    return wrapper


def safe_write_json(handler, status_code: int, payload: dict | list) -> None:
    """Escreve resposta JSON com tratamento de erro silencioso.

    Usada internamente pelo error_guard, mas tambem disponivel para
    uso direto em handlers que precisam de garantia extra.
    """
    try:
        handler.send_response(status_code)
        handler.send_header("Content-Type", "application/json; charset=utf-8")
        handler.end_headers()
        handler.wfile.write(json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8"))
    except CLIENT_DISCONNECT_ERRORS:
        return None
    except Exception:
        pass

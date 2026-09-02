"""Testes de regressão: rotas de engenharia devem liberar a conexão do pool
mesmo quando a query falha (FASE 10 — integração).

Antes da correção, os quatro handlers de ``engineering_route_support``
chamavam ``db_release`` dentro do ``try``: qualquer exceção na query
prendia a conexão no pool para sempre.
"""

from __future__ import annotations

import io

import pytest

from control.engineering_route_support import handle_engineering_api_get_route


class _FakeRequest:
    """Request mínimo para os handlers de API (resposta JSON descartada)."""

    def __init__(self) -> None:
        self.wfile = io.BytesIO()
        self.status: int | None = None

    def send_response(self, status: int) -> None:
        self.status = status

    def send_header(self, *_args) -> None:  # noqa: AN002 - assinatura http
        return None

    def end_headers(self) -> None:
        return None


class _ConExplosiva:
    """Conexão cuja query sempre falha (simula banco corrompido/lock)."""

    row_factory = None

    def execute(self, *_args, **_kwargs):  # noqa: AN002, AN003
        raise RuntimeError("banco indisponível")


class _PoolEspião:
    def __init__(self) -> None:
        self.acquired = 0
        self.released = 0

    def acquire(self):
        self.acquired += 1
        return _ConExplosiva()

    def release(self, _con) -> None:
        self.released += 1


_ENDPOINTS = [
    "/api/business/rules",
    "/api/business/gaps",
    "/api/journeys/report",
    "/api/catalog/entities",
]


@pytest.mark.parametrize("endpoint", _ENDPOINTS)
def test_handler_libera_conexao_quando_query_falha(endpoint: str) -> None:
    """Exceção na query não pode prender a conexão do pool."""
    pool = _PoolEspião()
    req = _FakeRequest()
    from urllib.parse import urlparse

    handled = handle_engineering_api_get_route(
        req, urlparse(endpoint), db_acquire=pool.acquire, db_release=pool.release
    )
    assert handled is True
    assert pool.acquired == 1
    assert pool.released == 1, f"{endpoint} prendeu a conexão do pool na exceção"
    assert req.status == 200

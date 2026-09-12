"""Memoização do render da tela esperada (replay_compare).

Medido na captura 13 (AIX): ``expected_screen_text_from_event`` renderiza a
MESMA tela (``screen_raw_b64`` imutável por evento) até 3× por compare —
os fallbacks volátil/echo/substituição chamam cada um o render — mais 1×
no predicado de fast-exit e 1× no registro da falha. Dezenas de renders
idênticos por run no caminho quente do checkpoint.
"""
from __future__ import annotations

import base64
from unittest import mock

from dakota_gateway import replay_compare


class _CountingScreenState:
    renders = 0

    def __init__(self, rows=25, cols=80, encoding="utf-8"):
        _CountingScreenState.renders += 1

    def feed_bytes(self, raw):
        return None

    def text(self):
        return "TELA ESPERADA"


def _event(raw: bytes) -> dict:
    return {"screen_raw_b64": base64.b64encode(raw).decode("ascii")}


def test_render_da_tela_esperada_e_memoizado_por_conteudo():
    """Mesmo raw_b64 + mesma geometria/encoding => um único render."""
    with mock.patch.object(replay_compare, "TerminalScreenState", _CountingScreenState):
        _CountingScreenState.renders = 0
        ev = _event(b"\x1b[2Jconteudo memo")
        t1 = replay_compare.expected_screen_text_from_event(ev, None)
        t2 = replay_compare.expected_screen_text_from_event(ev, None)
        assert t1 == t2 == "TELA ESPERADA"
        assert _CountingScreenState.renders == 1, (
            f"render repetido {_CountingScreenState.renders}x para o mesmo evento"
        )


def test_conteudo_diferente_renderiza_de_novo():
    """A chave é o conteúdo: raw diferente invalida."""
    with mock.patch.object(replay_compare, "TerminalScreenState", _CountingScreenState):
        _CountingScreenState.renders = 0
        replay_compare.expected_screen_text_from_event(_event(b"raw-a"), None)
        replay_compare.expected_screen_text_from_event(_event(b"raw-b"), None)
        assert _CountingScreenState.renders == 2


def test_fallback_para_screen_sample_quando_render_falha():
    """raw inválido (render quebra) cai no screen_sample, como antes."""

    class _BrokenScreenState:
        def __init__(self, **kw):
            raise ValueError("quebrado")

    with mock.patch.object(replay_compare, "TerminalScreenState", _BrokenScreenState):
        ev = _event(b"raw-quebrado")
        ev["screen_sample"] = "AMOSTRA"
        assert replay_compare.expected_screen_text_from_event(ev, None) == "AMOSTRA"


def test_sem_raw_usa_screen_sample_sem_render():
    with mock.patch.object(replay_compare, "TerminalScreenState", _CountingScreenState):
        _CountingScreenState.renders = 0
        assert replay_compare.expected_screen_text_from_event(
            {"screen_sample": "AMOSTRA"}, None
        ) == "AMOSTRA"
        assert _CountingScreenState.renders == 0

"""Testes do estado serializável da TerminalEngine (dívida X6 — cache em disco).

Contexto: o cache de estado do replay (seek O(1) em sessões enormes) exige
congelar o estado completo da engine num ponto de corte e retomar depois com
resultado idêntico ao processamento contínuo.

Contrato coberto:
  1. state_dict()/load_state() fazem round-trip fiel — texto, atributos,
     cursor, scroll region, charsets, contadores e warnings;
  2. sequência de escape partida entre feeds (CSI incompleto) é preservada;
  3. caractere multibyte UTF-8 partido entre feeds é preservado (decoder);
  4. is_state_clean() identifica pontos seguros de corte.

Run:
  PYTHONPATH=gateway python3 -m pytest tests/test_terminal_engine_state_unit.py -v
"""
from __future__ import annotations

from dakota_terminal import TerminalEngine


def _engine(rows: int = 24, cols: int = 80) -> TerminalEngine:
    return TerminalEngine(rows=rows, cols=cols, term="xterm", encoding="utf-8", session_id="s1")


def test_state_roundtrip_continua_identico_ao_fluxo_continuo():
    chunks = [
        b"primeira linha\r\n",
        b"\x1b[1;31mvermelho bold\x1b[1m\r\n",
        b"\x1b[2J\x1b[H",  # clear + home
        b"\x1b[5;10Hposicionado",
        b"\x1b(0q\x1b(B\r\n",  # charset DEC special
        b"\x1b[3;20r",  # scroll region
        b"mais texto com scroll\r\n" * 30,
    ]
    continuo = _engine()
    for ch in chunks[:3]:
        continuo.feed_bytes(ch, seq_global=1)
    for ch in chunks[3:]:
        continuo.feed_bytes(ch, seq_global=2)

    cortado = _engine()
    for ch in chunks[:3]:
        cortado.feed_bytes(ch, seq_global=1)
    state = cortado.state_dict()
    restaurado = _engine()
    restaurado.load_state(state)
    for ch in chunks[3:]:
        cortado.feed_bytes(ch, seq_global=2)
        restaurado.feed_bytes(ch, seq_global=2)

    assert restaurado.snapshot() == cortado.snapshot()
    assert restaurado.snapshot() == continuo.snapshot()


def test_state_preserva_csi_partido_entre_feeds():
    continuo = _engine()
    continuo.feed_bytes(b"\x1b[31;1", seq_global=1)  # CSI incompleto
    continuo.feed_bytes(b"5Hfim", seq_global=2)

    cortado = _engine()
    cortado.feed_bytes(b"\x1b[31;1", seq_global=1)
    assert not cortado.is_state_clean()  # scanner no meio de um CSI
    restaurado = _engine()
    restaurado.load_state(cortado.state_dict())
    restaurado.feed_bytes(b"5Hfim", seq_global=2)

    assert restaurado.snapshot() == continuo.snapshot()


def test_state_preserva_multibyte_utf8_partido():
    continuo = _engine()
    continuo.feed_bytes("caixa ┌".encode()[:-2], seq_global=1)  # corta o ┌ no meio
    continuo.feed_bytes("┌".encode()[-2:] + b" ok", seq_global=2)

    cortado = _engine()
    cortado.feed_bytes("caixa ┌".encode()[:-2], seq_global=1)
    restaurado = _engine()
    restaurado.load_state(cortado.state_dict())
    restaurado.feed_bytes("┌".encode()[-2:] + b" ok", seq_global=2)

    assert restaurado.snapshot() == continuo.snapshot()
    assert restaurado.text() == continuo.text()


def test_state_preserva_warnings_do_decoder():
    eng = _engine()
    eng.feed_bytes(b"\xc3", seq_global=1)  # prefixo UTF-8 incompleto
    eng.feed_bytes(b"(invalido)", seq_global=2)  # gera warning de malformed
    assert eng.decoder.warnings, "warning esperado antes do round-trip"
    restaurado = _engine()
    restaurado.load_state(eng.state_dict())
    assert restaurado.decoder.warnings == eng.decoder.warnings


def test_is_state_clean_em_ponto_de_corte_normal():
    eng = _engine()
    eng.feed_bytes(b"texto comum\r\n\x1b[2J", seq_global=1)
    assert eng.is_state_clean()


def test_load_state_rejeita_versao_desconhecida():
    eng = _engine()
    eng.feed_bytes(b"x", seq_global=1)
    state = eng.state_dict()
    state["state_version"] = 999
    restaurado = _engine()
    try:
        restaurado.load_state(state)
    except ValueError:
        return
    raise AssertionError("load_state deveria rejeitar state_version desconhecida")

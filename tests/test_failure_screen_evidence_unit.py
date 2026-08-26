"""Testes unitários da evidência de tela gravada nas falhas de replay.

Cobre os helpers de replay_compare (texto esperado do evento da trilha e
texto observado da sessão) e a inclusão das telas no evidence da falha
determinística — é o que permite à UI mostrar O QUE divergiu.
"""
from __future__ import annotations

import base64

from dakota_gateway.replay_compare import (
    expected_screen_text_from_event,
    observed_screen_text_from_session,
)
from dakota_gateway.replay_control.deterministic import _deterministic_failure


class _ScreenFake:
    def __init__(self, text_value: str):
        self._text = text_value

    def text(self) -> str:
        return self._text


class _SessionFake:
    def __init__(self, text_value: str):
        self.screen_state = _ScreenFake(text_value)


class _ConfigFake:
    rows = 25
    cols = 80
    encoding = "utf-8"


def test_observed_screen_text_from_session_retorna_texto_da_tela():
    session = _SessionFake("PEDIDO 123\nfrete 10")
    assert observed_screen_text_from_session(session) == "PEDIDO 123\nfrete 10"


def test_observed_screen_text_from_session_sem_screen_state_retorna_vazio():
    assert observed_screen_text_from_session(object()) == ""
    assert observed_screen_text_from_session(None) == ""


def test_observed_screen_text_from_session_erro_na_leitura_retorna_vazio():
    class _Quebrado:
        def text(self):
            raise RuntimeError("boom")

    class _Sessao:
        screen_state = _Quebrado()

    assert observed_screen_text_from_session(_Sessao()) == ""


def test_observed_screen_text_trunca_no_teto():
    session = _SessionFake("x" * 500)
    assert observed_screen_text_from_session(session, max_chars=100) == "x" * 100


def test_expected_screen_text_from_event_renderiza_screen_raw_b64():
    raw = b"MENU PRINCIPAL\r\n1. Pedidos"
    ev = {"screen_raw_b64": base64.b64encode(raw).decode("ascii")}
    text = expected_screen_text_from_event(ev, _ConfigFake())
    linhas = [ln.rstrip() for ln in text.split("\n") if ln.strip()]
    assert linhas[:2] == ["MENU PRINCIPAL", "1. Pedidos"]


def test_expected_screen_text_from_event_sem_raw_cai_no_screen_sample():
    ev = {"screen_sample": "Cliente: 00109829069"}
    assert expected_screen_text_from_event(ev, _ConfigFake()) == "Cliente: 00109829069"


def test_expected_screen_text_from_event_b64_invalido_cai_no_screen_sample():
    ev = {"screen_raw_b64": "@@@invalido@@@", "screen_sample": "fallback"}
    # b64decode tolerante pode não falhar, mas nunca deve estourar exceção
    text = expected_screen_text_from_event(ev, _ConfigFake())
    assert isinstance(text, str)


def test_expected_screen_text_from_event_evento_vazio_retorna_vazio():
    assert expected_screen_text_from_event({}, None) == ""


def _failure_kwargs() -> dict:
    return {
        "sid": "sess-1",
        "seq_global": 519,
        "seq_session": 12,
        "expected_sig": "sig-esperada",
        "observed_sig": "sig-observada",
        "params": {},
        "checkpoint_timeout_ms": 5000,
        "checkpoint_quiet_ms": 250,
        "mode_label": "strict-global-deterministic",
        "concurrent_mode": False,
    }


def test_deterministic_failure_inclui_telas_no_evidence():
    failure = _deterministic_failure(
        **{**_failure_kwargs(), "expected_screen": "tela esperada", "observed_screen": "tela observada"}
    )
    assert failure["evidence"]["expected_screen"] == "tela esperada"
    assert failure["evidence"]["observed_screen"] == "tela observada"


def test_deterministic_failure_sem_telas_omite_as_chaves():
    failure = _deterministic_failure(**_failure_kwargs())
    assert "expected_screen" not in failure["evidence"]
    assert "observed_screen" not in failure["evidence"]

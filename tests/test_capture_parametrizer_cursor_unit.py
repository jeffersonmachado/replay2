"""Testes unitários da posição de cursor por input no CaptureParametrizer.

A posição do campo digitado vem do fluxo bruto OUT (bytes do host com
posicionamentos ESC[r;cH), não do screen_raw da tela estável — a aplicação
estaciona o cursor no canto após o redraw (captura 13).
"""
from __future__ import annotations

import base64
import json

from dakota_gateway.synthetic.capture_parametrizer import CaptureParametrizer


def _b64(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def _bytes_out(seq: int, text: str) -> dict:
    return {
        "v": "v2", "type": "bytes", "session_id": "s1", "seq_global": seq,
        "dir": "out", "data_b64": _b64(text),
        "rows": 24, "cols": 80, "encoding": "utf-8",
    }


def _det(seq: int, key: str, sig: str = "L=24;W=80;LBL=Pedido") -> dict:
    return {
        "v": "v2", "type": "deterministic_input", "session_id": "s1",
        "seq_global": seq, "screen_sig": sig, "screen_sample": "Pedido",
        "key_b64": _b64(key), "key_kind": "printable" if len(key) == 1 else "enter",
        "rows": 24, "cols": 80, "encoding": "utf-8",
    }


def _write_trail(tmp_path, events: list[dict]):
    path = tmp_path / "audit-test.jsonl"
    with open(path, "w", encoding="utf-8") as f:
        for ev in events:
            f.write(json.dumps(ev) + "\n")
    return str(path)


def test_cursor_por_input_via_fluxo_out(tmp_path):
    """Cursor do fluxo OUT posiciona cada token fundido."""
    trail = _write_trail(tmp_path, [
        _bytes_out(1, "\x1b[5;13H"),   # posiciona no campo (row 4, col 12)
        _det(2, "a"),
        _bytes_out(3, "a"),            # eco: cursor avança para (4,13)
        _det(4, "b"),
        _bytes_out(5, "b"),
        _det(6, "\r"),
    ])
    tmpl = CaptureParametrizer().analyze_capture(trail)
    ctx = tmpl.screen_contexts[0]
    assert ctx["inputs"] == ["ab", "{KEY:ENTER}"]
    assert ctx["input_positions"][0] == (4, 12)


def test_salto_de_cursor_quebra_campo(tmp_path):
    """Teleporte do cursor (auto-avanço sem ENTER) separa dois campos."""
    trail = _write_trail(tmp_path, [
        _bytes_out(1, "\x1b[5;13H"),
        _det(2, "a"),
        _bytes_out(3, "a"),
        _det(4, "b"),
        _bytes_out(5, "b"),
        _bytes_out(6, "\x1b[8;10H"),   # auto-avanço: outro campo
        _det(7, "c"),
        _bytes_out(8, "c"),
        _det(9, "\r"),
    ])
    tmpl = CaptureParametrizer().analyze_capture(trail)
    ctx = tmpl.screen_contexts[0]
    assert ctx["inputs"] == ["ab", "c", "{KEY:ENTER}"]
    assert ctx["input_positions"][:2] == [(4, 12), (7, 9)]


def test_salto_pequeno_de_mascara_nao_quebra(tmp_path):
    """Máscara de edição (@R 999.999.999-99) avança 2 colunas sem quebrar."""
    trail = _write_trail(tmp_path, [
        _bytes_out(1, "\x1b[5;13H"),
        _det(2, "1"),
        _bytes_out(3, "1"),
        _det(4, "2"),
        _bytes_out(5, "2."),           # máscara insere '.' e pula 2 colunas
        _det(6, "3"),
        _bytes_out(7, "3"),
        _det(8, "\r"),
    ])
    tmpl = CaptureParametrizer().analyze_capture(trail)
    ctx = tmpl.screen_contexts[0]
    assert ctx["inputs"] == ["123", "{KEY:ENTER}"]
    assert ctx["input_positions"][0] == (4, 12)


def test_trilha_sem_fluxo_out_tem_posicoes_none(tmp_path):
    """Trilha só com deterministic_input (sem bytes OUT): posições None."""
    trail = _write_trail(tmp_path, [
        _det(1, "a"),
        _det(2, "b"),
        _det(3, "\r"),
    ])
    tmpl = CaptureParametrizer().analyze_capture(trail)
    ctx = tmpl.screen_contexts[0]
    assert ctx["inputs"] == ["ab", "{KEY:ENTER}"]
    assert ctx["input_positions"] == [None, None]

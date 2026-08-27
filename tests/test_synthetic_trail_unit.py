"""Testes unitários da trilha sintética (replay sintético em 1 clique).

Cobre:
- remoção do banner pré-sessão (registro de terminal);
- substituição dígito a dígito (campos com máscara, ex.: CPF);
- substituição simples de input;
- renumeração de seq_global e re-assinatura da cadeia (verify OK).
"""
from __future__ import annotations

import base64
import json

from pathlib import Path

import pytest

from dakota_gateway.synthetic.synthetic_trail import build_synthetic_trail, det_key
from dakota_gateway.verifier import verify_log

HMAC_KEY = b"test-hmac-key"


def _ev(seq, type_, **kw):
    ev = {
        "v": "v1",
        "seq_global": seq,
        "ts_ms": 1000 + seq,
        "type": type_,
        "actor": "tester",
        "session_id": "sess-1",
        "seq_session": seq,
    }
    ev.update(kw)
    return ev


def _det(seq, key, screen_sig="L=7;W=36;LBL=Digite a sua opcao"):
    return _ev(
        seq,
        "deterministic_input",
        key_b64=base64.b64encode(key.encode("utf-8")).decode("ascii"),
        key_text=key,
        screen_sig=screen_sig,
    )


def _write_trail(path, events):
    with open(path, "w", encoding="utf-8") as f:
        for ev in events:
            f.write(json.dumps(ev, ensure_ascii=False) + "\n")


def _read_trail(path):
    return [json.loads(l) for l in Path(str(path)).read_text(encoding="utf-8").splitlines() if l.strip()]


def _fixture_events():
    return [
        _ev(1, "session_start", logname="ferblo"),
        # banner pré-sessão (registro de terminal): telas vazias
        _det(2, "P", screen_sig=""),
        _det(3, "C", screen_sig="L=0;W=0"),
        # tela real: menu
        _det(4, "3"),
        _det(5, "\r"),
        # CPF digitado dígito a dígito
        *[_det(6 + i, d) for i, d in enumerate("00109829069")],
        _det(17, "\r"),
        # frete (input simples)
        _det(18, "1"),
        _det(19, "\r"),
        _ev(20, "session_end"),
    ]


def test_drop_banner_remove_eventos_pre_sessao(tmp_path):
    src = tmp_path / "audit-test.jsonl"
    _write_trail(src, _fixture_events())
    out = tmp_path / "out"
    result = build_synthetic_trail(src, [], out, hmac_key=HMAC_KEY)

    events = _read_trail(result["out"])
    assert result["dropped_banner"] == 2
    # session_start preservado, banner removido
    assert events[0]["type"] == "session_start"
    keys = [det_key(ev) for ev in events if ev["type"] == "deterministic_input"]
    assert "P" not in keys and "C" not in keys
    assert keys[0] == "3"


def test_sem_banner_nao_remove_nada(tmp_path):
    events = [e for e in _fixture_events() if e["seq_global"] not in (2, 3)]
    src = tmp_path / "audit-test.jsonl"
    _write_trail(src, events)
    result = build_synthetic_trail(src, [], tmp_path / "out", hmac_key=HMAC_KEY)
    assert result["dropped_banner"] == 0
    assert result["events"] == len(events)


def test_substituicao_digitos(tmp_path):
    src = tmp_path / "audit-test.jsonl"
    _write_trail(src, _fixture_events())
    result = build_synthetic_trail(
        src,
        [("00109829069", "18503257408")],
        tmp_path / "out",
        hmac_key=HMAC_KEY,
    )
    events = _read_trail(result["out"])
    keys = [det_key(ev) for ev in events if ev["type"] == "deterministic_input"]
    digits = "".join(k for k in keys if len(k) == 1 and k.isdigit())
    assert "18503257408" in digits
    assert "00109829069" not in digits
    assert result["applied"] and not result["warnings"]


def test_substituicao_simples(tmp_path):
    src = tmp_path / "audit-test.jsonl"
    _write_trail(src, _fixture_events())
    result = build_synthetic_trail(
        src,
        [("1", "104529,05")],
        tmp_path / "out",
        hmac_key=HMAC_KEY,
    )
    events = _read_trail(result["out"])
    keys = [det_key(ev) for ev in events if ev["type"] == "deterministic_input"]
    assert "104529,05" in keys
    assert result["applied"]


def test_substituicao_ausente_gera_warning(tmp_path):
    src = tmp_path / "audit-test.jsonl"
    _write_trail(src, _fixture_events())
    result = build_synthetic_trail(
        src,
        [("99999999999", "18503257408"), ("ZZZ", "abc")],
        tmp_path / "out",
        hmac_key=HMAC_KEY,
    )
    assert len(result["warnings"]) == 2
    assert not result["applied"]


def _fixture_grade_events():
    """Campos de grade digitados tecla a tecla (captura 13: modelo/valor)."""
    return [
        _ev(1, "session_start", logname="ferblo"),
        _det(2, "3"),
        _det(3, "\r"),
        # modelo: g2511 (alfanumérico, 5 teclas)
        *[_det(4 + i, c) for i, c in enumerate("g2511")],
        _det(9, "\t"),
        # valor: 229,9 (decimal com vírgula, 5 teclas)
        *[_det(10 + i, c) for i, c in enumerate("229,9")],
        _det(15, "\r"),
        _ev(16, "session_end"),
    ]


def test_substituicao_teclas_alfanumerica_mesmo_tamanho(tmp_path):
    """'g2511'→'q0983': 1 caractere por evento, como os dígitos de CPF."""
    src = tmp_path / "audit-test.jsonl"
    _write_trail(src, _fixture_grade_events())
    result = build_synthetic_trail(
        src, [("g2511", "q0983")], tmp_path / "out", hmac_key=HMAC_KEY)
    events = _read_trail(result["out"])
    keys = [det_key(ev) for ev in events if ev["type"] == "deterministic_input"]
    typed = "".join(k for k in keys if len(k) == 1)
    assert "q0983" in typed and "g2511" not in typed
    assert result["applied"] and not result["warnings"]


def test_substituicao_teclas_valor_mais_longo(tmp_path):
    """'229,9'→'345597,51': o evento final do run carrega o restante —
    input multi-caractere é válido no replay."""
    src = tmp_path / "audit-test.jsonl"
    _write_trail(src, _fixture_grade_events())
    result = build_synthetic_trail(
        src, [("229,9", "345597,51")], tmp_path / "out", hmac_key=HMAC_KEY)
    events = _read_trail(result["out"])
    keys = [det_key(ev) for ev in events if ev["type"] == "deterministic_input"]
    run = keys[keys.index("\t") + 1:keys.index("\r", 2)]
    assert run == ["3", "4", "5", "5", "97,51"]
    assert "".join(run) == "345597,51"
    assert result["applied"] and not result["warnings"]


def test_substituicao_teclas_valor_mais_curto(tmp_path):
    """'g2511'→'ab': eventos excedentes ficam vazios (nada é enviado)."""
    src = tmp_path / "audit-test.jsonl"
    _write_trail(src, _fixture_grade_events())
    result = build_synthetic_trail(
        src, [("g2511", "ab")], tmp_path / "out", hmac_key=HMAC_KEY)
    events = _read_trail(result["out"])
    keys = [det_key(ev) for ev in events if ev["type"] == "deterministic_input"]
    assert keys[2:7] == ["a", "b", "", "", ""]
    assert result["applied"] and not result["warnings"]
    # cadeia íntegra mesmo com eventos esvaziados
    verify_log(str(tmp_path / "out"), HMAC_KEY)


def test_tecla_de_controle_quebra_o_run(tmp_path):
    """ENTER no meio impede casamento espúrio através de campos."""
    events = [
        _ev(1, "session_start", logname="ferblo"),
        _det(2, "g"), _det(3, "2"), _det(4, "\r"), _det(5, "5"), _det(6, "1"), _det(7, "1"),
        _ev(8, "session_end"),
    ]
    src = tmp_path / "audit-test.jsonl"
    _write_trail(src, events)
    result = build_synthetic_trail(
        src, [("g2511", "q0983")], tmp_path / "out", hmac_key=HMAC_KEY)
    assert not result["applied"] and len(result["warnings"]) == 1


def test_seq_renumerado_sem_gaps(tmp_path):
    src = tmp_path / "audit-test.jsonl"
    _write_trail(src, _fixture_events())
    result = build_synthetic_trail(src, [], tmp_path / "out", hmac_key=HMAC_KEY)
    events = _read_trail(result["out"])
    assert [ev["seq_global"] for ev in events] == list(range(1, len(events) + 1))


def test_cadeia_reassinada_passa_no_verify(tmp_path):
    src = tmp_path / "audit-test.jsonl"
    _write_trail(src, _fixture_events())
    out = tmp_path / "out"
    build_synthetic_trail(
        src,
        [("00109829069", "18503257408"), ("1", "104529,05")],
        out,
        hmac_key=HMAC_KEY,
    )
    # verify_log levanta VerificationError se a cadeia/HMAC não fechar
    verify_log(str(out), HMAC_KEY)


def test_det_key_decodifica_b64():
    ev = {"key_b64": base64.b64encode("x".encode()).decode()}
    assert det_key(ev) == "x"
    assert det_key({"key_text": "y"}) == "y"
    assert det_key({}) == ""

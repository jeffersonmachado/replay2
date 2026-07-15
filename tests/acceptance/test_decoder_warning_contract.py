from __future__ import annotations

from dakota_terminal.decoder import TerminalDecoder


def test_decoder_records_structured_incomplete_multibyte_warning_on_finalize():
    decoder = TerminalDecoder(encoding="utf-8", session_id="s1")
    assert decoder.decode(b"\xc3", seq_global=123, direction="out") == ""
    text = decoder.finalize(seq_global=124, direction="out")
    assert "\ufffd" in text
    warning = decoder.warnings[-1]
    assert warning["type"] == "incomplete_multibyte_sequence"
    assert warning["session_id"] == "s1"
    assert warning["direction"] == "out"
    assert warning["encoding"] == "utf-8"
    assert warning["bytes_hex"] == "c3"
    assert warning["bytes_b64"] == "ww=="
    assert warning["action"] == "replacement_character"

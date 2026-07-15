from __future__ import annotations

import dakota_terminal
from dakota_terminal import TerminalEngine, snapshot_from_engine
from dakota_terminal.decoder import TerminalDecoder


def test_public_snapshot_api_exports_real_contract_functions_not_legacy_aliases():
    engine = TerminalEngine(rows=2, cols=3)
    engine.feed_bytes(b"A")
    snapshot = snapshot_from_engine(engine)

    canonical_payload = dakota_terminal.encode_canonical_snapshot(snapshot)
    render_payload = dakota_terminal.encode_render_snapshot(snapshot)

    assert dakota_terminal.encode_canonical_snapshot is not dakota_terminal.encode_snapshot
    assert dakota_terminal.decode_canonical_snapshot is not dakota_terminal.decode_snapshot
    assert dakota_terminal.encode_render_snapshot is not dakota_terminal.encode_snapshot_compact
    assert dakota_terminal.decode_render_snapshot is not dakota_terminal.decode_snapshot_compact
    assert isinstance(dakota_terminal.decode_canonical_snapshot(canonical_payload), dakota_terminal.CanonicalSnapshot)
    render = dakota_terminal.decode_render_snapshot(render_payload)
    assert isinstance(render, dakota_terminal.RenderSnapshot)
    assert render.engine_version == snapshot["engine_version"]


def test_terminal_engine_uses_structured_decoder_for_incomplete_utf8_finalize():
    engine = TerminalEngine(rows=1, cols=2, encoding="utf-8")
    assert isinstance(engine.decoder, TerminalDecoder)

    engine.feed_bytes(bytes([0xC3]))
    engine.finalize()

    text = "".join(cell["ch"] for cell in snapshot_from_engine(engine)["cells"])
    assert "\ufffd" in text
    warning = engine.decoder.warnings[-1]
    assert warning["type"] == "incomplete_multibyte_sequence"
    assert warning["direction"] == "out"
    assert warning["encoding"] == "utf-8"
    assert warning["bytes_hex"] == "c3"
    assert warning["bytes_b64"] == "ww=="

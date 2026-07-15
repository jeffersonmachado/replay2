from __future__ import annotations

import pytest

from dakota_terminal import TerminalEngine, snapshot_from_engine
from dakota_terminal.snapshot import (
    CanonicalSnapshot,
    RenderSnapshot,
    decode_canonical_snapshot,
    decode_render_snapshot,
    encode_canonical_snapshot,
    encode_render_snapshot,
)


def test_canonical_and_render_snapshots_are_distinct_real_types():
    assert CanonicalSnapshot is not RenderSnapshot
    engine = TerminalEngine(rows=2, cols=3)
    engine.feed_bytes(b"A")
    snap = snapshot_from_engine(engine)
    canonical = decode_canonical_snapshot(encode_canonical_snapshot(snap))
    render = decode_render_snapshot(encode_render_snapshot(snap))
    assert isinstance(canonical, CanonicalSnapshot)
    assert isinstance(render, RenderSnapshot)
    assert canonical.rows == render.rows == 2
    assert canonical.cols == render.cols == 3
    assert canonical.parser_state is not None
    assert hasattr(canonical, "decoder_state")
    assert not hasattr(render, "decoder_state")
    assert render.semantic_sig.startswith("sha256:")


def test_render_decode_does_not_claim_to_restore_canonical_state():
    engine = TerminalEngine(rows=1, cols=1)
    snap = snapshot_from_engine(engine)
    render = decode_render_snapshot(encode_render_snapshot(snap))
    with pytest.raises(TypeError):
        encode_canonical_snapshot(render)  # type: ignore[arg-type]

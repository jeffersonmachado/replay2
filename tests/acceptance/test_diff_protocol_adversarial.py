from __future__ import annotations

import copy

import pytest

from dakota_terminal import TerminalEngine, snapshot_from_engine
from dakota_terminal.diffs import apply_diff, create_diff, validate_diff
from dakota_terminal.signatures import semantic_sig, text_sig, visual_sig


def make_snap(text: bytes = b"", rows: int = 2, cols: int = 2) -> dict:
    engine = TerminalEngine(rows=rows, cols=cols)
    if text:
        engine.feed_bytes(text)
    snap = snapshot_from_engine(engine)
    snap.setdefault("seq_global", 0)
    return snap


def changed(base: dict, ch: str = "Z") -> dict:
    curr = copy.deepcopy(base)
    curr["cells"][0]["ch"] = ch
    curr["text_sig"] = text_sig(curr)
    curr["visual_sig"] = visual_sig(curr)
    curr["semantic_sig"] = semantic_sig(curr)
    return curr


def assert_rejected(snapshot: dict, diff: dict):
    assert validate_diff(snapshot, diff) is False
    with pytest.raises(ValueError):
        apply_diff(snapshot, diff)


def test_round_trip_recalculates_three_signatures_and_preserves_contract():
    base = make_snap()
    curr = changed(base, "A")
    diff = create_diff(base, curr, base_seq=0, seq=1, ts_ms=1234)
    result = apply_diff(base, diff)
    assert result["cells"] == curr["cells"]
    assert result["text_sig"] == curr["text_sig"]
    assert result["visual_sig"] == curr["visual_sig"]
    assert result["semantic_sig"] == curr["semantic_sig"]
    assert result["seq_global"] == 1


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d.update(base_seq_global=99),
        lambda d: d.update(base_text_sig="sha256:" + "0" * 64),
        lambda d: d.update(base_visual_sig="sha256:" + "1" * 64),
        lambda d: d.update(base_semantic_sig="sha256:" + "2" * 64),
        lambda d: d.update(base_rows=99),
        lambda d: d.update(base_cols=99),
        lambda d: d.update(text_sig="sha256:" + "3" * 64),
        lambda d: d.update(seq_global="1"),
        lambda d: d.update(seq_global=1.5),
        lambda d: d.update(seq_global=0),
        lambda d: d["changes"][0].update(row=-1),
        lambda d: d["changes"][0].update(row=999),
        lambda d: d.update(changes=[d["changes"][0], dict(d["changes"][0])]),
        lambda d: d.update(rows=1000, cols=1000, geometry_changed=True, resize={"from_rows": 2, "from_cols": 2, "to_rows": 1000, "to_cols": 1000}),
        lambda d: d.update(cursor={"row": -1, "col": 0}),
        lambda d: d.update(version="2"),
    ],
)
def test_adversarial_diffs_are_rejected_before_apply(mutate):
    base = make_snap()
    curr = changed(base, "A")
    diff = create_diff(base, curr, base_seq=0, seq=1)
    mutate(diff)
    assert_rejected(base, diff)


def test_reapplying_same_diff_is_rejected_by_sequence_identity():
    base = make_snap()
    curr = changed(base, "A")
    diff = create_diff(base, curr, base_seq=0, seq=1)
    applied = apply_diff(base, diff)
    assert_rejected(applied, diff)


def test_empty_idempotent_diff_reapplication_is_rejected():
    base = make_snap()
    diff = create_diff(base, base, base_seq=0, seq=1)
    applied = apply_diff(base, diff)
    assert_rejected(applied, diff)

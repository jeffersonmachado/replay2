"""Testes de validacao de diff: round-trip, ordem, rejeicao."""
from __future__ import annotations

import pytest
from dakota_terminal.diffs import create_diff, apply_diff, validate_diff, first_cell_diff, estimate_diff_size
from dakota_terminal import TerminalEngine, snapshot_from_engine


def _snap(cells=None, rows=25, cols=80, text=""):
    """Cria snapshot real via TerminalEngine."""
    engine = TerminalEngine(rows=rows, cols=cols)
    if text:
        engine.feed_bytes(text.encode("utf-8") if isinstance(text, str) else text)
    snap = snapshot_from_engine(engine)
    if cells is not None:
        snap["cells"] = cells
    return snap


class TestDiffRoundTrip:
    def test_round_trip_identical(self):
        snap = _snap()
        diff = create_diff(snap, snap, base_seq=0, seq=1)
        result = apply_diff(snap, diff)
        assert result["cells"] == snap["cells"]

    def test_round_trip_one_cell_changed(self):
        prev = _snap(rows=2, cols=2)
        curr = _snap(rows=2, cols=2)
        curr["cells"][0] = {"ch": "X", "fg": "red", "bg": "default",
                            "bold": True, "dim": False, "underline": False,
                            "blink": False, "reverse": False, "hidden": False}
        # Recalcula assinaturas para o snapshot modificado
        from dakota_terminal.signatures import text_sig, visual_sig, semantic_sig
        curr["text_sig"] = text_sig(curr)
        curr["visual_sig"] = visual_sig(curr)
        curr["semantic_sig"] = semantic_sig(curr)

        diff = create_diff(prev, curr, base_seq=5, seq=6, ts_ms=1000)
        result = apply_diff(prev, diff)

        assert result["cells"][0]["ch"] == "X"
        assert result["cells"][0]["fg"] == "red"
        assert result["cells"][0]["bold"] is True
        assert result["text_sig"] == curr["text_sig"]
        assert result["visual_sig"] == curr["visual_sig"]

    def test_round_trip_cursor_only(self):
        prev = _snap()
        curr = _snap()
        curr["cursor"] = {"row": 5, "col": 10, "visible": True, "wrap_pending": False}

        diff = create_diff(prev, curr, 0, 1)
        result = apply_diff(prev, diff)
        assert result["cursor"]["row"] == 5
        assert result["cursor"]["col"] == 10

    def test_round_trip_resize(self):
        prev = _snap(rows=25, cols=80)
        curr = _snap(rows=30, cols=100)

        diff = create_diff(prev, curr, 0, 1)
        assert diff["geometry_changed"] is True
        assert diff["resize"] is not None
        assert diff["resize"]["from_rows"] == 25
        assert diff["resize"]["to_rows"] == 30

        result = apply_diff(prev, diff)
        assert result["rows"] == 30
        assert result["cols"] == 100
        assert len(result["cells"]) == 3000

    def test_round_trip_empty_cells(self):
        prev = _snap(rows=2, cols=2)
        curr = _snap(rows=2, cols=2)
        curr["cells"][0] = {"ch": "A", "fg": "default", "bg": "default",
                            "bold": False, "dim": False, "underline": False,
                            "blink": False, "reverse": False, "hidden": False}
        from dakota_terminal.signatures import text_sig, visual_sig, semantic_sig
        curr["text_sig"] = text_sig(curr)
        curr["visual_sig"] = visual_sig(curr)
        curr["semantic_sig"] = semantic_sig(curr)

        diff = create_diff(prev, curr, 0, 1)
        result = apply_diff(prev, diff)
        assert result["cells"][0] == curr["cells"][0]
        for i in range(1, 4):
            assert result["cells"][i] == curr["cells"][i]


class TestDiffSequentialIdentity:
    def test_diff_has_base_seq_and_seq(self):
        diff = create_diff(_snap(rows=1,cols=1), _snap(rows=1,cols=1), base_seq=10, seq=11, ts_ms=5000)
        assert diff["base_seq_global"] == 10
        assert diff["seq_global"] == 11
        assert diff["timestamp_ms"] == 5000

    def test_diff_has_base_signatures(self):
        prev = _snap(rows=1, cols=1)
        curr = _snap(rows=1, cols=1, text="A")
        diff = create_diff(prev, curr, 0, 1)
        assert diff["base_text_sig"] == prev["text_sig"]
        assert diff["base_visual_sig"] == prev["visual_sig"]
        assert diff["text_sig"] == curr["text_sig"]
        assert diff["visual_sig"] == curr["visual_sig"]


class TestDiffValidation:
    def test_validate_correct_diff(self):
        prev = _snap(rows=1, cols=1)
        curr = _snap(rows=1, cols=1)
        curr["cells"][0]["ch"] = "Z"
        from dakota_terminal.signatures import text_sig, visual_sig, semantic_sig
        curr["text_sig"] = text_sig(curr)
        curr["visual_sig"] = visual_sig(curr)
        curr["semantic_sig"] = semantic_sig(curr)
        diff = create_diff(prev, curr, 0, 1)
        assert validate_diff(prev, diff) is True

    def test_validate_wrong_base_sig(self):
        prev = _snap(rows=1, cols=1)
        # Cria um diff com assinatura base errada manualmente
        diff = {"version": 1, "changes": [], "base_text_sig": "sha256:wrong", "base_visual_sig": prev.get("visual_sig","")}
        assert validate_diff(prev, diff) is False

    def test_validate_wrong_base_visual_sig(self):
        prev = _snap(rows=1, cols=1)
        diff = {"version": 1, "changes": [], "base_text_sig": prev.get("text_sig",""), "base_visual_sig": "sha256:wrong_vis"}
        assert validate_diff(prev, diff) is False

    def test_validate_invalid_version(self):
        diff = {"version": 2, "changes": []}
        assert validate_diff(_snap(), diff) is False

    def test_validate_resize_geometry(self):
        prev = _snap(rows=25, cols=80)
        curr = _snap(rows=30, cols=100)
        diff = create_diff(prev, curr, 0, 1)
        assert validate_diff(prev, diff) is True

    def test_validate_resize_too_large(self):
        prev = _snap()
        diff = {"version": 1, "changes": [], "geometry_changed": True, "rows": 999, "cols": 999}
        assert validate_diff(prev, diff) is False


class TestFirstCellDiff:
    def test_finds_first_diff(self):
        prev = _snap()
        curr = _snap()
        curr["cells"][5]["ch"] = "X"
        result = first_cell_diff(prev, curr)
        assert result is not None
        assert result["row"] == 0
        assert result["col"] == 5

    def test_identical_returns_none(self):
        snap = _snap()
        assert first_cell_diff(snap, snap) is None

    def test_length_diff(self):
        prev = _snap(rows=1, cols=1)
        curr = _snap(rows=1, cols=2)
        result = first_cell_diff(prev, curr)
        assert result is not None
        assert result["left"] == 1
        assert result["right"] == 2


class TestEstimateDiffSize:
    def test_empty_diff(self):
        from dakota_terminal.diffs import estimate_diff_size
        diff = create_diff(_snap(), _snap(), 0, 1)
        size = estimate_diff_size(diff)
        assert size > 0
        assert size < 5000

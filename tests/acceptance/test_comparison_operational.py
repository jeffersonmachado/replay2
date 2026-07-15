from __future__ import annotations

import selectors

import pytest

from dakota_gateway import replay as replay_mod
from dakota_terminal.comparison import compare_signatures


def snap(*, text="", visual="", semantic="", screen=""):
    return {
        "text_sig": text,
        "visual_sig": visual,
        "semantic_sig": semantic,
        "screen_sig": screen,
    }


def test_visual_requires_visual_sig_and_never_falls_back_to_legacy_screen_sig():
    result = compare_signatures(
        snap(screen="legacy"),
        snap(screen="legacy"),
        mode="visual",
        legacy_expected_screen_sig="legacy",
        legacy_observed_screen_sig="legacy",
    )
    assert result["matched"] is False
    assert result["comparison_mode_used"] is None
    assert result["fallback_reason"] == "visual_signature_missing"


def test_visual_detects_attribute_only_change_even_when_text_matches():
    result = compare_signatures(
        snap(text="sha256:text", visual="sha256:normal"),
        snap(text="sha256:text", visual="sha256:reverse"),
        mode="visual",
    )
    assert result["matched"] is False
    assert result["expected_sig"] == "sha256:normal"
    assert result["observed_sig"] == "sha256:reverse"


def test_text_and_semantic_are_exclusive_no_cross_fallback():
    assert compare_signatures(
        snap(text="sha256:t", visual="sha256:a"),
        snap(text="sha256:t", visual="sha256:b"),
        mode="text",
    )["matched"] is True
    semantic_missing = compare_signatures(
        snap(text="sha256:t", semantic=""),
        snap(text="sha256:t", semantic=""),
        mode="semantic",
    )
    assert semantic_missing["matched"] is False
    assert semantic_missing["comparison_mode_used"] is None
    assert semantic_missing["fallback_reason"] == "semantic_signature_missing"


@pytest.mark.parametrize(
    ("expected", "observed", "used"),
    [
        (snap(visual="sha256:v", text="sha256:t", semantic="sha256:s"), snap(visual="sha256:v", text="sha256:t", semantic="sha256:s"), "visual"),
        (snap(text="sha256:t", semantic="sha256:s"), snap(text="sha256:t", semantic="sha256:s"), "text"),
        (snap(semantic="sha256:s"), snap(semantic="sha256:s"), "semantic"),
        (snap(screen="legacy"), snap(screen="legacy"), "legacy_screen_sig"),
    ],
)
def test_hybrid_selects_one_common_level_for_both_sides(expected, observed, used):
    result = compare_signatures(expected, observed, mode="hybrid")
    assert result["matched"] is True
    assert result["comparison_mode_used"] == used
    assert result["comparison_mode_requested"] == "hybrid"
    assert result["fallback_reason"] is None


def test_hybrid_rejects_when_no_common_level_exists():
    result = compare_signatures(
        snap(visual="sha256:v"),
        snap(text="sha256:t"),
        mode="hybrid",
    )
    assert result["matched"] is False
    assert result["comparison_mode_used"] is None
    assert result["fallback_reason"] == "no_common_signature_level"


class FakeSession:
    session_id = "s1"
    last_out_ms = 0

    def canonical_snapshot_now(self):
        return snap(screen="legacy")

    def signature_now(self):
        raise AssertionError("canonical comparison must not call legacy signature_now")

    def read_out(self):
        return b""


class EmptySelector(selectors.BaseSelector):
    def register(self, fileobj, events, data=None):  # pragma: no cover
        raise NotImplementedError

    def unregister(self, fileobj):  # pragma: no cover
        raise NotImplementedError

    def modify(self, fileobj, events, data=None):  # pragma: no cover
        raise NotImplementedError

    def select(self, timeout=None):
        return []

    def get_map(self):  # pragma: no cover
        return {}

    def close(self):
        pass


def test_operational_wait_visual_with_only_screen_sig_returns_structured_failure(monkeypatch):
    monkeypatch.setattr(replay_mod.time, "time", lambda: 1000.0)
    result = replay_mod._wait_for_screen_signature(
        FakeSession(),
        EmptySelector(),
        {"screen_sig": "legacy"},
        checkpoint_quiet_ms=0,
        checkpoint_timeout_ms=1,
        comparison_mode="visual",
    )
    assert result["matched"] is False
    assert result["comparison_mode_used"] is None
    assert result["fallback_reason"] == "visual_signature_missing"

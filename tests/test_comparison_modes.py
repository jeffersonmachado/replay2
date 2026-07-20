"""Testes para modos de comparacao (visual, text, semantic, hybrid)."""
from __future__ import annotations

import pytest
from dakota_terminal.comparison import select_signature, compare_signatures, resolve_comparison_mode


def _snap(text_sig="sha256:text123", visual_sig="sha256:vis456", semantic_sig="sha256:sem789"):
    return {
        "text_sig": text_sig,
        "visual_sig": visual_sig,
        "semantic_sig": semantic_sig,
        "cells": [],
        "rows": 25, "cols": 80,
    }


class TestSelectSignature:
    def test_visual_selects_visual_sig(self):
        result = select_signature(_snap(), "visual")
        assert result["comparison_mode_requested"] == "visual"
        assert result["comparison_mode_used"] == "visual"
        assert result["signature"] == "sha256:vis456"
        assert result["fallback_reason"] is None

    def test_visual_fails_when_visual_sig_missing(self):
        result = select_signature(_snap(visual_sig=""), "visual")
        assert result["comparison_mode_used"] is None  # sem fallback
        assert result["signature"] is None
        assert result["fallback_reason"] == "visual_sig_not_available"

    def test_text_selects_text_sig(self):
        result = select_signature(_snap(), "text")
        assert result["comparison_mode_used"] == "text"
        assert result["signature"] == "sha256:text123"

    def test_semantic_selects_semantic_sig(self):
        result = select_signature(_snap(), "semantic")
        assert result["comparison_mode_used"] == "semantic"
        assert result["signature"] == "sha256:sem789"

    def test_semantic_no_fallback_to_legacy(self):
        """Semantic NAO faz fallback para screen_sig legado."""
        result = select_signature(_snap(semantic_sig=""), "semantic", legacy_screen_sig="legacy123")
        assert result["comparison_mode_used"] is None
        assert result["signature"] is None
        assert result["fallback_reason"] == "semantic_sig_not_available"

    def test_hybrid_prefers_visual(self):
        result = select_signature(_snap(), "hybrid")
        assert result["comparison_mode_used"] == "visual"
        assert result["signature"] == "sha256:vis456"
        assert result["fallback_reason"] is None

    def test_hybrid_falls_to_text(self):
        result = select_signature(_snap(visual_sig=""), "hybrid")
        assert result["comparison_mode_used"] == "text"
        assert result["signature"] == "sha256:text123"
        assert result["fallback_reason"] == "visual_sig_not_available"

    def test_hybrid_falls_to_semantic(self):
        result = select_signature(_snap(visual_sig="", text_sig=""), "hybrid")
        assert result["comparison_mode_used"] == "semantic"
        assert result["signature"] == "sha256:sem789"
        assert "visual_sig_and_text_sig_not_available" in (result["fallback_reason"] or "")

    def test_hybrid_falls_to_legacy(self):
        result = select_signature(
            _snap(visual_sig="", text_sig="", semantic_sig=""), "hybrid",
            legacy_screen_sig="legacy456"
        )
        assert result["comparison_mode_used"] == "legacy_screen_sig"  # explicitamente marcado
        assert result["signature"] == "legacy456"
        assert result["fallback_reason"] == "using_legacy_screen_sig"

    def test_unknown_mode_returns_none(self):
        result = select_signature(_snap(), "unknown_mode")
        assert result["comparison_mode_used"] is None


class TestCompareSignatures:
    def test_visual_match(self):
        result = compare_signatures(_snap(), _snap(), "visual")
        assert result["matched"] is True
        assert result["comparison_mode_used"] == "visual"

    def test_visual_mismatch(self):
        result = compare_signatures(
            _snap(visual_sig="sha256:A"),
            _snap(visual_sig="sha256:B"),
            "visual"
        )
        assert result["matched"] is False
        assert result["expected_sig"] == "sha256:A"
        assert result["observed_sig"] == "sha256:B"

    def test_text_match_visual_mismatch(self):
        """Texto igual, visual diferente (ex: normal vs reverse)."""
        result = compare_signatures(
            _snap(text_sig="sha256:text_ok", visual_sig="sha256:vis_normal"),
            _snap(text_sig="sha256:text_ok", visual_sig="sha256:vis_reverse"),
            "text"
        )
        assert result["matched"] is True
        assert result["comparison_mode_used"] == "text"

    def test_text_match_visual_mismatch_visual_mode(self):
        """Em modo visual, visual diferente = nao match."""
        result = compare_signatures(
            _snap(text_sig="sha256:text_ok", visual_sig="sha256:vis_normal"),
            _snap(text_sig="sha256:text_ok", visual_sig="sha256:vis_reverse"),
            "visual"
        )
        assert result["matched"] is False

    def test_hybrid_uses_visual_when_available(self):
        result = compare_signatures(_snap(), _snap(), "hybrid")
        assert result["matched"] is True
        assert result["comparison_mode_used"] == "visual"

    def test_hybrid_result_has_all_fields(self):
        result = compare_signatures(_snap(), _snap(), "hybrid")
        assert "comparison_mode_requested" in result
        assert "comparison_mode_used" in result
        assert "expected_sig" in result
        assert "observed_sig" in result
        assert "matched" in result
        assert "fallback_reason" in result
        assert result["comparison_mode_requested"] == "hybrid"

    def test_no_silent_fallback_from_visual_to_semantic(self):
        """Visual solicitado sem visual_sig: deve falhar, nao usar semantic."""
        result = compare_signatures(
            _snap(visual_sig=""),
            _snap(visual_sig="sha256:vis"),
            "visual"
        )
        assert result["matched"] is False
        assert result["comparison_mode_used"] is None  # sem fallback
        assert result["expected_sig"] is None

    def test_cross_level_comparison_prevented(self):
        """Hybrid nao pode comparar visual_sig esperado com text_sig observado."""
        # Caso 1: expected tem visual, observed nao → hybrid deve usar text (ambos tem)
        expected = _snap(visual_sig="sha256:VIS_ONLY_EXP", text_sig="sha256:SAME_TEXT")
        observed = _snap(visual_sig="", text_sig="sha256:SAME_TEXT")
        result = compare_signatures(expected, observed, "hybrid")
        # Ambos tem text_sig → hybrid usa text (nao compara visual com text)
        assert result["comparison_mode_used"] == "text"
        assert result["matched"] is True  # text_sig igual

        # Caso 2: text_sig diferentes → matched=False (nao faz fallback para outro nivel)
        expected2 = _snap(visual_sig="sha256:VIS", text_sig="sha256:TEXT_A")
        observed2 = _snap(visual_sig="", text_sig="sha256:TEXT_B")
        result2 = compare_signatures(expected2, observed2, "hybrid")
        assert result2["comparison_mode_used"] == "text"
        assert result2["matched"] is False  # text diferente, nao tenta outro nivel


class TestResolveComparisonMode:
    def test_precedence_event_session_replay_default(self):
        assert resolve_comparison_mode(
            event={"comparison_mode": "text"},
            session={"comparison_mode": "semantic"},
            replay={"comparison_mode": "hybrid"},
        ) == {"comparison_mode": "text", "source": "event"}
        assert resolve_comparison_mode(
            event={},
            session={"comparison_mode": "semantic"},
            replay={"comparison_mode": "hybrid"},
        ) == {"comparison_mode": "semantic", "source": "session"}
        assert resolve_comparison_mode(
            event={},
            session={},
            replay={"comparison_mode": "hybrid"},
        ) == {"comparison_mode": "hybrid", "source": "replay"}
        assert resolve_comparison_mode(event={}, session={}, replay={}) == {
            "comparison_mode": "visual",
            "source": "default",
        }

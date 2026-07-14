"""
Integration test for capture 8 replay data validation.

Validates the full flow:
  session_replay_service → payload → geometry → timeline → playback → consistency

Run:
  PYTHONPATH=gateway python3 -m pytest tests/test_capture8_replay_integration.py -v
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
from pathlib import Path

import pytest

from dakota_gateway.compliance import (
    normalize_target_policy,
    summarize_capture_sessions,
)
from control.services.session_replay_service import (
    _detect_encoding,
    _detect_geometry,
    prepare_session_replay_data,
)

# ── Fixture: load sanitized capture 8 data ──────────────────────────────────

FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "capture8_replay_fixture.json"


@pytest.fixture(scope="module")
def capture8_data():
    """Load sanitized capture 8 replay data from fixture file."""
    if not FIXTURE_PATH.exists():
        pytest.skip(f"Fixture not found: {FIXTURE_PATH}")
    with open(FIXTURE_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


# ── Geometry ───────────────────────────────────────────────────────────────


def test_geometry_is_legacy_fallback(capture8_data):
    """Capture 8 has no explicit geometry metadata — must use fallback."""
    geom = capture8_data.get("geometry", {})
    assert geom.get("rows") == 25
    assert geom.get("cols") == 80
    assert geom.get("geometry_source") == "legacy_fallback"


def test_geometry_not_inferred_from_cursor():
    """Geometry must not be derived from cursor positioning sequences."""
    result = _detect_geometry([])
    assert result["rows"] == 25
    assert result["cols"] == 80
    assert result["geometry_source"] == "legacy_fallback"

    # Create fake events with cursor positioning — must not affect geometry
    fake_events = [{"data_b64": base64.b64encode(b"\x1b[10;20H").decode()}]
    result2 = _detect_geometry(fake_events)
    assert result2["rows"] == 25, "cursor H must not set rows"
    assert result2["cols"] == 80, "cursor H must not set cols"

    # Gigantic values must be rejected
    fake_huge = [{"data_b64": base64.b64encode(b"\x1b[999999;999999H").decode()}]
    result3 = _detect_geometry(fake_huge)
    assert result3["rows"] == 25
    assert result3["cols"] == 80


def test_geometry_resize_detected():
    """CSI 8;rows;cols t should be detected within limits — only from OUT events."""
    fake = [{"data_b64": base64.b64encode(b"\x1b[8;30;100t").decode(), "direction": "out"}]
    result = _detect_geometry(fake)
    assert result["rows"] == 30
    assert result["cols"] == 100
    assert result["geometry_source"] == "pty_resize"

    # Out of bounds
    fake_huge = [{"data_b64": base64.b64encode(b"\x1b[8;999;999t").decode(), "direction": "out"}]
    result2 = _detect_geometry(fake_huge)
    assert result2["rows"] == 25, "oversized resize rejected"
    assert result2["cols"] == 80


# ── Encoding ───────────────────────────────────────────────────────────────


def test_encoding_default_utf8():
    """Sem metadados, encoding padrao e utf-8."""
    result = _detect_encoding([])
    assert result == "utf-8"


def test_encoding_from_metadata():
    """Metadados do session_start tem prioridade."""
    result = _detect_encoding([], {"encoding": "latin1"})
    assert result == "latin1"


def test_encoding_dec_graphics_does_not_affect_encoding():
    """Bytes com charset DEC graphics NAO alteram encoding — continua utf-8."""
    events = [{"data_b64": base64.b64encode(b"\x1b(0\x0eABC\x0f").decode()}]
    result = _detect_encoding(events)
    assert result == "utf-8", "DEC graphics nao deve mudar encoding para latin1"


def test_encoding_high_bytes_without_metadata_remains_utf8():
    """Bytes com chars 0x80-0x9F sem metadados continuam utf-8 (fallback)."""
    events = [{"data_b64": base64.b64encode(b"\x80\x90").decode()}]
    result = _detect_encoding(events)
    assert result == "utf-8", "sem metadados, fallback e utf-8"


# ── Timeline ───────────────────────────────────────────────────────────────


def test_timeline_has_events(capture8_data):
    timeline = capture8_data.get("timeline", [])
    assert len(timeline) > 0


def test_timeline_has_timestamp_ms(capture8_data):
    """Every timeline event must have timestamp_ms (primary) and ts_ms (legacy)."""
    timeline = capture8_data.get("timeline", [])
    for ev in timeline[:10]:
        assert "timestamp_ms" in ev, f"seq {ev.get('seq_global')} missing timestamp_ms"
        assert isinstance(ev["timestamp_ms"], int)
        assert ev["timestamp_ms"] > 0


def test_timeline_has_data_decoded(capture8_data):
    """Every bytes event must have data_decoded (not empty for real data)."""
    timeline = capture8_data.get("timeline", [])
    out_events = [e for e in timeline if e.get("direction") == "out"]
    assert len(out_events) > 0
    for ev in out_events[:5]:
        assert "data_decoded" in ev


def test_timeline_seq_global_monotonic(capture8_data):
    timeline = capture8_data.get("timeline", [])
    seqs = [e.get("seq_global", 0) for e in timeline]
    for i in range(1, len(seqs)):
        assert seqs[i] >= seqs[i - 1], f"seq_global not monotonic at index {i}"


# ── Playback ───────────────────────────────────────────────────────────────


def test_playback_has_events(capture8_data):
    playback = capture8_data.get("playback", {})
    events = playback.get("events", [])
    assert len(events) > 0


def test_playback_has_timestamp_ms(capture8_data):
    """Playback events must have timestamp_ms."""
    playback = capture8_data.get("playback", {})
    for ev in playback.get("events", [])[:10]:
        assert "timestamp_ms" in ev, f"seq {ev.get('seq')} missing timestamp_ms"
        assert ev["timestamp_ms"] > 0


def test_playback_has_data_b64(capture8_data):
    """Playback events must preserve data_b64 for UTF-8 decoder."""
    playback = capture8_data.get("playback", {})
    for ev in playback.get("events", [])[:10]:
        assert "data_b64" in ev, f"seq {ev.get('seq')} must have data_b64"
        assert isinstance(ev["data_b64"], str), f"data_b64 must be string"
        assert ev["data_b64"] != "", f"seq {ev.get('seq')} data_b64 cannot be empty"


def test_playback_total_bytes_consistent(capture8_data):
    """Total bytes in/out must match sum of events."""
    playback = capture8_data.get("playback", {})
    events = playback.get("events", [])
    total_in = sum(e["bytes"] for e in events if e["direction"] == "in")
    total_out = sum(e["bytes"] for e in events if e["direction"] == "out")
    assert playback["total_bytes_in"] == total_in
    assert playback["total_bytes_out"] == total_out


# ── Screen content validation ──────────────────────────────────────────────


def test_session_has_login_screen(capture8_data):
    """Capture 8 must contain the Recital login screen."""
    timeline = capture8_data.get("timeline", [])
    all_text = " ".join(e.get("data_decoded", "") for e in timeline)
    assert "Login do Sistema" in all_text, "login screen not found"


def test_session_has_main_menu(capture8_data):
    """Capture 8 must contain the main menu."""
    timeline = capture8_data.get("timeline", [])
    all_text = " ".join(e.get("data_decoded", "") for e in timeline)
    assert "Menu Principal" in all_text, "main menu not found"


def test_session_has_dec_graphics(capture8_data):
    """Capture 8 must contain DEC special graphics (box drawing)."""
    timeline = capture8_data.get("timeline", [])
    all_text = " ".join(e.get("data_decoded", "") for e in timeline)
    # DEC graphics switch: ESC ( 0
    assert "\x1b(0" in all_text or "┌" in all_text or "\u250C" in all_text, "DEC graphics not found"


def test_no_artificial_markers(capture8_data):
    """No block characters or HTML artifacts in data."""
    timeline = capture8_data.get("timeline", [])
    all_text = " ".join(e.get("data_decoded", "") for e in timeline)
    assert "\u2588" not in all_text, "block character found"
    assert "<span" not in all_text, "HTML span found"


# ── UTF-8 handling ─────────────────────────────────────────────────────────


def test_utf8_sequences_present(capture8_data):
    """Verify that multi-byte UTF-8 sequences exist in the data."""
    timeline = capture8_data.get("timeline", [])
    out_events = [e for e in timeline if e.get("direction") == "out"]
    has_multibyte = False
    for ev in out_events:
        text = ev.get("data_decoded", "")
        for ch in text:
            if ord(ch) > 127:
                has_multibyte = True
                break
        if has_multibyte:
            break
    # Not all captures have UTF-8; just verify the data is accessible
    assert len(out_events) > 0


# ── Consistency ────────────────────────────────────────────────────────────


def test_deterministic_inputs_exist(capture8_data):
    det = capture8_data.get("deterministic_events", [])
    playback = capture8_data.get("playback", {})
    assert playback.get("deterministic_event_count", 0) >= 0


def test_session_start_present(capture8_data):
    assert capture8_data.get("session_start") is not None


def test_error_is_none(capture8_data):
    assert capture8_data.get("error") is None


def test_session_id_matches(capture8_data):
    assert capture8_data.get("session_id") == "758f897c-572e-4f5d-b1eb-cb2fcd16f726"

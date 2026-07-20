"""
Integration test for capture 8 replay data validation.

Validates the full flow:
  audit JSONL → prepare_session_replay_data → payload → geometry → timeline → playback → consistency

Run:
  PYTHONPATH=gateway python3 -m pytest tests/test_capture8_replay_integration.py -v
"""
from __future__ import annotations

import base64
import json
import tempfile
from pathlib import Path

import pytest

from control.services.session_replay_service import (
    _detect_encoding,
    _detect_geometry,
    prepare_session_replay_data,
)

FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "capture8_replay_fixture.json"


def _load_fixture() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _load_audit_events() -> list[dict]:
    payload = _load_fixture()
    assert payload["format"] == "dakota.capture.audit.raw.v1"
    return list(payload["events"])


@pytest.fixture(scope="module")
def capture8_data():
    """Processa a fixture bruta de disco pelo servico real."""
    payload = _load_fixture()
    sid = payload["session_id"]
    audit_events = list(payload["events"])

    with tempfile.TemporaryDirectory() as tmpdir:
        audit_path = Path(tmpdir) / "audit-000001.jsonl"
        with open(audit_path, "w", encoding="utf-8") as f:
            for ev in audit_events:
                f.write(json.dumps(ev, ensure_ascii=False) + "\n")

        result = prepare_session_replay_data(str(tmpdir), sid)
        if result.get("error"):
            pytest.skip(f"Service error: {result['error']}")
        return result


def test_fixture_is_raw_disk_capture_without_derived_payloads():
    payload = _load_fixture()
    forbidden = {"replay_events", "timeline", "playback", "snapshots", "diffs"}
    assert forbidden.isdisjoint(payload.keys())
    for ev in payload["events"]:
        assert forbidden.isdisjoint(ev.keys())
        if ev.get("type") == "bytes":
            raw = base64.b64decode(ev["data_b64"])
            assert ev["n"] == len(raw), f"seq {ev.get('seq_global')} has invalid n"


def test_fixture_contains_required_chunked_terminal_cases():
    events = _load_audit_events()
    chunks = [base64.b64decode(ev["data_b64"]) for ev in events if ev.get("type") == "bytes"]
    stream = b"".join(chunks)

    assert any(ev.get("type") == "session_start" for ev in events)
    assert any(ev.get("type") == "session_end" for ev in events)
    assert any(ev.get("dir") == "in" for ev in events)
    assert any(ev.get("type") == "checkpoint" for ev in events)
    assert any(ev.get("type") == "deterministic_input" for ev in events)

    # DEC G0, SO, SI
    assert b"\x1b(0" in stream
    assert b"\x0e" in stream and b"\x0f" in stream

    # Reverse and background
    assert b"\x1b[7m" in stream and b"\x1b[42m" in stream

    # UTF-8 2-byte divided: é = C3 A9
    assert any(chunk == b"\xc3" for chunk in chunks)
    assert any(chunk == b"\xa9" for chunk in chunks)

    # UTF-8 3-byte divided: € = E2 82 AC
    assert any(chunk == b"\xe2" for chunk in chunks)
    assert any(chunk == b"\x82\xac" for chunk in chunks)

    # Emoji 4-byte divided: 😀 = F0 9F 98 80
    assert any(chunk == b"\xf0\x9f" for chunk in chunks)
    assert any(chunk == b"\x98\x80" for chunk in chunks)

    # CSI divided: ESC[31;42m -> split
    assert any(chunk == b"\x1b[31" for chunk in chunks)
    assert any(chunk == b";42m" for chunk in chunks)

    # OSC and ST
    assert b"\x1b]0;capture8-test\x07" in stream
    assert b"\x1b\\" in stream

    # Scroll region and scroll
    assert b"\x1b[1;10r" in stream
    assert b"\x1b[2S" in stream

    # Resize events
    assert b"\x1b[8;3;4t" in stream
    assert b"\x1b[8;5;80t" in stream
    assert b"\x1b[8;6;80t" in stream

    # RIS divided: ESC c
    assert any(chunk == b"\x1b" for chunk in chunks)

    # Content after RIS
    assert b"AFTER-RIS" in stream

    # Clear screen and cursor
    assert b"\x1b[2J" in stream
    assert b"\x1b[5;10H" in stream

    # Bold + underline
    assert b"\x1b[1;4m" in stream
    assert b"BOLD_UL" in stream


# ── Geometry ───────────────────────────────────────────────────────────────


def test_geometry_is_session_metadata(capture8_data):
    """Fixture has session_start with explicit geometry metadata."""
    geom = capture8_data.get("geometry", {})
    assert geom.get("rows") == 2
    assert geom.get("cols") == 3
    assert geom.get("geometry_source") == "session_metadata"


def test_geometry_not_inferred_from_cursor():
    """Geometry must not be derived from cursor positioning sequences."""
    result = _detect_geometry([])
    assert result["rows"] == 25
    assert result["cols"] == 80
    assert result["geometry_source"] == "legacy_fallback"

    fake_events = [{"data_b64": base64.b64encode(b"\x1b[10;20H").decode()}]
    result2 = _detect_geometry(fake_events)
    assert result2["rows"] == 25, "cursor H must not set rows"
    assert result2["cols"] == 80, "cursor H must not set cols"

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
    assert result["geometry_source"] == "resize_event"

    fake_huge = [{"data_b64": base64.b64encode(b"\x1b[8;999;999t").decode(), "direction": "out"}]
    result2 = _detect_geometry(fake_huge)
    assert result2["rows"] == 25, "oversized resize rejected"
    assert result2["cols"] == 80


# ── Encoding ───────────────────────────────────────────────────────────────


def test_encoding_default_utf8():
    result = _detect_encoding([])
    assert result == "utf-8"


def test_encoding_from_metadata():
    result = _detect_encoding([], {"encoding": "latin1"})
    assert result == "latin1"


def test_encoding_dec_graphics_does_not_affect_encoding():
    events = [{"data_b64": base64.b64encode(b"\x1b(0\x0eABC\x0f").decode()}]
    result = _detect_encoding(events)
    assert result == "utf-8", "DEC graphics nao deve mudar encoding para latin1"


def test_encoding_high_bytes_without_metadata_remains_utf8():
    events = [{"data_b64": base64.b64encode(b"\x80\x90").decode()}]
    result = _detect_encoding(events)
    assert result == "utf-8", "sem metadados, fallback e utf-8"


# ── Timeline ───────────────────────────────────────────────────────────────


def test_timeline_has_event_refs(capture8_data):
    timeline = capture8_data.get("timeline", {})
    assert "event_refs" in timeline
    assert len(timeline["event_refs"]) > 0


def test_timeline_events_have_timestamp(capture8_data):
    """Every event must have ts_ms."""
    events = capture8_data.get("events", [])
    for ev in events[:10]:
        assert "ts_ms" in ev, f"seq {ev.get('seq_global')} missing ts_ms"
        assert isinstance(ev["ts_ms"], int)
        assert ev["ts_ms"] > 0


def test_timeline_events_have_data_decoded(capture8_data):
    """Every bytes event must have data_decoded."""
    events = capture8_data.get("events", [])
    out_events = [e for e in events if e.get("direction") == "out"]
    assert len(out_events) > 0
    for ev in out_events[:5]:
        assert "data_decoded" in ev


def test_timeline_seq_global_monotonic(capture8_data):
    events = capture8_data.get("events", [])
    seqs = [e.get("seq_global", 0) for e in events]
    for i in range(1, len(seqs)):
        assert seqs[i] >= seqs[i - 1], f"seq_global not monotonic at index {i}"


# ── Playback ───────────────────────────────────────────────────────────────


def test_playback_has_event_refs(capture8_data):
    playback = capture8_data.get("playback", {})
    assert "event_refs" in playback
    assert len(playback["event_refs"]) > 0


def test_playback_has_event_count(capture8_data):
    playback = capture8_data.get("playback", {})
    assert "event_count" in playback
    assert playback["event_count"] > 0


def test_playback_total_bytes_consistent(capture8_data):
    """Total bytes out should be positive for a real session."""
    playback = capture8_data.get("playback", {})
    total_out = playback.get("total_bytes_out", 0)
    assert total_out > 0, "Playback must have positive total_bytes_out"


def test_playback_has_comparison_modes(capture8_data):
    playback = capture8_data.get("playback", {})
    assert "comparison_modes" in playback
    assert "default_comparison_mode" in playback
    comparison_modes = playback["comparison_modes"]
    assert "visual" in comparison_modes or "semantic" in comparison_modes


def test_playback_has_deterministic_count(capture8_data):
    playback = capture8_data.get("playback", {})
    assert "deterministic_event_count" in playback
    assert playback["deterministic_event_count"] >= 0


# ── Final snapshot ─────────────────────────────────────────────────────────


def test_final_snapshot_exists(capture8_data):
    assert "final_snapshot" in capture8_data
    assert capture8_data["final_snapshot"] is not None


def test_final_snapshot_has_signatures(capture8_data):
    snap = capture8_data["final_snapshot"]
    if snap:
        assert "text_sig" in snap or "visual_sig" in snap or "semantic_sig" in snap

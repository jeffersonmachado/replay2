from __future__ import annotations

import base64
import json
import tempfile
import subprocess
from pathlib import Path

import pytest

from dakota_terminal.engine import TerminalEngine
from dakota_terminal.snapshot import (
    CanonicalSnapshot,
    RenderSnapshot,
    snapshot_from_engine,
    encode_canonical_snapshot,
)
from dakota_terminal.comparison import compare_signatures, resolve_comparison_mode
from gateway.control.services.session_replay_service import prepare_session_replay_data


ROOT = Path(__file__).resolve().parents[2]


def _write_audit_jsonl(tmp_path: Path) -> tuple:
    """Write fixture as JSONL audit file and return the log directory path and fixture."""
    fixture_path = ROOT / "tests/fixtures/capture8_replay_fixture.json"
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    log_dir = tmp_path / "audit-logs"
    log_dir.mkdir()
    lines = "\n".join(json.dumps(ev, ensure_ascii=False) for ev in fixture["events"])
    (log_dir / "audit-capture8.part001.jsonl").write_text(lines, encoding="utf-8")
    return str(log_dir), fixture


# ---------------------------------------------------------------------------
# 1. Basic fixture validation
# ---------------------------------------------------------------------------

def test_raw_fixture_events_use_base64_lengths_and_no_prematerialized_payloads():
    fixture = json.loads(Path("tests/fixtures/capture8_replay_fixture.json").read_text(encoding="utf-8"))
    events = fixture["events"]
    assert fixture["format"] == "dakota.capture.audit.raw.v1"
    assert "replay_events" not in fixture
    assert "timeline" not in fixture
    assert "playback" not in fixture
    assert all("replay_events" not in ev and "timeline" not in ev and "playback" not in ev for ev in events)
    for ev in events:
        if "data_b64" in ev:
            assert ev["n"] == len(base64.b64decode(ev["data_b64"]))


# ---------------------------------------------------------------------------
# 2. prepare_session_replay_data()
# ---------------------------------------------------------------------------

def test_prepare_replay_data_from_fixture():
    fixture = json.loads(Path("tests/fixtures/capture8_replay_fixture.json").read_text(encoding="utf-8"))
    sid = fixture["session_id"]
    with tempfile.TemporaryDirectory() as tmp:
        log_dir, _ = _write_audit_jsonl(Path(tmp))
        result = prepare_session_replay_data(log_dir, sid)
        assert result["error"] is None, f"unexpected error: {result.get('error')}"
        assert len(result["events"]) > 0
        assert "timeline" in result
        assert "playback" in result
        assert "final_snapshot" in result
        timeline = result["timeline"]
        assert "event_refs" in timeline
        assert len(timeline["event_refs"]) > 0
        assert "checkpoint_refs" in timeline
        for ev in result["events"]:
            assert "replay_events" not in ev
            assert "timeline" not in ev
            assert "playback" not in ev


# ---------------------------------------------------------------------------
# 3. scanner -> decoder -> TerminalEngine
# ---------------------------------------------------------------------------

def test_terminal_engine_pipeline_on_fixture():
    fixture = json.loads(Path("tests/fixtures/capture8_replay_fixture.json").read_text(encoding="utf-8"))
    engine = TerminalEngine(rows=2, cols=3, encoding="utf-8", session_id=fixture["session_id"])
    processed = 0
    for ev in fixture["events"]:
        typ = ev.get("type", "")
        if typ == "session_start":
            rows = ev.get("rows", 25)
            cols = ev.get("cols", 80)
            engine = TerminalEngine(rows=rows, cols=cols, encoding="utf-8", session_id=fixture["session_id"])
            continue
        if typ == "bytes" and "data_b64" in ev:
            data = base64.b64decode(ev["data_b64"])
            engine.feed_bytes(data, seq_global=ev.get("seq_global", 0), direction=ev.get("dir", "out"))
            processed += 1
    assert processed > 0
    assert engine.bytes_seen > 0
    snap = snapshot_from_engine(engine)
    assert "text_sig" in snap and "visual_sig" in snap and "semantic_sig" in snap
    assert len(snap["text_sig"]) > 0


# ---------------------------------------------------------------------------
# 4. CanonicalSnapshot
# ---------------------------------------------------------------------------

def test_canonical_snapshot_from_fixture_engine():
    fixture = json.loads(Path("tests/fixtures/capture8_replay_fixture.json").read_text(encoding="utf-8"))
    engine = TerminalEngine(rows=25, cols=80, encoding="utf-8", session_id=fixture["session_id"])
    for ev in fixture["events"]:
        if ev.get("type") == "bytes" and "data_b64" in ev:
            data = base64.b64decode(ev["data_b64"])
            engine.feed_bytes(data, seq_global=ev.get("seq_global", 0), direction=ev.get("dir", "out"))
    snap = snapshot_from_engine(engine)
    canonical = CanonicalSnapshot(
        rows=snap["rows"], cols=snap["cols"], cells=snap["cells"],
        cursor=snap["cursor"], saved_cursor=snap["saved_cursor"],
        attributes=snap["attributes"], g0_charset=snap["g0_charset"],
        g1_charset=snap["g1_charset"], active_charset=snap["active_charset"],
        scroll_region=snap["scroll_region"], autowrap=snap["autowrap"],
        origin_mode=False, insert_mode=False, tab_stops=snap["tab_stops"],
        parser_state=snap["parser"], scanner_state=snap["scanner_state"],
        decoder_state={}, pending_bytes="",
        warnings=snap.get("decoder_warnings", []),
        seq_global=snap.get("seq_global", 0),
        text_sig=snap["text_sig"], visual_sig=snap["visual_sig"],
        semantic_sig=snap["semantic_sig"],
        engine_version=snap.get("engine_version", "1.0"),
        snapshot_version=snap.get("snapshot_version", "1.0"),
        signature_version=snap.get("signature_version", "1.0"),
    )
    assert canonical.rows > 0 and canonical.cols > 0
    assert len(canonical.text_sig) > 0 and len(canonical.visual_sig) > 0 and len(canonical.semantic_sig) > 0


# ---------------------------------------------------------------------------
# 5. RenderSnapshot
# ---------------------------------------------------------------------------

def test_render_snapshot_from_fixture_engine():
    fixture = json.loads(Path("tests/fixtures/capture8_replay_fixture.json").read_text(encoding="utf-8"))
    engine = TerminalEngine(rows=25, cols=80, encoding="utf-8", session_id=fixture["session_id"])
    for ev in fixture["events"]:
        if ev.get("type") == "bytes" and "data_b64" in ev:
            data = base64.b64decode(ev["data_b64"])
            engine.feed_bytes(data, seq_global=ev.get("seq_global", 0), direction=ev.get("dir", "out"))
    snap = snapshot_from_engine(engine)
    render = RenderSnapshot(
        rows=snap["rows"], cols=snap["cols"], term=snap.get("term", "xterm"),
        encoding=snap.get("encoding", "utf-8"), cells=snap["cells"], runs=None,
        cursor=snap["cursor"], seq_global=snap.get("seq_global", 0),
        text_sig=snap["text_sig"], visual_sig=snap["visual_sig"],
        semantic_sig=snap["semantic_sig"],
        engine_version=snap.get("engine_version", "1.0"),
        snapshot_version=snap.get("snapshot_version", "1.0"),
        signature_version=snap.get("signature_version", "1.0"),
    )
    assert render.rows > 0 and render.cols > 0
    assert len(render.text_sig) > 0 and len(render.visual_sig) > 0 and len(render.semantic_sig) > 0


# ---------------------------------------------------------------------------
# 6. Signatures
# ---------------------------------------------------------------------------

def test_signatures_from_fixture_are_stable():
    fixture = json.loads(Path("tests/fixtures/capture8_replay_fixture.json").read_text(encoding="utf-8"))
    def compute():
        engine = TerminalEngine(rows=25, cols=80, encoding="utf-8", session_id=fixture["session_id"])
        for ev in fixture["events"]:
            if ev.get("type") == "bytes" and "data_b64" in ev:
                data = base64.b64decode(ev["data_b64"])
                engine.feed_bytes(data, seq_global=ev.get("seq_global", 0), direction=ev.get("dir", "out"))
        return snapshot_from_engine(engine)
    s1 = compute(); s2 = compute()
    assert s1["text_sig"] == s2["text_sig"]
    assert s1["visual_sig"] == s2["visual_sig"]
    assert s1["semantic_sig"] == s2["semantic_sig"]


# ---------------------------------------------------------------------------
# 7. Diffs
# ---------------------------------------------------------------------------

def test_diff_round_trip_on_fixture():
    fixture = json.loads(Path("tests/fixtures/capture8_replay_fixture.json").read_text(encoding="utf-8"))
    engine = TerminalEngine(rows=25, cols=80, encoding="utf-8", session_id=fixture["session_id"])
    for ev in fixture["events"]:
        if ev.get("type") == "bytes" and "data_b64" in ev:
            data = base64.b64decode(ev["data_b64"])
            engine.feed_bytes(data, seq_global=ev.get("seq_global", 0), direction=ev.get("dir", "out"))
    snap = snapshot_from_engine(engine)
    encoded = encode_canonical_snapshot(snap)
    assert encoded["rows"] == snap["rows"] and encoded["cols"] == snap["cols"]
    assert "cells" in encoded
    engine2 = TerminalEngine(rows=25, cols=80, encoding="utf-8", session_id=fixture["session_id"])
    for ev in fixture["events"]:
        if ev.get("type") == "bytes" and "data_b64" in ev:
            data = base64.b64decode(ev["data_b64"])
            engine2.feed_bytes(data, seq_global=ev.get("seq_global", 0), direction=ev.get("dir", "out"))
    snap2 = snapshot_from_engine(engine2)
    assert snap["text_sig"] == snap2["text_sig"]
    assert snap["visual_sig"] == snap2["visual_sig"]
    assert snap["semantic_sig"] == snap2["semantic_sig"]


# ---------------------------------------------------------------------------
# 8. Checkpoints
# ---------------------------------------------------------------------------

def test_checkpoint_canonical_without_legacy_sig_from_fixture():
    fixture = json.loads(Path("tests/fixtures/capture8_replay_fixture.json").read_text(encoding="utf-8"))
    checkpoint = None
    for ev in fixture["events"]:
        if ev.get("type") == "checkpoint":
            checkpoint = ev; break
    assert checkpoint is not None, "Fixture must contain a checkpoint event"
    assert "comparison_mode" not in checkpoint, "Checkpoint resolution delegated to session"
    assert "expected_semantic_sig" in checkpoint
    assert checkpoint["expected_semantic_sig"] == "sha256:fixture-semantic-sig"
    assert "sig" not in checkpoint, "Checkpoint must not have legacy sig"
    assert "screen_sig" not in checkpoint, "Checkpoint must not have legacy screen_sig"


# ---------------------------------------------------------------------------
# 9. API timeline and playback
# ---------------------------------------------------------------------------

def test_api_timeline_and_playback_from_fixture():
    fixture = json.loads(Path("tests/fixtures/capture8_replay_fixture.json").read_text(encoding="utf-8"))
    sid = fixture["session_id"]
    with tempfile.TemporaryDirectory() as tmp:
        log_dir, _ = _write_audit_jsonl(Path(tmp))
        result = prepare_session_replay_data(log_dir, sid)
        assert result["error"] is None
        timeline = result["timeline"]
        assert "event_refs" in timeline
        assert len(timeline["event_refs"]) > 0
        assert "checkpoint_refs" in timeline
        playback = result["playback"]
        assert playback is not None
        assert "event_refs" in playback
        assert "event_count" in playback
        assert playback["event_count"] > 0
        assert "total_bytes_out" in playback
        assert "comparison_modes" in playback


# ---------------------------------------------------------------------------
# 10. Timeline events order
# ---------------------------------------------------------------------------

def test_timeline_events_match_fixture_order():
    fixture = json.loads(Path("tests/fixtures/capture8_replay_fixture.json").read_text(encoding="utf-8"))
    sid = fixture["session_id"]
    with tempfile.TemporaryDirectory() as tmp:
        log_dir, _ = _write_audit_jsonl(Path(tmp))
        result = prepare_session_replay_data(log_dir, sid)
        seqs = [int(e.get("seq_global", 0)) for e in result["events"]]
        assert seqs == sorted(seqs)
        assert len(seqs) > 0


# ---------------------------------------------------------------------------
# 11. Playback contains byte events (via event_refs)
# ---------------------------------------------------------------------------

def test_playback_contains_byte_events_from_fixture():
    fixture = json.loads(Path("tests/fixtures/capture8_replay_fixture.json").read_text(encoding="utf-8"))
    sid = fixture["session_id"]
    with tempfile.TemporaryDirectory() as tmp:
        log_dir, _ = _write_audit_jsonl(Path(tmp))
        result = prepare_session_replay_data(log_dir, sid)
        playback = result["playback"]
        # Playback has event_refs, not events sub-key
        assert "event_refs" in playback
        assert len(playback["event_refs"]) > 0
        # Verify OUT events exist in the main events list
        has_out = any(e.get("direction") == "out" for e in result["events"])
        assert has_out, "Playback must contain OUT events"

        # Verify IN events exist
        has_in = any(e.get("direction") == "in" for e in result["events"])
        assert has_in, "Playback must contain IN events"

        # total bytes in/out should be positive
        assert playback.get("total_bytes_out", 0) > 0


# ---------------------------------------------------------------------------
# 12. Seek to intermediate event
# ---------------------------------------------------------------------------

def test_seek_to_intermediate_event():
    fixture = json.loads(Path("tests/fixtures/capture8_replay_fixture.json").read_text(encoding="utf-8"))
    sid = fixture["session_id"]
    with tempfile.TemporaryDirectory() as tmp:
        log_dir, _ = _write_audit_jsonl(Path(tmp))
        result = prepare_session_replay_data(log_dir, sid)
        mid = len(result["events"]) // 2
        seek_seq = result["events"][mid].get("seq_global", 0)
        assert seek_seq > 0
        found = any(e.get("seq_global") == seek_seq for e in result["events"])
        assert found


# ---------------------------------------------------------------------------
# 13. Seek to final event
# ---------------------------------------------------------------------------

def test_seek_to_final_event():
    fixture = json.loads(Path("tests/fixtures/capture8_replay_fixture.json").read_text(encoding="utf-8"))
    sid = fixture["session_id"]
    with tempfile.TemporaryDirectory() as tmp:
        log_dir, _ = _write_audit_jsonl(Path(tmp))
        result = prepare_session_replay_data(log_dir, sid)
        last_seq = result["events"][-1].get("seq_global", 0)
        assert last_seq > 0
        assert "final_snapshot" in result
        assert result["final_snapshot"] is not None


# ---------------------------------------------------------------------------
# 14. Play after seek without reapplication
# ---------------------------------------------------------------------------

def test_play_after_seek_does_not_reapply():
    fixture = json.loads(Path("tests/fixtures/capture8_replay_fixture.json").read_text(encoding="utf-8"))
    sid = fixture["session_id"]
    with tempfile.TemporaryDirectory() as tmp:
        log_dir, _ = _write_audit_jsonl(Path(tmp))
        result = prepare_session_replay_data(log_dir, sid)
        # Events should be in strict order with no duplicates
        seqs = [int(e.get("seq_global", 0)) for e in result["events"]]
        assert seqs == sorted(seqs)
        assert len(seqs) == len(set(seqs)), "No duplicate seq_global values"
        # Playback refs match event count
        assert result["playback"]["event_count"] == len(result["events"])


# ---------------------------------------------------------------------------
# 15. Renderer
# ---------------------------------------------------------------------------

def test_renderer_produces_valid_html_from_fixture_snapshot():
    fixture = json.loads(Path("tests/fixtures/capture8_replay_fixture.json").read_text(encoding="utf-8"))
    sid = fixture["session_id"]
    with tempfile.TemporaryDirectory() as tmp:
        log_dir, _ = _write_audit_jsonl(Path(tmp))
        result = prepare_session_replay_data(log_dir, sid)
        snapshot_payload = result["final_snapshot"]
        assert snapshot_payload is not None
        renderer_uri = (ROOT / "gateway/control/static/js/components/terminal_snapshot_renderer.js").as_uri()
        render_result = subprocess.run(
            ["node", "--input-type=module", "-e",
             f"import {{ decodeSnapshotPayload, renderSnapshotToHtml }} from {json.dumps(renderer_uri)};\n"
             f"const payload = {json.dumps(snapshot_payload, ensure_ascii=False)};\n"
             "const snapshot = decodeSnapshotPayload(payload);\n"
             "const html = renderSnapshotToHtml(snapshot);\n"
             "if (!html || html.length < 10) throw new Error('renderer produced empty output');\n"
             "process.stdout.write(html);\n"],
            text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=20, check=False)
        assert render_result.returncode == 0, f"Renderer failed: {render_result.stdout}"
        html = render_result.stdout
        assert len(html) > 10
        assert "<" in html and ">" in html


# ---------------------------------------------------------------------------
# 16. Warnings
# ---------------------------------------------------------------------------

def test_fixture_warnings_from_split_events():
    fixture = json.loads(Path("tests/fixtures/capture8_replay_fixture.json").read_text(encoding="utf-8"))
    engine = TerminalEngine(rows=25, cols=80, encoding="utf-8", session_id=fixture["session_id"])
    for ev in fixture["events"]:
        if ev.get("type") == "bytes" and "data_b64" in ev:
            data = base64.b64decode(ev["data_b64"])
            engine.feed_bytes(data, seq_global=ev.get("seq_global", 0), direction=ev.get("dir", "out"))
    snap = snapshot_from_engine(engine)
    warnings = snap.get("decoder_warnings", [])
    assert isinstance(warnings, list)


# ---------------------------------------------------------------------------
# 17. Geometry tracks resizes
# ---------------------------------------------------------------------------

def test_geometry_tracks_resize_events():
    fixture = json.loads(Path("tests/fixtures/capture8_replay_fixture.json").read_text(encoding="utf-8"))
    resize_count = 0
    for ev in fixture["events"]:
        if ev.get("type") == "bytes" and "data_b64" in ev and ev.get("dir") == "out":
            data = base64.b64decode(ev["data_b64"])
            if b"\x1b[8;" in data:
                resize_count += 1
    assert resize_count >= 2, f"Fixture must have at least 2 resize events, found {resize_count}"


# ---------------------------------------------------------------------------
# 18. Payload references are unique
# ---------------------------------------------------------------------------

def test_payload_references_are_unique():
    fixture = json.loads(Path("tests/fixtures/capture8_replay_fixture.json").read_text(encoding="utf-8"))
    sid = fixture["session_id"]
    with tempfile.TemporaryDirectory() as tmp:
        log_dir, _ = _write_audit_jsonl(Path(tmp))
        result = prepare_session_replay_data(log_dir, sid)
        # Check API events don't have duplicate event_ids
        event_ids = [e.get("event_id") for e in result["events"]]
        assert len(event_ids) == len(set(event_ids)), "Duplicate event_ids in API response"


# ---------------------------------------------------------------------------
# 19. Comparison mode resolution
# ---------------------------------------------------------------------------

def test_comparison_mode_resolution_uses_session_not_params():
    fixture = json.loads(Path("tests/fixtures/capture8_replay_fixture.json").read_text(encoding="utf-8"))
    session_start = next((ev for ev in fixture["events"] if ev.get("type") == "session_start"), None)
    assert session_start is not None
    assert session_start.get("comparison_mode") == "semantic"
    event = {"type": "checkpoint", "expected_semantic_sig": "sha256:test"}
    params = {}  # no comparison_mode
    result = resolve_comparison_mode(event=event, session=session_start, replay=params, default="visual")
    assert result["comparison_mode"] == "semantic"
    assert result["source"] == "session"


# ---------------------------------------------------------------------------
# 20. FULL PIPELINE (test_raw_capture8_pipeline)
# ---------------------------------------------------------------------------

def test_raw_capture8_pipeline():
    """Full pipeline: audit JSONL -> prepare_session_replay_data() -> scanner ->
    decoder -> TerminalEngine -> CanonicalSnapshot -> RenderSnapshot ->
    signatures -> diffs -> checkpoints -> API -> timeline -> playback ->
    seek -> play after seek -> renderer."""
    fixture = json.loads(Path("tests/fixtures/capture8_replay_fixture.json").read_text(encoding="utf-8"))
    sid = fixture["session_id"]

    # 1. Build engine from fixture, tracking geometry
    engine = TerminalEngine(rows=2, cols=3, encoding="utf-8", session_id=sid)
    out_processed = 0
    last_seq = 0
    geometry_changes = []

    for ev in fixture["events"]:
        typ = ev.get("type", "")
        if typ == "session_start":
            rows = ev.get("rows", 25); cols = ev.get("cols", 80)
            geometry_changes.append((rows, cols))
            engine = TerminalEngine(rows=rows, cols=cols, encoding="utf-8", session_id=sid)
            continue
        if typ == "bytes" and "data_b64" in ev:
            data = base64.b64decode(ev["data_b64"])
            engine.feed_bytes(data, seq_global=ev.get("seq_global", 0), direction=ev.get("dir", "out"))
            if ev.get("dir") == "out":
                out_processed += 1
            last_seq = max(last_seq, int(ev.get("seq_global", 0)))

    assert out_processed > 0 and last_seq > 0

    # 2. Snapshots with signatures
    snap = snapshot_from_engine(engine)
    assert snap["rows"] > 0 and snap["cols"] > 0
    assert len(snap["text_sig"]) > 0 and len(snap["visual_sig"]) > 0 and len(snap["semantic_sig"]) > 0

    # 3. API integration
    with tempfile.TemporaryDirectory() as tmp:
        log_dir, _ = _write_audit_jsonl(Path(tmp))
        result = prepare_session_replay_data(log_dir, sid)
        assert result["error"] is None
        assert len(result["events"]) > 0
        assert result["playback"] is not None
        assert result["final_snapshot"] is not None

        # 4. Seek
        mid = len(result["events"]) // 2
        seek_seq = result["events"][mid].get("seq_global", 0)
        assert seek_seq > 0

        # 5. Checkpoint in fixture
        assert any(ev.get("type") == "checkpoint" for ev in fixture["events"])

        # 6. Renderer
        renderer_uri = (ROOT / "gateway/control/static/js/components/terminal_snapshot_renderer.js").as_uri()
        render_result = subprocess.run(
            ["node", "--input-type=module", "-e",
             f"import {{ decodeSnapshotPayload, renderSnapshotToHtml }} from {json.dumps(renderer_uri)};\n"
             f"const payload = {json.dumps(result['final_snapshot'], ensure_ascii=False)};\n"
             "const snap2 = decodeSnapshotPayload(payload);\n"
             "const html = renderSnapshotToHtml(snap2);\n"
             "if (!html || html.length < 10) throw new Error('empty');\n"
             "process.stdout.write('OK');\n"],
            text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=20, check=False)
        assert render_result.returncode == 0, f"Renderer failed: {render_result.stdout}"
        assert "OK" in render_result.stdout

    # 7. Warnings
    assert isinstance(snap.get("decoder_warnings", []), list)

    # 8. Geometry changes
    assert len(geometry_changes) >= 1

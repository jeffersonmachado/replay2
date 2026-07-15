from __future__ import annotations

import json
from pathlib import Path

from dakota_gateway.gateway import GatewayConfig, TerminalGateway
from dakota_gateway.screen import TerminalScreenState


def _read_events(log_dir: Path) -> list[dict]:
    events: list[dict] = []
    for path in sorted(log_dir.glob("audit-*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                events.append(json.loads(line))
    return events


def test_gateway_real_out_persists_canonical_contract_after_split_resize(tmp_path):
    source = Path(__file__).read_text(encoding="utf-8")
    assert ("Audit" + "Event(") not in source

    cfg = GatewayConfig(
        log_dir=str(tmp_path),
        hmac_key=b"gateway-contract-key",
        rows=2,
        cols=3,
        term="xterm",
        encoding="utf-8",
        geometry_source="test_gateway_config",
    )
    gateway = TerminalGateway(cfg)
    screen_state = TerminalScreenState(rows=2, cols=3, encoding="utf-8", session_id=gateway.session_id)

    gateway._append_session_start(gateway_endpoint="test-endpoint", command="")
    gateway._append_out_bytes_event(data=b"A", screen_state=screen_state)
    gateway._append_out_bytes_event(data=b"\x1b[8;", screen_state=screen_state)
    gateway._append_out_bytes_event(data=b"3;4tB", screen_state=screen_state)
    gateway._append_session_end()
    gateway.writer.close()

    events = _read_events(tmp_path)
    out_events = [ev for ev in events if ev.get("type") == "bytes" and ev.get("dir") == "out"]
    assert len(out_events) == 3

    resized = out_events[-1]
    assert resized["rows"] == 3
    assert resized["cols"] == 4
    assert resized["comparison_mode"] == "visual"
    assert resized["timestamp_ms"] == resized["ts_ms"]
    assert resized["seq_global"] > 0
    assert resized["text_sig"].startswith("sha256:")
    assert resized["visual_sig"].startswith("sha256:")
    assert resized["semantic_sig"].startswith("sha256:")
    assert resized["engine_version"]
    assert resized["snapshot_version"] == "1.0"
    assert resized["signature_version"] == "1.0"
    assert "expected_text_sig" not in resized or resized["expected_text_sig"] is None
    assert "expected_visual_sig" not in resized or resized["expected_visual_sig"] is None
    assert "expected_semantic_sig" not in resized or resized["expected_semantic_sig"] is None

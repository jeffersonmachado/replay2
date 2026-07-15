from __future__ import annotations

import base64
import json

from control.services.session_replay_service import build_reference_payload
from control.services.session_replay_service import prepare_session_replay_data


def test_reference_payload_serializes_each_diff_once_and_uses_event_refs():
    diff = {"version": 1, "changes": [], "event_id": "e1"}
    payload = build_reference_payload(
        initial_snapshot={"version": 1},
        events=[{"event_id": "e1", "type": "bytes", "direction": "out", "diff": diff, "semantic_sig": "sha256:" + "0" * 64}],
        checkpoints=[{"checkpoint_id": "c0", "seq_global": 0}],
        final_snapshot={"version": 1},
    )
    assert payload["events"][0]["diff"] is diff
    assert payload["timeline"]["event_refs"] == ["e1"]
    assert payload["playback"]["event_refs"] == ["e1"]
    assert "events" not in payload["timeline"]
    assert "events" not in payload["playback"]


def test_prepare_session_replay_data_serializes_real_diffs_once(tmp_path):
    session_id = "payload-real"
    events = [
        {"type": "session_start", "session_id": session_id, "seq_global": 1, "seq_session": 1, "ts_ms": 1000, "rows": 2, "cols": 4, "encoding": "utf-8"},
        {"type": "bytes", "session_id": session_id, "seq_global": 2, "seq_session": 2, "ts_ms": 1100, "dir": "out", "n": 1, "data_b64": base64.b64encode(b"A").decode()},
        {"type": "bytes", "session_id": session_id, "seq_global": 3, "seq_session": 3, "ts_ms": 1200, "dir": "out", "n": 1, "data_b64": base64.b64encode(b"B").decode()},
        {"type": "session_end", "session_id": session_id, "seq_global": 4, "seq_session": 4, "ts_ms": 1300},
    ]
    audit = tmp_path / "audit-0001.jsonl"
    audit.write_text("\n".join(json.dumps(ev) for ev in events), encoding="utf-8")

    payload = prepare_session_replay_data(str(tmp_path), session_id)
    wire_payload = json.loads(json.dumps(payload))
    serialized = json.dumps(wire_payload, sort_keys=True)

    assert payload["error"] is None
    assert len(wire_payload["events"]) == 2
    assert len(wire_payload["timeline"]["event_refs"]) == 2
    assert len(wire_payload["playback"]["event_refs"]) == 2
    assert "events" not in wire_payload["playback"]
    assert "events" not in wire_payload["timeline"]
    assert serialized.count('"changes"') == 2
    assert serialized.count('"base_seq_global"') == 2

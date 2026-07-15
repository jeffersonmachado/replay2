from __future__ import annotations

import base64
import json

from control.services.session_replay_service import prepare_session_replay_data


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def test_final_snapshot_seq_global_tracks_last_processed_out_not_in_or_session_end(tmp_path):
    session_id = "seq-contract"
    events = [
        {"type": "session_start", "session_id": session_id, "seq_global": 1, "seq_session": 1, "ts_ms": 1000, "rows": 2, "cols": 3, "encoding": "utf-8"},
        {"type": "bytes", "session_id": session_id, "seq_global": 2, "seq_session": 2, "ts_ms": 1010, "dir": "out", "n": 1, "data_b64": _b64(b"A")},
        {"type": "bytes", "session_id": session_id, "seq_global": 7, "seq_session": 3, "ts_ms": 1020, "dir": "in", "n": 1, "data_b64": _b64(b"x")},
        {"type": "bytes", "session_id": session_id, "seq_global": 10, "seq_session": 4, "ts_ms": 1030, "dir": "out", "n": 1, "data_b64": _b64(b"B")},
        {"type": "session_end", "session_id": session_id, "seq_global": 11, "seq_session": 5, "ts_ms": 1040},
    ]
    (tmp_path / "audit-seq.part001.jsonl").write_text(
        "\n".join(json.dumps(ev) for ev in events),
        encoding="utf-8",
    )

    replay = prepare_session_replay_data(str(tmp_path), session_id)

    assert replay["error"] is None
    assert replay["initial_snapshot"]["seq_global"] == 0
    assert replay["final_snapshot"]["seq_global"] == 10
    out_diffs = [ev["diff"] for ev in replay["events"] if ev.get("direction") == "out" and ev.get("diff")]
    assert [(d["base_seq_global"], d["seq_global"]) for d in out_diffs] == [(0, 2), (2, 10)]
    assert replay["checkpoints"][-1]["seq_global"] == 11
    assert replay["checkpoints"][-1]["render_snapshot"]["seq_global"] == 10

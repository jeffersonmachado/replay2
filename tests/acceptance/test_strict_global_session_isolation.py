from __future__ import annotations

from dataclasses import replace

from dakota_gateway.replay import ReplayConfig, SessionReplayState, _session_config_from_event


def test_session_start_derives_independent_replay_state_from_event_metadata():
    cfg = ReplayConfig(log_dir="/tmp/replay2", target_host="example.invalid", rows=25, cols=80, term="xterm", encoding="utf-8", comparison_mode="visual")
    a = _session_config_from_event(cfg, {"session_id": "A", "rows": 25, "cols": 80, "term": "xterm", "encoding": "utf-8", "comparison_mode": "visual"})
    b = _session_config_from_event(cfg, {"session_id": "B", "rows": 30, "cols": 132, "term": "xterm-256color", "encoding": "cp850", "comparison_mode": "hybrid"})
    assert a is not cfg
    assert b is not cfg
    assert (a.rows, a.cols, a.term, a.encoding, a.comparison_mode) == (25, 80, "xterm", "utf-8", "visual")
    assert (b.rows, b.cols, b.term, b.encoding, b.comparison_mode) == (30, 132, "xterm-256color", "cp850", "hybrid")
    assert cfg.rows == 25 and cfg.cols == 80 and cfg.encoding == "utf-8"


def test_session_replay_state_has_required_contract_fields():
    cfg = ReplayConfig(log_dir="/tmp/replay2", target_host="example.invalid")
    state = SessionReplayState(session_id="A", config=replace(cfg))
    for field in [
        "session_id", "rows", "cols", "term", "encoding", "comparison_mode",
        "engine", "scanner", "decoder", "warnings", "checkpoints",
        "current_seq_global", "last_out_seq_global", "last_snapshot", "versions",
    ]:
        assert hasattr(state, field), field
    assert state.rows == 25
    assert state.cols == 80

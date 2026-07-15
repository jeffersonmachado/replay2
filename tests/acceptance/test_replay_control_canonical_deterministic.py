from __future__ import annotations

import base64
import inspect
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from dakota_gateway.replay import ReplayConfig
from dakota_gateway import replay_control
from dakota_gateway.replay_control import replay_parallel_sessions_controlled


class _Selector:
    def register(self, *args, **kwargs):
        return None

    def select(self, timeout=None):
        return []

    def close(self):
        return None


class _Session:
    instances: list["_Session"] = []
    snapshots_by_session: dict[str, list[dict]] = {}

    def __init__(self, cfg, sid, target_user_override=None):
        self.master_fd = 0
        self.session_id = sid
        self.last_out_ms = 0
        self.screen_state = object()
        self._writes: list[bytes] = []
        self._snapshots = list(self.snapshots_by_session.get(sid, []))
        self.instances.append(self)

    def canonical_snapshot_now(self) -> dict:
        if len(self._snapshots) > 1:
            return self._snapshots.pop(0)
        if self._snapshots:
            return self._snapshots[0]
        return {"text_sig": "", "visual_sig": "", "semantic_sig": "", "screen_sig": ""}

    def read_out(self):
        return b""

    def write_in(self, data: bytes):
        self._writes.append(bytes(data))

    def close(self):
        return None


def _log_dir(tmp_path: Path, event: dict) -> str:
    entries = [
        {"type": "session_start", "session_id": "s1", "seq_global": 1, "seq_session": 1, "rows": 2, "cols": 3},
        event,
    ]
    (tmp_path / "audit-control.part001.jsonl").write_text(
        "\n".join(json.dumps(item) for item in entries),
        encoding="utf-8",
    )
    return str(tmp_path)


def _event(mode: str, sig: str) -> dict:
    key = {
        "visual": "expected_visual_sig",
        "text": "expected_text_sig",
        "semantic": "expected_semantic_sig",
    }.get(mode, "expected_visual_sig")
    event = {
        "type": "deterministic_input",
        "session_id": "s1",
        "seq_global": 2,
        "seq_session": 2,
        "key_b64": base64.b64encode(b"A").decode("ascii"),
        "comparison_mode": mode,
    }
    if mode == "hybrid":
        event.update(
            expected_visual_sig=sig,
            expected_text_sig="sha256:text-ok",
            expected_semantic_sig="sha256:semantic-ok",
        )
    else:
        event[key] = sig
    return event


def _observed() -> dict:
    return {
        "visual_sig": "sha256:visual-ok",
        "text_sig": "sha256:text-ok",
        "semantic_sig": "sha256:semantic-ok",
        "screen_sig": "",
    }


@pytest.mark.parametrize(
    ("mode", "wrong_sig", "right_sig"),
    [
        ("visual", "sha256:visual-wrong", "sha256:visual-ok"),
        ("text", "sha256:text-wrong", "sha256:text-ok"),
        ("semantic", "sha256:semantic-wrong", "sha256:semantic-ok"),
        ("hybrid", "sha256:visual-wrong", "sha256:visual-ok"),
    ],
)
def test_controlled_replay_canonical_only_checkpoint_blocks_then_allows(tmp_path, mode, wrong_sig, right_sig):
    failures = []

    wrong_dir = tmp_path / "wrong"
    wrong_dir.mkdir()
    wrong_log = _log_dir(wrong_dir, _event(mode, wrong_sig))
    _Session.instances = []
    _Session.snapshots_by_session = {"s1": [_observed()]}
    cfg = ReplayConfig(
        log_dir=wrong_log,
        target_host="local",
        input_mode="deterministic",
        on_deterministic_mismatch="skip",
        comparison_mode=mode,
        checkpoint_quiet_ms=0,
    )
    with patch.object(replay_control, "_TargetSession", _Session), patch.object(replay_control.selectors, "DefaultSelector", _Selector):
        replay_parallel_sessions_controlled(
            cfg,
            params={"input_mode": "deterministic", "on_deterministic_mismatch": "skip", "comparison_mode": mode},
            should_pause_or_cancel=lambda: None,
            on_progress=lambda *args: None,
            on_failure=failures.append,
            checkpoint_timeout_ms=20,
        )
    assert _Session.instances[0]._writes == []
    assert failures
    assert failures[-1]["evidence"]["match"]["matched"] is False

    right_dir = tmp_path / "right"
    right_dir.mkdir()
    right_log = _log_dir(right_dir, _event(mode, right_sig))
    _Session.instances = []
    _Session.snapshots_by_session = {"s1": [_observed()]}
    cfg.log_dir = right_log
    with patch.object(replay_control, "_TargetSession", _Session), patch.object(replay_control.selectors, "DefaultSelector", _Selector):
        replay_parallel_sessions_controlled(
            cfg,
            params={"input_mode": "deterministic", "on_deterministic_mismatch": "skip", "comparison_mode": mode},
            should_pause_or_cancel=lambda: None,
            on_progress=lambda *args: None,
            on_failure=failures.append,
            checkpoint_timeout_ms=20,
        )
    assert _Session.instances[0]._writes == [b"A"]


def test_controlled_replay_decision_is_not_screen_sig_only():
    source = inspect.getsource(replay_control)
    assert "_event_requires_deterministic_comparison" in source
    assert source.count("and _event_requires_deterministic_comparison") == 3

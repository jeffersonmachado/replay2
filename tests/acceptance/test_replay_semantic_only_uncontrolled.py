from __future__ import annotations

import base64
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from dakota_gateway import replay as replay_mod
from dakota_gateway.replay import ReplayConfig, replay_parallel_sessions, replay_strict_global


class _Selector:
    def register(self, *args, **kwargs):
        return None

    def select(self, timeout=None):
        return []

    def close(self):
        return None


class _Session:
    instances: list["_Session"] = []

    def __init__(self, cfg, sid, target_user_override=None):
        self.master_fd = 0
        self.session_id = sid
        self.last_out_ms = 0
        self._writes: list[bytes] = []
        self.screen_state = object()
        self.instances.append(self)

    def canonical_snapshot_now(self):
        return {
            "text_sig": "sha256:text-ok",
            "visual_sig": "sha256:visual-ok",
            "semantic_sig": "sha256:semantic-ok",
            "screen_sig": "",
        }

    def read_out(self):
        return b""

    def write_in(self, data: bytes):
        self._writes.append(bytes(data))

    def close(self):
        return None


def _log_dir(tmp_path: Path, semantic_sig: str) -> str:
    events = [
        {
            "type": "session_start",
            "session_id": "s1",
            "seq_global": 1,
            "seq_session": 1,
            "rows": 2,
            "cols": 3,
            "comparison_mode": "semantic",
        },
        {
            "type": "deterministic_input",
            "session_id": "s1",
            "seq_global": 2,
            "seq_session": 2,
            "expected_semantic_sig": semantic_sig,
            "key_b64": base64.b64encode(b"Z").decode("ascii"),
        },
    ]
    (tmp_path / "audit-semantic.part001.jsonl").write_text(
        "\n".join(json.dumps(event) for event in events),
        encoding="utf-8",
    )
    return str(tmp_path)


@pytest.mark.parametrize("runner", [replay_strict_global, replay_parallel_sessions])
def test_uncontrolled_replay_semantic_only_blocks_then_allows_without_screen_sig(tmp_path, runner):
    wrong = tmp_path / "wrong"
    wrong.mkdir()
    cfg = ReplayConfig(
        log_dir=_log_dir(wrong, "sha256:wrong"),
        target_host="local",
        input_mode="deterministic",
        on_deterministic_mismatch="skip",
        comparison_mode="visual",
        checkpoint_quiet_ms=0,
        checkpoint_timeout_ms=20,
    )
    _Session.instances = []
    with patch.object(replay_mod, "_TargetSession", _Session), patch.object(replay_mod.selectors, "DefaultSelector", _Selector):
        runner(cfg)
    assert _Session.instances[0]._writes == []

    right = tmp_path / "right"
    right.mkdir()
    cfg.log_dir = _log_dir(right, "sha256:semantic-ok")
    _Session.instances = []
    with patch.object(replay_mod, "_TargetSession", _Session), patch.object(replay_mod.selectors, "DefaultSelector", _Selector):
        runner(cfg)
    assert _Session.instances[0]._writes == [b"Z"]

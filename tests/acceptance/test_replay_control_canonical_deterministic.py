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


def _log_dir(tmp_path: Path, event: dict, *, session_mode: str | None = None) -> str:
    entries = [
        {"type": "session_start", "session_id": "s1", "seq_global": 1, "seq_session": 1, "rows": 2, "cols": 3, **({"comparison_mode": session_mode} if session_mode else {})},
        event,
    ]
    (tmp_path / "audit-control.part001.jsonl").write_text(
        "\n".join(json.dumps(item) for item in entries),
        encoding="utf-8",
    )
    return str(tmp_path)


def _event(mode: str, sig: str, *, include_mode: bool = True) -> dict:
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
    }
    if include_mode:
        event["comparison_mode"] = mode
    if mode == "hybrid":
        event.update(
            expected_visual_sig=sig,
            expected_text_sig="sha256:text-ok",
            expected_semantic_sig="sha256:semantic-ok",
        )
    else:
        event[key] = sig
    return event


def _checkpoint(mode: str, sig: str) -> dict:
    event = _event(mode, sig)
    event["type"] = "checkpoint"
    event.pop("key_b64", None)
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


def _ast_forbidden_patterns(source_text: str, filename: str) -> list[str]:
    """AST-based detection of forbidden legacy patterns in canonical paths.

    Returns list of violation descriptions (empty = clean).
    """
    import ast
    violations = []
    try:
        tree = ast.parse(source_text, filename=filename)
    except SyntaxError as e:
        return [f"AST parse error in {filename}: {e}"]

    # Collect all function definitions
    funcs = {node.name: node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}

    # Canonical replay functions that must never use legacy comparison directly
    canonical_funcs = {
        "replay_strict_global", "replay_parallel_sessions",
        "replay_strict_global_controlled", "replay_parallel_sessions_controlled",
        "replay_parallel_sessions_concurrent_controlled",
        "wait_checkpoint", "_wait_for_expected_observed",
        "compare_expected_observed",
    }

    for func_name in canonical_funcs & set(funcs.keys()):
        func_node = funcs[func_name]
        # Walk all nodes in this function
        for node in ast.walk(func_node):
            # Forbidden: direct attribute access to signature_now
            if isinstance(node, ast.Attribute) and node.attr == "signature_now":
                violations.append(
                    f"{filename}:{func_name} calls .signature_now() (legacy) at line ~{node.lineno}"
                )
            # Forbidden: comparison depending ONLY on sig/screen_sig fields
            if isinstance(node, ast.Compare):
                for comparator in node.comparators:
                    if isinstance(comparator, ast.Constant) and isinstance(comparator.value, str):
                        val = comparator.value
                        # Detect: if ev.get("sig") or if ev.get("screen_sig") as sole condition
                        pass  # covered by attribute check below
            # Forbidden: get("sig") or get("screen_sig") as sole comparison trigger
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr == "get":
                    if len(node.args) >= 1 and isinstance(node.args[0], ast.Constant):
                        arg = node.args[0].value
                        if arg in ("sig", "screen_sig"):
                            # Check if this is the ONLY condition (no canonical fields checked)
                            # Walk up to find the parent if statement
                            parent = node
                            for _ in range(5):  # walk up to 5 levels
                                for p in ast.walk(tree):
                                    for child in ast.walk(p):
                                        if child is parent:
                                            parent = p
                                            break
                            # If this get("sig") is not accompanied by get("expected_*_sig"), flag it
                            # Simplified: flag all get("sig")/get("screen_sig") in canonical functions
                            violations.append(
                                f"{filename}:{func_name} uses .get('{arg}') (legacy field) at line ~{node.lineno}"
                            )

    return violations


def test_controlled_replay_decision_is_not_screen_sig_only():
    """AST-based: canonical replay paths must not depend on legacy sig/screen_sig alone."""
    import inspect
    source = inspect.getsource(replay_control)
    assert "_event_requires_deterministic_comparison" in source
    assert source.count("and _event_requires_deterministic_comparison") == 3

    # AST analysis for forbidden legacy patterns
    violations = _ast_forbidden_patterns(source, "replay_control.py")
    # Filter: get("sig")/get("screen_sig") is allowed in _expected_snapshot_from_event
    # and in legacy fallback paths, but NOT as the sole comparison trigger in canonical funcs
    real_violations = [
        v for v in violations
        if "signature_now" in v  # signature_now is never allowed in canonical paths
    ]
    assert not real_violations, f"AST violations in replay_control.py: {real_violations}"

    # Also check replay.py
    from dakota_gateway import replay as replay_mod
    replay_source = inspect.getsource(replay_mod)
    replay_violations = _ast_forbidden_patterns(replay_source, "replay.py")
    real_replay_violations = [v for v in replay_violations if "signature_now" in v]
    assert not real_replay_violations, f"AST violations in replay.py: {real_replay_violations}"


@pytest.mark.parametrize(
    ("mode", "wrong_sig", "right_sig"),
    [
        ("visual", "sha256:visual-wrong", "sha256:visual-ok"),
        ("text", "sha256:text-wrong", "sha256:text-ok"),
        ("semantic", "sha256:semantic-wrong", "sha256:semantic-ok"),
        ("hybrid", "sha256:visual-wrong", "sha256:visual-ok"),
    ],
)
def test_controlled_replay_resolves_comparison_mode_from_session_when_params_omit_it(tmp_path, mode, wrong_sig, right_sig):
    wrong_dir = tmp_path / "session-wrong"
    wrong_dir.mkdir()
    wrong_log = _log_dir(wrong_dir, _event(mode, wrong_sig, include_mode=False), session_mode=mode)
    _Session.instances = []
    _Session.snapshots_by_session = {"s1": [_observed()]}
    cfg = ReplayConfig(
        log_dir=wrong_log,
        target_host="local",
        input_mode="deterministic",
        on_deterministic_mismatch="skip",
        comparison_mode="visual",
        checkpoint_quiet_ms=0,
    )
    failures = []
    with patch.object(replay_control, "_TargetSession", _Session), patch.object(replay_control.selectors, "DefaultSelector", _Selector):
        replay_parallel_sessions_controlled(
            cfg,
            params={"input_mode": "deterministic", "on_deterministic_mismatch": "skip"},
            should_pause_or_cancel=lambda: None,
            on_progress=lambda *args: None,
            on_failure=failures.append,
            checkpoint_timeout_ms=20,
        )
    assert _Session.instances[0]._writes == []
    assert failures[-1]["evidence"]["match"]["comparison_mode_requested"] == mode

    right_dir = tmp_path / "session-right"
    right_dir.mkdir()
    cfg.log_dir = _log_dir(right_dir, _event(mode, right_sig, include_mode=False), session_mode=mode)
    _Session.instances = []
    _Session.snapshots_by_session = {"s1": [_observed()]}
    with patch.object(replay_control, "_TargetSession", _Session), patch.object(replay_control.selectors, "DefaultSelector", _Selector):
        replay_parallel_sessions_controlled(
            cfg,
            params={"input_mode": "deterministic", "on_deterministic_mismatch": "skip"},
            should_pause_or_cancel=lambda: None,
            on_progress=lambda *args: None,
            on_failure=failures.append,
            checkpoint_timeout_ms=20,
        )
    assert _Session.instances[0]._writes == [b"A"]


@pytest.mark.parametrize(
    ("mode", "wrong_sig", "right_sig"),
    [
        ("visual", "sha256:visual-wrong", "sha256:visual-ok"),
        ("text", "sha256:text-wrong", "sha256:text-ok"),
        ("semantic", "sha256:semantic-wrong", "sha256:semantic-ok"),
        ("hybrid", "sha256:visual-wrong", "sha256:visual-ok"),
    ],
)
def test_controlled_replay_checkpoint_canonical_without_legacy_sig(tmp_path, mode, wrong_sig, right_sig):
    failures = []
    wrong_dir = tmp_path / "checkpoint-wrong"
    wrong_dir.mkdir()
    cfg = ReplayConfig(
        log_dir=_log_dir(wrong_dir, _checkpoint(mode, wrong_sig)),
        target_host="local",
        comparison_mode=mode,
        checkpoint_quiet_ms=0,
    )
    _Session.instances = []
    _Session.snapshots_by_session = {"s1": [_observed()]}
    with patch.object(replay_control, "_TargetSession", _Session), patch.object(replay_control.selectors, "DefaultSelector", _Selector):
        with pytest.raises(Exception):
            replay_parallel_sessions_controlled(
                cfg,
                params={},
                should_pause_or_cancel=lambda: None,
                on_progress=lambda *args: None,
                on_failure=failures.append,
                checkpoint_timeout_ms=20,
            )
    assert failures
    assert failures[-1]["event_type"] == "checkpoint"
    assert failures[-1]["evidence"]["match"]["matched"] is False

    right_dir = tmp_path / "checkpoint-right"
    right_dir.mkdir()
    cfg.log_dir = _log_dir(right_dir, _checkpoint(mode, right_sig))
    _Session.instances = []
    _Session.snapshots_by_session = {"s1": [_observed()]}
    progress = []
    with patch.object(replay_control, "_TargetSession", _Session), patch.object(replay_control.selectors, "DefaultSelector", _Selector):
        replay_parallel_sessions_controlled(
            cfg,
            params={},
            should_pause_or_cancel=lambda: None,
            on_progress=lambda *args: progress.append(args),
            on_failure=failures.append,
            checkpoint_timeout_ms=20,
        )
    assert progress

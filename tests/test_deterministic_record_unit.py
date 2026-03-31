#!/usr/bin/env python3
from __future__ import annotations

import json
import base64
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
GATEWAY_DIR = ROOT / "gateway"
sys.path.insert(0, str(GATEWAY_DIR))

from dakota_gateway.gateway import GatewayConfig, TerminalGateway, _StableScreenState
from dakota_gateway.replay import ReplayConfig, ReplayError, replay_parallel_sessions
from dakota_gateway.screen import analyze_input_chunk, build_screen_snapshot, build_screen_snapshot_from_bytes, split_input_for_deterministic_record


class _FakeSelector:
    def register(self, *args, **kwargs):
        return None

    def select(self, timeout=None):
        return []

    def close(self):
        return None


class DeterministicRecordUnitTests(unittest.TestCase):
    def _gateway(self) -> TerminalGateway:
        return TerminalGateway(
            GatewayConfig(
                log_dir="/tmp/dakota-det-record",
                hmac_key=b"x" * 32,
            )
        )

    def test_gateway_input_events_prefer_stable_snapshot_and_keep_order(self):
        gw = self._gateway()
        stable_snapshot = build_screen_snapshot_from_bytes(b"+ MENU +\nOpcao: 1\n")
        stable = _StableScreenState(snapshot=stable_snapshot, ts_ms=1_700_000_000_000, source="stable")

        events = gw._build_audit_events_for_input(
            data=b"A",
            screen_buf=b"ruido transitivo",
            stable_state=stable,
            now_ms=1_700_000_000_250,
        )

        self.assertEqual([ev.type for ev in events], ["deterministic_input", "bytes"])
        self.assertEqual(events[0].screen_source, "stable")
        self.assertEqual(events[0].screen_snapshot_age_ms, 250)
        self.assertIn("MENU", events[0].screen_sample or "")
        self.assertEqual(base64.b64decode(events[0].screen_raw_b64 or ""), stable_snapshot.raw_bytes)
        self.assertEqual(events[1].dir, "in")
        gw.writer.close()

    def test_gateway_input_events_fallback_to_buffer_when_no_stable_screen(self):
        gw = self._gateway()
        events = gw._build_audit_events_for_input(
            data=b"\x1b[A",
            screen_buf=b"+ TELA +\nAcao: seta\n",
            stable_state=_StableScreenState(),
            now_ms=1_700_000_000_500,
        )

        self.assertEqual(events[0].screen_source, "buffer")
        self.assertEqual(events[0].key_kind, "ansi_sequence")
        self.assertEqual(events[0].contains_escape, True)
        gw.writer.close()

    def test_gateway_input_events_fallback_to_empty_without_raw_snapshot(self):
        gw = self._gateway()
        events = gw._build_audit_events_for_input(
            data=b"\r",
            screen_buf=b"",
            stable_state=_StableScreenState(),
            now_ms=1_700_000_000_900,
        )

        self.assertEqual(events[0].screen_source, "empty")
        self.assertIsNone(events[0].screen_raw_b64)
        gw.writer.close()

    def test_gateway_input_events_keep_snapshot_raw_sig_sample_hash_coherent(self):
        gw = self._gateway()
        stable_snapshot = build_screen_snapshot_from_bytes(b"+ MENU +\nOpcao: 1\n")
        event = gw._build_deterministic_events_for_input(
            data=b"A",
            screen_buf=b"outra tela",
            stable_state=_StableScreenState(snapshot=stable_snapshot, ts_ms=1_700_000_000_000, source="stable"),
            now_ms=1_700_000_000_050,
        )[0]

        rebuilt = build_screen_snapshot_from_bytes(base64.b64decode(event.screen_raw_b64 or ""))
        self.assertEqual(rebuilt.screen_sig, event.screen_sig)
        self.assertEqual(rebuilt.screen_sample, event.screen_sample)
        self.assertEqual(rebuilt.norm_sha256, event.norm_sha256)
        self.assertEqual(rebuilt.norm_len, event.norm_len)
        gw.writer.close()

    def test_input_analysis_and_safe_split_cover_basic_cases(self):
        command_parts = split_input_for_deterministic_record(b"abc\r")
        printable_parts = split_input_for_deterministic_record(b"xyz")
        paste_action = analyze_input_chunk(b"linha1\nlinha2\nlinha3")

        self.assertEqual([item.key_text for item in command_parts], ["a", "b", "c", "\\r"])
        self.assertEqual(command_parts[-1].key_kind, "enter")
        self.assertEqual([item.key_kind for item in printable_parts], ["printable", "printable", "printable"])
        self.assertTrue(paste_action.is_probable_paste)
        self.assertIn(paste_action.key_kind, {"paste", "bytes"})


class _FakeTargetSession:
    instances: list["_FakeTargetSession"] = []
    signatures_by_session: dict[str, list[str]] = {}

    def __init__(self, cfg, session_id, *, target_user_override=None):
        self.cfg = cfg
        self.session_id = session_id
        self.target_user_override = target_user_override
        self.master_fd = len(_FakeTargetSession.instances) + 10
        self.last_out_ms = int(time.time() * 1000) - 5_000
        self.screen_buf = b""
        self._writes: list[bytes] = []
        self._signatures = list(_FakeTargetSession.signatures_by_session.get(session_id, [])) or [""]
        _FakeTargetSession.instances.append(self)

    def write_in(self, data: bytes):
        self._writes.append(data)

    def read_out(self) -> bytes:
        self.last_out_ms = int(time.time() * 1000) - 5_000
        return b""

    def signature_now(self) -> str:
        if len(self._signatures) > 1:
            return self._signatures.pop(0)
        return self._signatures[0]

    def close(self):
        return None


class DeterministicReplayUnitTests(unittest.TestCase):
    def setUp(self):
        _FakeTargetSession.instances = []
        _FakeTargetSession.signatures_by_session = {}

    def _write_log(self, entries: list[dict]) -> str:
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        log_file = Path(tmpdir.name) / "audit-20260330.part001.jsonl"
        log_file.write_text("\n".join(json.dumps(item) for item in entries), encoding="utf-8")
        return tmpdir.name

    def test_replay_parallel_sessions_deterministic_waits_for_signature_and_writes_input(self):
        log_dir = self._write_log(
            [
                {"type": "deterministic_input", "session_id": "s1", "seq_global": 1, "screen_sig": "SIG:MENU", "key_b64": "QQ=="},
            ]
        )
        _FakeTargetSession.signatures_by_session = {"s1": ["SIG:MENU"]}
        cfg = ReplayConfig(log_dir=log_dir, target_host="legacy", input_mode="deterministic")

        with patch("dakota_gateway.replay._TargetSession", _FakeTargetSession), patch("dakota_gateway.replay.selectors.DefaultSelector", _FakeSelector):
            replay_parallel_sessions(cfg)

        self.assertEqual(_FakeTargetSession.instances[0]._writes, [b"A"])

    def test_replay_parallel_sessions_deterministic_skip_mode_skips_mismatched_action(self):
        log_dir = self._write_log(
            [
                {"type": "deterministic_input", "session_id": "s1", "seq_global": 1, "screen_sig": "SIG:MENU", "key_b64": "QQ=="},
            ]
        )
        _FakeTargetSession.signatures_by_session = {"s1": ["SIG:OTHER"]}
        cfg = ReplayConfig(
            log_dir=log_dir,
            target_host="legacy",
            input_mode="deterministic",
            on_deterministic_mismatch="skip",
            checkpoint_timeout_ms=20,
        )

        with patch("dakota_gateway.replay._TargetSession", _FakeTargetSession), patch("dakota_gateway.replay.selectors.DefaultSelector", _FakeSelector):
            replay_parallel_sessions(cfg)

        self.assertEqual(_FakeTargetSession.instances[0]._writes, [])

    def test_replay_parallel_sessions_deterministic_fail_fast_raises_on_mismatch(self):
        log_dir = self._write_log(
            [
                {"type": "deterministic_input", "session_id": "s1", "seq_global": 1, "screen_sig": "SIG:MENU", "key_b64": "QQ=="},
            ]
        )
        _FakeTargetSession.signatures_by_session = {"s1": ["SIG:OTHER"]}
        cfg = ReplayConfig(
            log_dir=log_dir,
            target_host="legacy",
            input_mode="deterministic",
            on_deterministic_mismatch="fail-fast",
            checkpoint_timeout_ms=20,
        )

        with patch("dakota_gateway.replay._TargetSession", _FakeTargetSession), patch("dakota_gateway.replay.selectors.DefaultSelector", _FakeSelector):
            with self.assertRaises(ReplayError):
                replay_parallel_sessions(cfg)


if __name__ == "__main__":
    unittest.main(verbosity=2)

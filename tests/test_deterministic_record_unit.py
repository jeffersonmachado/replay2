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
from dakota_gateway.screen import (
    TerminalScreenState,
    analyze_input_chunk,
    build_screen_snapshot,
    build_screen_snapshot_from_bytes,
    split_input_for_deterministic_record,
)


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

    def test_backend_snapshot_uses_visible_terminal_state_not_byte_history(self):
        snap = build_screen_snapshot_from_bytes(b"ABC\rX", rows=1, cols=3)
        self.assertEqual(snap.raw_text.split("\n")[0], "XBC")
        self.assertNotIn("ABC\rX", snap.norm_text)

    def test_backend_snapshot_erase_and_encoding_are_canonical(self):
        erased = build_screen_snapshot_from_bytes(b"ABC\x1b[2K", rows=1, cols=3)
        self.assertEqual(erased.raw_text, "   ")
        cp850 = build_screen_snapshot_from_bytes(b"\x82", encoding="cp850", rows=1, cols=3)
        self.assertIn("é", cp850.raw_text)

    def test_backend_snapshot_handles_dec_graphics_shift_and_osc(self):
        top = build_screen_snapshot_from_bytes(b"\x1b(0lqqk\x1b(B", rows=1, cols=4)
        self.assertEqual(top.raw_text, "┌──┐")

        g1 = build_screen_snapshot_from_bytes(b"\x1b)0\x0elq\x0fAB", rows=1, cols=4)
        self.assertEqual(g1.raw_text, "┌─AB")

        osc = build_screen_snapshot_from_bytes(b"A\x1b]0;ignored title\x07B", rows=1, cols=4)
        self.assertEqual(osc.raw_text[:2], "AB")

    def test_backend_snapshot_handles_cursor_save_restore_and_ri(self):
        saved = build_screen_snapshot_from_bytes(b"A\x1b7BCD\x1b8Z", rows=1, cols=4)
        self.assertEqual(saved.raw_text, "AZCD")

        csi_saved = build_screen_snapshot_from_bytes(b"A\x1b[sBCD\x1b[uZ", rows=1, cols=4)
        self.assertEqual(csi_saved.raw_text, "AZCD")

        ri = build_screen_snapshot_from_bytes(b"A\x1bMZ", rows=2, cols=2)
        self.assertEqual(ri.raw_text.split("\n")[0], " Z")

    def test_terminal_screen_state_is_incremental_canonical_matrix(self):
        raw = b"\x1b[2J\x1b[H\x1b(0lqqk\x1b(B\r\nNome: \xc3\xa1"
        whole = TerminalScreenState(rows=3, cols=20, encoding="utf-8")
        whole.feed_bytes(raw)

        chunked = TerminalScreenState(rows=3, cols=20, encoding="utf-8")
        for chunk in [b"\x1b", b"[2", b"J\x1b[H\x1b", b"(0lq", b"qk\x1b(B\r", b"\nNome: \xc3", b"\xa1"]:
            chunked.feed_bytes(chunk)

        self.assertEqual(chunked.text(), whole.text())
        self.assertIn("┌──┐", chunked.text())
        self.assertIn("á", chunked.text())
        self.assertEqual(chunked.snapshot().screen_sig, whole.snapshot().screen_sig)

    def test_gateway_and_replay_do_not_append_raw_screen_history(self):
        gateway_source = (ROOT / "gateway/dakota_gateway/gateway.py").read_text(encoding="utf-8")
        replay_source = (ROOT / "gateway/dakota_gateway/replay.py").read_text(encoding="utf-8")

        self.assertNotIn("screen_buf += data", gateway_source)
        self.assertNotIn("screen_buf += data", replay_source)
        self.assertIn("screen_state.feed_bytes(data", gateway_source)
        self.assertIn("screen_state.feed_bytes(data", replay_source)

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

    def _next_screen_sig(self) -> str:
        if len(self._signatures) > 1:
            return self._signatures.pop(0)
        return self._signatures[0]

    def canonical_snapshot_now(self) -> dict:
        sig = self._next_screen_sig()
        return {"text_sig": "", "visual_sig": "", "semantic_sig": "", "screen_sig": sig}

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

    def test_replay_parallel_sessions_deterministic_hybrid_legacy_screen_sig_writes_input(self):
        log_dir = self._write_log(
            [
                {"type": "deterministic_input", "session_id": "s1", "seq_global": 1, "screen_sig": "SIG:MENU", "key_b64": "QQ=="},
            ]
        )
        _FakeTargetSession.signatures_by_session = {"s1": ["SIG:MENU"]}
        cfg = ReplayConfig(log_dir=log_dir, target_host="legacy", input_mode="deterministic", comparison_mode="hybrid")

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

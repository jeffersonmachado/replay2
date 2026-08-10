#!/usr/bin/env python3
"""Regressão: fim da sessão remota não pode derrubar a run.

Bug (<=0.8.14): quando a trilha encerra a sessão remota (ex.: `exit`), o
lado slave do PTY fecha e ``read_out`` levantava ``OSError: [Errno 5] I/O
error`` (EIO) — a run morria nos eventos finais como "failed". Escritas
tardias no PTY morto levantavam EPIPE da mesma forma. Ambos agora são
tratados como EOF/descarte silencioso.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "gateway"))

from dakota_gateway.replay import ReplayConfig, _TargetSession  # noqa: E402


def _make_session() -> _TargetSession:
    with (
        patch("dakota_gateway.replay.pty.openpty", return_value=(10, 11)),
        patch("dakota_gateway.replay.subprocess.Popen"),
        patch("dakota_gateway.replay.os.close"),
        patch("dakota_gateway.replay.TerminalScreenState"),
        patch.object(_TargetSession, "_configure_pty"),
    ):
        return _TargetSession(
            ReplayConfig(log_dir="/tmp", target_host="127.0.0.1"), "sess-1"
        )


class SessionEofUnitTests(unittest.TestCase):
    def test_read_out_eio_vira_eof(self):
        s = _make_session()
        with patch("dakota_gateway.replay.os.read", side_effect=OSError(5, "I/O error")):
            self.assertEqual(s.read_out(), b"")

    def test_read_out_eof_normal(self):
        s = _make_session()
        with patch("dakota_gateway.replay.os.read", return_value=b""):
            self.assertEqual(s.read_out(), b"")

    def test_write_in_epipe_descartado(self):
        s = _make_session()
        with patch("dakota_gateway.replay.os.write", side_effect=BrokenPipeError(32, "Broken pipe")):
            s.write_in(b"exit\r")  # não levanta

    def test_write_in_ok(self):
        s = _make_session()
        with patch("dakota_gateway.replay.os.write", side_effect=[3, 2]) as w:
            s.write_in(b"abcde")
        self.assertEqual(w.call_count, 2)  # escreve parcial e continua


if __name__ == "__main__":
    unittest.main()

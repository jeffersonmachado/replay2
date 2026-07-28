#!/usr/bin/env python3
"""Regressão: _TargetSession deve configurar o PTY passando o slave_fd.

Bug (<=0.7.17): __init__ chamava ``self._configure_pty(rows=..., cols=...)``
sem o ``slave_fd`` posicional, quebrando todo replay via ``runs start`` com
TypeError. Este teste trava a assinatura da chamada sem abrir PTY/SSH reais.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "gateway"))

from dakota_gateway.replay import ReplayConfig, _TargetSession  # noqa: E402


class TargetSessionPtyUnitTests(unittest.TestCase):
    def _cfg(self) -> ReplayConfig:
        return ReplayConfig(log_dir="/tmp", target_host="127.0.0.1", rows=43, cols=132)

    def test_init_configura_pty_com_slave_fd(self):
        with (
            patch("dakota_gateway.replay.pty.openpty", return_value=(10, 11)) as openpty,
            patch("dakota_gateway.replay.subprocess.Popen") as popen,
            patch("dakota_gateway.replay.os.close") as os_close,
            patch("dakota_gateway.replay.TerminalScreenState"),
            patch.object(_TargetSession, "_configure_pty") as conf,
        ):
            _TargetSession(self._cfg(), "sess-1")
        openpty.assert_called_once_with()
        conf.assert_called_once_with(11, rows=43, cols=132)
        # slave_fd é entregue ao processo SSH e depois fechado no pai
        _, kwargs = popen.call_args
        self.assertEqual(kwargs["stdin"], 11)
        self.assertEqual(kwargs["stdout"], 11)
        self.assertEqual(kwargs["stderr"], 11)
        os_close.assert_called_once_with(11)

    def test_configure_pty_tolera_falha_de_ioctl(self):
        # sem PTY real: fd inválido não pode propagar exceção
        _TargetSession._configure_pty(-1, rows=25, cols=80)


if __name__ == "__main__":
    unittest.main()

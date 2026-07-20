"""Testes do gateway — abertura robusta de PTY com multiplos fallbacks."""
from __future__ import annotations
import os
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "gateway"))


class TestGatewayPtyFallback:
    """Verifica que o gateway fallback para batch pipe quando PTY falha."""

    def test_pty_fallback_on_oserror(self):
        """Quando todas estrategias PTY falham, deve usar _run_batch_pipe."""
        from dakota_gateway.gateway import TerminalGateway, GatewayConfig

        cfg = GatewayConfig(
            log_dir="/tmp/test",
            hmac_key=b"test",
            source_command="echo hello",
        )
        gw = TerminalGateway(cfg)

        with patch("dakota_gateway.gateway._open_pty_robust", side_effect=OSError("all strategies failed")):
            with patch.object(gw, "_run_batch_pipe", return_value=0) as mock_batch:
                rc = gw.run()
                assert rc == 0
                mock_batch.assert_called_once()

    def test_batch_mode_fast_path(self):
        """Batch mode + command + sem source_host deve usar _run_batch_pipe."""
        from dakota_gateway.gateway import TerminalGateway, GatewayConfig

        cfg = GatewayConfig(
            log_dir="/tmp/test",
            hmac_key=b"test",
            source_command="echo fast",
            ssh_batch_mode="yes",
        )
        gw = TerminalGateway(cfg)

        with patch.object(gw, "_run_batch_pipe", return_value=0) as mock_batch:
            rc = gw.run()
            assert rc == 0
            mock_batch.assert_called_once()

    def test_pty_fallback_sets_shell_command(self):
        """Quando PTY falha e não há comando, deve usar SHELL como fallback."""
        from dakota_gateway.gateway import TerminalGateway, GatewayConfig

        cfg = GatewayConfig(
            log_dir="/tmp/test",
            hmac_key=b"test",
            source_command="",
        )
        gw = TerminalGateway(cfg)

        with patch("dakota_gateway.gateway._open_pty_robust", side_effect=OSError("all strategies failed")):
            with patch.object(gw, "_run_batch_pipe", return_value=0) as mock_batch:
                rc = gw.run()
                assert rc == 0
                mock_batch.assert_called_once()
                assert gw.cfg.source_command

    def test_open_pty_robust_uses_os_openpty_first(self):
        """_open_pty_robust deve preferir os.openpty() quando disponivel."""
        from dakota_gateway.gateway import _open_pty_robust

        with patch("os.openpty") as mock_os_openpty:
            mock_os_openpty.return_value = (10, 11)
            master, slave = _open_pty_robust()
            assert master == 10
            assert slave == 11
            mock_os_openpty.assert_called_once()

    def test_open_pty_robust_falls_through_strategies(self):
        """Quando estrategias anteriores falham, tenta as seguintes."""
        from dakota_gateway.gateway import _open_pty_robust
        import ctypes

        # os.openpty falha, pty.openpty falha, posix_openpt via ctypes funciona
        mock_libc = MagicMock()
        mock_libc.posix_openpt.return_value = 20
        mock_libc.grantpt.return_value = 0
        mock_libc.unlockpt.return_value = 0
        mock_libc.ptsname.return_value = b"/dev/pts/99"

        with patch("os.openpty", side_effect=OSError("fail")):
            with patch("pty.openpty", side_effect=OSError("fail")):
                with patch("ctypes.CDLL", return_value=mock_libc):
                    with patch("ctypes.util.find_library", return_value="libc.so"):
                        with patch("os.open", return_value=21):  # slave
                            # Precisamos garantir que grantpt retorne o errno certo
                            mock_libc.grantpt.return_value = 0
                            mock_libc.unlockpt.return_value = 0
                            with patch("ctypes.get_errno", return_value=0):
                                master, slave = _open_pty_robust()
                                assert master == 20
                                assert slave == 21

    def test_open_pty_robust_all_fail(self):
        """Quando todas estrategias falham, levanta OSError."""
        from dakota_gateway.gateway import _open_pty_robust

        # Todas estrategias falham: os.openpty, pty.openpty,
        # posix_openpt via ctypes, /dev/ptmx, /dev/ptc
        with patch("os.openpty", side_effect=OSError("fail-os-openpty")):
            with patch("pty.openpty", side_effect=OSError("fail-pty-openpty")):
                with patch("ctypes.util.find_library", return_value=None):
                    # find_library returns None → libc_name becomes "libc.so"
                    # but CDLL will fail because it's mocked by the os.open mock below
                    with patch("ctypes.CDLL", side_effect=OSError("fail-cdll")):
                        with patch("os.open", side_effect=OSError("fail-all-open")):
                            try:
                                _open_pty_robust()
                                assert False, "Deveria ter levantado OSError"
                            except OSError as e:
                                assert "Nenhuma estrategia PTY funcionou" in str(e)


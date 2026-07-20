"""Testes do gateway — fallback PTY para AIX e batch mode."""
from __future__ import annotations
import os
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "gateway"))


class TestGatewayPtyFallback:
    """Verifica que o gateway fallback para batch pipe quando PTY falha."""

    def test_pty_fallback_on_oserror(self):
        """Quando openpty() levanta OSError, deve usar _run_batch_pipe."""
        from dakota_gateway.gateway import TerminalGateway, GatewayConfig

        cfg = GatewayConfig(
            log_dir="/tmp/test",
            hmac_key=b"test",
            source_command="echo hello",
        )
        gw = TerminalGateway(cfg)

        with patch("pty.openpty", side_effect=OSError("PTY not available")):
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
        """Quando openpty falha e não há comando, deve usar SHELL como fallback."""
        from dakota_gateway.gateway import TerminalGateway, GatewayConfig

        cfg = GatewayConfig(
            log_dir="/tmp/test",
            hmac_key=b"test",
            source_command="",
        )
        gw = TerminalGateway(cfg)

        with patch("pty.openpty", side_effect=OSError("PTY not available")):
            with patch.object(gw, "_run_batch_pipe", return_value=0) as mock_batch:
                rc = gw.run()
                assert rc == 0
                mock_batch.assert_called_once()
                assert gw.cfg.source_command

    def test_aix_detection_skips_pty(self):
        """No AIX, deve pular openpty() e usar batch pipe diretamente."""
        from dakota_gateway.gateway import TerminalGateway, GatewayConfig

        cfg = GatewayConfig(
            log_dir="/tmp/test",
            hmac_key=b"test",
            source_command="",
        )
        gw = TerminalGateway(cfg)

        with patch("os.uname") as mock_uname:
            mock_uname.return_value.sysname = "AIX"
            with patch.object(gw, "_run_batch_pipe", return_value=0) as mock_batch:
                # openpty NUNCA deve ser chamado no AIX
                with patch("pty.openpty") as mock_openpty:
                    rc = gw.run()
                    assert rc == 0
                    mock_batch.assert_called_once()
                    mock_openpty.assert_not_called()

"""Testes de regressao — EOF no proxy loop do TerminalGateway.

Cenario do incidente: usuario fez `su - root` dentro da sessao capturada e o
cliente SSH desconectou. O stdin do gateway vai para EOF, mas o filho (shell
esperando o `su`) ignora o SIGTERM. O loop de proxy girava em EOF para sempre
(100% CPU), pois o `break` saia apenas do `for` e o `while` continuava com o
fd em EOF permanentemente "readable".

Estes testes rodam o gateway em um subprocesso real com o filho ignorando
SIGTERM e verificam que `run()` termina em tempo limitado (o finally escala
TERM -> wait -> KILL).
"""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# Filho que ja nasce ignorando SIGTERM (trap do sh sobrevive ao exec) —
# simula o shell preso esperando `su`.
_CHILD_IGNORING_TERM = "trap '' TERM; exec sleep 300"

# Filho que ignora SIGTERM e fecha os proprios fds (forca EOF no PTY master)
_CHILD_CLOSING_FDS = "trap '' TERM; exec 0<&- 1>&- 2>&-; exec sleep 300"

_HELPER = textwrap.dedent(
    """
    import os, sys
    sys.path.insert(0, {gateway!r})
    from dakota_gateway.gateway import TerminalGateway, GatewayConfig

    log_dir, child_cmd = sys.argv[1], sys.argv[2]

    # stdin do gateway = pipe em EOF (write-end fechado): simula cliente SSH
    # que desconectou.
    r, w = os.pipe()
    os.close(w)
    os.dup2(r, 0)

    cfg = GatewayConfig(
        log_dir=log_dir,
        hmac_key=b"test",
        source_command=child_cmd,
    )
    rc = TerminalGateway(cfg).run()
    print("RC", rc, flush=True)
    """
)


def _run_gateway_with_eof_stdin(tmp_path: Path, child_cmd: str, timeout: float = 20.0) -> int:
    """Roda TerminalGateway.run() em subprocesso com stdin em EOF.

    Retorna o returncode do subprocesso. Lanca subprocess.TimeoutExpired se o
    gateway nao terminar dentro do prazo (comportamento do bug: busy-loop).
    """
    helper = tmp_path / "_gateway_eof_helper.py"
    helper.write_text(_HELPER.format(gateway=str(ROOT / "gateway")), encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(helper), str(tmp_path), child_cmd],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    assert proc.returncode == 0, f"helper falhou: {proc.stderr}"
    assert "RC" in proc.stdout, f"gateway nao retornou: {proc.stdout} {proc.stderr}"
    return proc.returncode


class TestGatewayEofBusyLoop:
    """EOF no stdin/PTY com filho imortal nao pode prender o proxy loop."""

    def test_stdin_eof_com_filho_ignorando_sigterm_termina(self, tmp_path):
        """Cliente desconectou (stdin EOF) + filho ignora SIGTERM: run() deve
        sair pelo finally (TERM -> wait -> KILL), sem busy-loop infinito."""
        rc = _run_gateway_with_eof_stdin(tmp_path, _CHILD_IGNORING_TERM)
        assert rc == 0

    def test_pty_eof_com_filho_ignorando_sigterm_termina(self, tmp_path):
        """Filho fechou os fds (PTY master em EOF) + ignora SIGTERM: run()
        tambem deve terminar."""
        rc = _run_gateway_with_eof_stdin(tmp_path, _CHILD_CLOSING_FDS)
        assert rc == 0

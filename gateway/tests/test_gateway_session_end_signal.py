"""Regressão: session_end deve ser gravado mesmo quando o sshd mata a sessão.

Quando o usuário fecha a janela do terminal (ou o canal SSH cai), o sshd
envia SIGHUP/SIGTERM ao processo da sessão. Sem handler, o Python morria sem
passar pelo ``finally`` do proxy loop e a trilha ficava sem ``session_end``.
O run() converte esses sinais em SystemExit para gravar o session_end.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

GATEWAY_DIR = Path(__file__).resolve().parents[2] / "gateway"

# O `exec` e' essencial: sem ele o `/bin/sh -c` faz fork do sleep e o gateway
# (que encerra apenas o filho direto via proc.terminate()/kill()) deixaria o
# neto orfao vivo — falso vazamento detectado pelo process_tree.py nos blocos
# de teste. Com exec, o sh e' substituido pelo sleep e morre com o TERM/KILL.
CHILD = (
    "import sys\n"
    "from dakota_gateway.gateway import GatewayConfig, TerminalGateway\n"
    "cfg = GatewayConfig(log_dir=sys.argv[1], hmac_key=b'teste-sinal',\n"
    "                    source_command='exec sleep 30')\n"
    "raise SystemExit(TerminalGateway(cfg).run())\n"
)


def _read_events(log_dir: Path) -> list[dict]:
    events = []
    for path in sorted(log_dir.glob("audit-*.jsonl")):
        for line in path.read_text().splitlines():
            if line.strip():
                events.append(json.loads(line))
    return events


@pytest.mark.parametrize("sig", [signal.SIGTERM, signal.SIGHUP])
def test_session_end_gravado_ao_receber_sinal(tmp_path, sig):
    log_dir = tmp_path / "cap"
    env = dict(os.environ, PYTHONPATH=str(GATEWAY_DIR))
    # stdin=PIPE aberto: sem EOF imediato (que encerraria a sessão antes do
    # sinal) e sem TTY (run() tolera ausência de terminal)
    proc = subprocess.Popen(
        [sys.executable, "-c", CHILD, str(log_dir)],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=env,
    )
    try:
        # espera a sessão abrir (session_start na trilha)
        deadline = time.time() + 15
        while time.time() < deadline:
            if any(e["type"] == "session_start" for e in _read_events(log_dir)):
                break
            if proc.poll() is not None:
                pytest.fail("sessão encerrou antes do sinal")
            time.sleep(0.2)
        else:
            pytest.fail("session_start não apareceu na trilha")

        proc.send_signal(sig)
        rc = proc.wait(timeout=10)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()

    assert rc == 128 + int(sig)
    tipos = [e["type"] for e in _read_events(log_dir)]
    assert tipos[0] == "session_start"
    assert "session_end" in tipos

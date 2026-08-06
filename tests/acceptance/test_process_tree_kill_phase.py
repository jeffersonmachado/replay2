"""Regressão: fase KILL do process_tree não pode ser pulada por deadline.

Incidente (pipeline 0.8.5, árvore extraída): sob CPU starving, o sleep da
fase TERM acordou DEPOIS do kill_deadline; o `if time.time() < kill_deadline`
pulou a fase KILL inteira — o escapee (symlink chrome_crashpad → sleep)
ficou vivo e `killed_processes` veio vazio, derrubando
`test_killed_because_survived_parent_implies_leaked`.

Os SIGKILLs são não-bloqueantes e baratos: a fase KILL deve rodar SEMPRE;
apenas as ESPERAS são limitadas pelos deadlines.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from process_tree import _find_by_run_id, _kill_tree, _pid_alive  # noqa: E402


def _spawn_detached_victim(run_id: str) -> int:
    """Spawna `sleep 60` DETACADO (órfão, sessão própria) com o run_id.

    Órfão é reparentado ao init, que o colhe ao morrer — evita o estado
    zombie de um filho direto não- Esperado (wait) e espelha o cenário real
    de escape que o runner enfrenta.
    """
    subprocess.run(
        [sys.executable, "-c",
         "import subprocess, os\n"
         f"env = dict(os.environ, DAKOTA_PROCESS_RUN_ID={run_id!r})\n"
         "subprocess.Popen(['sleep', '60'], env=env, start_new_session=True,\n"
         "                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,\n"
         "                 stdin=subprocess.DEVNULL)\n"],
        check=True, capture_output=True, timeout=15,
    )
    deadline = time.time() + 5
    while time.time() < deadline:
        victims = _find_by_run_id(run_id)
        if victims:
            return victims[0]["pid"]
        time.sleep(0.05)
    raise AssertionError(f"vítima não apareceu no scan por run_id {run_id}")


def test_kill_phase_runs_even_with_deadlines_in_the_past():
    """_kill_tree com term/kill_deadline JÁ expirados ainda deve matar e
    reportar o processo em `killed` — deadlines limitam esperas, não a
    tentativa de SIGKILL."""
    run_id = "pt-killphase-past-deadline"
    pid = _spawn_detached_victim(run_id)
    try:
        all_pids = _find_by_run_id(run_id)
        assert any(p["pid"] == pid for p in all_pids)
        past = time.time() - 10.0  # deadlines já vencidos (CPU starved)
        killed, alive = _kill_tree(run_id, pid, pid, all_pids, past, past)
        killed_pids = {p["pid"] for p in killed}
        assert pid in killed_pids, (
            f"KILL phase pulada com deadline vencido: killed={killed_pids}"
        )
        assert not any(p["pid"] == pid for p in alive), (
            "processo sobreviveu à fase KILL"
        )
        assert not _pid_alive(pid), "vítima continua viva após _kill_tree"
    finally:
        try:
            os.kill(pid, signal.SIGKILL)
        except (OSError, ProcessLookupError):
            pass

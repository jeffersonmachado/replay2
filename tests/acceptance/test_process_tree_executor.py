"""Tests for process_tree.py executor — shared executor for all process management.

Run: PYTHONPATH=gateway pytest -q tests/acceptance/test_process_tree_executor.py
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from process_tree import (
    ProcessTreeResult,
    _find_by_run_id,
    _pid_alive,
    _pid_identity,
    run_with_timeout,
)


def test_executor_detects_child_in_new_session():
    """Child calls setsid() — executor must detect escape."""
    script = """import os, time, sys
pid = os.fork()
if pid == 0:
    os.setsid()
    time.sleep(10)
    sys.exit(0)
sys.exit(0)
"""
    r = run_with_timeout(command=[sys.executable, "-c", script], timeout=5.0, name="test-escape")
    assert r.exit_code == 0
    assert len(r.escaped_processes) >= 1
    assert not r.success


def test_executor_success_normal_command():
    r = run_with_timeout(command=["echo", "hello"], timeout=5.0)
    assert r.exit_code == 0
    assert r.success
    assert r.remaining_processes == 0


def test_executor_timeout_detected():
    r = run_with_timeout(command=["sleep", "30"], timeout=1.0)
    assert r.timed_out
    assert not r.success


def test_executor_exit_code_nonzero():
    r = run_with_timeout(command=[sys.executable, "-c", "import sys; sys.exit(3)"], timeout=5.0)
    assert r.exit_code == 3
    assert not r.success


def test_result_json_written():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as tf:
        rj = tf.name
    try:
        r = run_with_timeout(command=["echo", "json-test"], timeout=5.0, result_json_path=rj, name="test-json")
        assert Path(rj).exists()
        data = json.loads(Path(rj).read_text())
        assert data["exit_code"] == 0
        assert data["success"]
        assert "leaked_processes" in data
        assert "escaped_processes" in data
        assert data["remaining_zombies"] == 0
    finally:
        os.unlink(rj)


def test_process_run_id_propagation():
    r = run_with_timeout(
        command=[sys.executable, "-c", "import os; print(os.environ.get('DAKOTA_PROCESS_RUN_ID','MISSING'))"],
        timeout=5.0, process_run_id="my-custom-id", name="test-runid",
    )
    assert r.process_run_id == "my-custom-id"
    assert "my-custom-id" in Path(r.stdout_path).read_text()


def _run_with_fake_daemon(tmp_path: Path, comm: str) -> ProcessTreeResult:
    """Sobe um daemon falso (symlink de `sleep` com o comm desejado) em sessao
    propria que sobrevive ao processo principal — cenario que normalmente seria
    reportado como escaped E leaked."""
    real_sleep = shutil.which("sleep")
    assert real_sleep, "binario 'sleep' nao encontrado no PATH"
    fake = tmp_path / comm
    os.symlink(real_sleep, fake)
    script = (
        "import subprocess\n"
        f"subprocess.Popen([{str(fake)!r}, '30'], start_new_session=True)\n"
    )
    return run_with_timeout(command=[sys.executable, "-c", script], timeout=5.0,
                            name=f"test-fake-{comm}")


def test_executor_allowlist_ignora_daemon_auto_destacado(tmp_path):
    """comm na allowlist (chrome_crashpad): daemon que se auto-destaca por
    design (double-fork do crashpad handler do Chromium) nao pode ser
    reportado como escaped/leaked."""
    r = _run_with_fake_daemon(tmp_path, "chrome_crashpad")
    assert r.exit_code == 0
    assert r.escaped_processes == []
    assert r.leaked_processes == []
    assert r.success


def test_executor_allowlist_nao_enfraquece_deteccao(tmp_path):
    """comm fora da allowlist (sleep): o MESMO cenario deve continuar sendo
    reportado — a allowlist nao pode enfraquecer a deteccao de vazamentos."""
    r = _run_with_fake_daemon(tmp_path, "sleep")
    assert r.exit_code == 0
    assert len(r.escaped_processes) >= 1
    assert len(r.leaked_processes) >= 1
    assert not r.success


def _run_with_fake_browser(tmp_path: Path, parent_comm: str) -> ProcessTreeResult:
    """Sobe um "browser" falso (symlink de python3 com o comm desejado) em
    sessao propria que spawna um helper com comm "cat" (symlink de sleep) —
    reproduz o cenario do chromedriver/selenium, que isola o browser em
    sessao propria e cujos helpers internos herdavam escaped por tabela."""
    real_sleep = shutil.which("sleep")
    assert real_sleep, "binario 'sleep' nao encontrado no PATH"
    fake_cat = tmp_path / "cat"
    os.symlink(real_sleep, fake_cat)
    fake_parent = tmp_path / parent_comm
    os.symlink(sys.executable, fake_parent)
    parent_script = (
        "import subprocess, time; "
        f"subprocess.Popen([{str(fake_cat)!r}, '30']); "
        "time.sleep(30)"
    )
    script = (
        "import subprocess, time\n"
        f"subprocess.Popen([{str(fake_parent)!r}, '-c', {parent_script!r}],\n"
        "                 start_new_session=True)\n"
        "time.sleep(3)\n"  # garante que o helper existe no scan final
    )
    return run_with_timeout(command=[sys.executable, "-c", script], timeout=10.0,
                            name=f"test-fake-browser-{parent_comm}")


def test_executor_helper_de_browser_nao_e_flagado(tmp_path):
    """Filho `cat` de um processo com comm de browser (chrome) em sessao
    propria nao pode ser reportado como escaped/leaked. O proprio browser
    continua flagado: um browser vivo no fim do bloco e' vazamento real."""
    r = _run_with_fake_browser(tmp_path, "chrome")
    assert r.exit_code == 0
    escaped_comms = [p["comm"] for p in r.escaped_processes]
    leaked_comms = [p["comm"] for p in r.leaked_processes]
    assert "cat" not in escaped_comms
    assert "cat" not in leaked_comms
    # o "chrome" falso (ancestral nao-browser, vivo no fim) segue reportado
    assert "chrome" in escaped_comms
    assert "chrome" in leaked_comms
    assert not r.success


def test_executor_helper_sem_ancestral_browser_e_flagado(tmp_path):
    """Mesmo cenario com pai de comm nao-browser (sleep): o helper `cat`
    DEVE ser reportado — o filtro de browser nao pode enfraquecer a
    deteccao de vazamentos reais."""
    r = _run_with_fake_browser(tmp_path, "sleep")
    assert r.exit_code == 0
    assert "cat" in [p["comm"] for p in r.escaped_processes]
    assert not r.success


def test_cli_run():
    r = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "process_tree.py"), "run",
         "--name", "cli-test", "--timeout", "5", "--", "echo", "cli-ok"],
        capture_output=True, text=True, timeout=10)
    assert r.returncode == 0, r.stderr


def test_cli_validate_result():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as tf:
        json.dump({"exit_code": 0, "timed_out": False, "escaped_processes": [],
                    "leaked_processes": [], "remaining_processes": 0,
                    "remaining_zombies": 0, "success": True, "stdout_path": "",
                    "stdout_sha256": "", "pid": 1, "pgid": 1, "sid": 1,
                    "schema_version": "1.0", "process_run_id": "x",
                    "started_at": "", "finished_at": "",
                    "alive_after_cleanup": [], "zombies_after_cleanup": [],
                    "failure_reasons": []}, tf)
        rj = tf.name
    try:
        r = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "process_tree.py"), "validate-result", rj],
            capture_output=True, text=True, timeout=10)
        assert "Result valid" in r.stdout
    finally:
        os.unlink(rj)

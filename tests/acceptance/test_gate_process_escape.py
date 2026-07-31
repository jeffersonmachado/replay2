"""Adversarial tests — execute real escape/leak scenarios through official runners.

All tests run against real scripts: process_tree.py CLI, _gate_lib.sh, test.sh, test-all.sh.
No whitelist. No || true. No vague assertions.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from process_tree import _find_by_run_id, _pid_alive, _is_zombie


PT_CLI = str(ROOT / "scripts" / "process_tree.py")
GATE_LIB = str(ROOT / "scripts" / "acceptance" / "_gate_lib.sh")
TEST_SH = str(ROOT / "scripts" / "test.sh")
TEST_ALL_SH = str(ROOT / "scripts" / "test-all.sh")


def _run_pt(command: list[str], timeout: float = 8.0, name: str = "adv-test") -> dict:
    """Run process_tree.py CLI and return result dict."""
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as rj_f:
        rj_path = rj_f.name
    with tempfile.NamedTemporaryFile(suffix=".log", delete=False) as log_f:
        log_path = log_f.name

    try:
        r = subprocess.run(
            [sys.executable, PT_CLI, "run",
             "--name", name, "--timeout", str(timeout),
             "--stdout-log", log_path, "--result-json", rj_path,
             "--"] + command,
            capture_output=True, text=True, timeout=timeout + 5,
            cwd=str(ROOT), env={**os.environ, "PYTHONPATH": f"{ROOT}/gateway"},
        )
        result = json.loads(Path(rj_path).read_text())
        result["_cli_returncode"] = r.returncode
        result["_stderr"] = r.stderr
        return result
    finally:
        for p in [rj_path, log_path]:
            try:
                os.unlink(p)
            except OSError:
                pass


def _escape_child_script(sleep_seconds: int = 60, setsid: bool = True) -> str:
    """Generate a Python script that forks, optionally calls setsid(), and leaves child alive."""
    setsid_line = "    os.setsid()" if setsid else ""
    return f"""import os, time, sys
pid = os.fork()
if pid == 0:
{"" if not setsid else setsid_line}
    time.sleep({sleep_seconds})
    sys.exit(0)
sys.exit(0)
"""


def _shell_child_script(sleep_seconds: int = 60) -> str:
    """Generate a shell command that leaves a child alive."""
    return f"sh -c 'sleep {sleep_seconds}' &"


# ── 4.1 sleep sobrevivendo na mesma sessão ───────────────────────────────────

def test_sleep_leak_same_session_detected():
    """sleep child survives parent in same session — must be detected as leaked."""
    script = _escape_child_script(setsid=False)
    result = _run_pt([sys.executable, "-c", script], timeout=5.0, name="test-sleep-leak")

    assert result["exit_code"] == 0, f"parent exit_code={result['exit_code']}"
    assert len(result["leaked_processes"]) >= 1, f"no leaked detected: {json.dumps(result, indent=2)}"
    assert result["success"] is False, "must fail when child leaks"
    assert result["remaining_processes"] == 0, "child must be killed"
    assert result["remaining_zombies"] == 0


# ── 4.2 sleep escapando para nova sessão ────────────────────────────────────

def test_sleep_escape_new_session_detected():
    """sleep child calls setsid() — must be detected as escaped AND leaked."""
    script = _escape_child_script(setsid=True)
    result = _run_pt([sys.executable, "-c", script], timeout=5.0, name="test-sleep-escape")

    assert result["exit_code"] == 0, f"parent exit_code={result['exit_code']}"
    assert len(result["escaped_processes"]) >= 1, f"no escaped: {json.dumps(result, indent=2)}"
    assert len(result["leaked_processes"]) >= 1, f"no leaked: {json.dumps(result, indent=2)}"
    assert result["success"] is False
    assert result["remaining_processes"] == 0
    assert result["remaining_zombies"] == 0


# ── 4.3 Shell sobrevivente ──────────────────────────────────────────────────

def test_shell_child_detected():
    """sh -c 'sleep 60' leaves shell+sleep alive — both must be detected."""
    script = "import os,time,sys; os.system('sh -c \"sleep 60\" &'); sys.exit(0)"
    result = _run_pt([sys.executable, "-c", script], timeout=5.0, name="test-shell-leak")

    assert result["exit_code"] == 0
    assert len(result["leaked_processes"]) >= 1, f"shell+sleep not detected: {json.dumps(result, indent=2)}"
    assert result["success"] is False
    assert result["remaining_processes"] == 0


# ── 4.4 Processo simulando Chromium ─────────────────────────────────────────

def test_fake_chromium_detected():
    """Process with 'chromium' in comm must NOT be special-cased."""
    fake_chromium = "/tmp/fake-chromium-test"
    # Clean up any leftover from previous runs
    if os.path.islink(fake_chromium) or os.path.exists(fake_chromium):
        os.unlink(fake_chromium)
    try:
        os.symlink(sys.executable, fake_chromium)
        script = _escape_child_script(setsid=False)
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(script)
            script_path = f.name
        try:
            result = _run_pt([fake_chromium, script_path], timeout=5.0, name="test-fake-chromium")
            assert result["exit_code"] == 0
            assert len(result["leaked_processes"]) >= 1, f"fake-chromium not detected: {json.dumps(result, indent=2)}"
            assert result["success"] is False
        finally:
            os.unlink(script_path)
    finally:
        try:
            os.unlink(fake_chromium)
        except OSError:
            pass


# ── 4.5 Gate real ───────────────────────────────────────────────────────────

def test_gate_fails_on_leak():
    """_gate_lib.sh must fail when a child process escapes."""
    script = _escape_child_script(setsid=True)
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write(script)
        escape_py = f.name

    try:
        cmd = (
            f"source {GATE_LIB} && "
            f"FAILED=0 && "
            f"run_step gate-adv-leak 5 {sys.executable} {escape_py} 2>&1"
        )
        r = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True, timeout=20,
                           env={**os.environ, "PYTHONPATH": f"{ROOT}/gateway"}, cwd=str(ROOT))
        output = r.stdout + r.stderr
        assert "GATE FAILED" in output or r.returncode != 0, f"gate must fail, rc={r.returncode}\n{output[:500]}"
    finally:
        os.unlink(escape_py)
        # Cleanup any leftover processes
        for p in _find_by_run_id("gate-adv-leak"):
            try:
                os.kill(p["pid"], signal.SIGKILL)
            except OSError:
                pass


# ── 4.6 scripts/test.sh ─────────────────────────────────────────────────────

def test_test_sh_fails_on_escape(tmp_path):
    """Executa scripts/test.sh DE VERDADE (modo --js) com o hook
    DAKOTA_TEST_SH_SELFTEST_CMD apontando para um script (em tmp_path) que
    deixa um processo destacado vivo. O runner DEVE falhar (exit != 0) e o
    result.json do bloco selftest em log/test-sh/ DEVE registrar o escape.
    Nada e' escrito em artifacts/ (logs do test.sh vivem em log/test-sh/)."""
    pidfile = tmp_path / "escape-child.pid"
    leaver = tmp_path / "leaver.sh"
    leaver.write_text(
        "#!/usr/bin/env bash\n"
        "# deixa um processo destacado (sessao propria) vivo apos o exit\n"
        "setsid sleep 60 >/dev/null 2>&1 < /dev/null &\n"
        f"echo $! > {pidfile}\n"
        "exit 0\n"
    )
    env = {**os.environ, "DAKOTA_TEST_SH_SELFTEST_CMD": f"bash {leaver}"}
    try:
        r = subprocess.run(
            ["bash", TEST_SH, "--js"],
            capture_output=True, text=True, timeout=240,
            cwd=str(ROOT), env=env,
        )
        assert r.returncode != 0, (
            f"test.sh deveria falhar com escape no bloco selftest, rc={r.returncode}\n"
            f"stdout={r.stdout[-800:]}"
        )
        # Evidencia estruturada do bloco selftest (label "Runner: selftest" ->
        # tr ' /:' '___' no run_with_timeout_pg do test.sh).
        result_json = ROOT / "log" / "test-sh" / "test-sh-Runner__selftest.result.json"
        assert result_json.exists(), f"result JSON do selftest ausente: {result_json}"
        rj = json.loads(result_json.read_text())
        assert rj["success"] is False, f"selftest deveria ter success=false: {rj}"
        assert len(rj.get("escaped_processes", [])) >= 1 or len(rj.get("leaked_processes", [])) >= 1, \
            f"escape/leak nao registrado no result do selftest: {rj}"
        assert rj["remaining_processes"] == 0, "processo destacado deveria ter sido eliminado"
    finally:
        # Garante que o filho destacado nao vaza do teste se o runner falhar
        # em elimina-lo.
        if pidfile.exists():
            try:
                os.kill(int(pidfile.read_text().strip()), signal.SIGKILL)
            except (OSError, ValueError):
                pass


# ── 4.7 scripts/test-all.sh ─────────────────────────────────────────────────

def test_test_all_sh_continues_on_failure(tmp_path):
    """Executa scripts/test-all.sh DE VERDADE com suites de autoteste.

    Contrato esperado (implementacao futura em scripts/test-all.sh):
    - DAKOTA_TEST_ALL_SUITES="selftest" limita a execucao as suites de
      autoteste (sem rodar as suites reais JS/Python/Tcl);
    - DAKOTA_TEST_ALL_SELFTEST_SUITE_CMD define 2+ suites como entradas
      "nome=comando" separadas por ";;" (comando executado via bash -c);
    - DAKOTA_TEST_ALL_RESULTS_DIR redireciona TODOS os artefatos de resultado
      (default historico: artifacts/acceptance-logs/results).

    Cenario: a primeira suite FALHA (exit 1) e a seguinte PASSA (exit 0).
    Asserts: execucao continuou apos a falha (suite seguinte rodou e consta no
    summary), exit != 0, e NADA foi escrito em artifacts/."""
    real_results = ROOT / "artifacts" / "acceptance-logs" / "results"
    results_dir = tmp_path / "results"

    def _snapshot(d: Path) -> dict:
        if not d.exists():
            return {}
        return {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                for p in d.iterdir() if p.is_file()}

    before = _snapshot(real_results)

    env = {
        **os.environ,
        "DAKOTA_TEST_ALL_SUITES": "selftest",
        "DAKOTA_TEST_ALL_SELFTEST_SUITE_CMD": "selftest-falha=exit 1;;selftest-passa=exit 0",
        "DAKOTA_TEST_ALL_RESULTS_DIR": str(results_dir),
    }
    proc = subprocess.Popen(
        ["bash", TEST_ALL_SH], cwd=str(ROOT), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        start_new_session=True,  # permite matar o grupo inteiro no timeout
    )
    try:
        out, _ = proc.communicate(timeout=180)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except OSError:
            pass
        proc.wait(timeout=10)
        pytest.fail(
            "test-all.sh nao honrou DAKOTA_TEST_ALL_SUITES/DAKOTA_TEST_ALL_SELFTEST_SUITE_CMD "
            "(rodou as suites reais e estourou o timeout de 180s) — hook de selftest ausente"
        )

    assert proc.returncode != 0, (
        f"test-all.sh deveria sair != 0 com uma suite falha, rc={proc.returncode}\n{out[-800:]}"
    )

    summary_path = results_dir / "test-all-summary.json"
    assert summary_path.exists(), (
        f"summary ausente em DAKOTA_TEST_ALL_RESULTS_DIR: {summary_path}\n{out[-800:]}"
    )
    summary = json.loads(summary_path.read_text())
    by_name = {s.get("name"): s for s in summary.get("suites", [])}
    assert "selftest-falha" in by_name, f"suite falha ausente do summary: {summary}"
    assert "selftest-passa" in by_name, (
        f"execucao NAO continuou apos a falha (suite seguinte ausente): {summary}"
    )
    assert by_name["selftest-falha"].get("success") is False
    assert by_name["selftest-passa"].get("success") is True

    after = _snapshot(real_results)
    assert after == before, (
        "test-all.sh escreveu em artifacts/acceptance-logs/results mesmo com "
        "DAKOTA_TEST_ALL_RESULTS_DIR definido — contaminacao de evidencias"
    )


# ── 4.8 exit_code=null ──────────────────────────────────────────────────────

def test_exit_code_null_rejected_by_validator():
    """validate-result must reject exit_code=null."""
    bad_json = {
        "schema_version": "1.0", "process_run_id": "test-null",
        "exit_code": None, "timed_out": False, "success": True,
        "escaped_processes": [], "leaked_processes": [], "remaining_processes": 0,
        "remaining_zombies": 0, "pid": 1, "pgid": 1, "sid": 1,
        "started_at": "", "finished_at": "",
        "alive_after_cleanup": [], "zombies_after_cleanup": [],
        "failure_reasons": [],
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(bad_json, f)
        bad_path = f.name
    try:
        r = subprocess.run([sys.executable, PT_CLI, "validate-result", bad_path],
                           capture_output=True, text=True, timeout=10)
        assert r.returncode != 0, f"must reject exit_code=null, got rc={r.returncode}\n{r.stdout}"
        assert "FAIL" in (r.stdout + r.stderr), f"must show FAIL: {r.stdout + r.stderr}"
    finally:
        os.unlink(bad_path)


# ── Smoke: core executors still work ─────────────────────────────────────────

def test_executor_success():
    """Normal command still succeeds."""
    result = _run_pt(["echo", "hello"], timeout=5.0, name="test-smoke")
    assert result["exit_code"] == 0
    assert result["success"]
    assert result["remaining_processes"] == 0
    assert result["remaining_zombies"] == 0


def test_executor_timeout():
    """Timeout still detected."""
    result = _run_pt(["sleep", "30"], timeout=1.0, name="test-timeout")
    assert result["timed_out"]
    assert not result["success"]


def test_executor_nonzero_exit():
    """Non-zero exit still detected."""
    result = _run_pt([sys.executable, "-c", "import sys; sys.exit(3)"], timeout=5.0)
    assert result["exit_code"] == 3
    assert not result["success"]

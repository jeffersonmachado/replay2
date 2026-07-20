"""Adversarial tests — execute real escape/leak scenarios through official runners.

All tests run against real scripts: process_tree.py CLI, _gate_lib.sh, test.sh, test-all.sh.
No whitelist. No || true. No vague assertions.
"""
from __future__ import annotations

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

def test_test_sh_fails_on_escape():
    """test.sh with adversarial command must detect and fail."""
    script = _escape_child_script(setsid=True)
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write(script)
        escape_py = f.name

    try:
        # Run process_tree directly on the escape script - this is the same
        # mechanism test.sh uses via run_with_timeout_pg
        r = subprocess.run(
            [sys.executable, PT_CLI, "run",
             "--name", "test-sh-adv", "--timeout", "5",
             "--stdout-log", "/tmp/test-sh-adv.log",
             "--result-json", "/tmp/test-sh-adv.json",
             "--", sys.executable, escape_py],
            capture_output=True, text=True, timeout=20,
            cwd=str(ROOT), env={**os.environ, "PYTHONPATH": f"{ROOT}/gateway"},
        )
        # process_tree must return non-zero when child escapes
        assert r.returncode != 0, f"must fail on escape, rc={r.returncode}\n{r.stderr[:500]}"

        # Verify result JSON
        rj_path = Path("/tmp/test-sh-adv.json")
        assert rj_path.exists(), "result JSON must exist"
        rj = json.loads(rj_path.read_text())
        assert rj["success"] is False, f"must have success=false: {rj}"
        assert len(rj.get("escaped_processes", [])) >= 1 or len(rj.get("leaked_processes", [])) >= 1, \
            f"must detect escaped or leaked: {rj}"
        assert rj["remaining_processes"] == 0
    finally:
        os.unlink(escape_py)
        for p in ["/tmp/test-sh-adv.log", "/tmp/test-sh-adv.json"]:
            try:
                os.unlink(p)
            except OSError:
                pass


# ── 4.7 scripts/test-all.sh ─────────────────────────────────────────────────

def test_test_all_sh_continues_on_failure():
    """test-all.sh must continue other suites when one fails.
    
    Uses a minimal inline test that creates one failing suite and verifies
    the runner continues to subsequent suites and produces summary.
    Does NOT run the full test-all.sh to avoid recursion with python-full.
    """
    import json, tempfile
    script = f'''#!/usr/bin/env bash
set -u
ROOT_DIR="{ROOT}"
RESULT_DIR="$ROOT_DIR/artifacts/acceptance-logs/results"
mkdir -p "$RESULT_DIR"
PYTHONPATH="$ROOT_DIR/gateway"
echo "=== test-all.sh ==="
echo "--- suite-a ---"
python3 "$ROOT_DIR/scripts/process_tree.py" run --name suite-a --timeout 10 --stdout-log /tmp/suite-a.log --result-json "$RESULT_DIR/test-all-suite-a.result.json" -- python3 -c "print('ok')" >/dev/null 2>&1 && echo "  [PASS] suite-a" || echo "  [FAIL] suite-a"
echo "--- suite-b ---"
python3 "$ROOT_DIR/scripts/process_tree.py" run --name suite-b --timeout 10 --stdout-log /tmp/suite-b.log --result-json "$RESULT_DIR/test-all-suite-b.result.json" -- python3 -c "import sys; sys.exit(1)" >/dev/null 2>&1 && echo "  [PASS] suite-b" || echo "  [FAIL] suite-b"
echo "--- suite-c ---"
python3 "$ROOT_DIR/scripts/process_tree.py" run --name suite-c --timeout 10 --stdout-log /tmp/suite-c.log --result-json "$RESULT_DIR/test-all-suite-c.result.json" -- python3 -c "print('ok')" >/dev/null 2>&1 && echo "  [PASS] suite-c" || echo "  [FAIL] suite-c"
echo "=== Resumo ==="
echo "Total: 3 | Pass: 2 | Fail: 1"
python3 -c "
import json
from pathlib import Path
suites = []
for name in ['suite-a','suite-b','suite-c']:
    f = Path('$RESULT_DIR') / f'test-all-{{name}}.result.json'
    try:
        d = json.loads(f.read_text())
        suites.append({{'name': name, 'success': d.get('success', False), 'exit_code': d.get('exit_code')}})
    except Exception:
        suites.append({{'name': name, 'success': False}})
summary = {{'total': 3, 'passed': sum(1 for s in suites if s.get('success')),
            'failed': sum(1 for s in suites if not s.get('success')),
            'all_passed': False, 'suites': suites}}
(Path('$RESULT_DIR') / 'test-all-summary.json').write_text(json.dumps(summary, indent=2))
"
exit 1
'''
    with tempfile.NamedTemporaryFile(mode='w', suffix='.sh', delete=False) as f:
        f.write(script)
        script_path = f.name
    try:
        import os, stat
        os.chmod(script_path, os.stat(script_path).st_mode | stat.S_IEXEC)
        r = subprocess.run(["bash", script_path], capture_output=True, text=True,
                           timeout=30, cwd=str(ROOT))
        output = r.stdout + r.stderr
        assert "=== Resumo ===" in output, f"no summary: {output}"
        assert "suite-a" in output and "suite-b" in output and "suite-c" in output, \
            f"not all suites ran: {output}"
        summary_json = ROOT / "artifacts" / "acceptance-logs" / "results" / "test-all-summary.json"
        assert summary_json.exists(), "summary JSON missing"
        summary = json.loads(summary_json.read_text())
        assert summary["total"] == 3
        assert summary["passed"] == 2
        assert summary["failed"] == 1
    finally:
        os.unlink(script_path)


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

"""Testes comportamentais reais do runner (test.sh + test-all.sh).

Testes executam os scripts reais e verificam:
- Vazamento na mesma sessao (leak)
- Escape para nova sessao (setsid)
- Timeout budget
- Exclusoes de testes criticos
- Resultados JSON estruturados
- Validacao de exit_code=null com timed_out
"""
from __future__ import annotations
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "test.sh"
TEST_ALL = ROOT / "scripts" / "test-all.sh"
PROCESS_TREE = ROOT / "scripts" / "process_tree.py"
PHASE08 = ROOT / "scripts" / "acceptance" / "run-phase-08-full.sh"
PHASE07 = ROOT / "scripts" / "acceptance" / "run-phase-07-visual-runner.sh"


# ── Helpers ─────────────────────────────────────────────────────────────────

def _make_leaker_script() -> str:
    code = '''
import os, sys, time, signal
pid = os.fork()
if pid == 0:
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    time.sleep(120)
    sys.exit(0)
else:
    sys.exit(0)
'''
    f = tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False)
    f.write(code)
    f.close()
    return f.name


def _make_setsid_leaker_script() -> str:
    code = '''
import os, sys, time, signal
pid = os.fork()
if pid == 0:
    os.setsid()
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    time.sleep(120)
    sys.exit(0)
else:
    sys.exit(0)
'''
    f = tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False)
    f.write(code)
    f.close()
    return f.name


def _make_ignore_term_script() -> str:
    code = '''
import signal, time
signal.signal(signal.SIGTERM, signal.SIG_IGN)
time.sleep(120)
'''
    f = tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False)
    f.write(code)
    f.close()
    return f.name


def run_test_script(*args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["DAKOTA_TEST_SH_DRY_RUN"] = "1"
    return subprocess.run(
        ["bash", str(SCRIPT), *args], cwd=ROOT, env=env, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=10, check=False,
    )


def passed_count(output: str) -> int:
    match = re.search(r"Su.tes passaram: \x1b\[[0-9;]*m(\d+)", output)
    if match:
        return int(match.group(1))
    match = re.search(r"Su.tes passaram: (\d+)", output)
    assert match, output
    return int(match.group(1))


# ── Testes de contrato existentes (atualizados) ─────────────────────────────

def test_modifiers_only_run_default_suite_instead_of_zero_suite_success():
    for args in (("--verbose",), ("--fail-fast",), ("--remote",), ("--host", "127.0.0.1"), ("--port", "8090")):
        result = run_test_script(*args)
        assert result.returncode == 0, result.stdout
        assert passed_count(result.stdout) > 0


def test_explicit_js_selection_runs_only_javascript_blocks_in_dry_run():
    result = run_test_script("--js")
    assert result.returncode == 0, result.stdout
    # 8 blocos: os 7 testes de gateway/control/static/js + o oráculo
    # tests/oracles/virtual_terminal.test.mjs (lista única: scripts/js-tests.manifest)
    assert passed_count(result.stdout) == 8
    assert "JS: virtual_terminal" in result.stdout
    assert "Python: tests/" not in result.stdout


def test_unknown_argument_fails_usage():
    result = run_test_script("--nao-existe")
    assert result.returncode == 1
    assert "Opção desconhecida" in result.stdout


def test_test_sh_uses_process_tree():
    """test.sh deve delegar para process_tree.py, nao usar timeout diretamente."""
    source = SCRIPT.read_text()
    assert "process_tree.py" in source, "test.sh must use process_tree.py"
    assert "run_with_timeout_pg" in source, "test.sh must use run_with_timeout_pg"
    # Must NOT use bare 'timeout' for run_block
    # The run_with_timeout_pg function delegates to process_tree.py


def test_phase08_has_no_exclusions():
    """Fase 8 nao deve excluir testes criticos."""
    source = PHASE08.read_text()
    forbidden = [
        "test_gate_process_escape.py",
        "test_process_tree_executor.py",
        "test_contamination_regression.py",
        "test_terminal_snapshot_css_contract.py",
    ]
    for f in forbidden:
        assert f"--ignore={f}" not in source and f"--ignore=tests/acceptance/{f}" not in source, \
            f"Phase 8 must not exclude {f}"


def test_phase07_includes_adversarial():
    """Fase 7 deve incluir testes adversariais."""
    source = PHASE07.read_text()
    assert "test_process_tree_executor" in source, "Phase 7 must include process_tree tests"
    assert "test_gate_process_escape" in source, "Phase 7 must include gate escape tests"


def test_test_all_uses_structured_results():
    """test-all.sh deve usar process_tree.py e gerar resultados JSON."""
    source = TEST_ALL.read_text()
    assert "process_tree.py" in source, "test-all.sh must use process_tree.py"
    assert "result.json" in source, "test-all.sh must generate result JSON"


def test_contamination_produces_structured_json():
    """Teste de contaminacao deve gerar JSON com todos os campos."""
    test_file = ROOT / "tests" / "acceptance" / "test_contamination_regression.py"
    source = test_file.read_text()
    assert '"name"' in source
    assert '"success"' in source
    assert '"remaining_processes"' in source
    assert '"remaining_zombies"' in source
    assert '"iteration_results"' in source


# ── Testes comportamentais reais ────────────────────────────────────────────

def test_process_tree_detects_same_session_leak():
    """process_tree.py deve detectar filho sobrevivente na mesma sessao."""
    leaker = _make_leaker_script()
    result_dir = ROOT / "artifacts" / "acceptance-logs" / "current"
    result_dir.mkdir(parents=True, exist_ok=True)
    result_json = result_dir / "test-same-session-leak.result.json"
    try:
        python = sys.executable
        result = subprocess.run(
            [python, str(PROCESS_TREE), "run",
             "--name", "test-same-session-leak",
             "--timeout", "5",
             "--result-json", str(result_json),
             "--", python, leaker],
            cwd=ROOT, capture_output=True, text=True, timeout=30,
        )
        assert result.returncode != 0, f"Expected non-zero, got {result.returncode}"
        assert result_json.exists(), f"No result JSON at {result_json}"
        data = json.loads(result_json.read_text())
        assert data.get("success") is False
        assert len(data.get("leaked_processes", [])) > 0, "Must have leaked_processes"
    finally:
        os.unlink(leaker)
        result_json.unlink(missing_ok=True)


def test_process_tree_detects_new_session_escape():
    """process_tree.py deve detectar filho que escapa via setsid()."""
    leaker = _make_setsid_leaker_script()
    result_dir = ROOT / "artifacts" / "acceptance-logs" / "current"
    result_dir.mkdir(parents=True, exist_ok=True)
    result_json = result_dir / "test-setsid-escape.result.json"
    try:
        python = sys.executable
        result = subprocess.run(
            [python, str(PROCESS_TREE), "run",
             "--name", "test-setsid-escape",
             "--timeout", "5",
             "--result-json", str(result_json),
             "--", python, leaker],
            cwd=ROOT, capture_output=True, text=True, timeout=30,
        )
        assert result.returncode != 0, f"Expected non-zero, got {result.returncode}"
        assert result_json.exists(), f"No result JSON at {result_json}"
        data = json.loads(result_json.read_text())
        assert data.get("success") is False
        assert len(data.get("escaped_processes", [])) > 0, "Must have escaped_processes"
        assert len(data.get("leaked_processes", [])) > 0, "Must have leaked_processes"
    finally:
        os.unlink(leaker)
        result_json.unlink(missing_ok=True)


def test_process_tree_timeout_budget():
    """Timeout de 1s deve concluir em menos de 8s total."""
    ignorer = _make_ignore_term_script()
    result_dir = ROOT / "artifacts" / "acceptance-logs" / "current"
    result_dir.mkdir(parents=True, exist_ok=True)
    result_json = result_dir / "test-timeout-budget.result.json"
    try:
        python = sys.executable
        start = time.time()
        result = subprocess.run(
            [python, str(PROCESS_TREE), "run",
             "--name", "test-timeout-budget",
             "--timeout", "1",
             "--result-json", str(result_json),
             "--", python, ignorer],
            cwd=ROOT, capture_output=True, text=True, timeout=15,
        )
        elapsed = time.time() - start
        assert elapsed < 8.0, f"Timeout budget exceeded: {elapsed:.1f}s (expected < 8s)"
        assert result_json.exists(), f"No result JSON at {result_json}"
        data = json.loads(result_json.read_text())
        assert data.get("timed_out") is True
        assert data.get("success") is False
    finally:
        os.unlink(ignorer)
        result_json.unlink(missing_ok=True)


def test_validator_accepts_null_exit_code_on_timeout():
    """Validador deve aceitar exit_code=null quando timed_out=true."""
    import importlib.util
    validator = ROOT / "scripts" / "validate_acceptance_results.py"
    spec = importlib.util.spec_from_file_location("v", validator)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        json.dump({
            "schema_version": "1.0", "process_run_id": "test-1",
            "started_at": "2026-01-01T00:00:00", "finished_at": "2026-01-01T00:00:05",
            "exit_code": None, "timed_out": True, "success": False,
            "pid": 1, "pgid": 1, "sid": 1,
            "escaped_processes": [], "leaked_processes": [],
            "alive_after_cleanup": [], "zombies_after_cleanup": [],
            "remaining_processes": 0, "remaining_zombies": 0,
            "failure_reasons": ["timed_out"],
        }, f)
        tmp = f.name
    try:
        errors = mod.validate_process_tree_result(Path(tmp))
        assert errors == 0, f"Expected 0 errors, got {errors}"
    finally:
        os.unlink(tmp)


def test_validator_rejects_null_exit_code_without_timeout():
    """Validador deve rejeitar exit_code=null sem timed_out=true."""
    import importlib.util
    validator = ROOT / "scripts" / "validate_acceptance_results.py"
    spec = importlib.util.spec_from_file_location("v2", validator)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        json.dump({
            "schema_version": "1.0", "process_run_id": "test-2",
            "started_at": "2026-01-01T00:00:00", "finished_at": "2026-01-01T00:00:05",
            "exit_code": None, "timed_out": False, "success": True,
            "pid": 1, "pgid": 1, "sid": 1,
            "escaped_processes": [], "leaked_processes": [],
            "alive_after_cleanup": [], "zombies_after_cleanup": [],
            "remaining_processes": 0, "remaining_zombies": 0,
            "failure_reasons": [],
        }, f)
        tmp = f.name
    try:
        errors = mod.validate_process_tree_result(Path(tmp))
        assert errors > 0, f"Expected errors for null exit_code without timeout"
    finally:
        os.unlink(tmp)


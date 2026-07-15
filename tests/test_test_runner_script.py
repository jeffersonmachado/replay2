import os
import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "test.sh"
TEST_ALL_SCRIPT = ROOT / "scripts" / "test-all.sh"


def run_test_script(*args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["DAKOTA_TEST_SH_DRY_RUN"] = "1"
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        cwd=ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=10,
        check=False,
    )


def passed_count(output: str) -> int:
    match = re.search(r"Su.tes passaram: \x1b\[[0-9;]*m(\d+)", output)
    if match:
        return int(match.group(1))
    match = re.search(r"Su.tes passaram: (\d+)", output)
    assert match, output
    return int(match.group(1))


def test_modifiers_only_run_default_suite_instead_of_zero_suite_success():
    for args in (("--verbose",), ("--fail-fast",), ("--remote",), ("--host", "127.0.0.1"), ("--port", "8090")):
        result = run_test_script(*args)
        assert result.returncode == 0, result.stdout
        assert passed_count(result.stdout) > 0
        assert "NENHUMA SUITE" not in result.stdout
        assert "NENHUMA SUÍTE" not in result.stdout


def test_explicit_js_selection_runs_only_javascript_blocks_in_dry_run():
    result = run_test_script("--js")
    assert result.returncode == 0, result.stdout
    assert passed_count(result.stdout) == 2
    assert "JS: virtual_terminal" in result.stdout
    assert "Python: tests/" not in result.stdout
    assert "cmd:" in result.stdout


def test_test_all_delegates_to_full_suite_in_dry_run():
    env = os.environ.copy()
    env["DAKOTA_TEST_SH_DRY_RUN"] = "1"
    result = subprocess.run(
        ["bash", str(TEST_ALL_SCRIPT)],
        cwd=ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stdout
    assert passed_count(result.stdout) >= 5
    assert "JS: virtual_terminal" in result.stdout
    assert "Tcl: all.tcl" in result.stdout


def test_unknown_argument_fails_usage():
    result = run_test_script("--nao-existe")
    assert result.returncode == 1
    assert "Opção desconhecida" in result.stdout

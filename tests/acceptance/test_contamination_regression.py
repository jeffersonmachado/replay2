"""Teste de regressao: contaminacao — usa executor compartilhado.

NAO instala pacotes. NAO usa pytest.skip.
NAO cria sessoes aninhadas (sem start_new_session=True interno).
"""
from __future__ import annotations
import json, os, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from process_tree import run_with_timeout


def _check_deps():
    try:
        import websocket
        from PIL import Image
    except ImportError as e:
        raise AssertionError(f"Deps missing: {e}") from e


def test_visual_then_canonical_does_not_hang():
    _check_deps()
    python = sys.executable
    env = {**os.environ, "PYTHONPATH": f"{ROOT}/gateway"}
    r = run_with_timeout(
        [python, "-m", "pytest",
         "tests/test_terminal_snapshot_css_contract.py",
         "tests/test_dakota_terminal_canonical.py", "-q", "--tb=short"],
        timeout=120, cwd=str(ROOT), env=env,
    )
    assert r.exit_code == 0, f"Failed: exit={r.exit_code} alive={r.alive_after_cleanup}"
    assert not r.timed_out
    assert r.alive_after_cleanup == [], f"Leftover: {r.alive_after_cleanup}"
    assert r.zombies_after_cleanup == [], f"Zombies: {r.zombies_after_cleanup}"


def test_contamination_regression_iterations():
    _check_deps()
    python = sys.executable
    env = {**os.environ, "PYTHONPATH": f"{ROOT}/gateway"}
    results = []
    for i in range(3):
        r = run_with_timeout(
            [python, "-m", "pytest",
             "tests/test_terminal_snapshot_css_contract.py::test_terminal_snapshot_box_drawing_renders_with_real_browser_pixels",
             "tests/test_dakota_terminal_canonical.py::DakotaTerminalCanonicalTests::test_python_and_javascript_match_all_shared_vectors",
             "-q", "--tb=short"],
            timeout=90, cwd=str(ROOT), env=env,
        )
        results.append({
            "iteration": i + 1, "exit_code": r.exit_code,
            "timed_out": r.timed_out, "duration_seconds": r.duration_seconds,
            "alive": r.alive_after_cleanup, "zombies": r.zombies_after_cleanup,
        })
        assert r.exit_code == 0, f"Iter {i+1} fail: exit={r.exit_code}"
        assert r.alive_after_cleanup == [], f"Iter {i+1} leftover: {r.alive_after_cleanup}"
        assert r.zombies_after_cleanup == [], f"Iter {i+1} zombies: {r.zombies_after_cleanup}"
        assert not r.timed_out

    out = ROOT / "artifacts/acceptance-logs/results/contamination-regression.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "name": "contamination-regression",
        "iterations": 3,
        "iteration_results": results,
        "exit_code": 0,
        "timed_out": False,
        "escaped_processes": [],
        "leaked_processes": [],
        "remaining_processes": 0,
        "remaining_zombies": 0,
        "success": True,
    }, indent=2))

    for it in results:
        assert it["exit_code"] == 0
        assert not it["timed_out"]
        assert it["alive"] == []
        assert it["zombies"] == []

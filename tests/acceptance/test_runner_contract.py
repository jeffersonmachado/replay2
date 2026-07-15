from __future__ import annotations

from pathlib import Path


def test_acceptance_runner_uses_real_timeout_and_process_group_cleanup():
    runner = Path("scripts/acceptance/_gate_lib.sh").read_text(encoding="utf-8")
    assert "TIMEOUT" in runner
    assert "exec setsid" in runner
    assert 'kill -TERM -- "-$pid"' in runner
    assert 'kill -KILL -- "-$pid"' in runner
    assert "exit_code" in runner
    assert "duration=" in runner
    assert "MISSING required file" in runner
    assert 'export PYTHONPATH="$ROOT_DIR/gateway' in runner


def test_full_gate_invokes_all_phase_gates_and_hash_check():
    full = Path("scripts/acceptance/run-phase-08-full.sh").read_text(encoding="utf-8")
    for phase in ["01-comparison", "02-diffs", "03-sessions", "04-snapshots-gateway", "05-payload-frontend", "06-decoder-fixture", "07-visual-runner"]:
        assert phase in full
    assert "sha256sum -c artifacts/acceptance-test-baseline.sha256" in full


def test_visual_gate_runs_real_chromium_pixel_contract():
    visual = Path("scripts/acceptance/run-phase-07-visual-runner.sh").read_text(encoding="utf-8")
    browser_contract = Path("tests/test_terminal_snapshot_css_contract.py").read_text(encoding="utf-8")
    assert "tests/test_terminal_snapshot_css_contract.py" in visual
    assert "--headless" in browser_contract
    assert "--screenshot=" in browser_contract
    assert "Chromium/Chrome headless is required" in browser_contract
    assert "Pillow is required" in browser_contract

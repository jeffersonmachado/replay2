"""Tests for validate_acceptance_results.py — strict validation.

Run: PYTHONPATH=gateway pytest -q tests/acceptance/test_acceptance_results_validation.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
VALIDATOR = str(ROOT / "scripts" / "validate_acceptance_results.py")


def _run_validator(data: dict, expect_fail: bool = False) -> subprocess.CompletedProcess:
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(data, f)
        path = f.name
    try:
        r = subprocess.run(
            [sys.executable, VALIDATOR, path],
            capture_output=True, text=True, timeout=10)
        if expect_fail:
            assert r.returncode != 0, f"expected failure but got rc=0\n{r.stdout}"
        else:
            assert r.returncode == 0, f"expected success but got rc={r.returncode}\n{r.stderr}"
        return r
    finally:
        os.unlink(path)


def _base_valid_results() -> dict:
    return {
        "schema_version": "1.0",
        "release_run_id": "release-test",
        "run_id": "test-run",
        "generated_at": "2026-07-17T00:00:00+0000",
        "source_tree_sha256_before": "a" * 64,
        "source_tree_sha256_after": "a" * 64,
        "tree_validation_passed": True,
        "no_pending_issues": True,
        "source_tree_unchanged": True,
        "baseline_verified": True,
        "visual_test_verified": True,
        "timeline_verified": True,
        "contamination_regression_verified": True,
        "full_python_suite_passed": True,
        "gateway_suite_passed": True,
        "javascript_suite_passed": True,
        "tcl_suite_passed": True,
        "test_all_passed": True,
        "phase_08_passed": True,
        "remaining_processes": 0,
        "remaining_zombies": 0,
        "commands": {
            "phase-01-comparison": {"name": "phase-01", "success": True, "exit_code": 0, "timed_out": False},
            "phase-02-diffs": {"name": "phase-02", "success": True, "exit_code": 0, "timed_out": False},
            "phase-03-sessions": {"name": "phase-03", "success": True, "exit_code": 0, "timed_out": False},
            "phase-04-snapshots-gateway": {"name": "phase-04", "success": True, "exit_code": 0, "timed_out": False},
            "phase-05-payload-frontend": {"name": "phase-05", "success": True, "exit_code": 0, "timed_out": False},
            "phase-06-decoder-fixture": {"name": "phase-06", "success": True, "exit_code": 0, "timed_out": False},
            "phase-07-visual-runner": {"name": "phase-07", "success": True, "exit_code": 0, "timed_out": False},
            "acceptance-baseline": {"name": "acceptance-baseline", "success": True, "exit_code": 0, "timed_out": False},
            "python-acceptance": {"name": "python-acceptance", "success": True, "exit_code": 0, "timed_out": False},
            "python-full": {"name": "python-full", "success": True, "exit_code": 0, "timed_out": False},
            "gateway-tests": {"name": "gateway-tests", "success": True, "exit_code": 0, "timed_out": False},
            "javascript-tests": {"name": "javascript-tests", "success": True, "exit_code": 0, "timed_out": False},
            "tcl-tests": {"name": "tcl-tests", "success": True, "exit_code": 0, "timed_out": False},
            "test-all": {"name": "test-all", "success": True, "exit_code": 0, "timed_out": False},
            "process-cleanup": {"name": "process-cleanup", "success": True, "exit_code": 0, "timed_out": False},
            "visual-evidence": {"name": "visual-evidence", "success": True, "exit_code": 0, "timed_out": False},
            "contamination-regression": {"name": "contamination-regression", "success": True, "exit_code": 0, "timed_out": False},
        },
    }


# ── 3.7 Contradictory result ─────────────────────────────────────────────────

def test_contradictory_tree_gate_true_gateway_false():
    data = _base_valid_results()
    data["tree_validation_passed"] = True
    data["gateway_suite_passed"] = False
    data["commands"]["gateway-tests"]["success"] = False
    data["commands"]["gateway-tests"]["exit_code"] = 1
    # no_pending_issues must be False when gateway fails
    data["no_pending_issues"] = False  # corrected
    r = _run_validator(data)
    assert "All validations passed" in r.stdout


def test_no_pending_issues_true_with_gateway_false_must_fail():
    data = _base_valid_results()
    data["gateway_suite_passed"] = False
    data["commands"]["gateway-tests"]["success"] = False
    data["commands"]["gateway-tests"]["exit_code"] = 1
    # no_pending_issues=true is a contradiction
    r = _run_validator(data, expect_fail=True)
    assert "no_pending_issues" in (r.stdout + r.stderr).lower()


# ── 3.8 exit_code=null ──────────────────────────────────────────────────────

def test_exit_code_null_rejected():
    data = _base_valid_results()
    data["commands"]["python-full"]["exit_code"] = None
    data["commands"]["python-full"]["success"] = True
    r = _run_validator(data, expect_fail=True)
    assert "exit_code" in (r.stdout + r.stderr).lower()


# ── Commands empty ───────────────────────────────────────────────────────────

def test_empty_commands_rejected():
    data = _base_valid_results()
    data["commands"] = {}
    r = _run_validator(data, expect_fail=True)


# ── success=true with exit_code != 0 ─────────────────────────────────────────

def test_success_true_with_nonzero_exit():
    data = _base_valid_results()
    data["commands"]["python-full"]["success"] = True
    data["commands"]["python-full"]["exit_code"] = 1
    r = _run_validator(data, expect_fail=True)


# ── success=true with timed_out ──────────────────────────────────────────────

def test_success_true_with_timed_out():
    data = _base_valid_results()
    data["commands"]["python-full"]["success"] = True
    data["commands"]["python-full"]["timed_out"] = True
    r = _run_validator(data, expect_fail=True)


# ── Missing required boolean ────────────────────────────────────────────────

def test_missing_required_bool():
    data = _base_valid_results()
    del data["full_python_suite_passed"]
    r = _run_validator(data, expect_fail=True)


# ── Invalid JSON ─────────────────────────────────────────────────────────────

def test_invalid_json_rejected():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        f.write("not json")
        path = f.name
    try:
        r = subprocess.run([sys.executable, VALIDATOR, path], capture_output=True, text=True, timeout=10)
        assert r.returncode != 0
    finally:
        os.unlink(path)


# ── remaining_zombies > 0 ────────────────────────────────────────────────────

def test_zombies_must_be_zero():
    data = _base_valid_results()
    data["remaining_zombies"] = 2
    data["no_pending_issues"] = False  # correct with zombies
    r = _run_validator(data, expect_fail=True)


# ── Valid results pass ──────────────────────────────────────────────────────

def test_valid_results_pass():
    data = _base_valid_results()
    r = _run_validator(data)
    assert "All validations passed" in r.stdout


# ── no_pending_issues contradiction with remaining_processes ──────────────────

def test_no_pending_issues_with_remaining_processes():
    data = _base_valid_results()
    data["remaining_processes"] = 3
    r = _run_validator(data, expect_fail=True)

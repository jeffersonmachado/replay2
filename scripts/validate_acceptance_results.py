#!/usr/bin/env python3
"""Strict acceptance results validator — all contradictions are FATAL errors.

Usage:
  python3 scripts/validate_acceptance_results.py
  python3 scripts/validate_acceptance_results.py <path-to-results.json>
  python3 scripts/validate_acceptance_results.py --all
  python3 scripts/validate_acceptance_results.py --process-tree-result <path>
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


def _error(msg: str) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)


def _ok(msg: str) -> None:
    print(f"  OK: {msg}")


def _is_bool(v: Any) -> bool:
    return isinstance(v, bool)


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _is_str(v: Any) -> bool:
    return isinstance(v, str)


REQUIRED_COMMANDS = [
    "phase-01-comparison", "phase-02-diffs", "phase-03-sessions",
    "phase-04-snapshots-gateway", "phase-05-payload-frontend",
    "phase-06-decoder-fixture", "phase-07-visual-runner",
    "acceptance-baseline", "python-acceptance", "python-full",
    "gateway-tests", "javascript-tests", "tcl-tests", "test-all",
    "process-cleanup", "visual-evidence", "contamination-regression",
]

REQUIRED_FLAGS = [
    "baseline_verified", "visual_test_verified",
    "contamination_regression_verified", "full_python_suite_passed",
    "gateway_suite_passed", "javascript_suite_passed",
    "tcl_suite_passed", "test_all_passed", "source_tree_unchanged",
]


def validate_results_json(path: Path) -> int:
    if not path.exists():
        _error(f"file not found: {path}")
        return 1

    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as e:
        _error(f"invalid JSON: {e}")
        return 1

    errors = 0

    for f in ["schema_version", "release_run_id", "run_id", "generated_at",
              "source_tree_sha256_before", "source_tree_sha256_after"]:
        if not _is_str(data.get(f)):
            _error(f"'{f}' must be string, got {type(data.get(f)).__name__}")
            errors += 1

    for f in ["tree_validation_passed", "no_pending_issues", "source_tree_unchanged",
              "baseline_verified", "visual_test_verified", "timeline_verified",
              "contamination_regression_verified", "full_python_suite_passed",
              "gateway_suite_passed", "javascript_suite_passed", "tcl_suite_passed",
              "test_all_passed", "phase_08_passed"]:
        if not _is_bool(data.get(f)):
            _error(f"'{f}' must be bool, got {type(data.get(f)).__name__}")
            errors += 1

    for f in ["remaining_processes", "remaining_zombies"]:
        v = data.get(f)
        if not isinstance(v, int) or isinstance(v, bool) or v < 0:
            _error(f"'{f}' must be non-negative int, got {type(v).__name__}: {v}")
            errors += 1

    # Strict: remaining_processes and remaining_zombies MUST be 0
    if data.get("remaining_processes", -1) != 0:
        _error(f"remaining_processes must be 0, got {data.get('remaining_processes')}")
        errors += 1
    if data.get("remaining_zombies", -1) != 0:
        _error(f"remaining_zombies must be 0, got {data.get('remaining_zombies')}")
        errors += 1

    commands = data.get("commands")
    if not isinstance(commands, dict) or len(commands) == 0:
        _error("'commands' must be non-empty dict")
        errors += 1
    else:
        for req in REQUIRED_COMMANDS:
            if req not in commands:
                _error(f"required command '{req}' missing from commands")
                errors += 1

        for name, cmd in commands.items():
            if not isinstance(cmd, dict):
                _error(f"commands.{name} must be dict")
                errors += 1
                continue
            # exit_code: must be int, EXCEPT when timed_out=true (then null is acceptable)
            if cmd.get("exit_code") is None:
                if not cmd.get("timed_out"):
                    _error(f"commands.{name}.exit_code is null but timed_out is not true — must be int")
                    errors += 1
            elif not _is_int(cmd.get("exit_code")):
                _error(f"commands.{name}.exit_code must be int")
                errors += 1
            if not _is_bool(cmd.get("success")):
                _error(f"commands.{name}.success must be bool")
                errors += 1
            if cmd.get("success") and cmd.get("exit_code") is not None and cmd.get("exit_code") != 0:
                _error(f"commands.{name}: success=true but exit_code={cmd.get('exit_code')}")
                errors += 1
            if cmd.get("success") and cmd.get("timed_out"):
                _error(f"commands.{name}: success=true but timed_out=true")
                errors += 1

    # ── Cross-field contradictions (ALL FATAL) ──
    if data.get("no_pending_issues") and not data.get("tree_validation_passed"):
        _error("no_pending_issues=true but tree_validation_passed=false")
        errors += 1
    if data.get("no_pending_issues") and data.get("remaining_processes", -1) != 0:
        _error(f"no_pending_issues=true but remaining_processes={data.get('remaining_processes')}")
        errors += 1
    if data.get("no_pending_issues") and data.get("remaining_zombies", -1) != 0:
        _error(f"no_pending_issues=true but remaining_zombies={data.get('remaining_zombies')}")
        errors += 1
    if data.get("tree_validation_passed") and not data.get("phase_08_passed"):
        _error("tree_validation_passed=true but phase_08_passed=false")
        errors += 1

    suite_map = {
        "full_python_suite_passed": "python-full",
        "gateway_suite_passed": "gateway-tests",
        "javascript_suite_passed": "javascript-tests",
        "tcl_suite_passed": "tcl-tests",
        "test_all_passed": "test-all",
    }
    for flag, cmd_name in suite_map.items():
        if data.get(flag) and commands.get(cmd_name, {}).get("success") is False:
            _error(f"{flag}=true but {cmd_name}.success=false")
            errors += 1

    if data.get("no_pending_issues"):
        for f in REQUIRED_FLAGS:
            if not data.get(f):
                _error(f"no_pending_issues=true but {f}=false")
                errors += 1

    if errors == 0:
        _ok(f"final-acceptance-results.json: {len(commands)} commands, all valid")
    return errors


def validate_process_tree_result(path: Path) -> int:
    if not path.exists():
        _error(f"file not found: {path}")
        return 1
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as e:
        _error(f"invalid JSON in {path}: {e}")
        return 1
    errors = 0
    for f in ["schema_version", "process_run_id", "started_at", "finished_at"]:
        if not _is_str(data.get(f)):
            _error(f"{path.name}: '{f}' must be string")
            errors += 1
    # exit_code: must be int, EXCEPT when timed_out=true
    if data.get("exit_code") is None:
        if not data.get("timed_out"):
            _error(f"{path.name}: exit_code is null but timed_out is not true — must be int")
            errors += 1
    elif not _is_int(data.get("exit_code")):
        _error(f"{path.name}: exit_code must be int")
        errors += 1
    for f in ["timed_out", "success"]:
        if not _is_bool(data.get(f)):
            _error(f"{path.name}: '{f}' must be bool")
            errors += 1
    for f in ["pid", "pgid", "sid", "remaining_processes", "remaining_zombies"]:
        v = data.get(f)
        if not isinstance(v, int) or isinstance(v, bool):
            _error(f"{path.name}: '{f}' must be int")
            errors += 1
    for f in ["escaped_processes", "leaked_processes", "alive_after_cleanup",
              "zombies_after_cleanup", "failure_reasons"]:
        if not isinstance(data.get(f), list):
            _error(f"{path.name}: '{f}' must be list")
            errors += 1
    if data.get("success") and data.get("exit_code") is not None and data["exit_code"] != 0:
        _error(f"{path.name}: success=true but exit_code={data['exit_code']}")
        errors += 1
    if data.get("success") and data.get("timed_out"):
        _error(f"{path.name}: success=true but timed_out=true")
        errors += 1
    if data.get("success") and data.get("escaped_processes"):
        _error(f"{path.name}: success=true with escaped_processes")
        errors += 1
    if data.get("success") and data.get("leaked_processes"):
        _error(f"{path.name}: success=true with leaked_processes")
        errors += 1
    if data.get("success") and data.get("remaining_processes", 0) > 0:
        _error(f"{path.name}: success=true with remaining_processes={data.get('remaining_processes')}")
        errors += 1
    if data.get("success") and data.get("remaining_zombies", 0) > 0:
        _error(f"{path.name}: success=true with remaining_zombies={data.get('remaining_zombies')}")
        errors += 1
    if errors == 0:
        _ok(f"{path.name}: exit={data.get('exit_code')} success={data.get('success')}")
    return errors


def main() -> int:
    import argparse
    p = argparse.ArgumentParser(description="Validate acceptance result JSON files")
    p.add_argument("path", nargs="?", default="")
    p.add_argument("--all", action="store_true")
    p.add_argument("--process-tree-result", type=str, default="")
    opts = p.parse_args()

    total_errors = 0
    sd = Path(__file__).resolve().parent.parent
    results_path = Path(opts.path) if opts.path else sd / "artifacts" / "final-acceptance-results.json"

    if opts.process_tree_result:
        total_errors += validate_process_tree_result(Path(opts.process_tree_result))
        return 1 if total_errors > 0 else 0

    total_errors += validate_results_json(results_path)

    if opts.all:
        logs_dir = sd / "artifacts" / "acceptance-logs" / "current"
        if logs_dir.exists():
            for rj in sorted(logs_dir.glob("*.result.json")):
                total_errors += validate_process_tree_result(rj)

    if total_errors == 0:
        print("\nAll validations passed.")
    else:
        print(f"\n{total_errors} validation error(s) found.")
    return 1 if total_errors > 0 else 0


if __name__ == "__main__":
    sys.exit(main())

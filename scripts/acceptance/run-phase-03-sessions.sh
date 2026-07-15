#!/usr/bin/env bash
set -u
. "$(dirname "$0")/_gate_lib.sh"

require_file tests/acceptance/test_strict_global_session_isolation.py
assert_no_unexpected_skip tests/acceptance/test_strict_global_session_isolation.py
run_step phase03-pytest "$ACCEPTANCE_TIMEOUT" python -m pytest -q tests/acceptance/test_strict_global_session_isolation.py tests/test_scanner_and_isolation.py
finish_gate

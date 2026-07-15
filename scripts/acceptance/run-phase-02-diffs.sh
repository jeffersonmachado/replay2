#!/usr/bin/env bash
set -u
. "$(dirname "$0")/_gate_lib.sh"

require_file tests/acceptance/test_diff_protocol_adversarial.py
assert_no_unexpected_skip tests/acceptance/test_diff_protocol_adversarial.py
run_step phase02-pytest "$ACCEPTANCE_TIMEOUT" python -m pytest -q tests/acceptance/test_diff_protocol_adversarial.py tests/test_diff_validation.py
finish_gate

#!/usr/bin/env bash
set -u
. "$(dirname "$0")/_gate_lib.sh"

require_file tests/acceptance/test_runner_contract.py
require_file tests/test_terminal_snapshot_css_contract.py
assert_no_unexpected_skip tests/acceptance/test_runner_contract.py
run_step phase07-pytest "$ACCEPTANCE_TIMEOUT" python -m pytest -q tests/acceptance/test_runner_contract.py tests/test_test_runner_script.py tests/test_terminal_snapshot_css_contract.py
run_step phase07-js "$ACCEPTANCE_TIMEOUT" node --test gateway/control/static/js/components/terminal_snapshot_renderer.test.mjs gateway/control/static/js/components/replay_snapshot_state.test.mjs
finish_gate

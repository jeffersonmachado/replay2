#!/usr/bin/env bash
set -u
. "$(dirname "$0")/_gate_lib.sh"

require_file tests/acceptance/test_comparison_operational.py
require_file tests/acceptance/test_replay_control_canonical_deterministic.py
require_file tests/acceptance/test_replay_semantic_only_uncontrolled.py
assert_no_unexpected_skip tests/acceptance/test_comparison_operational.py
assert_no_unexpected_skip tests/acceptance/test_replay_control_canonical_deterministic.py
assert_no_unexpected_skip tests/acceptance/test_replay_semantic_only_uncontrolled.py
run_step phase01-pytest "$ACCEPTANCE_TIMEOUT" python -m pytest -q tests/acceptance/test_comparison_operational.py tests/acceptance/test_replay_control_canonical_deterministic.py tests/acceptance/test_replay_semantic_only_uncontrolled.py tests/test_comparison_modes.py
run_step phase01-structural 30 bash -c '! rg -n "if not has_canonical and mode == .visual.|mode = .hybrid.  # fallback automatico|got == expected_sig|signature_now\\(\\).*==|== expected_sig" gateway/dakota_gateway/replay.py gateway/dakota_gateway/replay_control.py'
run_step phase01-ast 120 python -m pytest -q tests/acceptance/test_replay_control_canonical_deterministic.py::test_controlled_replay_decision_is_not_screen_sig_only -v
finish_gate

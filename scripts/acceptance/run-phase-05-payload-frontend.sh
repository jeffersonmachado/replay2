#!/usr/bin/env bash
set -u
. "$(dirname "$0")/_gate_lib.sh"

require_file tests/acceptance/test_payload_no_duplication.py
require_file tests/js/acceptance/terminal_payload_adversarial.test.mjs
require_file tests/js/acceptance/playback_event_references.test.mjs
assert_no_unexpected_skip tests/acceptance/test_payload_no_duplication.py
run_step phase05-pytest "$ACCEPTANCE_TIMEOUT" python -m pytest -q tests/acceptance/test_payload_no_duplication.py
run_step phase05-node "$ACCEPTANCE_TIMEOUT" node --test tests/js/acceptance/terminal_payload_adversarial.test.mjs tests/js/acceptance/playback_event_references.test.mjs
finish_gate

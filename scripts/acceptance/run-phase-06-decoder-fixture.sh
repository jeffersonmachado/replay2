#!/usr/bin/env bash
set -u
. "$(dirname "$0")/_gate_lib.sh"

require_file tests/acceptance/test_decoder_warning_contract.py
require_file tests/acceptance/test_raw_capture8_pipeline.py
assert_no_unexpected_skip tests/acceptance/test_decoder_warning_contract.py
assert_no_unexpected_skip tests/acceptance/test_raw_capture8_pipeline.py
run_step phase06-pytest "$ACCEPTANCE_TIMEOUT" python -m pytest -q tests/acceptance/test_decoder_warning_contract.py tests/acceptance/test_raw_capture8_pipeline.py
finish_gate

#!/usr/bin/env bash
set -u
. "$(dirname "$0")/_gate_lib.sh"

require_file tests/acceptance/test_snapshot_contracts.py
require_file tests/acceptance/test_gateway_persisted_out_event.py
require_file tests/acceptance/test_public_api_and_engine_decoder_contract.py
assert_no_unexpected_skip tests/acceptance/test_snapshot_contracts.py
assert_no_unexpected_skip tests/acceptance/test_gateway_persisted_out_event.py
assert_no_unexpected_skip tests/acceptance/test_public_api_and_engine_decoder_contract.py
run_step phase04-pytest "$ACCEPTANCE_TIMEOUT" python -m pytest -q tests/acceptance/test_snapshot_contracts.py tests/acceptance/test_gateway_persisted_out_event.py tests/acceptance/test_public_api_and_engine_decoder_contract.py
finish_gate

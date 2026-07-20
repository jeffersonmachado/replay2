#!/usr/bin/env bash
set -u
. "$(dirname "$0")/_gate_lib.sh"

# Ensure PYTHONPATH is set for all Python steps
export PYTHONPATH="$ROOT_DIR/gateway${PYTHONPATH:+:$PYTHONPATH}"

require_file tests/test_terminal_snapshot_css_contract.py
require_file tests/acceptance/test_contamination_regression.py
require_file tests/acceptance/test_runner_contract.py
require_file gateway/control/static/js/components/terminal_snapshot_renderer.test.mjs
require_file gateway/control/static/js/components/replay_snapshot_state.test.mjs
assert_no_unexpected_skip tests/acceptance/test_contamination_regression.py

PHASE7_ID="phase7-$(date +%Y%m%d-%H%M%S)"
log_gate "PHASE7 id=$PHASE7_ID"

# Remove previous evidence
rm -f artifacts/visual-test-result.json

TREE_HASH=$(python3 scripts/tree_hash.py)
log_gate "PHASE7 tree=$TREE_HASH"

# Validate baseline BEFORE running tests
run_step phase07-baseline-pre 30 sha256sum -c artifacts/acceptance-test-baseline.sha256

# Visual test - clear DAKOTA_PROCESS_RUN_ID so chromium children are not tracked
run_step phase07-visual "$ACCEPTANCE_TIMEOUT" env -u DAKOTA_PROCESS_RUN_ID python -m pytest -q tests/test_terminal_snapshot_css_contract.py

# Anti-false-positive - clear run_id for chromium
run_step phase07-antifalse 30 env -u DAKOTA_PROCESS_RUN_ID python -m pytest -q tests/test_terminal_snapshot_css_contract.py::test_visual_antifalse_positive_chromium_really_started

# Contamination regression (3 iterations inside test) - clear run_id for chromium
run_step phase07-contamination "$ACCEPTANCE_TIMEOUT" env -u DAKOTA_PROCESS_RUN_ID python -m pytest -q tests/acceptance/test_contamination_regression.py

# JS renderer tests
run_step phase07-js-renderer 60 node --test gateway/control/static/js/components/terminal_snapshot_renderer.test.mjs gateway/control/static/js/components/replay_snapshot_state.test.mjs

# Runner contract
run_step phase07-runner "$ACCEPTANCE_TIMEOUT" python -m pytest -q tests/acceptance/test_runner_contract.py

# Adversarial process tests (must run with DAKOTA_PROCESS_RUN_ID for proper tracking)
run_step phase07-process-executor 120 python -m pytest -q tests/acceptance/test_process_tree_executor.py

run_step phase07-gate-escape 900 env -u DAKOTA_PROCESS_RUN_ID python -m pytest -q tests/acceptance/test_gate_process_escape.py

# Evidence validator test
run_step phase07-evidence-validator 60 python -m pytest -q tests/acceptance/test_visual_evidence_validation.py

# Playback test - clear run_id for chromium
run_step phase07-playback 60 env -u DAKOTA_PROCESS_RUN_ID python -m pytest -q tests/test_dakota_terminal_canonical.py -k "test_python_and_javascript"

# Validate evidence
run_step phase07-validate-evidence 30 bash -c "
  E='artifacts/visual-test-result.json'
  test -f \"\$E\" || { echo MISSING; exit 1; }
  python3 -c \"
import json, sys
v=json.load(open('\$E'))
assert v.get('passed')==True, f'passed={v.get(\"passed\")}'
assert v.get('screenshot_created'), 'no screenshot'
assert v.get('screenshot_bytes',0)>0, 'empty screenshot'
assert v.get('pixel_validation_passed'), 'no pixel'
assert v.get('remaining_processes',-1)==0, f'remaining={v.get(\"remaining_processes\")}'
assert v.get('remaining_zombies',-1)==0, f'zombies={v.get(\"remaining_zombies\")}'
assert v.get('timeline_module_loaded')==True, f'timeline_module_loaded={v.get(\"timeline_module_loaded\")}'
assert v.get('timeline_render_calls',0)>=1, f'timeline={v.get(\"timeline_render_calls\")}'
assert v.get('out_rows_created',0)==1, f'out_rows={v.get(\"out_rows_created\")}'
assert v.get('terminal_renderer_calls',0)>=1, f'renderer_calls={v.get(\"terminal_renderer_calls\")}'
assert v.get('renderer_completed')==True, f'renderer_completed={v.get(\"renderer_completed\")}'
assert v.get('out_card_found'), 'no out card'
assert len(v.get('source_tree_sha256',''))==64, f'hash_len={len(v.get(\"source_tree_sha256\",\"\"))}'
assert v.get('source_tree_sha256')!='e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855', 'empty tree hash'
assert v.get('screenshot_sha256') is not None and len(v.get('screenshot_sha256',''))==64, f'screenshot_hash_len={len(v.get(\"screenshot_sha256\",\"\"))}'
assert v.get('run_id','').startswith('visual-'), f'run_id={v.get(\"run_id\")}'
assert v.get('chromium_started')==True
assert v.get('cdp_connected')==True
assert v.get('document_loaded_via_cdp')==True
assert v.get('renderer_marker_found')==True
assert v.get('computed_styles_collected')==True
assert v.get('pixel_analysis_executed')==True
print('Evidence OK')
  \"
"

# Process check (kill remaining chromium aggressively)
sleep 3
# Process check
sleep 3
run_step phase07-process 15 python3 "$ROOT_DIR/scripts/_cleanup_chromium.py"

# Validate baseline AFTER running tests
run_step phase07-baseline-post 30 sha256sum -c artifacts/acceptance-test-baseline.sha256

finish_gate

#!/usr/bin/env bash
set -u
. "$(dirname "$0")/_gate_lib.sh"

# Phase 08: Tree gate only — no packaging, no reporting, no tarball.
# Runs all prior phases + every test suite + process cleanup.
#
# Deduplicação: as suítes Python completa (tests/), gateway/tests/, Tcl e os
# testes JS (lista única em scripts/js-tests.manifest) rodam UMA vez via
# scripts/test-all.sh — o relatório de release lê os resultados estruturados
# em artifacts/acceptance-logs/results/. O passo python-acceptance é mantido
# separado porque o relatório consome seu log dedicado.

for phase in 01-comparison; do
  run_step "phase-$phase" 300 bash "scripts/acceptance/run-phase-$phase.sh"
done
for phase in 02-diffs 03-sessions 04-snapshots-gateway 05-payload-frontend 06-decoder-fixture; do
  run_step "phase-$phase" 240 bash "scripts/acceptance/run-phase-$phase.sh"
done
run_step phase-07-visual-runner 1500 bash scripts/acceptance/run-phase-07-visual-runner.sh
run_step acceptance-baseline 30 sha256sum -c artifacts/acceptance-test-baseline.sha256
run_step python-acceptance 900 env -u DAKOTA_PROCESS_RUN_ID python -m pytest -q tests/acceptance
run_step test-all 1200 env -u DAKOTA_PROCESS_RUN_ID bash scripts/test-all.sh
run_step visual-evidence-exists 10 bash -c '
  test -f artifacts/visual-test-result.json || { echo "MISSING visual-test-result.json"; exit 1; }
  echo "visual evidence present"
'
run_step process-cleanup 30 python3 "$ROOT_DIR/scripts/_cleanup_chromium.py"

finish_gate

#!/usr/bin/env bash
set -u
. "$(dirname "$0")/_gate_lib.sh"

for phase in 01-comparison 02-diffs 03-sessions 04-snapshots-gateway 05-payload-frontend 06-decoder-fixture 07-visual-runner; do
  run_step "phase-$phase" "$ACCEPTANCE_TIMEOUT" bash "scripts/acceptance/run-phase-$phase.sh"
done
run_step acceptance-hash-check 30 sha256sum -c artifacts/acceptance-test-baseline.sha256
run_step python-acceptance "$ACCEPTANCE_TIMEOUT" python -m pytest -q tests/acceptance
run_step js-acceptance "$ACCEPTANCE_TIMEOUT" node --test tests/js/acceptance/*.test.mjs
run_step scripts-test-all "${FULL_TEST_TIMEOUT:-300}" bash scripts/test-all.sh
run_step final-report "$ACCEPTANCE_TIMEOUT" python scripts/acceptance/generate-final-report.py
run_step packaging "$ACCEPTANCE_TIMEOUT" bash scripts/build-tarball.sh
run_step tarball-inspection 30 bash -c '
  set -euo pipefail
  tarball="$(ls -t dist/dakota-replay2-*.tar.gz | head -n 1)"
  test -n "$tarball"
  tar -tzf "$tarball" >/dev/null
  for required in \
    scripts/acceptance \
    tests/acceptance \
    tests/js/acceptance \
    artifacts/acceptance-matrix.json \
    artifacts/acceptance-test-baseline.sha256 \
    artifacts/final-acceptance-report.md \
    artifacts/final-acceptance-results.json \
    artifacts/manual-validation.json \
    artifacts/acceptance-log-summary.json
  do
    tar -tzf "$tarball" | rg "(^|/)${required}(/|$)" >/dev/null
  done
  ! tar -tzf "$tarball" | rg "(^|/)(node_modules|__pycache__|\\.pytest_cache|\\.git|\\.env|logs?|state/captures|chromium|\\.coverage|htmlcov)(/|$)|\\.(pyc|pyo|db|sqlite|sqlite3|pem|key|crt|pfx|ppk|tmp|swp|swo)$|id_rsa|id_ed25519|id_ecdsa|dakota-replay2-.*\\.tar\\.gz"
'
finish_gate

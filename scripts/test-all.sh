#!/bin/sh
# =============================================================================
# test-all.sh — Suite completa estrita (todos os testes obrigatorios + Tcl)
# =============================================================================
set -e
cd "$(dirname "$0")/.."

echo "=== Full Test Suite (strict) ==="

# Bloco JS — todos os testes JavaScript
echo "--- JS: virtual_terminal ---"
node --test gateway/control/static/js/virtual_terminal.test.mjs

echo "--- JS: capture_replay_timeline ---"
node --test gateway/control/static/js/components/capture_replay_timeline.test.mjs

# Bloco Python — todos os testes do diretorio tests/
echo "--- Python: tests/ ---"
PYTHONPATH=gateway python3 -m pytest -q tests/ "$@"

# Bloco Python — gateway unit tests
echo "--- Python: gateway/tests/ ---"
PYTHONPATH=gateway python3 -m pytest -q gateway/tests/ "$@"

# Tcl
echo "--- Tcl ---"
tclsh tests/all.tcl

echo ""
echo "=== All Tests OK ==="

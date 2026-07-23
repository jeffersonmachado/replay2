#!/usr/bin/env bash
# =============================================================================
# test-all.sh — Suite completa com resultados estruturados
# Cada suite gera resultado JSON próprio
# Resumo calculado exclusivamente dos arquivos .result.json
# =============================================================================
set -u

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

RESULT_DIR="$ROOT_DIR/artifacts/acceptance-logs/results"
mkdir -p "$RESULT_DIR"
# Remove resultados de execuções anteriores para não misturar suítes antigas
# (renomeadas/removidas) no resumo desta execução
rm -f "$RESULT_DIR"/test-all-*.result.json "$RESULT_DIR"/test-all-*.log "$RESULT_DIR"/test-all-summary.json 2>/dev/null || true

TIMEOUT="${DAKOTA_TEST_SH_TIMEOUT:-450}"
FAIL_COUNT=0
PASS_COUNT=0
TOTAL=0

# ── Cores ───────────────────────────────────────────────────────────────────
ESC_GREEN='\033[0;32m'
ESC_RED='\033[0;31m'
ESC_BOLD='\033[1m'
ESC_RESET='\033[0m'

run_suite() {
  local name="$1"
  local timeout_s="$2"
  shift 2
  local result_json="$RESULT_DIR/test-all-${name}.result.json"
  local stdout_log="$RESULT_DIR/test-all-${name}.log"
  TOTAL=$((TOTAL + 1))

  printf "${ESC_BOLD}--- %s ---${ESC_RESET}\n" "$name"

  PYTHONPATH="$ROOT_DIR/gateway${PYTHONPATH:+:$PYTHONPATH}" python3 "$ROOT_DIR/scripts/process_tree.py" run \
    --name "$name" \
    --timeout "$timeout_s" \
    --stdout-log "$stdout_log" \
    --result-json "$result_json" \
    -- "$@" >/dev/null 2>&1

  if [ -f "$result_json" ]; then
    local success
    success=$(python3 -c "import json; d=json.load(open('$result_json')); print('true' if d.get('success') else 'false')" 2>/dev/null || echo "false")
    if [ "$success" = "true" ]; then
      printf "  ${ESC_GREEN}[PASS]${ESC_RESET} %s\n" "$name"
      PASS_COUNT=$((PASS_COUNT + 1))
      return 0
    fi
  fi
  printf "  ${ESC_RED}[FAIL]${ESC_RESET} %s\n" "$name"
  FAIL_COUNT=$((FAIL_COUNT + 1))
  return 1
}

echo ""
printf "${ESC_BOLD}=== test-all.sh — Suite Completa ===${ESC_RESET}\n"
echo ""

# ═══════════════════════════════════════════════════════════════════════════
# JavaScript suites — lista única em scripts/js-tests.manifest
# ═══════════════════════════════════════════════════════════════════════════
JS_SEEN=""
if [ -f "$ROOT_DIR/scripts/js-tests.manifest" ]; then
  while IFS= read -r js_test; do
    case "$js_test" in
      ''|'#'*) continue ;;
    esac
    js_base=$(basename "$js_test" .test.mjs)
    # Evita colisão de nomes de suíte quando dois arquivos têm o mesmo basename
    case " $JS_SEEN " in
      *" $js_base "*) js_base="$(basename "$(dirname "$js_test")")-$js_base" ;;
    esac
    JS_SEEN="$JS_SEEN $js_base"
    js_suite="js-$(printf '%s' "$js_base" | tr '_ ' '--')"
    run_suite "$js_suite" 60 node --test "$ROOT_DIR/$js_test" || true
  done < "$ROOT_DIR/scripts/js-tests.manifest"
else
  printf "${ESC_RED}[FAIL]${ESC_RESET} manifesto JS não encontrado: scripts/js-tests.manifest\n"
  FAIL_COUNT=$((FAIL_COUNT + 1))
fi

# Python — COMPLETO, sem exclusões
run_suite python-full "$TIMEOUT" env -u DAKOTA_PROCESS_RUN_ID python -m pytest -q tests/ || true
run_suite gateway-tests 120 env -u DAKOTA_PROCESS_RUN_ID python -m pytest -q gateway/tests/ || true

# Tcl
run_suite tcl-tests 30 env -u DAKOTA_PROCESS_RUN_ID tclsh tests/all.tcl || true

echo ""
printf "${ESC_BOLD}=== Resumo ===${ESC_RESET}\n"
printf "Total: %d | ${ESC_GREEN}Pass: %d${ESC_RESET} | ${ESC_RED}Fail: %d${ESC_RESET}\n" "$TOTAL" "$PASS_COUNT" "$FAIL_COUNT"

# Generate summary JSON
python3 -c "
import json
from pathlib import Path

results_dir = Path('$RESULT_DIR')
suites = []
all_pass = True
for f in sorted(results_dir.glob('test-all-*.result.json')):
    try:
        d = json.loads(f.read_text())
        suites.append({
            'name': d.get('name', f.stem),
            'success': d.get('success', False),
            'exit_code': d.get('exit_code'),
            'timed_out': d.get('timed_out', False),
            'duration_seconds': d.get('duration_seconds', 0),
            'remaining_processes': d.get('remaining_processes', 0),
            'remaining_zombies': d.get('remaining_zombies', 0),
        })
        if not d.get('success'):
            all_pass = False
    except Exception:
        suites.append({'name': f.stem, 'success': False, 'error': 'invalid JSON'})
        all_pass = False

summary = {
    'schema_version': '1.0',
    'total': len(suites),
    'passed': sum(1 for s in suites if s.get('success')),
    'failed': sum(1 for s in suites if not s.get('success')),
    'all_passed': all_pass,
    'suites': suites,
}
(results_dir / 'test-all-summary.json').write_text(json.dumps(summary, indent=2))
print(json.dumps(summary, indent=2))
"

if [ "$FAIL_COUNT" -gt 0 ]; then
  exit 1
fi
exit 0

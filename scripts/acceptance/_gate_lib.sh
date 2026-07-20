#!/usr/bin/env bash
set -u

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ACCEPTANCE_TIMEOUT="${ACCEPTANCE_TIMEOUT:-120}"
FAILED=0

log_gate() {
  printf '[acceptance] %s\n' "$*"
}

require_file() {
  local path="$1"
  if [[ ! -e "$ROOT_DIR/$path" ]]; then
    log_gate "MISSING required file: $path"
    FAILED=1
    return 1
  fi
}

run_step() {
  local name="$1"
  local timeout_s="$2"
  shift 2
  local log_file="$ROOT_DIR/artifacts/acceptance-logs/current/${name}.log"
  local result_json="$ROOT_DIR/artifacts/acceptance-logs/current/${name}.result.json"
  mkdir -p "$(dirname "$log_file")"

  log_gate "START name=$name timeout=${timeout_s}s command=$* log=$log_file"

  local rc=0 duration=0
  local ts_start
  ts_start=$(date +%s)

  # Delegate to process_tree.py for cross-session escape/leak detection + cleanup
  # Command stdout goes to --stdout-log, result JSON to --result-json
  # process_tree's own stdout goes to /dev/null
  PYTHONPATH="$ROOT_DIR/gateway${PYTHONPATH:+:$PYTHONPATH}" python3 "$ROOT_DIR/scripts/process_tree.py" run \
    --name "$name" \
    --timeout "$timeout_s" \
    --stdout-log "$log_file" \
    --result-json "$result_json" \
    -- "$@" >/dev/null 2>&1
  rc=$?

  local ts_end
  ts_end=$(date +%s)
  duration=$((ts_end - ts_start))

  local timed_out=false
  if [ -f "$result_json" ]; then
    timed_out=$(python3 -c "import json; d=json.load(open('$result_json')); print('true' if d.get('timed_out') else 'false')" 2>/dev/null || echo "false")
  fi

  log_gate "END name=$name duration=${duration}s exit_code=$rc timeout=$timed_out"

  if [ "$rc" -ne 0 ]; then
    FAILED=1
  fi
  return "$rc"
}

_kill_tree() {
  local pid="$1"
  # Find all descendants recursively
  _collect_descendants() {
    local p="$1"
    local children
    children=$(pgrep -P "$p" 2>/dev/null || true)
    for c in $children; do
      printf '%s\n' "$c"
      _collect_descendants "$c"
    done
  }
  local all_descendants
  all_descendants=$(_collect_descendants "$pid")
  # Kill session leader's process group
  kill -TERM -- "-$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null || true
  sleep 1
  # Kill any remaining descendants
  for c in $all_descendants; do
    kill -KILL "$c" 2>/dev/null || true
  done
  # Kill process group again
  kill -KILL -- "-$pid" 2>/dev/null || kill -KILL "$pid" 2>/dev/null || true
  sleep 0.5
  # Final: pkill by PGID
  local pgid
  pgid=$(ps -o pgid= -p "$pid" 2>/dev/null | tr -d ' ' || true)
  if [[ -n "$pgid" ]]; then
    pkill -KILL -g "$pgid" 2>/dev/null || true
  fi
}

assert_no_unexpected_skip() {
  local file="$1"
  if [[ -f "$file" ]]; then
    local result
    result=$(python3 -c "
import ast, sys
try:
    tree = ast.parse(open('$file').read())
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Attribute):
                if node.func.attr == 'skip' and isinstance(node.func.value, ast.Name) and node.func.value.id == 'pytest':
                    print(f'FOUND: pytest.skip at line {node.lineno}')
                    sys.exit(1)
    print('CLEAN')
except SyntaxError:
    print('CLEAN')
" 2>/dev/null)
    if [[ "$result" != "CLEAN" ]]; then
      log_gate "UNEXPECTED skip marker in $file: $result"
      FAILED=1
      return 1
    fi
  fi
}

finish_gate() {
  if [[ "$FAILED" -ne 0 ]]; then
    log_gate "GATE FAILED"
    exit 1
  fi
  log_gate "GATE PASSED"
}

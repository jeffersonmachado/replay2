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
  local start end duration rc
  start="$(date +%s)"
  log_gate "START name=$name timeout=${timeout_s}s command=$*"
  (
    cd "$ROOT_DIR" || exit 97
    export PYTHONPATH="$ROOT_DIR/gateway${PYTHONPATH:+:$PYTHONPATH}"
    exec setsid "$@"
  ) &
  local pid=$!
  local deadline=$((start + timeout_s))
  while kill -0 "$pid" 2>/dev/null; do
    if (( "$(date +%s)" >= deadline )); then
      log_gate "TIMEOUT name=$name pid=$pid"
      kill -TERM -- "-$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null || true
      sleep 2
      kill -KILL -- "-$pid" 2>/dev/null || kill -KILL "$pid" 2>/dev/null || true
      wait "$pid" 2>/dev/null || true
      rc=124
      end="$(date +%s)"
      duration=$((end - start))
      log_gate "END name=$name duration=${duration}s exit_code=$rc timeout=true"
      FAILED=1
      return 124
    fi
    sleep 0.2
  done
  wait "$pid"
  rc=$?
  end="$(date +%s)"
  duration=$((end - start))
  log_gate "END name=$name duration=${duration}s exit_code=$rc timeout=false"
  if [[ "$rc" -ne 0 ]]; then
    FAILED=1
  fi
  return "$rc"
}

assert_no_unexpected_skip() {
  local file="$1"
  if [[ -f "$file" ]] && rg -n "pytest\.skip|@pytest\.mark\.skip|skip\(" "$file" >/dev/null; then
    log_gate "UNEXPECTED skip marker in $file"
    FAILED=1
    return 1
  fi
}

finish_gate() {
  if [[ "$FAILED" -ne 0 ]]; then
    log_gate "GATE FAILED"
    exit 1
  fi
  log_gate "GATE PASSED"
}

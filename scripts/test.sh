#!/usr/bin/env bash
# =============================================================================
# test.sh — Script principal de testes do Replay2
#
# Uso:
#   ./scripts/test.sh [OPÇÕES]
#
# Opções (acumulativas, executa todas as suítes selecionadas):
#   --all        Todas as suítes (padrão se nenhuma opção fornecida)
#   --unit       Testes unitários (JS + Python + Tcl)
#   --js         Testes JavaScript (terminal virtual + timeline)
#   --python     Testes Python (tests/ + gateway/tests/)
#   --tcl        Testes Tcl
#   --smoke      Smoke test remoto (requer acesso SSH)
#   --capture    Testes específicos de captura
#   --replay     Testes específicos de replay
#   --integration Testes de integração
#   --quick      Testes rápidos (~10s): apenas a suíte JavaScript
#   --ci         Modo CI: todas as suítes locais, exceto Tcl
#
# Modificadores:
#   --verbose    Saída detalhada (pytest -v)
#   --fail-fast  Parar no primeiro erro (pytest -x)
#   --remote     Executar smoke tests contra servidor remoto
#   --host HOST  Servidor para smoke test (padrão: 10.5.8.24)
#   --port PORT  Porta do servidor (padrão: 8080)
#
# Exemplos:
#   ./scripts/test.sh --quick            # Testes rápidos locais
#   ./scripts/test.sh --unit             # Apenas unitários
#   ./scripts/test.sh --capture --replay # Foco em capture/replay
#   ./scripts/test.sh --smoke --remote --host 10.5.8.25
#   ./scripts/test.sh --ci               # Pipeline CI
#
# Tipos de teste documentados em: TESTES.md
# =============================================================================
set -e

ROOT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
cd "$ROOT_DIR"

# ── Constantes ──────────────────────────────────────────────────────────────
# Lista única de testes JS (ver scripts/js-tests.manifest)
JS_TESTS_MANIFEST="scripts/js-tests.manifest"
JS_TESTS="$(grep -v '^#' "$JS_TESTS_MANIFEST" 2>/dev/null | grep -v '^[[:space:]]*$' || true)"
PYTEST_DIR="tests/"
GW_PYTEST_DIR="gateway/tests/"
TCL_TEST="tests/all.tcl"
SMOKE_SCRIPT="scripts/smoke-test-capture.py"
REPLAY_SMOKE_SCRIPT="scripts/smoke-test-replay.py"

# ── Flags ───────────────────────────────────────────────────────────────────
FLAG_ALL=0
FLAG_UNIT=0
FLAG_JS=0
FLAG_PYTHON=0
FLAG_TCL=0
FLAG_SMOKE=0
FLAG_CAPTURE=0
FLAG_REPLAY=0
FLAG_INTEGRATION=0
FLAG_QUICK=0
FLAG_CI=0
VERBOSE=""
FAIL_FAST=""
REMOTE=0
REMOTE_HOST="10.5.8.24"
REMOTE_PORT="8080"
HAS_ARGS=0  # rastreia se o usuario passou argumentos explicitos
HAS_SUITE_ARGS=0  # rastreia se o usuario selecionou suites explicitamente
DRY_RUN="${DAKOTA_TEST_SH_DRY_RUN:-0}"
BLOCK_TIMEOUT="${DAKOTA_TEST_SH_TIMEOUT:-450}"

# ── Cores ───────────────────────────────────────────────────────────────────
ESC_GREEN='\033[0;32m'
ESC_RED='\033[0;31m'
ESC_YELLOW='\033[0;33m'
ESC_BOLD='\033[1m'
ESC_RESET='\033[0m'

PASS_COUNT=0
FAIL_COUNT=0

# ── Helpers ─────────────────────────────────────────────────────────────────

banner() {
  printf "${ESC_BOLD}=== %s ===${ESC_RESET}\n" "$1"
}

run_with_timeout_pg() {
  local timeout_s="$1"
  local label="$2"
  shift 2
  # Use process_tree.py for escape/leak detection + structured results
  # Logs em log/ (gitignored) — NUNCA em artifacts/, que é evidência de release
  local result_dir="$ROOT_DIR/log/test-sh"
  mkdir -p "$result_dir"
  local safe_label
  safe_label=$(echo "$label" | tr ' /:' '___')
  local result_json="$result_dir/test-sh-${safe_label}.result.json"
  local stdout_log="$result_dir/test-sh-${safe_label}.log"

  PYTHONPATH="$ROOT_DIR/gateway${PYTHONPATH:+:$PYTHONPATH}" python3 "$ROOT_DIR/scripts/process_tree.py" run \
    --name "$label" \
    --timeout "$timeout_s" \
    --stdout-log "$stdout_log" \
    --result-json "$result_json" \
    -- "$@" >/dev/null 2>&1
  local rc=$?

  if [ -f "$result_json" ]; then
    local success
    success=$(python3 -c "import json; d=json.load(open('$result_json')); print('true' if d.get('success') else 'false')" 2>/dev/null || echo "false")
    if [ "$success" = "true" ]; then
      return 0
    fi
  fi
  return 1
}

collect_descendants() {
  local root="$1"
  local direct child
  direct="$(pgrep -P "$root" 2>/dev/null || true)"
  for child in $direct; do
    printf '%s\n' "$child"
    collect_descendants "$child"
  done
}

run_block() {
  local label="$1"
  shift
  local start_ts
  start_ts=$(date +%s)
  printf "${ESC_BOLD}--- %s ---${ESC_RESET}\n" "$label"
  printf "  cmd:"
  printf " %q" "$@"
  printf "\n"
  if [ "$DRY_RUN" = "1" ]; then
    printf "  ${ESC_GREEN}[PASS]${ESC_RESET} %s (dry-run, %ss)\n" "$label" "$(( $(date +%s) - start_ts ))"
    PASS_COUNT=$((PASS_COUNT + 1))
    return 0
  fi
  set +e
  run_with_timeout_pg "$BLOCK_TIMEOUT" "$label" "$@"
  local rc=$?
  set -e
  if [ "$rc" -eq 0 ]; then
    printf "  ${ESC_GREEN}[PASS]${ESC_RESET} %s (%ss)\n" "$label" "$(( $(date +%s) - start_ts ))"
    PASS_COUNT=$((PASS_COUNT + 1))
    return 0
  else
    printf "  ${ESC_RED}[FAIL]${ESC_RESET} %s (%ss)\n" "$label" "$(( $(date +%s) - start_ts ))"
    FAIL_COUNT=$((FAIL_COUNT + 1))
    return 1
  fi
}

require_mandatory_file() {
  local path="$1"
  if [ -f "$path" ]; then
    return 0
  fi
  printf "  ${ESC_RED}[FAIL]${ESC_RESET} %s (arquivo obrigatório não encontrado)\n" "$path"
  FAIL_COUNT=$((FAIL_COUNT + 1))
  return 1
}

require_mandatory_dir() {
  local path="$1"
  if [ -d "$path" ]; then
    return 0
  fi
  printf "  ${ESC_RED}[FAIL]${ESC_RESET} %s (diretório obrigatório não encontrado)\n" "$path"
  FAIL_COUNT=$((FAIL_COUNT + 1))
  return 1
}

pytest_args() {
  if [ -n "$VERBOSE" ]; then printf '%s' "-v "; fi
  if [ -n "$FAIL_FAST" ]; then printf '%s' "-x "; fi
}

# ── Parse arguments ─────────────────────────────────────────────────────────

while [[ $# -gt 0 ]]; do
  case "$1" in
    --all)         FLAG_ALL=1; HAS_ARGS=1; HAS_SUITE_ARGS=1 ;;
    --unit)        FLAG_UNIT=1; HAS_ARGS=1; HAS_SUITE_ARGS=1 ;;
    --js)          FLAG_JS=1; HAS_ARGS=1; HAS_SUITE_ARGS=1 ;;
    --python)      FLAG_PYTHON=1; HAS_ARGS=1; HAS_SUITE_ARGS=1 ;;
    --tcl)         FLAG_TCL=1; HAS_ARGS=1; HAS_SUITE_ARGS=1 ;;
    --smoke)       FLAG_SMOKE=1; HAS_ARGS=1; HAS_SUITE_ARGS=1 ;;
    --capture)     FLAG_CAPTURE=1; HAS_ARGS=1; HAS_SUITE_ARGS=1 ;;
    --replay)      FLAG_REPLAY=1; HAS_ARGS=1; HAS_SUITE_ARGS=1 ;;
    --integration) FLAG_INTEGRATION=1; HAS_ARGS=1; HAS_SUITE_ARGS=1 ;;
    --quick)       FLAG_QUICK=1; HAS_ARGS=1; HAS_SUITE_ARGS=1 ;;
    --ci)          FLAG_CI=1; HAS_ARGS=1; HAS_SUITE_ARGS=1 ;;
    --verbose)     VERBOSE="-v"; HAS_ARGS=1 ;;
    --fail-fast)   FAIL_FAST="-x"; HAS_ARGS=1 ;;
    --remote)      REMOTE=1; HAS_ARGS=1 ;;
    --host)
      if [[ -z "${2:-}" || "${2:0:2}" == "--" ]]; then
        echo "Erro: --host requer um valor"
        exit 1
      fi
      REMOTE_HOST="$2"
      shift
      ;;
    --port)
      if [[ -z "${2:-}" || "${2:0:2}" == "--" ]]; then
        echo "Erro: --port requer um valor"
        exit 1
      fi
      REMOTE_PORT="$2"
      shift
      ;;
    --help|-h)
      echo "Uso: $0 [OPÇÕES]"
      echo "  --all, --unit, --js, --python, --tcl, --smoke, --capture, --replay"
      echo "  --integration, --quick, --ci, --verbose, --fail-fast, --remote"
      echo "  --host HOST, --port PORT"
      exit 0
      ;;
    *)
      echo "Opção desconhecida: $1"
      echo "Use --help para opções"
      exit 1
      ;;
  esac
  shift
done

# ── Resolve combos ──────────────────────────────────────────────────────────

# --quick: JS + smoke sem SSH
if [ "$FLAG_QUICK" = "1" ]; then
  FLAG_JS=1
fi

# --ci: tudo menos Tcl
if [ "$FLAG_CI" = "1" ]; then
  FLAG_JS=1
  FLAG_PYTHON=1
  FLAG_CAPTURE=1
  FLAG_REPLAY=1
  FLAG_INTEGRATION=1
fi

# --unit = JS + Python + Tcl
if [ "$FLAG_UNIT" = "1" ]; then
  FLAG_JS=1
  FLAG_PYTHON=1
  FLAG_TCL=1
fi

# --capture e --replay implicam --python
if [ "$FLAG_CAPTURE" = "1" ] || [ "$FLAG_REPLAY" = "1" ] || [ "$FLAG_INTEGRATION" = "1" ]; then
  FLAG_PYTHON=1
fi

# --all = tudo, ou default se nenhuma suite foi selecionada.
# Modificadores como --verbose, --fail-fast e --remote nao podem deixar
# a execucao com zero suites e falso sucesso.
if [ "$FLAG_ALL" = "1" ] || [ "$HAS_SUITE_ARGS" = "0" ]; then
  FLAG_JS=1
  FLAG_PYTHON=1
  FLAG_TCL=1
  FLAG_CAPTURE=1
  FLAG_REPLAY=1
  FLAG_INTEGRATION=1
fi

# ── Execução ────────────────────────────────────────────────────────────────

echo ""
banner "Replay2 — Suite de Testes"
printf "Modo: %s\n" "$([ "$FLAG_CI" = "1" ] && echo 'CI' || echo 'completo')"
printf "Host remoto: %s:%s\n" "$REMOTE_HOST" "$REMOTE_PORT"
echo ""

# ATENÇÃO (segurança): backdoor de autoteste do PRÓPRIO runner, usada apenas
# pelos testes de desenvolvimento em tests/test_test_runner_script.py.
# Executa um comando ARBITRÁRIO via bash -c a partir da variável de ambiente
# DAKOTA_TEST_SH_SELFTEST_CMD. Nunca defina essa variável em CI, release ou
# produção — qualquer valor presente no ambiente será executado.
if [ -n "${DAKOTA_TEST_SH_SELFTEST_CMD:-}" ]; then
  FLAG_JS=0
  FLAG_PYTHON=0
  FLAG_TCL=0
  FLAG_CAPTURE=0
  FLAG_REPLAY=0
  FLAG_INTEGRATION=0
  FLAG_SMOKE=0
  banner "Runner selftest"
  run_block "Runner: selftest" bash -c "$DAKOTA_TEST_SH_SELFTEST_CMD" || true
fi

# ═══════════════════════════════════════════════════════════════════════════
# Bloco 1: JavaScript
# ═══════════════════════════════════════════════════════════════════════════
if [ "$FLAG_JS" = "1" ]; then
  banner "1. JavaScript"
  for js_test in $JS_TESTS; do
    js_label=$(basename "$js_test" .test.mjs)
    if require_mandatory_file "$js_test"; then
      run_block "JS: $js_label" node --test "$js_test" || true
    fi
  done
  echo ""
fi

# ═══════════════════════════════════════════════════════════════════════════
# Bloco 2: Python
# ═══════════════════════════════════════════════════════════════════════════
if [ "$FLAG_PYTHON" = "1" ]; then
  banner "2. Python"

  if require_mandatory_dir "$PYTEST_DIR"; then
    run_block "Python: tests/" \
      sh -c "PYTHONPATH=gateway python3 -m pytest $(pytest_args) -q $PYTEST_DIR" || true
  fi

  if require_mandatory_dir "$GW_PYTEST_DIR"; then
    run_block "Python: gateway/tests/" \
      sh -c "PYTHONPATH=gateway python3 -m pytest $(pytest_args) -q $GW_PYTEST_DIR" || true
  fi
  echo ""
fi

# ═══════════════════════════════════════════════════════════════════════════
# Bloco 3: Tcl
# ═══════════════════════════════════════════════════════════════════════════
if [ "$FLAG_TCL" = "1" ]; then
  banner "3. Tcl"
  if require_mandatory_file "$TCL_TEST"; then
    run_block "Tcl: all.tcl" tclsh "$TCL_TEST" || true
  fi
  echo ""
fi

# ═══════════════════════════════════════════════════════════════════════════
# Bloco 4: Capture (testes específicos)
# ═══════════════════════════════════════════════════════════════════════════
if [ "$FLAG_CAPTURE" = "1" ]; then
  banner "4. Capture"
  run_block "Python: test_capture_realtime_counting" \
    sh -c "PYTHONPATH=gateway python3 -m pytest $(pytest_args) -q tests/test_capture_realtime_counting.py" || true
  run_block "Python: test_runtime_capture_session" \
    sh -c "PYTHONPATH=gateway python3 -m pytest $(pytest_args) -q tests/test_runtime_capture_session_unit.py" || true
  echo ""
fi

# ═══════════════════════════════════════════════════════════════════════════
# Bloco 5: Replay (testes específicos)
# ═══════════════════════════════════════════════════════════════════════════
if [ "$FLAG_REPLAY" = "1" ]; then
  banner "5. Replay"
  run_block "Python: test_capture8_replay_integration" \
    sh -c "PYTHONPATH=gateway python3 -m pytest $(pytest_args) -q tests/test_capture8_replay_integration.py" || true
  run_block "Python: test_gateway_status_unit (replay)" \
    sh -c "PYTHONPATH=gateway python3 -m pytest $(pytest_args) -q tests/test_gateway_status_unit.py" || true
  echo ""
fi

# ═══════════════════════════════════════════════════════════════════════════
# Bloco 6: Smoke (remoto)
# ═══════════════════════════════════════════════════════════════════════════
if [ "$FLAG_SMOKE" = "1" ]; then
  banner "6. Smoke (remoto)"
  if [ "$REMOTE" = "1" ]; then
    if require_mandatory_file "$SMOKE_SCRIPT"; then
      run_block "Smoke: capture" python3 "$SMOKE_SCRIPT" --host "$REMOTE_HOST" --port "$REMOTE_PORT" || true
    fi
    if require_mandatory_file "$REPLAY_SMOKE_SCRIPT"; then
      run_block "Smoke: replay" python3 "$REPLAY_SMOKE_SCRIPT" --host "$REMOTE_HOST" --port "$REMOTE_PORT" || true
    fi
  else
    printf "  ${ESC_YELLOW}[INFO]${ESC_RESET} Smoke remoto não selecionado; use --remote para executar contra host externo\n"
  fi
  echo ""
fi

# ═══════════════════════════════════════════════════════════════════════════
# Sumário
# ═══════════════════════════════════════════════════════════════════════════
banner "Resultado Final"
printf "Suítes passaram: ${ESC_GREEN}%d${ESC_RESET}\n" "$PASS_COUNT"
printf "Suítes falharam: ${ESC_RED}%d${ESC_RESET}\n" "$FAIL_COUNT"

if [ "$PASS_COUNT" -eq 0 ] && [ "$FAIL_COUNT" -eq 0 ]; then
  echo ""
  printf "${ESC_RED}${ESC_BOLD}NENHUMA SUÍTE FOI EXECUTADA${ESC_RESET}\n"
  exit 1
fi

if [ "$FAIL_COUNT" -gt 0 ]; then
  echo ""
  printf "${ESC_RED}${ESC_BOLD}ALGUNS TESTES FALHARAM${ESC_RESET}\n"
  exit 1
else
  echo ""
  printf "${ESC_GREEN}${ESC_BOLD}Todos os testes passaram${ESC_RESET}\n"
  exit 0
fi

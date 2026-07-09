#!/usr/bin/env bash
# smoke-test.sh — validação end-to-end do stack Replay2
# Uso: ./scripts/smoke-test.sh [--quick]
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

PASS=0
FAIL=0
QUICK="${1:-}"

check() {
    local desc="$1"
    shift
    if "$@" >/dev/null 2>&1; then
        echo -e "  ${GREEN}✓${NC} $desc"
        PASS=$((PASS + 1))
    else
        echo -e "  ${RED}✗${NC} $desc"
        FAIL=$((FAIL + 1))
    fi
}

echo "=============================================="
echo "  Dakota Replay2 — Smoke Test"
echo "  $(date '+%Y-%m-%d %H:%M:%S')"
echo "=============================================="

# ── Python ──
echo ""
echo "── Python ──"
check "python3 disponível" command -v python3

# ── Compileall ──
echo ""
echo "── Compilação ──"
check "compileall gateway/" python3 -m compileall gateway/

# ── Unit tests (rápidos) ──
echo ""
echo "── Testes unitários ──"
check "P2 Knowledge Base (35 testes)" python3 -m pytest \
    tests/test_screen_entity_linker_unit.py \
    tests/test_p2_knowledge_base.py \
    tests/test_capture_knowledge_integrator.py -q

if [ "$QUICK" != "--quick" ]; then
    check "Source Parser + E2E + Screen" python3 -m pytest \
        tests/test_source_parser_inferencer_unit.py \
        tests/test_integrated_pipeline_e2e.py \
        tests/test_screen_registry_unit.py \
        tests/test_screen_contracts.py -q

    check "Gateway (42 testes)" python3 -m pytest \
        gateway/tests/test_roteiro_synthesizer.py -q
fi

# ── Tcl ──
echo ""
echo "── Tcl/Expect ──"
if command -v tclsh >/dev/null 2>&1; then
    check "bin/main.exp (encoding UTF-8)" tclsh bin/main.exp 2>&1 | grep -q "encoding\|plugins_loaded\|Config"
    check "bin/replay2.exp (encoding UTF-8)" tclsh bin/replay2.exp 2>&1 | grep -q "encoding\|Uso\|doctor"
else
    echo -e "  ${YELLOW}⚠${NC} tclsh não disponível — pulando verificação Tcl"
fi

# ── DB ──
echo ""
echo "── Banco de dados ──"
DB_PATH="gateway/state/replay.db"
check "Banco SQLite existe ou pode ser criado" python3 -c "
import sqlite3, os
os.makedirs('gateway/state', exist_ok=True)
con = sqlite3.connect('$DB_PATH')
con.execute('CREATE TABLE IF NOT EXISTS smoke_test (id INTEGER PRIMARY KEY, ts TEXT)')
con.execute('INSERT INTO smoke_test (ts) VALUES (datetime(\"now\"))')
con.execute('DROP TABLE smoke_test')
con.close()
"

# ── Control server ──
echo ""
echo "── Control server ──"
SECRETS_DIR=".local-secrets"
mkdir -p "$SECRETS_DIR"
head -c 32 /dev/urandom > "$SECRETS_DIR/hmac.key" 2>/dev/null || true
head -c 32 /dev/urandom > "$SECRETS_DIR/cookie_secret.key" 2>/dev/null || true

# Inicia servidor em porta alta para teste
TEST_PORT=18990
export PYTHONPATH="$PROJECT_ROOT/gateway${PYTHONPATH:+:$PYTHONPATH}"
python3 gateway/control/server.py \
    --listen "127.0.0.1:$TEST_PORT" \
    --db "$DB_PATH" \
    --cookie-secret-file "$SECRETS_DIR/cookie_secret.key" \
    --hmac-key-file "$SECRETS_DIR/hmac.key" \
    --bootstrap-admin "smoke:SmokeTest1!" \
    > /tmp/replay2-smoke.log 2>&1 &

SERVER_PID=$!
sleep 2

if kill -0 "$SERVER_PID" 2>/dev/null; then
    echo -e "  ${GREEN}✓${NC} servidor iniciado (pid $SERVER_PID)"
    PASS=$((PASS + 1))
else
    echo -e "  ${RED}✗${NC} servidor falhou ao iniciar"
    tail -20 /tmp/replay2-smoke.log
    FAIL=$((FAIL + 1))
fi

# Health endpoints
if kill -0 "$SERVER_PID" 2>/dev/null; then
    check "/health" curl -sf "http://127.0.0.1:$TEST_PORT/health"
    check "/ready" curl -sf "http://127.0.0.1:$TEST_PORT/ready"
    check "/metrics (localhost)" curl -sf "http://127.0.0.1:$TEST_PORT/metrics"
fi

# ── Build ──
echo ""
echo "── Build ──"
check "build-tarball.sh" ./scripts/build-tarball.sh

# ── Cleanup ──
if [ -n "${SERVER_PID:-}" ] && kill -0 "$SERVER_PID" 2>/dev/null; then
    kill "$SERVER_PID" 2>/dev/null || true
    echo -e "  ${GREEN}✓${NC} servidor parado"
fi
rm -f /tmp/replay2-smoke.log

# ── Resultado ──
echo ""
echo "=============================================="
TOTAL=$((PASS + FAIL))
if [ "$FAIL" -eq 0 ]; then
    echo -e "  ${GREEN}Resultado: $PASS/$TOTAL passaram${NC}"
else
    echo -e "  ${RED}Resultado: $PASS/$TOTAL passaram, $FAIL falharam${NC}"
fi
echo "=============================================="

exit $FAIL

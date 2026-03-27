#!/bin/bash
#
# quick-test-web.sh - Teste rápido da interface web
# Uso: bash tests/quick-test-web.sh
#

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

GATEWAY_DIR="$PROJECT_ROOT/gateway"
PYTHONPATH_VALUE="$GATEWAY_DIR"
TMPDIR="/tmp/dakota-quick-test-$$"
DB_FILE="$TMPDIR/replay.db"
HMAC_KEY_FILE="$TMPDIR/hmac.key"
COOKIE_SECRET_FILE="$TMPDIR/cookie_secret.key"
COOKIE_JAR="$TMPDIR/cookies.txt"
TEST_PORT="$(python3 - <<'PY'
import socket
s = socket.socket()
s.bind(("127.0.0.1", 0))
print(s.getsockname()[1])
s.close()
PY
)"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Cleanup on exit
cleanup() {
    echo ""
    echo -e "${BLUE}Limpando...${NC}"
    kill $SERVER_PID 2>/dev/null || true
    rm -rf "$TMPDIR"
    echo -e "${GREEN}Done!${NC}"
}
trap cleanup EXIT

# Setup
echo -e "${BLUE}=== Dakota Replay2 - Quick Web Test ===${NC}"
echo ""
echo -e "${YELLOW}1. Setup${NC}"

mkdir -p "$TMPDIR"
head -c 32 /dev/urandom > "$HMAC_KEY_FILE"
head -c 32 /dev/urandom > "$COOKIE_SECRET_FILE"

echo -e "  Temp dir: ${GREEN}$TMPDIR${NC}"
echo -e "  DB: ${GREEN}$DB_FILE${NC}"

# Start server
echo ""
echo -e "${YELLOW}2. Iniciando Control Server${NC}"

export PYTHONPATH="$PYTHONPATH_VALUE"

python3 -u "$GATEWAY_DIR/control/server.py" \
  --listen 127.0.0.1:$TEST_PORT \
  --db "$DB_FILE" \
  --cookie-secret-file "$COOKIE_SECRET_FILE" \
  --hmac-key-file "$HMAC_KEY_FILE" \
  --bootstrap-admin admin:admin123 > /tmp/server_start_$$.log 2>&1 &

SERVER_PID=$!
PORT="$TEST_PORT"

# Wait for server process and TCP port readiness
for i in {1..40}; do
    if ! kill -0 "$SERVER_PID" 2>/dev/null; then
        break
    fi
    if python3 - <<PY
import socket
s = socket.socket()
s.settimeout(0.2)
ok = s.connect_ex(("127.0.0.1", int("$TEST_PORT"))) == 0
s.close()
raise SystemExit(0 if ok else 1)
PY
    then
        break
    fi
    sleep 0.25
done

BASE_URL="http://127.0.0.1:$PORT"

if [ -z "$PORT" ] || ! kill -0 "$SERVER_PID" 2>/dev/null; then
    echo -e "${RED}❌ Failed to start server${NC}"
    cat /tmp/server_start_$$.log
    exit 1
fi

echo -e "  Server running on: ${GREEN}$BASE_URL${NC}"

# Wait for server to be ready
for i in {1..10}; do
    if curl -s "$BASE_URL/login" > /dev/null 2>&1; then
        break
    fi
    sleep 0.5
done

# Tests
echo ""
echo -e "${YELLOW}3. Executando Testes${NC}"

PASS=0
FAIL=0

test_endpoint() {
    local method=$1
    local path=$2
    local data=$3
    local expected_status=$4
    local description=$5
    local auth=${6:-true}
    
    echo -n "  Test: $description... "

        if [ "$auth" = true ]; then
                if [ -z "$data" ]; then
                        STATUS=$(curl -s -o /dev/null -w "%{http_code}" \
                            -X "$method" "$BASE_URL$path" \
                            -b "$COOKIE_JAR" \
                            -H "Content-Type: application/json")
                else
                        STATUS=$(curl -s -o /dev/null -w "%{http_code}" \
                            -X "$method" "$BASE_URL$path" \
                            -b "$COOKIE_JAR" \
                            -H "Content-Type: application/json" \
                            -d "$data")
                fi
        else
                if [ -z "$data" ]; then
                        STATUS=$(curl -s -o /dev/null -w "%{http_code}" \
                            -X "$method" "$BASE_URL$path" \
                            -H "Content-Type: application/json")
                else
                        STATUS=$(curl -s -o /dev/null -w "%{http_code}" \
                            -X "$method" "$BASE_URL$path" \
                            -H "Content-Type: application/json" \
                            -d "$data")
                fi
        fi
    
    if [ "$STATUS" = "$expected_status" ]; then
        echo -e "${GREEN}✓ ($STATUS)${NC}"
        PASS=$((PASS + 1))
    else
        echo -e "${RED}✗ (got $STATUS, expected $expected_status)${NC}"
        FAIL=$((FAIL + 1))
    fi
}

# Test 1: Login page
test_endpoint "GET" "/login" "" "200" "GET /login (login page)" "false"

# Test 2: Login success
echo -n "  Test: POST /api/login (success)... "
STATUS=$(curl -s -o /dev/null -w "%{http_code}" \
  -X POST "$BASE_URL/api/login" \
    -c "$COOKIE_JAR" \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"admin123"}')
if [ "$STATUS" = "200" ]; then
    echo -e "${GREEN}✓ ($STATUS)${NC}"
    PASS=$((PASS + 1))
else
    echo -e "${RED}✗ (got $STATUS)${NC}"
    FAIL=$((FAIL + 1))
fi

# Test 3: Login failure
echo -n "  Test: POST /api/login (failure)... "
STATUS=$(curl -s -o /dev/null -w "%{http_code}" \
  -X POST "$BASE_URL/api/login" \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"wrongpass"}')
if [ "$STATUS" = "401" ]; then
    echo -e "${GREEN}✓ ($STATUS)${NC}"
    PASS=$((PASS + 1))
else
    echo -e "${RED}✗ (got $STATUS, expected 401)${NC}"
    FAIL=$((FAIL + 1))
fi

# Test 4: Get me
echo -n "  Test: GET /api/me (current user)... "
RESULT=$(curl -s -b "$COOKIE_JAR" "$BASE_URL/api/me")
if echo "$RESULT" | grep -Eq '"username"[[:space:]]*:[[:space:]]*"admin"'; then
    echo -e "${GREEN}✓${NC}"
    PASS=$((PASS + 1))
else
    echo -e "${RED}✗${NC}"
    FAIL=$((FAIL + 1))
fi

# Test 5: Get runs (empty)
echo -n "  Test: GET /api/runs (empty list)... "
RESULT=$(curl -s -b "$COOKIE_JAR" "$BASE_URL/api/runs")
if echo "$RESULT" | grep -q '"runs"'; then
    echo -e "${GREEN}✓${NC}"
    PASS=$((PASS + 1))
else
    echo -e "${RED}✗${NC}"
    FAIL=$((FAIL + 1))
fi

# Test 6: Create run
echo -n "  Test: POST /api/runs (create)... "
RUN_RESULT=$(curl -s -b "$COOKIE_JAR" \
  -X POST "$BASE_URL/api/runs" \
  -H "Content-Type: application/json" \
  -d '{
    "log_dir": "/tmp/test-log",
    "target_host": "test.example.com",
    "target_user": "testuser",
    "mode": "strict-global"
  }')

RUN_ID=$(echo "$RUN_RESULT" | grep -oP '"id"[[:space:]]*:[[:space:]]*\K[0-9]+' | head -1)
if [ -n "$RUN_ID" ]; then
    echo -e "${GREEN}✓ (run_id=$RUN_ID)${NC}"
    PASS=$((PASS + 1))
else
    echo -e "${RED}✗${NC}"
    FAIL=$((FAIL + 1))
    RUN_ID="unknown"
fi

# Test 7: Get runs (non-empty)
echo -n "  Test: GET /api/runs (with run)... "
RESULT=$(curl -s -b "$COOKIE_JAR" "$BASE_URL/api/runs")
if echo "$RESULT" | grep -q "$RUN_ID"; then
    echo -e "${GREEN}✓${NC}"
    PASS=$((PASS + 1))
else
    echo -e "${RED}✗${NC}"
    FAIL=$((FAIL + 1))
fi

# Test 8: Dashboard
echo -n "  Test: GET / (dashboard)... "
STATUS=$(curl -s -o /dev/null -w "%{http_code}" \
    -b "$COOKIE_JAR" \
  "$BASE_URL/")
if [ "$STATUS" = "200" ]; then
    echo -e "${GREEN}✓${NC}"
    PASS=$((PASS + 1))
else
    echo -e "${RED}✗ (got $STATUS)${NC}"
    FAIL=$((FAIL + 1))
fi

# Summary
echo ""
echo -e "${BLUE}=== Resumo ===${NC}"
echo -e "  Passou: ${GREEN}$PASS${NC}"
echo -e "  Falhou: ${RED}$FAIL${NC}"

if [ $FAIL -eq 0 ]; then
    echo ""
    echo -e "${GREEN}✅ Todos os testes passaram!${NC}"
    exit 0
else
    echo ""
    echo -e "${RED}❌ Alguns testes falharam${NC}"
    exit 1
fi

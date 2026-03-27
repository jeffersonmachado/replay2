#!/bin/bash
#
# quick-test-web.sh - Teste rápido da interface web
# Uso: bash tests/quick-test-web.sh
#

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

GATEWAY_DIR="$PROJECT_ROOT/gateway"
TMPDIR="/tmp/dakota-quick-test-$$"
DB_FILE="$TMPDIR/replay.db"
HMAC_KEY_FILE="$TMPDIR/hmac.key"
COOKIE_SECRET_FILE="$TMPDIR/cookie_secret.key"

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

python3 "$GATEWAY_DIR/control/server.py" \
  --listen 127.0.0.1:0 \
  --db "$DB_FILE" \
  --cookie-secret-file "$COOKIE_SECRET_FILE" \
  --hmac-key-file "$HMAC_KEY_FILE" \
  --bootstrap-admin admin:admin123 > /tmp/server_start_$$.log 2>&1 &

SERVER_PID=$!
sleep 1

# Extract port from log
PORT=$(grep "listening on http://127.0.0.1:" /tmp/server_start_$$.log | grep -oP ':\K[0-9]+' | head -1)
BASE_URL="http://127.0.0.1:$PORT"

if [ -z "$PORT" ]; then
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
    
    # Get auth cookie if needed
    if [ "$auth" = true ]; then
        COOKIES=$(curl -s -c - -X POST "$BASE_URL/api/login" \
          -H "Content-Type: application/json" \
          -d '{"username":"admin","password":"admin123"}' | grep dakota_session | awk '{print $NF}')
        COOKIE_HEADER="-b dakota_session=$COOKIES"
    else
        COOKIE_HEADER=""
    fi
    
    if [ -z "$data" ]; then
        STATUS=$(curl -s -o /dev/null -w "%{http_code}" \
          -X "$method" "$BASE_URL$path" \
          $COOKIE_HEADER \
          -H "Content-Type: application/json")
    else
        STATUS=$(curl -s -o /dev/null -w "%{http_code}" \
          -X "$method" "$BASE_URL$path" \
          $COOKIE_HEADER \
          -H "Content-Type: application/json" \
          -d "$data")
    fi
    
    if [ "$STATUS" = "$expected_status" ]; then
        echo -e "${GREEN}✓ ($STATUS)${NC}"
        ((PASS++))
    else
        echo -e "${RED}✗ (got $STATUS, expected $expected_status)${NC}"
        ((FAIL++))
    fi
}

# Test 1: Login page
test_endpoint "GET" "/login" "" "200" "GET /login (login page)" "false"

# Test 2: Login success
echo -n "  Test: POST /api/login (success)... "
STATUS=$(curl -s -o /dev/null -w "%{http_code}" \
  -X POST "$BASE_URL/api/login" \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"admin123"}')
if [ "$STATUS" = "200" ]; then
    echo -e "${GREEN}✓ ($STATUS)${NC}"
    ((PASS++))
else
    echo -e "${RED}✗ (got $STATUS)${NC}"
    ((FAIL++))
fi

# Get cookie for auth tests
COOKIES=$(curl -s -c - -X POST "$BASE_URL/api/login" \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"admin123"}' | grep dakota_session | awk '{print $NF}')

# Test 3: Login failure
echo -n "  Test: POST /api/login (failure)... "
STATUS=$(curl -s -o /dev/null -w "%{http_code}" \
  -X POST "$BASE_URL/api/login" \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"wrongpass"}')
if [ "$STATUS" = "401" ]; then
    echo -e "${GREEN}✓ ($STATUS)${NC}"
    ((PASS++))
else
    echo -e "${RED}✗ (got $STATUS, expected 401)${NC}"
    ((FAIL++))
fi

# Test 4: Get me
echo -n "  Test: GET /api/me (current user)... "
RESULT=$(curl -s -b "dakota_session=$COOKIES" "$BASE_URL/api/me")
if echo "$RESULT" | grep -q '"username":"admin"'; then
    echo -e "${GREEN}✓${NC}"
    ((PASS++))
else
    echo -e "${RED}✗${NC}"
    ((FAIL++))
fi

# Test 5: Get runs (empty)
echo -n "  Test: GET /api/runs (empty list)... "
RESULT=$(curl -s -b "dakota_session=$COOKIES" "$BASE_URL/api/runs")
if echo "$RESULT" | grep -q '"runs"'; then
    echo -e "${GREEN}✓${NC}"
    ((PASS++))
else
    echo -e "${RED}✗${NC}"
    ((FAIL++))
fi

# Test 6: Create run
echo -n "  Test: POST /api/runs (create)... "
RUN_RESULT=$(curl -s -b "dakota_session=$COOKIES" \
  -X POST "$BASE_URL/api/runs" \
  -H "Content-Type: application/json" \
  -d '{
    "log_dir": "/tmp/test-log",
    "target_host": "test.example.com",
    "target_user": "testuser",
    "mode": "strict-global"
  }')

RUN_ID=$(echo "$RUN_RESULT" | grep -oP '"id":\K[0-9]+' | head -1)
if [ -n "$RUN_ID" ]; then
    echo -e "${GREEN}✓ (run_id=$RUN_ID)${NC}"
    ((PASS++))
else
    echo -e "${RED}✗${NC}"
    ((FAIL++))
    RUN_ID="unknown"
fi

# Test 7: Get runs (non-empty)
echo -n "  Test: GET /api/runs (with run)... "
RESULT=$(curl -s -b "dakota_session=$COOKIES" "$BASE_URL/api/runs")
if echo "$RESULT" | grep -q "$RUN_ID"; then
    echo -e "${GREEN}✓${NC}"
    ((PASS++))
else
    echo -e "${RED}✗${NC}"
    ((FAIL++))
fi

# Test 8: Dashboard
echo -n "  Test: GET / (dashboard)... "
STATUS=$(curl -s -o /dev/null -w "%{http_code}" \
  -b "dakota_session=$COOKIES" \
  "$BASE_URL/")
if [ "$STATUS" = "200" ]; then
    echo -e "${GREEN}✓${NC}"
    ((PASS++))
else
    echo -e "${RED}✗ (got $STATUS)${NC}"
    ((FAIL++))
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

#!/bin/sh
# =============================================================================
# smoke-test-capture.sh — Valida o pipeline de captura via API HTTP
#
# Verifica:
#  1. Health/ready do control plane
#  2. Login e obtenção de sessão
#  3. Listagem de capturas
#  4. Detalhe de captura (GET /api/captures/{id})
#  5. Sessões dentro da captura (GET /api/captures/{id}/sessions)
#  6. Eventos da captura (GET /api/captures/{id}/events)
#
# Uso:
#   ./scripts/smoke-test-capture.sh [--host HOST] [--port PORT]
#
# Requisitos: python3, curl (ou python3 urllib)
# =============================================================================
set -e

HOST="10.5.8.24"
PORT="8080"
ADMIN_USER="admin"
ADMIN_PASS="Dakota@2026!"

PASS=0
FAIL=0

pass() { printf '  [PASS] %s\n' "$1"; PASS=$((PASS + 1)); }
fail() { printf '  [FAIL] %s — %s\n' "$1" "$2"; FAIL=$((FAIL + 1)); }

# ── Parse args ──────────────────────────────────────────────────────────────
while [ $# -gt 0 ]; do
  case "$1" in
    --host) HOST="$2"; shift 2 ;;
    --port) PORT="$2"; shift 2 ;;
    --user) ADMIN_USER="$2"; shift 2 ;;
    --pass) ADMIN_PASS="$2"; shift 2 ;;
    *) echo "Opção desconhecida: $1"; exit 1 ;;
  esac
done

BASE_URL="http://${HOST}:${PORT}"
echo "=== Smoke Test: Capture ==="
echo "Servidor: ${BASE_URL}"
echo ""

# ── Helper: HTTP request via python3 com cookie jar ─────────────────────────
COOKIE_JAR=$(mktemp /tmp/smoke-capture-cookies.XXXXXX)
cleanup() { rm -f "$COOKIE_JAR"; }
trap cleanup EXIT

http() {
  # Uso: http METHOD PATH [BODY_JSON]
  python3 -c "
import urllib.request, json, sys, os, http.cookiejar

cookie_jar = http.cookiejar.MozillaCookieJar()
cookie_file = '$COOKIE_JAR'
if os.path.exists(cookie_file) and os.path.getsize(cookie_file) > 0:
    try:
        cookie_jar.load(cookie_file, ignore_discard=True, ignore_expires=True)
    except Exception:
        pass

opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookie_jar))

method = '${1}'
path = '${2}'
body_json = '''${3:-}'''
url = '${BASE_URL}' + path
data = body_json.encode() if body_json else None
req = urllib.request.Request(url, data=data, method=method)
req.add_header('Content-Type', 'application/json')
try:
    resp = opener.open(req, timeout=10)
    cookie_jar.save(cookie_file, ignore_discard=True, ignore_expires=True)
    print(resp.status)
    body = resp.read().decode()[:2000]
    print(body if body.strip() else '{}')
except urllib.error.HTTPError as e:
    cookie_jar.save(cookie_file, ignore_discard=True, ignore_expires=True)
    print(e.code)
    body = e.read().decode()[:1000]
    print(body if body.strip() else '{}')
except Exception as e:
    print('0')
    print('{\"error\":\"' + str(e).replace('\"', '\\\\\"') + '\"}')
"
}

# ── 1. Health ───────────────────────────────────────────────────────────────
echo "--- 1. Health/Ready ---"
STATUS=$(http GET /health 2>/dev/null | head -1 || echo "0")
if [ "$STATUS" = "200" ]; then
  pass "GET /health → 200"
else
  fail "GET /health" "status=$STATUS"
fi

STATUS=$(http GET /ready 2>/dev/null | head -1 || echo "0")
if [ "$STATUS" = "200" ]; then
  pass "GET /ready → 200"
else
  fail "GET /ready" "status=$STATUS"
fi
echo ""

# ── 2. Login ────────────────────────────────────────────────────────────────
echo "--- 2. Autenticação ---"
LOGIN_RESP=$(http POST /api/login "{\"username\":\"${ADMIN_USER}\",\"password\":\"${ADMIN_PASS}\"}" 2>/dev/null)
LOGIN_STATUS=$(echo "$LOGIN_RESP" | head -1)
if [ "$LOGIN_STATUS" = "200" ]; then
  pass "POST /api/login → 200"
else
  fail "POST /api/login" "status=$LOGIN_STATUS"
fi
echo ""

# ── 3. Listagem de capturas ─────────────────────────────────────────────────
echo "--- 3. Listagem ---"
CAPTURES_RESP=$(http GET /api/captures 2>/dev/null)
CAPTURES_STATUS=$(echo "$CAPTURES_RESP" | head -1)
CAPTURES_BODY=$(echo "$CAPTURES_RESP" | tail -n +2)

if [ "$CAPTURES_STATUS" = "200" ]; then
  pass "GET /api/captures → 200"
  TOTAL=$(echo "$CAPTURES_BODY" | python3 -c "import sys,json; d=json.loads(sys.stdin.read()); print(d.get('total',0))" 2>/dev/null || echo "?")
  echo "         total de capturas: $TOTAL"
else
  fail "GET /api/captures" "status=$CAPTURES_STATUS"
fi
echo ""

# ── 4. Detalhe da primeira captura ──────────────────────────────────────────
echo "--- 4. Detalhe ---"
FIRST_ID=$(echo "$CAPTURES_BODY" | python3 -c "
import sys,json
d=json.loads(sys.stdin.read())
caps=d.get('captures',[])
print(caps[0]['id'] if caps else '')
" 2>/dev/null)

if [ -n "$FIRST_ID" ] && [ "$FIRST_ID" != "" ]; then
  DETAIL_RESP=$(http GET "/api/captures/${FIRST_ID}" 2>/dev/null)
  DETAIL_STATUS=$(echo "$DETAIL_RESP" | head -1)
  if [ "$DETAIL_STATUS" = "200" ]; then
    pass "GET /api/captures/${FIRST_ID} → 200"
    DETAIL_BODY=$(echo "$DETAIL_RESP" | tail -n +2)
    CAP_STATUS=$(echo "$DETAIL_BODY" | python3 -c "import sys,json; d=json.loads(sys.stdin.read()); print(d.get('status','?'))" 2>/dev/null)
    CAP_SESSIONS=$(echo "$DETAIL_BODY" | python3 -c "import sys,json; d=json.loads(sys.stdin.read()); print(d.get('session_count','?'))" 2>/dev/null)
    CAP_EVENTS=$(echo "$DETAIL_BODY" | python3 -c "import sys,json; d=json.loads(sys.stdin.read()); print(d.get('event_count','?'))" 2>/dev/null)
    echo "         status=$CAP_STATUS sessions=$CAP_SESSIONS events=$CAP_EVENTS"
  else
    fail "GET /api/captures/${FIRST_ID}" "status=$DETAIL_STATUS"
  fi

  # ── 5. Sessões ────────────────────────────────────────────────────────────
  echo "--- 5. Sessões ---"
  SESSIONS_RESP=$(http GET "/api/captures/${FIRST_ID}/sessions" 2>/dev/null)
  SESSIONS_STATUS=$(echo "$SESSIONS_RESP" | head -1)
  if [ "$SESSIONS_STATUS" = "200" ]; then
    pass "GET /api/captures/${FIRST_ID}/sessions → 200"
    SESSIONS_BODY=$(echo "$SESSIONS_RESP" | tail -n +2)
    SESSION_COUNT=$(echo "$SESSIONS_BODY" | python3 -c "import sys,json; d=json.loads(sys.stdin.read()); s=d.get('sessions',[]); print(len(s))" 2>/dev/null)
    echo "         total de sessões: $SESSION_COUNT"

    # ── 6. Replay da primeira sessão ────────────────────────────────────────
    FIRST_SESSION=$(echo "$SESSIONS_BODY" | python3 -c "
import sys,json
d=json.loads(sys.stdin.read())
s=d.get('sessions',[])
print(s[0].get('session_id','') if s else '')
" 2>/dev/null)
    if [ -n "$FIRST_SESSION" ]; then
      echo "--- 6. Replay ---"
      REPLAY_RESP=$(http GET "/api/captures/${FIRST_ID}/replay?session_id=${FIRST_SESSION}" 2>/dev/null)
      REPLAY_STATUS=$(echo "$REPLAY_RESP" | head -1)
      if [ "$REPLAY_STATUS" = "200" ]; then
        pass "GET /api/captures/${FIRST_ID}/replay?session_id=... → 200"
        REPLAY_BODY=$(echo "$REPLAY_RESP" | tail -n +2)
        HAS_GEOMETRY=$(echo "$REPLAY_BODY" | python3 -c "import sys,json; d=json.loads(sys.stdin.read()); g=d.get('geometry',{}); print('yes' if g.get('rows') else 'no')" 2>/dev/null)
        HAS_TIMELINE=$(echo "$REPLAY_BODY" | python3 -c "import sys,json; d=json.loads(sys.stdin.read()); t=d.get('timeline',[]); print(len(t))" 2>/dev/null)
        HAS_PLAYBACK=$(echo "$REPLAY_BODY" | python3 -c "import sys,json; d=json.loads(sys.stdin.read()); p=d.get('playback',{}); print(p.get('event_count',0))" 2>/dev/null)
        echo "         geometry=$HAS_GEOMETRY timeline_events=$HAS_TIMELINE playback_events=$HAS_PLAYBACK"
      else
        fail "GET replay" "status=$REPLAY_STATUS"
      fi
    else
      fail "Sessão replay" "nenhuma sessão disponível na captura"
    fi
  else
    fail "GET sessions" "status=$SESSIONS_STATUS"
  fi

  # ── 7. Eventos ────────────────────────────────────────────────────────────
  echo "--- 7. Eventos ---"
  EVENTS_RESP=$(http GET "/api/captures/${FIRST_ID}/events" 2>/dev/null)
  EVENTS_STATUS=$(echo "$EVENTS_RESP" | head -1)
  if [ "$EVENTS_STATUS" = "200" ]; then
    pass "GET /api/captures/${FIRST_ID}/events → 200"
    EVENTS_BODY=$(echo "$EVENTS_RESP" | tail -n +2)
    EVENT_COUNT=$(echo "$EVENTS_BODY" | python3 -c "import sys,json; d=json.loads(sys.stdin.read()); e=d.get('events',[]); print(len(e))" 2>/dev/null)
    echo "         eventos retornados: $EVENT_COUNT"
  else
    fail "GET events" "status=$EVENTS_STATUS"
  fi
else
  echo "--- 4-7. Pulados (sem capturas disponíveis) ---"
fi
echo ""

# ── Sumário ─────────────────────────────────────────────────────────────────
echo "=== Resultado: Capture Smoke ==="
echo "Pass: $PASS | Fail: $FAIL"
if [ "$FAIL" -gt 0 ]; then
  exit 1
fi
exit 0

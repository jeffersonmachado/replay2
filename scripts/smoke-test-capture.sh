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

HOST="${TARGET_HOST:-127.0.0.1}"
PORT="${TARGET_PORT:-8080}"
ADMIN_USER="${ADMIN_USER:-}"
ADMIN_PASS="${ADMIN_PASS:-}"

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
    resp = opener.open(req, timeout=30)  # 30s: AIX sob carga responde replay em 4-15s
    cookie_jar.save(cookie_file, ignore_discard=True, ignore_expires=True)
    print(resp.status)
    body = resp.read().decode()[:500000]
    print(body if body.strip() else '{}')
except urllib.error.HTTPError as e:
    cookie_jar.save(cookie_file, ignore_discard=True, ignore_expires=True)
    print(e.code)
    body = e.read().decode()[:500000]
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
CAPTURES_BODY=$(printf '%s' "$CAPTURES_RESP" | tail -n +2)

if [ "$CAPTURES_STATUS" = "200" ]; then
  pass "GET /api/captures → 200"
  TOTAL=$(printf '%s' "$CAPTURES_BODY" | python3 -c "import sys,json; d=json.loads(sys.stdin.read()); print(d.get('total',0))" 2>/dev/null || echo "?")
  echo "         total de capturas: $TOTAL"
else
  fail "GET /api/captures" "status=$CAPTURES_STATUS"
fi
echo ""

# ── 4. Detalhe da captura escolhida ─────────────────────────────────────────
# Para os passos 4-7 usa a MENOR captura com conteúdo real (event_count>10):
# capturas enormes (ex.: >50k eventos) levam dezenas de segundos por endpoint
# e estouram o timeout do smoke — o objetivo é validar o pipeline, não medir
# desempenho com sessões gigantes.
echo "--- 4. Detalhe ---"
FIRST_ID=$(printf '%s' "$CAPTURES_BODY" | python3 -c "
import sys,json
d=json.loads(sys.stdin.read())
caps=d.get('captures',[])
com_conteudo=[c for c in caps if c.get('session_count',0)>0 and c.get('event_count',0)>10]
com_sessao=[c for c in caps if c.get('session_count',0)>0]
pool=com_conteudo or com_sessao or caps
escolhida=min(pool, key=lambda c: c.get('event_count',0)) if pool else None
print(escolhida['id'] if escolhida else '')
" 2>/dev/null)

if [ -n "$FIRST_ID" ] && [ "$FIRST_ID" != "" ]; then
  DETAIL_RESP=$(http GET "/api/captures/${FIRST_ID}" 2>/dev/null)
  DETAIL_STATUS=$(echo "$DETAIL_RESP" | head -1)
  if [ "$DETAIL_STATUS" = "200" ]; then
    pass "GET /api/captures/${FIRST_ID} → 200"
    DETAIL_BODY=$(printf '%s' "$DETAIL_RESP" | tail -n +2)
    CAP_STATUS=$(printf '%s' "$DETAIL_BODY" | python3 -c "import sys,json; d=json.loads(sys.stdin.read()); print(d.get('status','?'))" 2>/dev/null)
    CAP_SESSIONS=$(printf '%s' "$DETAIL_BODY" | python3 -c "import sys,json; d=json.loads(sys.stdin.read()); print(d.get('session_count','?'))" 2>/dev/null)
    CAP_EVENTS=$(printf '%s' "$DETAIL_BODY" | python3 -c "import sys,json; d=json.loads(sys.stdin.read()); print(d.get('event_count','?'))" 2>/dev/null)
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
    SESSIONS_BODY=$(printf '%s' "$SESSIONS_RESP" | tail -n +2)
    SESSION_COUNT=$(printf '%s' "$SESSIONS_BODY" | python3 -c "import sys,json; d=json.loads(sys.stdin.read()); s=d.get('sessions',[]); print(len(s))" 2>/dev/null)
    echo "         total de sessões: $SESSION_COUNT"

    # ── 6. Replay da menor sessão com conteúdo ────────────────────────────────
    # Sessões vazias (bytes_out=0) não têm replay; sessões enormes (ex.: >50k
    # eventos) estouram o timeout do smoke. O objetivo é validar o pipeline.
    REPLAY_PICK=$(printf '%s' "$CAPTURES_BODY" | python3 -c "
import sys,json,http.cookiejar,urllib.request
d=json.loads(sys.stdin.read())
caps=d.get('captures',[])
jar=http.cookiejar.MozillaCookieJar('$COOKIE_JAR')
try:
    jar.load(ignore_discard=True, ignore_expires=True)
except Exception:
    pass
opener=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
best=None
for cap in caps:
    if cap.get('event_count',0)>10000:
        continue
    try:
        resp=opener.open('${BASE_URL}/api/captures/%s/sessions' % cap['id'], timeout=10)
        s=json.loads(resp.read())
    except Exception:
        continue
    for sess in s.get('sessions',[]):
        sid=sess.get('session_id','')
        if sid and sess.get('bytes_out',0)>0:
            cnt=sess.get('event_count',0)
            if best is None or cnt<best[2]:
                best=(cap['id'],sid,cnt)
print(f'{best[0]}|{best[1]}' if best else '')
" 2>/dev/null)
    if [ -n "$REPLAY_PICK" ]; then
      REPLAY_CID=$(echo "$REPLAY_PICK" | cut -d'|' -f1)
      FIRST_SESSION=$(echo "$REPLAY_PICK" | cut -d'|' -f2)
      echo "--- 6. Replay ---"
      REPLAY_RESP=$(http GET "/api/captures/${REPLAY_CID}/replay?session_id=${FIRST_SESSION}&limit=20" 2>/dev/null)
      REPLAY_STATUS=$(echo "$REPLAY_RESP" | head -1)
      if [ "$REPLAY_STATUS" = "200" ]; then
        pass "GET /api/captures/${REPLAY_CID}/replay?session_id=... → 200"
        REPLAY_BODY=$(printf '%s' "$REPLAY_RESP" | tail -n +2)
        HAS_GEOMETRY=$(printf '%s' "$REPLAY_BODY" | python3 -c "import sys,json; d=json.loads(sys.stdin.read()); g=d.get('geometry',{}); print('yes' if g.get('rows') else 'no')" 2>/dev/null)
        HAS_TIMELINE=$(printf '%s' "$REPLAY_BODY" | python3 -c "import sys,json; d=json.loads(sys.stdin.read()); t=d.get('timeline_items') or []; print(len(t))" 2>/dev/null)
        HAS_PLAYBACK=$(printf '%s' "$REPLAY_BODY" | python3 -c "import sys,json; d=json.loads(sys.stdin.read()); p=d.get('playback',{}); print(p.get('event_count',0))" 2>/dev/null)
        echo "         capture=$REPLAY_CID geometry=$HAS_GEOMETRY timeline_events=$HAS_TIMELINE playback_events=$HAS_PLAYBACK"
      else
        fail "GET replay" "status=$REPLAY_STATUS"
      fi
    else
      fail "Sessão replay" "nenhuma sessão com conteúdo (bytes_out>0) nas capturas"
    fi
  else
    fail "GET sessions" "status=$SESSIONS_STATUS"
  fi

  # ── 7. Eventos ────────────────────────────────────────────────────────────
  echo "--- 7. Eventos ---"
  EVENTS_RESP=$(http GET "/api/captures/${FIRST_ID}/events?limit=20" 2>/dev/null)
  EVENTS_STATUS=$(echo "$EVENTS_RESP" | head -1)
  if [ "$EVENTS_STATUS" = "200" ]; then
    pass "GET /api/captures/${FIRST_ID}/events → 200"
    EVENTS_BODY=$(printf '%s' "$EVENTS_RESP" | tail -n +2)
    EVENT_COUNT=$(printf '%s' "$EVENTS_BODY" | python3 -c "import sys,json; d=json.loads(sys.stdin.read()); e=d.get('events',[]); print(len(e))" 2>/dev/null)
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

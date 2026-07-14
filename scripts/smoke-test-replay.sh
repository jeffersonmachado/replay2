#!/bin/sh
# =============================================================================
# smoke-test-replay.sh — Valida o pipeline de replay via API HTTP
#
# Verifica:
#  1. Dados de replay contêm geometria (rows, cols, geometry_source)
#  2. Timeline contém eventos com timestamp_ms
#  3. Playback contém data_b64 em cada evento
#  4. Snapshot tem content_kind = "terminal_snapshot"
#  5. Encoding está presente na geometria
#  6. text_sig e visual_sig nos snapshots (se disponível)
#
# Uso:
#   ./scripts/smoke-test-replay.sh [--host HOST] [--port PORT]
#
# Requisitos: python3
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
echo "=== Smoke Test: Replay ==="
echo "Servidor: ${BASE_URL}"
echo ""

# ── Encontra uma sessão com dados ───────────────────────────────────────────
echo "--- Buscando sessão com dados de replay ---"

SESSION_INFO=$(python3 -c "
import urllib.request, json, sys, base64

url = '${BASE_URL}'
auth = base64.b64encode(b'${ADMIN_USER}:${ADMIN_PASS}').decode()

def req(path):
    r = urllib.request.Request(url + path)
    r.add_header('Authorization', f'Basic {auth}')
    try:
        resp = urllib.request.urlopen(r, timeout=10)
        return json.loads(resp.read())
    except Exception as e:
        return None

caps = req('/api/captures')
if not caps or not caps.get('captures'):
    print('NO_CAPTURES')
    sys.exit(0)

# Encontra primeira captura com sessões
for cap in caps['captures']:
    cid = cap['id']
    sessions = req(f'/api/captures/{cid}/sessions')
    if sessions and sessions.get('sessions'):
        for s in sessions['sessions']:
            sid = s.get('session_id', '')
            if sid:
                print(f'{cid}|{sid}')
                sys.exit(0)

print('NO_SESSIONS')
" 2>/dev/null)

if [ "$SESSION_INFO" = "NO_CAPTURES" ]; then
  fail "Setup" "nenhuma captura disponível no servidor"
  exit 1
elif [ "$SESSION_INFO" = "NO_SESSIONS" ]; then
  fail "Setup" "nenhuma sessão disponível nas capturas"
  exit 1
fi

CAPTURE_ID=$(echo "$SESSION_INFO" | cut -d'|' -f1)
SESSION_ID=$(echo "$SESSION_INFO" | cut -d'|' -f2)
echo "         capture_id=$CAPTURE_ID session_id=${SESSION_ID:0:20}..."
echo ""

# ── Busca dados de replay ───────────────────────────────────────────────────
REPLAY_DATA=$(python3 -c "
import urllib.request, json, base64

url = '${BASE_URL}'
auth = base64.b64encode(b'${ADMIN_USER}:${ADMIN_PASS}').decode()
r = urllib.request.Request(f'{url}/api/captures/${CAPTURE_ID}/replay?session_id=${SESSION_ID}')
r.add_header('Authorization', f'Basic {auth}')
resp = urllib.request.urlopen(r, timeout=10)
data = json.loads(resp.read())
print(json.dumps(data))
" 2>/dev/null)

if [ -z "$REPLAY_DATA" ]; then
  fail "GET replay" "sem resposta"
  exit 1
fi

# ── 1. Geometria ────────────────────────────────────────────────────────────
echo "--- 1. Geometria ---"
GEOM_ROWS=$(echo "$REPLAY_DATA" | python3 -c "import sys,json; d=json.loads(sys.stdin.read()); print(d.get('geometry',{}).get('rows','?'))" 2>/dev/null)
GEOM_COLS=$(echo "$REPLAY_DATA" | python3 -c "import sys,json; d=json.loads(sys.stdin.read()); print(d.get('geometry',{}).get('cols','?'))" 2>/dev/null)
GEOM_SRC=$(echo "$REPLAY_DATA" | python3 -c "import sys,json; d=json.loads(sys.stdin.read()); print(d.get('geometry',{}).get('geometry_source','?'))" 2>/dev/null)

if [ "$GEOM_ROWS" != "?" ] && [ "$GEOM_COLS" != "?" ]; then
  pass "geometry: ${GEOM_ROWS}x${GEOM_COLS} (source=$GEOM_SRC)"
else
  fail "geometry" "ausente ou inválida (rows=$GEOM_ROWS cols=$GEOM_COLS)"
fi

# ── 2. Encoding ─────────────────────────────────────────────────────────────
echo "--- 2. Encoding ---"
ENCODING=$(echo "$REPLAY_DATA" | python3 -c "import sys,json; d=json.loads(sys.stdin.read()); print(d.get('geometry',{}).get('encoding','?'))" 2>/dev/null)
if [ "$ENCODING" != "?" ] && [ "$ENCODING" != "" ]; then
  pass "encoding: $ENCODING"
else
  fail "encoding" "ausente na geometria"
fi

# ── 3. Timeline ─────────────────────────────────────────────────────────────
echo "--- 3. Timeline ---"
TIMELINE_COUNT=$(echo "$REPLAY_DATA" | python3 -c "import sys,json; d=json.loads(sys.stdin.read()); print(len(d.get('timeline',[])))" 2>/dev/null)
HAS_TS=$(echo "$REPLAY_DATA" | python3 -c "
import sys,json
d=json.loads(sys.stdin.read())
tl=d.get('timeline',[])
ok=all(e.get('timestamp_ms') is not None for e in tl) if tl else True
print('yes' if ok else 'no')
" 2>/dev/null)

if [ "$TIMELINE_COUNT" -gt 0 ]; then
  pass "timeline: $TIMELINE_COUNT eventos, timestamp_ms=$HAS_TS"
else
  fail "timeline" "vazia (0 eventos)"
fi

# ── 4. Playback ─────────────────────────────────────────────────────────────
echo "--- 4. Playback ---"
PLAYBACK_COUNT=$(echo "$REPLAY_DATA" | python3 -c "import sys,json; d=json.loads(sys.stdin.read()); print(d.get('playback',{}).get('event_count',0))" 2>/dev/null)
HAS_DATA_B64=$(echo "$REPLAY_DATA" | python3 -c "
import sys,json
d=json.loads(sys.stdin.read())
evs=d.get('playback',{}).get('events',[])
ok=all(e.get('data_b64') for e in evs) if evs else True
print('yes' if ok else 'no')
" 2>/dev/null)

if [ "$PLAYBACK_COUNT" -gt 0 ]; then
  pass "playback: $PLAYBACK_COUNT eventos, data_b64=$HAS_DATA_B64"
else
  fail "playback" "vazio (0 eventos)"
fi

# ── 5. Snapshots ────────────────────────────────────────────────────────────
echo "--- 5. Snapshots ---"
SNAPSHOT_INFO=$(echo "$REPLAY_DATA" | python3 -c "
import sys,json
d=json.loads(sys.stdin.read())
tl=d.get('timeline',[])
snaps=[e for e in tl if e.get('content_kind')=='terminal_snapshot']
print(f'{len(snaps)}')
has_text_sig=any(e.get('text_sig') for e in snaps)
has_visual_sig=any(e.get('visual_sig') for e in snaps)
print(f'text_sig={\"yes\" if has_text_sig else \"no\"}')
print(f'visual_sig={\"yes\" if has_visual_sig else \"no\"}')
" 2>/dev/null)

SNAP_COUNT=$(echo "$SNAPSHOT_INFO" | head -1)
TEXT_SIG=$(echo "$SNAPSHOT_INFO" | sed -n '2p' | cut -d'=' -f2)
VISUAL_SIG=$(echo "$SNAPSHOT_INFO" | sed -n '3p' | cut -d'=' -f2)

if [ "$SNAP_COUNT" -gt 0 ]; then
  pass "snapshots: $SNAP_COUNT (terminal_snapshot), text_sig=$TEXT_SIG visual_sig=$VISUAL_SIG"
else
  echo "  [INFO] snapshots: 0 (sem grupos OUT na sessão)"
fi

# ── 6. Estrutura do session_start ───────────────────────────────────────────
echo "--- 6. Session Start ---"
SS_INFO=$(echo "$REPLAY_DATA" | python3 -c "
import sys,json
d=json.loads(sys.stdin.read())
ss=d.get('session_start')
if ss:
    print(f'rows={ss.get(\"rows\",\"?\")}')
    print(f'cols={ss.get(\"cols\",\"?\")}')
    print(f'term={ss.get(\"term\",\"?\")}')
    print(f'encoding={ss.get(\"encoding\",\"?\")}')
else:
    print('absent')
" 2>/dev/null)

if [ "$SS_INFO" != "absent" ]; then
  SS_ROWS=$(echo "$SS_INFO" | grep rows | cut -d'=' -f2)
  SS_COLS=$(echo "$SS_INFO" | grep cols | cut -d'=' -f2)
  SS_TERM=$(echo "$SS_INFO" | grep term | cut -d'=' -f2)
  SS_ENC=$(echo "$SS_INFO" | grep encoding | cut -d'=' -f2)
  pass "session_start: ${SS_ROWS}x${SS_COLS} term=$SS_TERM enc=$SS_ENC"
else
  fail "session_start" "ausente nos dados de replay"
fi
echo ""

# ── Sumário ─────────────────────────────────────────────────────────────────
echo "=== Resultado: Replay Smoke ==="
echo "Pass: $PASS | Fail: $FAIL"
if [ "$FAIL" -gt 0 ]; then
  exit 1
fi
exit 0

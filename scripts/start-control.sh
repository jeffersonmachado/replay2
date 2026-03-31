#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

LOG_DIR="${LOG_DIR:-$PROJECT_ROOT/log}"
LISTEN="${LISTEN:-127.0.0.1:8090}"
DB_PATH="${DB_PATH:-gateway/state/replay.db}"
SECRETS_DIR="${SECRETS_DIR:-.local-secrets}"
COOKIE_SECRET_FILE="${COOKIE_SECRET_FILE:-$SECRETS_DIR/cookie_secret.key}"
HMAC_KEY_FILE="${HMAC_KEY_FILE:-$SECRETS_DIR/hmac.key}"
BOOTSTRAP_ADMIN="${BOOTSTRAP_ADMIN:-${DAKOTA_ADMIN:-}}"
BOOTSTRAP_ADMIN="${BOOTSTRAP_ADMIN:-admin:Admin123!}"
LOG_FILE="${LOG_FILE:-$LOG_DIR/replay2-control.log}"
PID_FILE="${PID_FILE:-/tmp/replay2-control.pid}"

is_listen_busy() {
  local host_port="$1"
  ss -ltn 2>/dev/null | awk -v hp="$host_port" 'index($0, hp) { found=1 } END { exit found ? 0 : 1 }'
}

port_pids() {
  local host_port="$1"
  ss -ltnp 2>/dev/null \
    | awk -v hp="$host_port" '
        index($0, hp) {
          while (match($0, /pid=[0-9]+/)) {
            pid = substr($0, RSTART + 4, RLENGTH - 4)
            print pid
            $0 = substr($0, RSTART + RLENGTH)
          }
        }
      ' \
    | sort -u
}

mkdir -p "$(dirname "$DB_PATH")" "$SECRETS_DIR" "$LOG_DIR"

if [[ ! -f "$HMAC_KEY_FILE" ]]; then
  head -c 32 /dev/urandom > "$HMAC_KEY_FILE"
fi

if [[ ! -f "$COOKIE_SECRET_FILE" ]]; then
  head -c 32 /dev/urandom > "$COOKIE_SECRET_FILE"
fi

if [[ -f "$PID_FILE" ]]; then
  OLD_PID="$(cat "$PID_FILE" 2>/dev/null || true)"
  if [[ -n "$OLD_PID" ]] && kill -0 "$OLD_PID" 2>/dev/null; then
    echo "Control server ja esta em execucao (pid $OLD_PID)."
    exit 0
  fi
fi

if is_listen_busy "$LISTEN"; then
  BUSY_PIDS="$(port_pids "$LISTEN" || true)"
  echo "Falha ao iniciar control server: endereco $LISTEN ja esta em uso."
  if [[ -n "$BUSY_PIDS" ]]; then
    echo "PIDs ouvindo em $LISTEN: $BUSY_PIDS"
  fi
  echo "Execute ./scripts/stop-control.sh para limpar processos antigos e tente novamente."
  exit 1
fi

export PYTHONPATH="$PROJECT_ROOT/gateway${PYTHONPATH:+:$PYTHONPATH}"

CMD=(
  python3 gateway/control/server.py
  --listen "$LISTEN"
  --db "$DB_PATH"
  --cookie-secret-file "$COOKIE_SECRET_FILE"
  --hmac-key-file "$HMAC_KEY_FILE"
)

if [[ -n "$BOOTSTRAP_ADMIN" ]]; then
  CMD+=(--bootstrap-admin "$BOOTSTRAP_ADMIN")
fi

nohup "${CMD[@]}" > "$LOG_FILE" 2>&1 &

PID=$!
echo "$PID" > "$PID_FILE"

sleep 1
if ! kill -0 "$PID" 2>/dev/null; then
  echo "Falha ao iniciar control server. Ultimas linhas do log:"
  tail -n 30 "$LOG_FILE" || true
  exit 1
fi

echo "Control server iniciado."
echo "URL: http://$LISTEN"
echo "PID: $PID"
echo "LOG: $LOG_FILE"

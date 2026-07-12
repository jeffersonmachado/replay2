#!/usr/bin/bash --
# Wrapper de captura de sessao SSH para o Replay2
# Controla o ForceCommand do usuario results no sshd_config.
# O "--" no shebang impede que -l do sshd AIX seja interpretado como flag.

# sshd do AIX 7 injeta -l como primeiro argumento ao ForceCommand
case "$1" in
  -l|-lc) shift ;;
esac

PROJECT_ROOT=/opt/dakota/replay2
DB_PATH=/opt/dakota/replay2/gateway/state/replay.db
HMAC_KEY_FILE=/opt/dakota/replay2/.local-secrets/hmac-key
PYTHON_BIN=/usr/bin/python3

fallback_login() {
  if [ -n "${SSH_ORIGINAL_COMMAND:-}" ]; then
    exec /bin/ksh -c "$SSH_ORIGINAL_COMMAND"
  fi
  exec /bin/ksh
}

# Verifica se ha captura ativa antes de iniciar o gateway
if ! "$PYTHON_BIN" -c "
import sqlite3
con = sqlite3.connect('$DB_PATH')
try:
    row = con.execute(\"SELECT id FROM capture_sessions WHERE status='active' ORDER BY id DESC LIMIT 1\").fetchone()
    raise SystemExit(0 if row else 1)
finally:
    con.close()
" >/dev/null 2>&1; then
  fallback_login
fi

export PYTHONPATH="$PROJECT_ROOT/gateway${PYTHONPATH:+:$PYTHONPATH}"
cd "$PROJECT_ROOT"

exec "$PYTHON_BIN" "$PROJECT_ROOT/gateway/dakota-gateway" capture-session \
  --db "$DB_PATH" \
  --hmac-key-file "$HMAC_KEY_FILE" \
  --source-user "${LOGNAME:-${USER:-}}" \
  "$@"

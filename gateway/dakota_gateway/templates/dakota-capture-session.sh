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
CAPTURE_SOCKET=/opt/dakota/replay2/gateway/state/daemon/capture.sock
PYTHON_BIN=/usr/bin/python3

# A escrita auditavel (hash-chain/HMAC/arquivos) e feita pelo capture-daemon
# (usuario de servico); este processo roda como o usuario SSH final e apenas
# envia eventos via socket — nao precisa da chave HMAC nem do banco.

# Como o sshd tradicional, a sessao inicia no HOME do usuario: profiles do
# legado usam caminhos relativos ao CWD (ex.: o ".menu" do Recital).
cd "$HOME" 2>/dev/null || cd /

fallback_login() {
  if [ -n "${SSH_ORIGINAL_COMMAND:-}" ]; then
    exec /bin/ksh -c "$SSH_ORIGINAL_COMMAND"
  fi
  # ksh do AIX nao aceita flag -l; "exec -a -ksh" simula login shell
  # (argv[0] com '-'), fazendo o ksh ler /etc/profile e ~/.profile
  user_shell="${SHELL:-/bin/ksh}"
  exec -a "-$(basename "$user_shell")" "$user_shell"
}

export PYTHONPATH="$PROJECT_ROOT/gateway${PYTHONPATH:+:$PYTHONPATH}"

# Verifica se ha captura ativa (via daemon) antes de iniciar o gateway.
# Daemon fora do ar ou sem captura ativa -> login normal (mesma semantica
# do pre-check anterior, que caia no fallback quando a consulta falhava).
if ! "$PYTHON_BIN" "$PROJECT_ROOT/gateway/dakota-gateway" capture-resolve \
    --socket "$CAPTURE_SOCKET" >/dev/null 2>&1; then
  fallback_login
fi

exec "$PYTHON_BIN" "$PROJECT_ROOT/gateway/dakota-gateway" capture-session \
  --db "$DB_PATH" \
  --capture-socket "$CAPTURE_SOCKET" \
  --source-user "${LOGNAME:-${USER:-}}" \
  "$@"

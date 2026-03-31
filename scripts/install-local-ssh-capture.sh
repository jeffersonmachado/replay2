#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DB_PATH="${DB_PATH:-$PROJECT_ROOT/gateway/state/replay.db}"
HMAC_KEY_FILE="${HMAC_KEY_FILE:-$PROJECT_ROOT/.local-secrets/hmac.key}"
PYTHON_BIN="${PYTHON_BIN:-$(command -v python3 || true)}"
WRAPPER_PATH="${WRAPPER_PATH:-/usr/local/bin/dakota-capture-session}"
CONFIG_NAME="${CONFIG_NAME:-90-dakota-capture.conf}"
SSHD_CONFIG_DIR="${SSHD_CONFIG_DIR:-/etc/ssh/sshd_config.d}"
MATCH_USER="${MATCH_USER:-}"
RELOAD_SSHD=1

info() { printf '%s\n' "$*"; }
die() { printf '%s\n' "Erro: $*" >&2; exit 1; }
have_cmd() { command -v "$1" >/dev/null 2>&1; }
is_root() { [ "$(id -u 2>/dev/null || echo 1)" = "0" ]; }

sudo_or_die() {
  if is_root; then
    "$@"
    return 0
  fi
  if have_cmd sudo; then
    sudo "$@"
    return 0
  fi
  die "precisa de root (ou sudo) para instalar a configuracao do sshd."
}

usage() {
  cat <<EOF
Uso:
  ./scripts/install-local-ssh-capture.sh --match-user <usuario> [opcoes]

Opcoes:
  --match-user <usuario>   Usuario SSH que deve passar pelo capture-session
  --db <arquivo>           Caminho do replay.db (default: $DB_PATH)
  --hmac-key-file <file>   Caminho da chave HMAC (default: $HMAC_KEY_FILE)
  --python <bin>           Python a usar no wrapper (default: python3 do PATH)
  --wrapper-path <file>    Wrapper ForceCommand (default: $WRAPPER_PATH)
  --config-name <nome>     Nome do snippet em sshd_config.d (default: $CONFIG_NAME)
  --no-reload              Nao tenta recarregar o sshd ao final
  --help                   Mostra esta ajuda

Exemplo:
  ./scripts/install-local-ssh-capture.sh --match-user teste
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --match-user)
      shift
      [ $# -gt 0 ] || die "falta valor para --match-user"
      MATCH_USER="$1"
      ;;
    --db)
      shift
      [ $# -gt 0 ] || die "falta valor para --db"
      DB_PATH="$1"
      ;;
    --hmac-key-file)
      shift
      [ $# -gt 0 ] || die "falta valor para --hmac-key-file"
      HMAC_KEY_FILE="$1"
      ;;
    --python)
      shift
      [ $# -gt 0 ] || die "falta valor para --python"
      PYTHON_BIN="$1"
      ;;
    --wrapper-path)
      shift
      [ $# -gt 0 ] || die "falta valor para --wrapper-path"
      WRAPPER_PATH="$1"
      ;;
    --config-name)
      shift
      [ $# -gt 0 ] || die "falta valor para --config-name"
      CONFIG_NAME="$1"
      ;;
    --no-reload)
      RELOAD_SSHD=0
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      die "opcao desconhecida: $1"
      ;;
  esac
  shift
done

[ -n "$MATCH_USER" ] || die "informe --match-user <usuario>"
[ -n "$PYTHON_BIN" ] || die "python3 nao encontrado no PATH"
[ -f "$DB_PATH" ] || die "banco nao encontrado: $DB_PATH"
[ -f "$HMAC_KEY_FILE" ] || die "chave HMAC nao encontrada: $HMAC_KEY_FILE"
[ -f "$PROJECT_ROOT/gateway/dakota-gateway" ] || die "nao achei gateway/dakota-gateway em $PROJECT_ROOT"
[ -d "$PROJECT_ROOT/gateway/state" ] || die "nao achei gateway/state em $PROJECT_ROOT"

CONFIG_PATH="$SSHD_CONFIG_DIR/$CONFIG_NAME"
TMP_DIR="$(mktemp -d)"
cleanup() {
  rm -rf "$TMP_DIR"
}
trap cleanup EXIT INT TERM

WRAPPER_TMP="$TMP_DIR/dakota-capture-session"
cat > "$WRAPPER_TMP" <<EOF
#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$PROJECT_ROOT"
DB_PATH="$DB_PATH"
HMAC_KEY_FILE="$HMAC_KEY_FILE"
PYTHON_BIN="$PYTHON_BIN"

resolve_user_shell() {
  local user_name
  user_name="\${LOGNAME:-\${USER:-}}"
  if command -v getent >/dev/null 2>&1; then
    getent passwd "\$user_name" 2>/dev/null | awk -F: 'NF >= 7 { print \$7; exit }'
    return 0
  fi
  if [ -r /etc/passwd ]; then
    awk -F: -v u="\$user_name" '(\$1 == u && NF >= 7) { print \$7; exit }' /etc/passwd
    return 0
  fi
  return 0
}

fallback_login() {
  local original_cmd shell_path
  original_cmd="\${SSH_ORIGINAL_COMMAND:-}"
  if [ -n "\$original_cmd" ]; then
    exec /bin/sh -lc "\$original_cmd"
  fi
  shell_path="\$(resolve_user_shell)"
  shell_path="\${shell_path:-/bin/sh}"
  exec "\$shell_path" -l
}

if ! "\$PYTHON_BIN" - <<'PY' >/dev/null 2>&1
import sqlite3
con = sqlite3.connect(r"$DB_PATH")
try:
    row = con.execute("SELECT id FROM capture_sessions WHERE status='active' ORDER BY id DESC LIMIT 1").fetchone()
    raise SystemExit(0 if row else 1)
finally:
    con.close()
PY
then
  fallback_login
fi

export PYTHONPATH="\$PROJECT_ROOT/gateway\${PYTHONPATH:+:\$PYTHONPATH}"
cd "\$PROJECT_ROOT"

exec "\$PYTHON_BIN" "\$PROJECT_ROOT/gateway/dakota-gateway" capture-session \
  --db "\$DB_PATH" \
  --hmac-key-file "\$HMAC_KEY_FILE" \
  --source-user "\${LOGNAME:-\${USER:-}}" \
  "\$@"
EOF

CONFIG_TMP="$TMP_DIR/$CONFIG_NAME"
cat > "$CONFIG_TMP" <<EOF
Match User $MATCH_USER
    ForceCommand $WRAPPER_PATH
    PermitTTY yes
    X11Forwarding no
    AllowTcpForwarding no
EOF

info "Instalando wrapper em $WRAPPER_PATH"
sudo_or_die mkdir -p "$(dirname "$WRAPPER_PATH")"
sudo_or_die cp -f "$WRAPPER_TMP" "$WRAPPER_PATH"
sudo_or_die chmod 755 "$WRAPPER_PATH"

info "Preparando permissoes locais para o usuario $MATCH_USER"
sudo_or_die chmod 755 "$PROJECT_ROOT/gateway/dakota-gateway"
if have_cmd setfacl; then
  current="$PROJECT_ROOT"
  while [ "$current" != "/" ]; do
    sudo_or_die setfacl -m "u:$MATCH_USER:rx" "$current"
    parent="$(dirname "$current")"
    [ "$parent" = "$current" ] && break
    current="$parent"
    case "$current" in
      /home|/home/*) ;;
      *) break ;;
    esac
  done
  sudo_or_die setfacl -R -m "u:$MATCH_USER:rX" "$PROJECT_ROOT/gateway"
  sudo_or_die setfacl -R -m "u:$MATCH_USER:rwx" "$PROJECT_ROOT/gateway/state"
  sudo_or_die setfacl -R -d -m "u:$MATCH_USER:rwx" "$PROJECT_ROOT/gateway/state"
  sudo_or_die setfacl -m "u:$MATCH_USER:r" "$HMAC_KEY_FILE"
else
  info "setfacl nao encontrado. Ajuste as permissoes do repo/estado manualmente para o usuario $MATCH_USER."
fi

info "Instalando snippet sshd em $CONFIG_PATH"
sudo_or_die mkdir -p "$SSHD_CONFIG_DIR"
sudo_or_die cp -f "$CONFIG_TMP" "$CONFIG_PATH"
sudo_or_die chmod 644 "$CONFIG_PATH"

if [ "$RELOAD_SSHD" -eq 1 ] && have_cmd systemctl; then
  if sudo_or_die systemctl reload sshd 2>/dev/null; then
    info "sshd recarregado via systemctl reload sshd."
  elif sudo_or_die systemctl reload ssh 2>/dev/null; then
    info "sshd recarregado via systemctl reload ssh."
  else
    info "Nao foi possivel recarregar sshd automaticamente. Recarregue manualmente."
  fi
elif [ "$RELOAD_SSHD" -eq 1 ]; then
  info "systemctl nao encontrado. Recarregue o sshd manualmente."
fi

info ""
info "OK: integracao local instalada."
info "Usuario capturado: $MATCH_USER"
info "Wrapper: $WRAPPER_PATH"
info "Config:   $CONFIG_PATH"
info ""
info "Proximo passo:"
info "  ssh $MATCH_USER@localhost"

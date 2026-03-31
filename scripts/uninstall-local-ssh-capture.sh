#!/usr/bin/env bash
set -euo pipefail

WRAPPER_PATH="${WRAPPER_PATH:-/usr/local/bin/dakota-capture-session}"
CONFIG_NAME="${CONFIG_NAME:-90-dakota-capture.conf}"
SSHD_CONFIG_DIR="${SSHD_CONFIG_DIR:-/etc/ssh/sshd_config.d}"
RELOAD_SSHD=1
PROJECT_ROOT="${PROJECT_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
HMAC_KEY_FILE="${HMAC_KEY_FILE:-$PROJECT_ROOT/.local-secrets/hmac.key}"
MATCH_USER="${MATCH_USER:-}"

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
  die "precisa de root (ou sudo) para remover a configuracao do sshd."
}

usage() {
  cat <<EOF
Uso:
  ./scripts/uninstall-local-ssh-capture.sh [opcoes]

Opcoes:
  --wrapper-path <file>  Wrapper ForceCommand (default: $WRAPPER_PATH)
  --config-name <nome>   Nome do snippet em sshd_config.d (default: $CONFIG_NAME)
  --match-user <usuario> Remove tambem ACLs locais do usuario informado
  --no-reload            Nao tenta recarregar o sshd ao final
  --help                 Mostra esta ajuda
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
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
    --match-user)
      shift
      [ $# -gt 0 ] || die "falta valor para --match-user"
      MATCH_USER="$1"
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

CONFIG_PATH="$SSHD_CONFIG_DIR/$CONFIG_NAME"

if [ -z "$MATCH_USER" ] && [ -f "$CONFIG_PATH" ]; then
  MATCH_USER="$(sed -n 's/^Match User[[:space:]]\+//p' "$CONFIG_PATH" | head -n 1 | tr -d '\r\n')"
fi

if [ -e "$CONFIG_PATH" ]; then
  info "Removendo $CONFIG_PATH"
  sudo_or_die rm -f "$CONFIG_PATH"
fi

if [ -e "$WRAPPER_PATH" ]; then
  info "Removendo $WRAPPER_PATH"
  sudo_or_die rm -f "$WRAPPER_PATH"
fi

if [ -n "$MATCH_USER" ] && have_cmd setfacl; then
  info "Removendo ACLs locais do usuario $MATCH_USER"
  current="$PROJECT_ROOT"
  while [ "$current" != "/" ]; do
    sudo_or_die setfacl -x "u:$MATCH_USER" "$current" 2>/dev/null || true
    parent="$(dirname "$current")"
    [ "$parent" = "$current" ] && break
    current="$parent"
    case "$current" in
      /home|/home/*) ;;
      *) break ;;
    esac
  done
  sudo_or_die setfacl -R -x "u:$MATCH_USER" "$PROJECT_ROOT/gateway" 2>/dev/null || true
  sudo_or_die setfacl -R -k "$PROJECT_ROOT/gateway/state" 2>/dev/null || true
  sudo_or_die setfacl -x "u:$MATCH_USER" "$HMAC_KEY_FILE" 2>/dev/null || true
fi

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

info "OK: integracao local removida."

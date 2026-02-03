#!/bin/sh
set -eu

info() { printf '%s\n' "$*"; }
warn() { printf '%s\n' "Aviso: $*" >&2; }
die() { printf '%s\n' "Erro: $*" >&2; exit 1; }

have_cmd() { command -v "$1" >/dev/null 2>&1; }
is_root() { [ "$(id -u 2>/dev/null || echo 1)" = "0" ]; }

sudo_or_die_prefix() {
  if is_root; then
    "$@"
    return 0
  fi
  if have_cmd sudo; then
    sudo "$@"
    return 0
  fi
  die "precisa de root (ou sudo) para remover instalação em prefixos protegidos."
}

usage() {
  cat <<'EOF'
Uso:
  ./uninstall.sh [--prefix <dir>] [--link-dir <dir>]

Opções:
  --prefix <dir>     Prefixo instalado (default: detecta pelo local deste script)
  --link-dir <dir>   Remove symlink <dir>/replay2 (ex.: /usr/local/bin)

Exemplos:
  sudo /opt/dakota-replay2/uninstall.sh
  ./uninstall.sh --prefix /tmp/dakota-replay2
EOF
}

PREFIX=""
LINK_DIR=""

while [ $# -gt 0 ]; do
  case "$1" in
    --prefix)
      shift
      [ $# -gt 0 ] || die "falta valor para --prefix"
      PREFIX="$1"
      ;;
    --link-dir)
      shift
      [ $# -gt 0 ] || die "falta valor para --link-dir"
      LINK_DIR="$1"
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      die "opção desconhecida: $1 (use --help)"
      ;;
  esac
  shift
done

if [ -z "$PREFIX" ]; then
  # Detecta prefixo pelo local deste script (prefix/uninstall.sh)
  SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
  PREFIX="$(CDPATH= cd -- "$SCRIPT_DIR" && pwd)"
fi

info "Removendo instalação em: $PREFIX"

if [ -n "$LINK_DIR" ]; then
  if [ -L "$LINK_DIR/replay2" ] || [ -e "$LINK_DIR/replay2" ]; then
    sudo_or_die_prefix rm -f "$LINK_DIR/replay2"
    info "Symlink removido: $LINK_DIR/replay2"
  fi
fi

if [ -d "$PREFIX" ]; then
  sudo_or_die_prefix rm -rf "$PREFIX"
  info "Prefixo removido."
else
  warn "prefixo não existe: $PREFIX"
fi

#!/bin/sh
set -eu

APP_NAME="dakota-replay2"
DEFAULT_PREFIX="/opt/dakota-replay2"

info() { printf '%s\n' "$*"; }
warn() { printf '%s\n' "Aviso: $*" >&2; }
die() { printf '%s\n' "Erro: $*" >&2; exit 1; }

have_cmd() { command -v "$1" >/dev/null 2>&1; }
is_root() { [ "$(id -u 2>/dev/null || echo 1)" = "0" ]; }

usage() {
  cat <<'EOF'
Uso:
  ./install.sh [--prefix <dir>] [--no-deps] [--link-dir <dir>] [--force]

Opções:
  --prefix <dir>     Prefixo de instalação (default: /opt/dakota-replay2)
  --no-deps          Não tenta instalar dependências (Expect/Tcl)
  --link-dir <dir>   Cria symlink do comando em <dir> (ex.: /usr/local/bin)
  --force            Remove instalação anterior no prefixo antes de copiar

Env vars:
  DEPS_EXPECT=<nome>  Override do nome do pacote Expect (default: expect)
  DEPS_TCL=<nome>     Override do nome do pacote Tcl (default: tcl)

Exemplos:
  ./install.sh --prefix /opt/dakota-replay2
  ./install.sh --prefix /tmp/dakota-replay2 --no-deps
EOF
}

detect_os() {
  # linux | aix | other
  u="$(uname -s 2>/dev/null || echo unknown)"
  case "$u" in
    Linux) echo "linux" ;;
    AIX) echo "aix" ;;
    *) echo "other" ;;
  esac
}

sudo_or_die_prefix() {
  # Executa comando com privilégios (root/sudo) se necessário
  if is_root; then
    "$@"
    return 0
  fi
  if have_cmd sudo; then
    sudo "$@"
    return 0
  fi
  die "precisa de root (ou sudo) para instalar dependências/instalar em prefixos protegidos. Rode como root ou use --no-deps e um --prefix gravável."
}

ensure_expect_available() {
  if have_cmd expect; then return 0; fi
  if [ -x /opt/freeware/bin/expect ]; then
    # Em AIX Toolbox, /opt/freeware/bin pode não estar no PATH
    PATH="/opt/freeware/bin:$PATH"
    export PATH
    have_cmd expect && return 0
  fi
  return 1
}

ensure_tclsh_available() {
  if have_cmd tclsh; then return 0; fi
  if [ -x /opt/freeware/bin/tclsh ]; then
    PATH="/opt/freeware/bin:$PATH"
    export PATH
    have_cmd tclsh && return 0
  fi
  return 1
}

install_deps_linux() {
  deps_expect="${DEPS_EXPECT:-expect}"
  deps_tcl="${DEPS_TCL:-tcl}"

  if have_cmd apt-get; then
    sudo_or_die_prefix apt-get update -y
    sudo_or_die_prefix apt-get install -y "$deps_expect" "$deps_tcl"
    return 0
  fi
  if have_cmd dnf; then
    sudo_or_die_prefix dnf install -y "$deps_expect" "$deps_tcl"
    return 0
  fi
  if have_cmd yum; then
    sudo_or_die_prefix yum install -y "$deps_expect" "$deps_tcl"
    return 0
  fi
  if have_cmd zypper; then
    sudo_or_die_prefix zypper --non-interactive install -y "$deps_expect" "$deps_tcl"
    return 0
  fi

  die "não encontrei gerenciador de pacotes suportado (apt-get/dnf/yum/zypper). Use --no-deps e instale Expect/Tcl manualmente."
}

install_deps_aix() {
  deps_expect="${DEPS_EXPECT:-expect}"
  deps_tcl="${DEPS_TCL:-tcl}"

  dnf="/opt/freeware/bin/dnf"
  [ -x "$dnf" ] || die "dnf do AIX Toolbox não encontrado em $dnf. Instale o AIX Toolbox/dnf ou use --no-deps."

  # Expect costuma estar como "expect". Tcl pode variar.
  sudo_or_die_prefix "$dnf" install -y "$deps_expect" || true

  if ensure_expect_available; then :; else
    die "falha ao instalar/achar 'expect'. Tente: DEPS_EXPECT=<nome_do_pacote> ./install.sh ..."
  fi

  if ensure_tclsh_available; then
    return 0
  fi

  # Tenta alguns nomes comuns de Tcl no AIX Toolbox.
  for cand in "$deps_tcl" tcl8 tcl8.6 tcl86 tcl8.5 tcl85; do
    sudo_or_die_prefix "$dnf" install -y "$cand" && true
    ensure_tclsh_available && return 0
  done

  die "instale Tcl (tclsh) via AIX Toolbox. Você pode tentar: DEPS_TCL=<nome_do_pacote> ./install.sh ..."
}

PREFIX="$DEFAULT_PREFIX"
NO_DEPS=0
LINK_DIR=""
FORCE=0

while [ $# -gt 0 ]; do
  case "$1" in
    --prefix)
      shift
      [ $# -gt 0 ] || die "falta valor para --prefix"
      PREFIX="$1"
      ;;
    --no-deps)
      NO_DEPS=1
      ;;
    --link-dir)
      shift
      [ $# -gt 0 ] || die "falta valor para --link-dir"
      LINK_DIR="$1"
      ;;
    --force)
      FORCE=1
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

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
SRC_ROOT="$SCRIPT_DIR"

[ -d "$SRC_ROOT/bin" ] || die "estrutura inválida: não achei $SRC_ROOT/bin"
[ -d "$SRC_ROOT/lib" ] || die "estrutura inválida: não achei $SRC_ROOT/lib"
[ -d "$SRC_ROOT/screens" ] || die "estrutura inválida: não achei $SRC_ROOT/screens"

OS="$(detect_os)"
info "Instalando $APP_NAME em: $PREFIX (SO: $OS)"

if [ "$NO_DEPS" -eq 0 ]; then
  info "Instalando dependências (best effort): expect + tcl"
  case "$OS" in
    linux) install_deps_linux ;;
    aix) install_deps_aix ;;
    *) warn "SO não reconhecido. Pulando auto-instalação de dependências. Use --no-deps se quiser silenciar." ;;
  esac
else
  info "Pulando dependências (--no-deps)."
fi

ensure_expect_available || die "expect não encontrado no PATH. Instale o pacote 'expect' (ou ajuste PATH) e tente novamente."
ensure_tclsh_available || warn "tclsh não encontrado no PATH (necessário apenas para o simulador local)."

if [ -e "$PREFIX" ] && [ "$FORCE" -eq 1 ]; then
  info "Removendo instalação anterior em $PREFIX (--force)."
  sudo_or_die_prefix rm -rf "$PREFIX"
fi

# Cria prefixo e copia arquivos
if [ -e "$PREFIX" ]; then
  # Evita o cp criar bin/bin quando o destino já existe.
  if [ -e "$PREFIX/bin" ] || [ -e "$PREFIX/lib" ] || [ -e "$PREFIX/screens" ]; then
    die "o prefixo já contém uma instalação (bin/lib/screens). Use --force ou escolha outro --prefix."
  fi
fi

sudo_or_die_prefix mkdir -p "$PREFIX"
sudo_or_die_prefix cp -R "$SRC_ROOT/bin" "$SRC_ROOT/lib" "$SRC_ROOT/screens" "$PREFIX/"
if [ -f "$SRC_ROOT/README.md" ]; then sudo_or_die_prefix cp -f "$SRC_ROOT/README.md" "$PREFIX/"; fi
if [ -f "$SRC_ROOT/VERSION" ]; then sudo_or_die_prefix cp -f "$SRC_ROOT/VERSION" "$PREFIX/"; fi
sudo_or_die_prefix cp -f "$SRC_ROOT/uninstall.sh" "$PREFIX/uninstall.sh"

sudo_or_die_prefix chmod +x "$PREFIX/bin/main.exp" "$PREFIX/uninstall.sh"

# Wrapper replay2
WRAPPER="$PREFIX/bin/replay2"
sudo_or_die_prefix sh -c '
cat >"$1" <<'"'"'EOF'"'"'
#!/bin/sh
set -eu

PREFIX_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)

if command -v expect >/dev/null 2>&1; then
  exec expect "$PREFIX_DIR/bin/main.exp" "$@"
fi
if [ -x /opt/freeware/bin/expect ]; then
  exec /opt/freeware/bin/expect "$PREFIX_DIR/bin/main.exp" "$@"
fi
printf "%s\n" "Erro: expect não encontrado. Instale o pacote Expect e/ou ajuste PATH." >&2
exit 127
EOF
' sh "$WRAPPER"
sudo_or_die_prefix chmod +x "$WRAPPER"

# Symlink opcional
if [ -z "$LINK_DIR" ] && is_root && [ -d /usr/local/bin ] && [ -w /usr/local/bin ]; then
  LINK_DIR="/usr/local/bin"
fi
if [ -n "$LINK_DIR" ]; then
  sudo_or_die_prefix mkdir -p "$LINK_DIR"
  sudo_or_die_prefix ln -sf "$WRAPPER" "$LINK_DIR/replay2"
  info "Symlink criado: $LINK_DIR/replay2 -> $WRAPPER"
else
  info "Sem symlink global (use: $WRAPPER ou ajuste seu PATH)."
fi

info "Instalação concluída."
info "Teste: $WRAPPER"

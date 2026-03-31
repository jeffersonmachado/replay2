#!/bin/sh
set -eu

APP_NAME="dakota-replay2"

info() { printf '%s\n' "$*"; }
die() { printf '%s\n' "Erro: $*" >&2; exit 1; }

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
ROOT_DIR="$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)"

[ -d "$ROOT_DIR/bin" ] || die "não achei $ROOT_DIR/bin"
[ -d "$ROOT_DIR/lib" ] || die "não achei $ROOT_DIR/lib"
[ -d "$ROOT_DIR/screens" ] || die "não achei $ROOT_DIR/screens"
[ -f "$ROOT_DIR/install.sh" ] || die "não achei $ROOT_DIR/install.sh"
[ -f "$ROOT_DIR/uninstall.sh" ] || die "não achei $ROOT_DIR/uninstall.sh"

VERSION_FILE="$ROOT_DIR/VERSION"
if [ -f "$VERSION_FILE" ]; then
  VERSION="$(sed -n '1p' "$VERSION_FILE" | tr -d '\r\n')"
else
  VERSION="$(date +%Y.%m.%d-%H%M%S)"
  printf '%s\n' "$VERSION" >"$VERSION_FILE"
fi

DIST_DIR="$ROOT_DIR/dist"
STAGE_PARENT="$(mktemp -d 2>/dev/null || mktemp -d -t "${APP_NAME}.XXXXXX")"
STAGE_DIR="$STAGE_PARENT/${APP_NAME}-${VERSION}"

cleanup() {
  rm -rf "$STAGE_PARENT"
}
trap cleanup EXIT INT TERM

mkdir -p "$DIST_DIR"
mkdir -p "$STAGE_DIR"

info "Staging em: $STAGE_DIR"

cp -R "$ROOT_DIR/bin" "$ROOT_DIR/lib" "$ROOT_DIR/screens" "$STAGE_DIR/"
if [ -d "$ROOT_DIR/gateway" ]; then cp -R "$ROOT_DIR/gateway" "$STAGE_DIR/"; fi
if [ -d "$ROOT_DIR/tests" ]; then cp -R "$ROOT_DIR/tests" "$STAGE_DIR/"; fi
cp -f "$ROOT_DIR/install.sh" "$ROOT_DIR/uninstall.sh" "$ROOT_DIR/VERSION" "$STAGE_DIR/"
if [ -f "$ROOT_DIR/README.md" ]; then cp -f "$ROOT_DIR/README.md" "$STAGE_DIR/"; fi
if [ -d "$ROOT_DIR/scripts" ]; then
  mkdir -p "$STAGE_DIR/scripts"
  cp -f "$ROOT_DIR/scripts/"*.sh "$STAGE_DIR/scripts/"
fi

# Garante executáveis
chmod +x "$STAGE_DIR/install.sh" "$STAGE_DIR/uninstall.sh" "$STAGE_DIR/bin/main.exp" "$STAGE_DIR/scripts/"*.sh 2>/dev/null || true
chmod +x "$STAGE_DIR/bin/replay2.exp" 2>/dev/null || true
chmod +x "$STAGE_DIR/gateway/dakota-gateway" "$STAGE_DIR/gateway/control/server.py" 2>/dev/null || true

# Remove caches Python e arquivos de estado do stage (não são artefatos oficiais)
rm -rf \
  "$STAGE_DIR/gateway/__pycache__" \
  "$STAGE_DIR/gateway/dakota_gateway/__pycache__" \
  "$STAGE_DIR/gateway/control/__pycache__" \
  "$STAGE_DIR/gateway/tests/__pycache__" \
  "$STAGE_DIR/tests/__pycache__" 2>/dev/null || true

# Remove arquivos de banco de dados e estado local
find "$STAGE_DIR" \
  -name "*.db" \
  -o -name "*.db-wal" \
  -o -name "*.db-shm" \
  -o -name "*.pyc" \
  2>/dev/null | xargs rm -f || true

# Remove diretório gateway/state se existir vazio, ou deixa estrutura limpa
if [ -d "$STAGE_DIR/gateway/state" ]; then
  find "$STAGE_DIR/gateway/state" -maxdepth 1 -type f \
    \( -name "*.db" -o -name "*.db-*" \) \
    -delete 2>/dev/null || true
fi

TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
OUT="$DIST_DIR/${APP_NAME}-${VERSION}-${TIMESTAMP}.tar.gz"
info "Gerando: $OUT"

(cd "$STAGE_PARENT" && {
  # Em alguns AIX o `tar` não suporta -z. Preferimos tar+gzip quando necessário.
  if tar -czf "$OUT" "${APP_NAME}-${VERSION}" >/dev/null 2>&1; then
    :
  else
    rm -f "$OUT"
    if command -v gzip >/dev/null 2>&1; then
      tar -cf - "${APP_NAME}-${VERSION}" | gzip -c >"$OUT"
    else
      die "tar não suporta -z e gzip não encontrado. Instale gzip ou use um tar com suporte a -z."
    fi
  fi
})

info "OK: $OUT"

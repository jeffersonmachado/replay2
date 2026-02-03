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
cp -f "$ROOT_DIR/install.sh" "$ROOT_DIR/uninstall.sh" "$ROOT_DIR/VERSION" "$STAGE_DIR/"
if [ -f "$ROOT_DIR/README.md" ]; then cp -f "$ROOT_DIR/README.md" "$STAGE_DIR/"; fi
if [ -d "$ROOT_DIR/scripts" ]; then
  mkdir -p "$STAGE_DIR/scripts"
  cp -f "$ROOT_DIR/scripts/build-tarball.sh" "$STAGE_DIR/scripts/"
fi

# Garante executáveis
chmod +x "$STAGE_DIR/install.sh" "$STAGE_DIR/uninstall.sh" "$STAGE_DIR/bin/main.exp" "$STAGE_DIR/scripts/build-tarball.sh" 2>/dev/null || true

OUT="$DIST_DIR/${APP_NAME}-${VERSION}.tar.gz"
info "Gerando: $OUT"

(cd "$STAGE_PARENT" && tar -czf "$OUT" "${APP_NAME}-${VERSION}")

info "OK: $OUT"

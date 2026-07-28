#!/bin/sh
# =============================================================================
# build-selfinstall.sh — Gera o self-installing archive (.run) do Replay2
#
# Concatena scripts/selfinstall-stub.sh + tarball de distribuição em um
# único arquivo executável dist/dakota-replay2-<VERSION>-<ts>.run.
#
# Uso:
#   bash scripts/build-selfinstall.sh                 # usa o tarball mais recente de dist/
#   bash scripts/build-selfinstall.sh --build         # rebuilda o tarball antes
#   bash scripts/build-selfinstall.sh --tarball dist/arquivo.tar.gz
# =============================================================================
set -eu

info() { printf '%s\n' "$*"; }
die() { printf '%s\n' "Erro: $*" >&2; exit 1; }

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
ROOT_DIR="$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)"
STUB="$SCRIPT_DIR/selfinstall-stub.sh"
DIST_DIR="$ROOT_DIR/dist"

TARBALL=""
BUILD=0
while [ $# -gt 0 ]; do
  case "$1" in
    --build) BUILD=1 ;;
    --tarball) shift; [ $# -gt 0 ] || die "falta valor para --tarball"; TARBALL="$1" ;;
    -h|--help) sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) die "opção desconhecida: $1" ;;
  esac
  shift
done

[ -f "$STUB" ] || die "stub não encontrado: $STUB"
grep -q '^__ARCHIVE_BELOW__$' "$STUB" || die "stub sem marcador __ARCHIVE_BELOW__"

if [ "$BUILD" -eq 1 ]; then
  info "Rebuildando tarball..."
  bash "$SCRIPT_DIR/build-tarball.sh"
fi

if [ -z "$TARBALL" ]; then
  TARBALL=$(ls -1t "$DIST_DIR"/*.tar.gz 2>/dev/null | head -1 || true)
fi
[ -n "$TARBALL" ] && [ -f "$TARBALL" ] || die "nenhum tarball encontrado (use --build ou --tarball)"

BASE=$(basename "$TARBALL" .tar.gz)
OUT="$DIST_DIR/$BASE.run"

info "Gerando self-installing archive: $OUT"
info "  stub:    $STUB"
info "  payload: $TARBALL"

cat "$STUB" > "$OUT"
cat "$TARBALL" >> "$OUT"
chmod +x "$OUT"

# Sanity: o marcador deve existir exatamente 1 vez e o payload deve extrair
LINE=$(awk '/^__ARCHIVE_BELOW__$/ { print NR + 1; exit 0; }' "$OUT")
[ -n "$LINE" ] || die "sanity: marcador não encontrado no .run"
tail -n +"$LINE" "$OUT" | gzip -t 2>/dev/null || die "sanity: payload gzip inválido"

if command -v sha256sum >/dev/null 2>&1; then
  (cd "$DIST_DIR" && sha256sum "$BASE.run" > "$BASE.run.sha256")
  info "  sha256:  $OUT.sha256"
fi

info ""
info "✓ Self-installer pronto: $OUT ($(du -k "$OUT" | awk '{print $1}') KB)"
info "  Uso no servidor: sh $BASE.run [--prefix /opt/dakota/replay2]"

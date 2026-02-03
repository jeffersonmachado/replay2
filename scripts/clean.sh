#!/bin/sh
set -eu

info() { printf '%s\n' "$*"; }

ROOT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"

# Remove somente artefatos NÃO-oficiais/temporários.
rm -rf "$ROOT_DIR/tests/tmp" 2>/dev/null || true

if [ -d "$ROOT_DIR/dist" ]; then
  # Mantém dist/*.tar.gz (artefato oficial), remove o resto.
  for f in "$ROOT_DIR/dist/"*; do
    [ -e "$f" ] || continue
    case "$f" in
      *.tar.gz) : ;;
      *) rm -rf -- "$f" ;;
    esac
  done
fi

# Metadados locais do editor (se reaparecerem)
if [ -d "$ROOT_DIR/.cursor" ]; then
  rmdir "$ROOT_DIR/.cursor" 2>/dev/null || true
fi

info "OK: limpeza concluída."


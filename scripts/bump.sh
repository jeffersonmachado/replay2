#!/bin/bash
# =============================================================================
# bump.sh — Incrementa a versão do Replay2
#
# Uso:
#   bash scripts/bump.sh [patch|minor|major]
#
# Exemplos:
#   bash scripts/bump.sh patch    # 0.3.18 → 0.3.19
#   bash scripts/bump.sh minor    # 0.3.18 → 0.4.0
#   bash scripts/bump.sh major    # 0.3.18 → 1.0.0
# =============================================================================
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION_FILE="$ROOT_DIR/VERSION"
LEVEL="${1:-patch}"

CURRENT=$(cat "$VERSION_FILE" 2>/dev/null || echo "0.0.0")
MAJOR=$(echo "$CURRENT" | cut -d. -f1)
MINOR=$(echo "$CURRENT" | cut -d. -f2)
PATCH=$(echo "$CURRENT" | cut -d. -f3)

case "$LEVEL" in
  major)
    MAJOR=$((MAJOR + 1))
    MINOR=0
    PATCH=0
    ;;
  minor)
    MINOR=$((MINOR + 1))
    PATCH=0
    ;;
  patch)
    PATCH=$((PATCH + 1))
    ;;
  *)
    echo "Nível inválido: $LEVEL (use: patch, minor, major)"
    exit 1
    ;;
esac

NEW="${MAJOR}.${MINOR}.${PATCH}"
echo "$NEW" > "$VERSION_FILE"
echo "$CURRENT → $NEW"

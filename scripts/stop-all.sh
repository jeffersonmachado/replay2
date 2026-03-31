#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

echo "Parando Control Server..."
./scripts/stop-control.sh || true

echo ""
echo "Todos os servicos foram finalizados."

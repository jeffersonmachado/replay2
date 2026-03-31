#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

echo "Finalizando instancias anteriores (se existirem)..."
./scripts/stop-all.sh || true

echo "Iniciando Control Server..."
./scripts/start-control.sh

echo ""
echo "Tudo iniciado com sucesso."
echo "Control/UI: http://127.0.0.1:8090"
echo "Dica: para fazer 'ssh <usuario>@localhost' cair no gateway/capture-session, instale a integracao local com ./scripts/install-local-ssh-capture.sh --match-user <usuario>"

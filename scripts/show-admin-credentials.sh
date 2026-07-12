#!/usr/bin/env bash
# show-admin-credentials.sh
# Exibe as credenciais de admin configuradas para o ambiente local.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# 1. Variáveis de ambiente (prioridade máxima)
if [[ -n "${DAKOTA_ADMIN:-}" ]]; then
    echo "DAKOTA_ADMIN (ambiente): ${DAKOTA_ADMIN}"
elif [[ -n "${BOOTSTRAP_ADMIN:-}" ]]; then
    echo "BOOTSTRAP_ADMIN (ambiente): ${BOOTSTRAP_ADMIN}"
else
    # 2. Default do dev.sh
    echo "Default (dev.sh fallback): admin:Admin123!"
fi

# 3. Consultar banco de dados (se existir)
DB_PATH="${DB_PATH:-$PROJECT_ROOT/gateway/state/replay.db}"
if [[ -f "$DB_PATH" ]]; then
    echo ""
    echo "--- Usuários no banco ($DB_PATH) ---"
    sqlite3 "$DB_PATH" "SELECT username, role, created_at_ms FROM users;" 2>/dev/null || echo "  (banco não disponível ou sqlite3 não instalado)"
else
    echo ""
    echo "Banco não encontrado em: $DB_PATH"
fi

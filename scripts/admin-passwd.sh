#!/usr/bin/env bash
# admin-passwd.sh — Mostra ou altera a senha do admin no banco local.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
DB_PATH="${DB_PATH:-$PROJECT_ROOT/gateway/state/replay.db}"
PYTHONPATH="${PYTHONPATH:-$PROJECT_ROOT/gateway}"

usage() {
    cat <<EOF
Uso: $(basename "$0") [OPÇÃO]

Mostra ou altera a senha do admin no banco local.

Opções:
  (sem argumentos)   Mostra as credenciais atuais (usuário e role)
  --reset            Redefine a senha para o default: Admin123!
  --set SENHA        Define uma nova senha personalizada
  --help             Mostra esta ajuda

Banco: $DB_PATH
EOF
    exit 0
}

# --- Mostrar ---
show_admin() {
    echo "=== Credenciais do admin ==="
    if [[ ! -f "$DB_PATH" ]]; then
        echo "ERRO: Banco não encontrado em $DB_PATH"
        exit 1
    fi

    PYTHONPATH="$PYTHONPATH" python3 -c "
import sys
sys.path.insert(0, '$PROJECT_ROOT/gateway')
from dakota_gateway.state_db import connect
con = connect('$DB_PATH')
row = con.execute(\"SELECT username, role, created_at_ms FROM users WHERE username='admin'\").fetchone()
if row:
    print(f'  Usuário: {row[\"username\"]}')
    print(f'  Role:    {row[\"role\"]}')
    print(f'  Criado:  {row[\"created_at_ms\"]}')
else:
    print('  Admin não encontrado no banco.')
con.close()
"
    echo ""
    echo "Default dev.sh: admin:Admin123!"
}

# --- Resetar ---
reset_admin() {
    local new_pass="${1:-Admin123!}"
    echo "Redefinindo senha do admin para: $new_pass"

    PYTHONPATH="$PYTHONPATH" python3 -c "
import sys
sys.path.insert(0, '$PROJECT_ROOT/gateway')
from dakota_gateway import auth
from dakota_gateway.state_db import connect
con = connect('$DB_PATH')
row = con.execute(\"SELECT username FROM users WHERE username='admin'\").fetchone()
if not row:
    print('ERRO: usuário admin não existe no banco.')
    sys.exit(1)
new_hash = auth.pbkdf2_hash_password('$new_pass')
con.execute(\"UPDATE users SET password_hash = ? WHERE username = 'admin'\", (new_hash,))
con.commit()
con.close()
print('Senha alterada com sucesso.')
"
}

# --- Main ---
case "${1:-}" in
    --help|-h)
        usage
        ;;
    --reset)
        reset_admin "Admin123!"
        echo ""
        show_admin
        ;;
    --set)
        if [[ -z "${2:-}" ]]; then
            echo "ERRO: informe a senha. Ex: $(basename "$0") --set NovaSenha123"
            exit 1
        fi
        reset_admin "$2"
        echo ""
        show_admin
        ;;
    "")
        show_admin
        ;;
    *)
        echo "ERRO: opção desconhecida: $1"
        usage
        ;;
esac

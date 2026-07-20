#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_ROOT"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

log_info() {
  echo -e "${BLUE}→${NC} $*"
}

log_success() {
  echo -e "${GREEN}✓${NC} $*"
}

log_warn() {
  echo -e "${YELLOW}⚠${NC} $*"
}

log_error() {
  echo -e "${RED}✗${NC} $*"
}

# Configuration
LOG_DIR="${LOG_DIR:-$PROJECT_ROOT/log}"
LISTEN="${LISTEN:-127.0.0.1:8090}"
DB_PATH="${DB_PATH:-$PROJECT_ROOT/gateway/state/replay.db}"
SECRETS_DIR="${SECRETS_DIR:-$PROJECT_ROOT/.local-secrets}"
COOKIE_SECRET_FILE="${COOKIE_SECRET_FILE:-$SECRETS_DIR/cookie_secret.key}"
HMAC_KEY_FILE="${HMAC_KEY_FILE:-$SECRETS_DIR/hmac.key}"
BOOTSTRAP_ADMIN="${BOOTSTRAP_ADMIN:-${DAKOTA_ADMIN:-}}"
BOOTSTRAP_ADMIN="${BOOTSTRAP_ADMIN:-admin:Admin123!}"
LOG_FILE="${LOG_FILE:-$LOG_DIR/replay2-control.log}"
PID_FILE="${PID_FILE:-/tmp/replay2-control.pid}"
WATCH_MODE="${WATCH_MODE:-1}"

# Cleanup on exit
cleanup() {
  log_info "Limpando instâncias..."
  if [[ -f "$PID_FILE" ]]; then
    PID="$(cat "$PID_FILE" 2>/dev/null || true)"
    if [[ -n "$PID" ]]; then
      kill "$PID" 2>/dev/null || true
    fi
    rm -f "$PID_FILE"
  fi
}

trap cleanup EXIT INT TERM

log_info "Dakota Replay2 - Ambiente de Desenvolvimento"
log_info "============================================"

# Create necessary directories
mkdir -p "$(dirname "$DB_PATH")" "$SECRETS_DIR" "$LOG_DIR"
log_success "Diretórios criados: $LOG_DIR, $SECRETS_DIR"

# Generate secrets if needed
if [[ ! -f "$HMAC_KEY_FILE" ]]; then
  log_info "Gerando chave HMAC..."
  head -c 32 /dev/urandom > "$HMAC_KEY_FILE"
  log_success "Chave HMAC gerada"
fi

if [[ ! -f "$COOKIE_SECRET_FILE" ]]; then
  log_info "Gerando secret de cookie..."
  head -c 32 /dev/urandom > "$COOKIE_SECRET_FILE"
  log_success "Secret de cookie gerado"
fi

# Check Python environment
if [[ -z "${VIRTUAL_ENV:-}" ]]; then
  log_warn "Nenhum virtualenv ativado. Ativando..."
  if [[ -f "$PROJECT_ROOT/.venv/bin/activate" ]]; then
    source "$PROJECT_ROOT/.venv/bin/activate"
    log_success "Virtualenv ativado: $VIRTUAL_ENV"
  else
    log_error "Virtualenv não encontrado. Execute: python3 -m venv .venv && source .venv/bin/activate"
    exit 1
  fi
fi

# Install dependencies if needed
log_info "Verificando dependências Python..."
if ! python -c "import flask, watchfiles" 2>/dev/null; then
  log_warn "Dependências faltando. Instalando..."
  pip install -q flask bottle werkzeug watchfiles || {
    log_error "Falha ao instalar dependências"
    exit 1
  }
  log_success "Dependências instaladas"
fi

# Build/compile if needed (Tcl/Expect)
if [[ -d "bin/" ]]; then
  if [[ ! -f "bin/main.exp" ]] || [[ ! -f "bin/replay2.exp" ]]; then
    log_warn "Binários Tcl/Expect não encontrados. Necessário compilar."
    if [[ -x "scripts/build-tarball.sh" ]]; then
      log_info "Compilando com build-tarball.sh..."
      ./scripts/build-tarball.sh || log_warn "Falha na compilação (não crítico para dev)"
    fi
  else
    log_success "Binários Tcl/Expect encontrados"
  fi
fi

# Start Control Server
log_info ""
log_info "Iniciando Control Server (gateway/control/server.py)..."
log_info ""

export FLASK_APP="gateway/control/server.py"
export FLASK_ENV="development"
export PYTHONPATH="$PROJECT_ROOT/gateway${PYTHONPATH:+:$PYTHONPATH}"
export PROJECT_ROOT="$PROJECT_ROOT"
export LOG_DIR="$LOG_DIR"
export LISTEN="$LISTEN"
export DB_PATH="$DB_PATH"
export SECRETS_DIR="$SECRETS_DIR"
export COOKIE_SECRET_FILE="$COOKIE_SECRET_FILE"
export HMAC_KEY_FILE="$HMAC_KEY_FILE"
export BOOTSTRAP_ADMIN="$BOOTSTRAP_ADMIN"
export LOG_FILE="$LOG_FILE"

cd "$PROJECT_ROOT/gateway/control"

# Start server with nodemon-like auto-reload when watchfiles is available.
if [[ "$WATCH_MODE" == "1" ]] && python -c "import watchfiles" 2>/dev/null; then
  log_success "Usando watchfiles para auto-reload (estilo nodemon)"
  python -u - <<'PY' &
import os
import subprocess
import sys

from watchfiles import watch

project_root = os.environ["PROJECT_ROOT"]
control_dir = os.path.join(project_root, "gateway", "control")
watch_dirs = [
    os.path.join(project_root, "gateway", "control"),
    os.path.join(project_root, "gateway", "dakota_gateway"),
  os.path.join(project_root, "lib"),
  os.path.join(project_root, "bin"),
  os.path.join(project_root, "screens"),
]
watch_ext = {".py", ".tcl", ".exp", ".html", ".js", ".css"}
ignore_parts = {
  ".git",
  ".venv",
  "__pycache__",
  ".pytest_cache",
  "node_modules",
  "log",
  "tmp",
}
cmd = [
    sys.executable,
    "-u",
    "server.py",
    "--listen",
    os.environ["LISTEN"],
    "--db",
    os.environ["DB_PATH"],
    "--cookie-secret-file",
    os.environ["COOKIE_SECRET_FILE"],
    "--hmac-key-file",
    os.environ["HMAC_KEY_FILE"],
    "--bootstrap-admin",
    os.environ["BOOTSTRAP_ADMIN"],
    "--gateway-auto-activate",
]


def stop_proc(proc):
    if proc is None or proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


child = subprocess.Popen(cmd, cwd=control_dir, env=os.environ.copy())


def relevant_changes(changes):
    selected = []
    for _kind, changed in changes:
        changed_path = str(changed)
        rel = os.path.relpath(changed_path, project_root)
        parts = set(rel.split(os.sep))
        if parts & ignore_parts:
            continue
        _, ext = os.path.splitext(changed_path)
        if ext.lower() in watch_ext:
            selected.append(rel)
    return selected


try:
    for changes in watch(*watch_dirs, debounce=400):
        if not changes:
            continue
        selected = relevant_changes(changes)
        if not selected:
            continue
        print("[dev] Mudança detectada em: {}. Reiniciando server.py...".format(selected[0]), flush=True)
        stop_proc(child)
        child = subprocess.Popen(cmd, cwd=control_dir, env=os.environ.copy())
except KeyboardInterrupt:
    pass
finally:
    stop_proc(child)
PY
else
  if [[ "$WATCH_MODE" == "1" ]]; then
    log_warn "watchfiles não disponível. Executando sem auto-reload."
  else
    log_info "WATCH_MODE=0: auto-reload desabilitado."
  fi
  python -u server.py \
    --listen "$LISTEN" \
    --db "$DB_PATH" \
    --cookie-secret-file "$COOKIE_SECRET_FILE" \
    --hmac-key-file "$HMAC_KEY_FILE" \
    --bootstrap-admin "$BOOTSTRAP_ADMIN" \
    --gateway-auto-activate &
fi

SERVER_PID=$!
echo "$SERVER_PID" > "$PID_FILE"

log_success "Servidor iniciado (PID: $SERVER_PID)"
log_info ""
log_info "============================================"
log_info "Ambiente de Desenvolvimento Pronto!"
log_info "============================================"
log_info ""
log_info "Dashboard: ${BLUE}http://$LISTEN${NC}"
log_info "Log:        ${BLUE}$LOG_FILE${NC}"
log_info "Admin:      ${BLUE}$BOOTSTRAP_ADMIN${NC}"
log_info ""
log_info "Dicas:"
log_info "  • Verificar logs: tail -f $LOG_FILE"
log_info "  • Parar: Ctrl+C"
log_info "  • Gateway SSH: ./scripts/install-local-ssh-capture.sh --match-user <usuario>"
log_info ""

# Wait for server
wait $SERVER_PID 2>/dev/null || {
  log_error "Servidor encerrou com erro"
  exit 1
}

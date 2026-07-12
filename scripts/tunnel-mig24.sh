#!/bin/sh
# =============================================================================
# tunnel-mig24.sh — Túnel SSH para acessar a UI do Replay2 no MIG24
#
# Redireciona localhost:8080 → MIG24:8080 via túnel SSH
#
# Uso:
#   bash scripts/tunnel-mig24.sh
#   bash scripts/tunnel-mig24.sh --port 9090
#   bash scripts/tunnel-mig24.sh --kill    # mata túnel existente
# =============================================================================

HOST="10.5.8.25"
USER="results"
KEY="$HOME/.ssh/dakota_mig24"
LOCAL_PORT="8080"
REMOTE_PORT="8080"

case "$1" in
    --port) LOCAL_PORT="$2"; shift 2; REMOTE_PORT="${1:-8080}";;
    --kill) LOCAL_PORT="${2:-8080}";;
esac

if [ ! -f "$KEY" ]; then
    echo "Erro: chave SSH não encontrada: $KEY"
    exit 1
fi

# ── Verifica se já existe túnel nesta porta ──
EXISTING_PID=""
# Procura por processo SSH com forward para a porta e host corretos
for pid in $(lsof -ti "TCP:localhost:$LOCAL_PORT" -sTCP:LISTEN 2>/dev/null || true); do
    CMD=$(ps -p "$pid" -o args= 2>/dev/null || true)
    if echo "$CMD" | grep -q "ssh.*-L.*${LOCAL_PORT}:localhost:${REMOTE_PORT}.*${HOST}"; then
        EXISTING_PID="$pid"
        break
    fi
done

# Fallback: verifica com ss/netstat
if [ -z "$EXISTING_PID" ]; then
    for pid in $(ss -tlnp 2>/dev/null | grep "127.0.0.1:$LOCAL_PORT" | sed -E 's/.*pid=([0-9]+).*/\1/' || true); do
        CMD=$(ps -p "$pid" -o args= 2>/dev/null || true)
        if echo "$CMD" | grep -q "ssh.*-L.*${LOCAL_PORT}"; then
            EXISTING_PID="$pid"
            break
        fi
    done
fi

if [ -n "$EXISTING_PID" ]; then
    if [ "$1" = "--kill" ]; then
        echo "==> Matando túnel SSH na porta $LOCAL_PORT (PID $EXISTING_PID)"
        kill "$EXISTING_PID" 2>/dev/null
        sleep 1
        echo "    Túnel encerrado."
        exit 0
    else
        echo "==> Túnel SSH já ativo na porta $LOCAL_PORT (PID $EXISTING_PID)"
        echo "    Para matar: bash scripts/tunnel-mig24.sh --kill"
        echo "    Acesse: http://localhost:$LOCAL_PORT"
        exit 0
    fi
fi

# Verifica se a porta está ocupada por OUTRO processo (não-SSH)
PORT_CHECK=$(lsof -ti "TCP:localhost:$LOCAL_PORT" -sTCP:LISTEN 2>/dev/null || ss -tlnp 2>/dev/null | grep "127.0.0.1:$LOCAL_PORT" || true)
if [ -n "$PORT_CHECK" ]; then
    echo "Aviso: porta $LOCAL_PORT já está em uso por outro processo (não é túnel MIG24)."
    echo "       Use --port <outra_porta> ou libere a porta manualmente."
    exit 1
fi

echo "==> Túnel SSH: localhost:$LOCAL_PORT → $HOST:$REMOTE_PORT"
echo "    Acesse: http://localhost:$LOCAL_PORT"
echo "    Ctrl+C para parar | scripts/tunnel-mig24.sh --kill para matar"

ssh -i "$KEY" -o StrictHostKeyChecking=no -N \
    -L "${LOCAL_PORT}:localhost:${REMOTE_PORT}" \
    "${USER}@${HOST}"

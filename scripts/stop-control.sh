#!/usr/bin/env bash
set -euo pipefail

PID_FILE="${PID_FILE:-/tmp/replay2-control.pid}"
LISTEN="${LISTEN:-127.0.0.1:8090}"

kill_if_running() {
  local pid="$1"
  if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
    kill "$pid" 2>/dev/null || true
    sleep 1
    if kill -0 "$pid" 2>/dev/null; then
      kill -9 "$pid" 2>/dev/null || true
    fi
    return 0
  fi
  return 1
}

collect_port_pids() {
  local host_port="$1"
  ss -ltnp 2>/dev/null \
    | awk -v hp="$host_port" '
        index($0, hp) {
          while (match($0, /pid=[0-9]+/)) {
            pid = substr($0, RSTART + 4, RLENGTH - 4)
            print pid
            $0 = substr($0, RSTART + RLENGTH)
          }
        }
      ' \
    | sort -u
}

killed_any=false

if [[ -f "$PID_FILE" ]]; then
  PID="$(cat "$PID_FILE" 2>/dev/null || true)"
  if kill_if_running "$PID"; then
    echo "Control server parado (pid $PID)."
    killed_any=true
  else
    echo "PID file encontrado, mas processo nao esta ativo."
  fi
  rm -f "$PID_FILE"
fi

# Fallback: tenta encerrar por nome de processo (cobre execucoes fora do PID file).
PROC_PIDS="$(pgrep -f '(gateway/control/server.py|control/server.py)' || true)"
if [[ -n "$PROC_PIDS" ]]; then
  while read -r pid; do
    [[ -z "$pid" ]] && continue
    if kill_if_running "$pid"; then
      echo "Control server parado por nome de processo (pid $pid)."
      killed_any=true
    fi
  done <<< "$PROC_PIDS"
fi

# Fallback final: se ainda houver algo ouvindo em LISTEN, encerra pelo pid da porta.
PORT_PIDS="$(collect_port_pids "$LISTEN" || true)"
if [[ -n "$PORT_PIDS" ]]; then
  while read -r pid; do
    [[ -z "$pid" ]] && continue
    if kill_if_running "$pid"; then
      echo "Processo na porta $LISTEN finalizado (pid $pid)."
      killed_any=true
    fi
  done <<< "$PORT_PIDS"
fi

if [[ "$killed_any" == false ]]; then
  echo "Nenhum processo ativo do control server encontrado."
fi

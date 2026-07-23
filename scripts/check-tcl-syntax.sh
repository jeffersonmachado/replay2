#!/bin/sh
# =============================================================================
# check-tcl-syntax.sh — Verificação real de sintaxe Tcl (parse-only)
#
# Para cada arquivo, lê o conteúdo com tclsh e exige que `info complete`
# confirme um script completo e bem-formado (chaves/aspas/colchetes
# balanceados). NÃO executa o código — diferente de `tclsh <arquivo>`,
# que rodaria o script (e falharia em entrypoints Expect sob tclsh).
#
# Uso: sh scripts/check-tcl-syntax.sh <arquivo.tcl|arquivo.exp> [...]
# =============================================================================
set -eu

if [ $# -eq 0 ]; then
  echo "Uso: $0 <arquivo> [...]" >&2
  exit 2
fi

rc=0
for f in "$@"; do
  if [ ! -f "$f" ]; then
    echo "FALHA: $f (arquivo não encontrado)" >&2
    rc=1
    continue
  fi
  if TCL_FILE="$f" tclsh <<'EOF'
set fd [open $env(TCL_FILE) r]
set src [read $fd]
close $fd
if {![info complete $src]} {
  puts stderr "script incompleto ou malformado"
  exit 1
}
EOF
  then
    echo "  OK: $f"
  else
    echo "FALHA: $f (sintaxe Tcl incompleta/malformada)" >&2
    rc=1
  fi
done
exit "$rc"

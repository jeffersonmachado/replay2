#!/usr/bin/env bash
# =============================================================================
# regen-baseline.sh — regenera artifacts/acceptance-test-baseline.sha256
#
# A baseline registra o sha256 REAL de uma lista explícita de arquivos
# protegidos da cadeia de release (testes de aceitação/imutáveis, runners e
# scripts de gate). O pipeline valida a baseline com `sha256sum -c`; qualquer
# alteração nesses arquivos exige regerar a baseline por ESTE script.
#
# Regras de segurança (spec da cadeia de release):
# - A raiz do repositório é resolvida a partir do caminho do próprio script
#   (BASH_SOURCE), nunca do cwd — funciona independente do diretório atual;
# - Para NÃO tocar nos artifacts reais por engano, o script só roda quando o
#   cwd é a raiz do repositório (fail-closed: fora da raiz, exit != 0);
# - Se a lista de arquivos protegidos resolver VAZIA, o script FALHA
#   (exit != 0) — passe vacuoso (hash do conteúdo vazio, path "-") é proibido;
# - O formato de saída é o do sha256sum ("<hash>  <relpath>"), validável com
#   `sha256sum -c` a partir da raiz do repositório.
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

if [ "$(pwd -P)" != "$ROOT_DIR" ]; then
  echo "ERROR: regen-baseline.sh deve ser executado a partir da raiz do repositório:" >&2
  echo "  cd $ROOT_DIR && bash scripts/acceptance/regen-baseline.sh" >&2
  exit 1
fi

cd "$ROOT_DIR"

protected=()

# ── Testes de aceitação e contratos imutáveis ───────────────────────────────
while IFS= read -r f; do
  protected+=("$f")
done < <(find tests/acceptance -maxdepth 1 -type f -name 'test_*.py' | LC_ALL=C sort)

for f in \
  tests/test_tree_hash_manifest_unit.py \
; do
  [ -f "$f" ] && protected+=("$f")
done

# ── Runners e scripts da cadeia de release ──────────────────────────────────
for f in \
  scripts/process_tree.py \
  scripts/test.sh \
  scripts/test-all.sh \
  scripts/acceptance/_gate_lib.sh \
  scripts/acceptance/gen-evidence-manifest.sh \
  scripts/acceptance/run-phase-07-visual-runner.sh \
  scripts/acceptance/run-phase-08-full.sh \
  scripts/final-acceptance.sh \
  scripts/validate_acceptance_results.py \
; do
  [ -f "$f" ] && protected+=("$f")
done

# ── Código do benchmark (implementado em paralelo — não falhar se ausente) ──
if [ -d gateway/dakota_gateway/benchmark ]; then
  while IFS= read -r f; do
    protected+=("$f")
  done < <(find gateway/dakota_gateway/benchmark -type f -name '*.py' \
             ! -path '*__pycache__*' | LC_ALL=C sort)
fi

# ── Guarda anti-passe-vacuoso ────────────────────────────────────────────────
if [ "${#protected[@]}" -eq 0 ]; then
  echo "ERROR: lista de arquivos protegidos vazia — recusando gerar baseline" >&2
  echo "(passe vacuoso proibido pela spec da cadeia de release)" >&2
  exit 1
fi

# ── Geração atômica (formato sha256sum, relativo à raiz) ────────────────────
mkdir -p artifacts
tmp_out="artifacts/.acceptance-test-baseline.sha256.tmp"
sha256sum "${protected[@]}" > "$tmp_out"
mv "$tmp_out" artifacts/acceptance-test-baseline.sha256

echo "baseline regenerada: artifacts/acceptance-test-baseline.sha256 (${#protected[@]} arquivos protegidos)"

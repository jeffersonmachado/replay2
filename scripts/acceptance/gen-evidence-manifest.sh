#!/usr/bin/env bash
# =============================================================================
# gen-evidence-manifest.sh — gera artifacts/evidence-manifest.sha256
#
# Gerador ÚNICO do manifest de evidências (spec da cadeia de release, §28):
# usado standalone e pelo scripts/final-acceptance.sh (que o invoca POR
# ULTIMO, antes do tarball).
#
# Regras:
# - NÃO auto-incluir a própria entrada (hash stale por construção);
# - NÃO incluir artefatos velhos/proibidos: *.tar.gz, acceptance-matrix.json,
#   final-artifact-manifest.json, test-all-suite-*.result.json;
# - NÃO incluir artifacts/acceptance-logs/**: são logs VOLÁTEIS de execução —
#   cada rodada de test.sh/test-all.sh/fases os regrava, o que invalidaria o
#   manifest fora do pipeline de release (a evidência protegida é o conjunto
#   ESTÁVEL: relatórios finais, manifestos, artefatos de benchmark; os logs
#   continuam indo para o tarball via build-tarball.sh, sem hash-pinning);
# - baseline e source-tree-* são validados separadamente (não entram aqui);
# - incluir apenas arquivos existentes, com hash do conteúdo recém-computado;
# - FALHAR se a lista ficar vazia (passe vacuoso proibido);
# - validar o resultado com `sha256sum -c` (fail-closed).
#
# Uso:
#   cd <raiz do repositório> && bash scripts/acceptance/gen-evidence-manifest.sh
#   bash scripts/acceptance/gen-evidence-manifest.sh --root <dir>
#       Gera o manifest para OUTRA raiz (ex.: o stage do build-tarball.sh, que
#       carrega só um subconjunto de artifacts/benchmarks — o manifest do
#       pacote cobre exatamente o que foi empacotado).
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

TARGET_ROOT="$ROOT_DIR"
if [ "${1:-}" = "--root" ]; then
  [ -n "${2:-}" ] || { echo "ERROR: --root requer um diretório" >&2; exit 2; }
  TARGET_ROOT="$(cd "$2" && pwd -P)"
elif [ $# -gt 0 ]; then
  echo "uso: gen-evidence-manifest.sh [--root DIR]" >&2
  exit 2
fi

if [ "$TARGET_ROOT" = "$ROOT_DIR" ] && [ "$(pwd -P)" != "$ROOT_DIR" ]; then
  echo "ERROR: gen-evidence-manifest.sh deve ser executado a partir da raiz do repositório:" >&2
  echo "  cd $ROOT_DIR && bash scripts/acceptance/gen-evidence-manifest.sh" >&2
  exit 1
fi

cd "$TARGET_ROOT"

python3 << 'PYEOF'
import hashlib
from pathlib import Path

# Padroes proibidos no manifest de evidencias (artefatos velhos/lixo).
PROHIBITED_BASENAMES = {"acceptance-matrix.json", "final-artifact-manifest.json"}

elines = []
for ep in sorted(Path('artifacts').glob('**/*')):
    if not ep.is_file():
        continue
    erel = str(ep.relative_to('.'))
    name = ep.name
    # Nunca auto-incluir o proprio manifest (hash stale por construcao).
    if name == 'evidence-manifest.sha256':
        continue
    # Logs volateis de execucao: regravados a cada rodada de testes/fases —
    # nao sao evidencia estavel de release (vao ao tarball sem hash-pinning).
    if erel.startswith('artifacts/acceptance-logs/'):
        continue
    # visual-test-result.json é regravado pelos testes visuais a cada rodada
    # (tests/test_terminal_snapshot_css_contract.py) — volátil como os logs;
    # a fase 07 o regenera e valida no release, e o build-tarball o exige.
    if erel == 'artifacts/visual-test-result.json':
        continue
    # Relatórios finais são REGRAVADOS no meio do pipeline de release
    # (CLEANING do final-acceptance.sh + geração no passo 10): cobri-los
    # tornaria o manifest estruturalmente stale durante o próprio pipeline.
    # Sua integridade é garantida pelo sha256 do tarball que os contém
    # (build-tarball.sh os exige); aqui fica a evidência ESTÁVEL (benchmarks).
    if erel in ('artifacts/final-acceptance-report.md',
                'artifacts/final-acceptance-results.json',
                'artifacts/manual-validation.json'):
        continue
    # Tarballs e artefatos velhos/lixo nao sao evidencia desta run.
    if name.endswith('.tar.gz'):
        continue
    if name in PROHIBITED_BASENAMES:
        continue
    if name.startswith('test-all-suite-') and name.endswith('.result.json'):
        continue
    # Manifestos de fonte e baseline sao validados separadamente.
    if erel.startswith('artifacts/acceptance-test-baseline'):
        continue
    if erel.startswith('artifacts/source-tree-'):
        continue
    elines.append(f'{hashlib.sha256(ep.read_bytes()).hexdigest()}  {erel}')

if not elines:
    raise SystemExit('ERROR: evidence manifest ficou vazio — recusando gerar passe vacuoso')
Path('artifacts/evidence-manifest.sha256').write_text('\n'.join(sorted(elines)) + '\n')
print(f'evidence-manifest.sha256: {len(elines)} entradas (hashes frescos)')
PYEOF

# Valida o manifest recém-gerado — falha se qualquer entrada estiver ausente
# ou com hash stale (release não empacota manifest inválido).
sha256sum -c artifacts/evidence-manifest.sha256 > /dev/null \
  || { echo "ERROR: evidence manifest invalido (sha256sum -c reprovou)" >&2; exit 1; }
echo "evidence-manifest.sha256 validado com sha256sum -c"

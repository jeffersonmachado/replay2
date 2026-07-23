#!/bin/sh
set -eu

APP_NAME="dakota-replay2"

info() { printf '%s\n' "$*"; }
die() { printf '%s\n' "Erro: $*" >&2; exit 1; }

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
ROOT_DIR="$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)"

[ -d "$ROOT_DIR/bin" ] || die "não achei $ROOT_DIR/bin"
[ -d "$ROOT_DIR/lib" ] || die "não achei $ROOT_DIR/lib"
[ -d "$ROOT_DIR/screens" ] || die "não achei $ROOT_DIR/screens"
[ -d "$ROOT_DIR/examples" ] || die "não achei $ROOT_DIR/examples"
[ -f "$ROOT_DIR/install.sh" ] || die "não achei $ROOT_DIR/install.sh"
[ -f "$ROOT_DIR/uninstall.sh" ] || die "não achei $ROOT_DIR/uninstall.sh"

VERSION_FILE="$ROOT_DIR/VERSION"
# VERSION é a fonte única da versão e deve existir no repositório — nunca
# gerar um VERSION sintético como efeito colateral do build.
[ -f "$VERSION_FILE" ] || die "arquivo VERSION não encontrado em $ROOT_DIR
Crie a versão antes de buildar (ex.: bash scripts/bump.sh patch)."
VERSION="$(sed -n '1p' "$VERSION_FILE" | tr -d '\r\n')"
[ -n "$VERSION" ] || die "arquivo VERSION está vazio"

DIST_DIR="$ROOT_DIR/dist"
STAGE_PARENT="$(mktemp -d 2>/dev/null || mktemp -d -t "${APP_NAME}.XXXXXX")"
STAGE_DIR="$STAGE_PARENT/${APP_NAME}-${VERSION}"

cleanup() {
  rm -rf "$STAGE_PARENT"
}
trap cleanup EXIT INT TERM

mkdir -p "$DIST_DIR"
mkdir -p "$STAGE_DIR"

info "Staging em: $STAGE_DIR"

cp -R "$ROOT_DIR/bin" "$ROOT_DIR/lib" "$ROOT_DIR/screens" "$ROOT_DIR/examples" "$STAGE_DIR/"
if [ -d "$ROOT_DIR/gateway" ]; then
  cp -R "$ROOT_DIR/gateway" "$STAGE_DIR/"
  # Remove itens que NÃO devem ir para o artefato
  rm -rf "$STAGE_DIR/gateway/.venv" \
    "$STAGE_DIR/gateway/.pytest_cache" \
    "$STAGE_DIR/gateway/node_modules" \
    "$STAGE_DIR/gateway/state/captures" 2>/dev/null || true
  # Remove __pycache__ e caches em qualquer nivel
  find "$STAGE_DIR" -type d -name '__pycache__' -exec rm -rf {} + 2>/dev/null || true
  find "$STAGE_DIR" -type d -name '.pytest_cache' -exec rm -rf {} + 2>/dev/null || true
  find "$STAGE_DIR" -type f -name '*.pyc' -delete 2>/dev/null || true
fi
if [ -d "$ROOT_DIR/tests" ]; then cp -R "$ROOT_DIR/tests" "$STAGE_DIR/"; fi
# Remove tmp e caches de teste
rm -rf "$STAGE_DIR/tests/tmp" 2>/dev/null || true
cp -f "$ROOT_DIR/install.sh" "$ROOT_DIR/uninstall.sh" "$ROOT_DIR/VERSION" "$STAGE_DIR/"
# conftest.py da raiz aplica os markers do pytest (necessário para rodar a suíte no tarball)
if [ -f "$ROOT_DIR/conftest.py" ]; then cp -f "$ROOT_DIR/conftest.py" "$STAGE_DIR/"; fi
if [ -f "$ROOT_DIR/README.md" ]; then cp -f "$ROOT_DIR/README.md" "$STAGE_DIR/"; fi
if [ -d "$ROOT_DIR/scripts" ]; then
  mkdir -p "$STAGE_DIR/scripts"
  cp -R "$ROOT_DIR/scripts/." "$STAGE_DIR/scripts/"
  # Remove scripts com credenciais ou hosts internos
  rm -f "$STAGE_DIR/scripts/show-admin-credentials.sh" 2>/dev/null || true
  # tunnel-mig24.sh referencia host interno e chave SSH — ferramenta de dev,
  # não deve ir para o artefato de distribuição
  rm -f "$STAGE_DIR/scripts/tunnel-mig24.sh" 2>/dev/null || true
fi
if [ -d "$ROOT_DIR/artifacts" ]; then
  mkdir -p "$STAGE_DIR/artifacts"
  MISSING_ARTIFACTS=""
  for artifact in \
    acceptance-test-baseline.sha256 \
    final-acceptance-report.md \
    final-acceptance-results.json \
    manual-validation.json \
    visual-test-result.json \
    source-tree-manifest.sha256 \
    source-tree-hash.json \
    evidence-manifest.sha256
  do
    if [ -f "$ROOT_DIR/artifacts/$artifact" ]; then
      cp -f "$ROOT_DIR/artifacts/$artifact" "$STAGE_DIR/artifacts/"
    else
      MISSING_ARTIFACTS="$MISSING_ARTIFACTS $artifact"
    fi
  done
  if [ -n "$MISSING_ARTIFACTS" ]; then
    die "Artefatos obrigatórios ausentes:$MISSING_ARTIFACTS

Execute primeiro:  bash scripts/final-acceptance.sh

Esse comando gera todos os artefatos necessários (relatório, resultados JSON,
evidência visual, manifestos e logs) e depois chama o build-tarball automaticamente.
Não execute build-tarball.sh manualmente sem antes rodar o release completo."
  fi
  if [ -d "$ROOT_DIR/artifacts/acceptance-logs" ]; then
    cp -R "$ROOT_DIR/artifacts/acceptance-logs" "$STAGE_DIR/artifacts/"
  else
    die "Missing artifacts/acceptance-logs/"
  fi
fi

# Garante executáveis
chmod +x "$STAGE_DIR/install.sh" "$STAGE_DIR/uninstall.sh" "$STAGE_DIR/bin/main.exp" "$STAGE_DIR/scripts/"*.sh 2>/dev/null || true
chmod +x "$STAGE_DIR/bin/replay2.exp" 2>/dev/null || true
chmod +x "$STAGE_DIR/gateway/dakota-gateway" "$STAGE_DIR/gateway/control/server.py" 2>/dev/null || true

# Remove caches Python, virtualenvs e artefatos de teste do stage
find "$STAGE_DIR" -type d -name '__pycache__' -prune -exec rm -rf {} + 2>/dev/null || true
find "$STAGE_DIR" -type f \( -name '*.pyc' -o -name '*.pyo' \) -delete 2>/dev/null || true
rm -rf \
  "$STAGE_DIR/gateway/.venv" \
  "$STAGE_DIR/gateway/.pytest_cache" \
  "$STAGE_DIR/.pytest_cache" \
  "$STAGE_DIR/.mypy_cache" \
  "$STAGE_DIR/.ruff_cache" \
  "$STAGE_DIR/htmlcov" \
  "$STAGE_DIR/.coverage" 2>/dev/null || true

# Remove arquivos sensíveis e de estado local que NUNCA devem ir no artefato
find "$STAGE_DIR" \
  \( -name "*.db" \
  -o -name "*.db-wal" \
  -o -name "*.db-shm" \
  -o -name "*.sqlite" \
  -o -name "*.sqlite3" \
  -o -name "*.pyc" \
  -o -name "*.pyo" \
  -o -name ".env" \
  -o -name ".env.*" \
  -o -name "*.pem" \
  -o -name "*.key" \
  -o -name "*.crt" \
  -o -name "*.pfx" \
  -o -name "*.ppk" \
  -o -name "id_rsa*" \
  -o -name "id_ed25519*" \
  -o -name "id_ecdsa*" \
  -o -name ".token.env" \
  -o -name "*.tmp" \
  -o -name "*.swp" \
  -o -name "*.swo" \) \
  -delete 2>/dev/null || true

# Remove diretórios que NUNCA devem ir no artefato
rm -rf \
  "$STAGE_DIR/gateway/state/captures" \
  "$STAGE_DIR/gateway/state" \
  "$STAGE_DIR/node_modules" \
  "$STAGE_DIR/.git" \
  "$STAGE_DIR/dist" \
  "$STAGE_DIR/log" \
  "$STAGE_DIR/logs" 2>/dev/null || true

TIMESTAMP="${DAKOTA_TARBALL_TIMESTAMP:-$(date +%Y%m%d-%H%M%S)}"
OUT="$DIST_DIR/${APP_NAME}-${VERSION}-${TIMESTAMP}.tar.gz"
info "Gerando: $OUT"

(cd "$STAGE_PARENT" && {
  # Em alguns AIX o `tar` não suporta -z. Preferimos tar+gzip quando necessário.
  if tar -czf "$OUT" "${APP_NAME}-${VERSION}" >/dev/null 2>&1; then
    :
  else
    rm -f "$OUT"
    if command -v gzip >/dev/null 2>&1; then
      tar -cf - "${APP_NAME}-${VERSION}" | gzip -c >"$OUT"
    else
      die "tar não suporta -z e gzip não encontrado. Instale gzip ou use um tar com suporte a -z."
    fi
  fi
})

info "OK: $OUT"

#!/bin/sh
set -eu

APP_NAME="dakota-replay2"

info() { printf '%s\n' "$*"; }
die() { printf '%s\n' "Erro: $*" >&2; exit 1; }

# ── Argumentos ──────────────────────────────────────────────────────────────
# --with-benchmarks <id|none>  (default: auto = experimento oficial mais
#   recente com experiment-manifest.json válido). Históricos de benchmark
#   NUNCA entram automaticamente no pacote de runtime (FASE 11).
WITH_BENCHMARKS="auto"
while [ $# -gt 0 ]; do
  case "$1" in
    --with-benchmarks)
      [ $# -ge 2 ] || die "--with-benchmarks requer <id|none>"
      WITH_BENCHMARKS="$2"; shift 2 ;;
    --with-benchmarks=*)
      WITH_BENCHMARKS="${1#*=}"; shift ;;
    -h|--help)
      info "uso: build-tarball.sh [--with-benchmarks <id|none>]"
      exit 0 ;;
    *) die "argumento desconhecido: $1 (uso: build-tarball.sh [--with-benchmarks <id|none>])" ;;
  esac
done

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

# ── Modo release × modo dev ─────────────────────────────────────────────────
# Com artifacts/ presente (árvore de release), o aceite DEVE estar vinculado
# à árvore atual: results JSON da MESMA árvore (hash before/after) e da MESMA
# VERSION, com a suíte completa aprovada (FASE 2 — incidente 0.8.85: pacote
# carregou aceite de outra árvore, 57 checksums divergentes). Sem artifacts/
# (build de dev), os artefatos obrigatórios abaixo não são exigidos.
RELEASE_MODE=0
[ -d "$ROOT_DIR/artifacts" ] && RELEASE_MODE=1

TREE_HASH=""
BENCH_ID=""
if [ "$RELEASE_MODE" = "1" ]; then
  command -v python3 >/dev/null 2>&1 || die "python3 não encontrado (necessário para validar o aceite)"
  TREE_HASH="$(python3 "$ROOT_DIR/scripts/tree_hash.py")" || die "falha ao calcular o hash da árvore"
  info "Hash da árvore (aceite): $TREE_HASH"
  python3 "$ROOT_DIR/scripts/build_validate.py" check-acceptance \
    --root "$ROOT_DIR" --tree-hash "$TREE_HASH" \
    || die "o aceite em artifacts/ NÃO pertence a esta árvore/versão.
Rode primeiro:  bash scripts/final-acceptance.sh
(reaproveitar final-acceptance-results.json antigo é proibido)"
  BENCH_ID="$(python3 "$ROOT_DIR/scripts/build_validate.py" select-benchmark \
    --root "$ROOT_DIR" --with-benchmarks "$WITH_BENCHMARKS")" \
    || die "falha ao selecionar a evidência de benchmark"
  [ -n "$BENCH_ID" ] || die "nenhum experimento de benchmark válido selecionado.
O pacote de release exige a evidência oficial (artifacts/benchmarks/<id> com
experiment-manifest.json válido). Restaure o experimento aprovado ou indique
um id explícito com --with-benchmarks <id>."
  info "Benchmark oficial empacotado: $BENCH_ID"
fi

DIST_DIR="$ROOT_DIR/dist"
STAGE_PARENT="$(mktemp -d 2>/dev/null || mktemp -d -t "${APP_NAME}.XXXXXX")"
STAGE_DIR="$STAGE_PARENT/${APP_NAME}-${VERSION}"

cleanup() {
  rm -rf "$STAGE_PARENT"
  # TMP_OUT/TMP_TAR podem não existir ainda (trap registrado antes da definição)
  [ -z "${TMP_OUT:-}" ] || rm -f "$TMP_OUT"
  [ -z "${TMP_TAR:-}" ] || rm -f "$TMP_TAR"
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
# pytest.ini: sem ele a árvore extraída perde pythonpath=gateway e os markers
# registrados (collection quebra sem PYTHONPATH externo — incidente 0.8.7)
if [ -f "$ROOT_DIR/pytest.ini" ]; then cp -f "$ROOT_DIR/pytest.ini" "$STAGE_DIR/"; fi
if [ -f "$ROOT_DIR/README.md" ]; then cp -f "$ROOT_DIR/README.md" "$STAGE_DIR/"; fi
# package.json + package-lock.json: puppeteer pinned (§29) — a árvore
# extraída resolve a dependência visual sem instalação global silenciosa
if [ -f "$ROOT_DIR/package.json" ]; then cp -f "$ROOT_DIR/package.json" "$STAGE_DIR/"; fi
if [ -f "$ROOT_DIR/package-lock.json" ]; then cp -f "$ROOT_DIR/package-lock.json" "$STAGE_DIR/"; fi
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
  # Evidência do benchmark real AIX×Linux (§33): SOMENTE o experimento oficial
  # selecionado (FASE 11) — históricos (cap13-*-v1..vN antigos) NUNCA entram
  # automaticamente no pacote de runtime. Seleção: --with-benchmarks <id> ou,
  # por default, o mais recente com experiment-manifest.json válido.
  if [ -n "$BENCH_ID" ]; then
    mkdir -p "$STAGE_DIR/artifacts/benchmarks"
    cp -R "$ROOT_DIR/artifacts/benchmarks/$BENCH_ID" "$STAGE_DIR/artifacts/benchmarks/"
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

# O manifest de evidências do pacote cobre EXATAMENTE o que foi empacotado
# (subconjunto de benchmarks): regenerado no stage pelo gerador único
# (fail-closed — reprova se ficar vazio ou inválido).
if [ "$RELEASE_MODE" = "1" ]; then
  bash "$ROOT_DIR/scripts/acceptance/gen-evidence-manifest.sh" --root "$STAGE_DIR" \
    || die "falha ao regenerar o evidence manifest do pacote no stage"
fi

TIMESTAMP="${DAKOTA_TARBALL_TIMESTAMP:-$(date +%Y%m%d-%H%M%S)}"
OUT="$DIST_DIR/${APP_NAME}-${VERSION}-${TIMESTAMP}.tar.gz"
# Escrita atômica: grava em nome temporário e publica via rename (mesma FS).
# Sem isso, leitores concorrentes (build-selfinstall.sh, deploys AIX/Linux em
# paralelo) podem ler um payload parcial — incidente real no deploy 0.8.3
# ("sanity: payload gzip inválido").
TMP_OUT="$OUT.tmp.$$"
TMP_TAR="$OUT.tmp.$$.tar"
info "Gerando: $OUT"

# ── Determinismo do tarball (FASE 11) ───────────────────────────────────────
# Ordem estável de arquivos, owner/group 0, mtimes normalizados e gzip sem
# timestamp/nome: dois builds da mesma árvore (com DAKOTA_TARBALL_TIMESTAMP e
# SOURCE_DATE_EPOCH pinados) produzem o MESMO sha256. Feature-tested: tars
# sem suporte (ex.: AIX) caem no caminho clássico — ver CHECKLIST_EMPACOTAMENTO.md.
SOURCE_DATE_EPOCH="${SOURCE_DATE_EPOCH:-$(date +%s)}"
TAR_DET_FLAGS=""
if tar --sort=name --owner=0 --group=0 --numeric-owner --mtime="@${SOURCE_DATE_EPOCH}" \
     -cf /dev/null -T /dev/null >/dev/null 2>&1; then
  TAR_DET_FLAGS="--sort=name --owner=0 --group=0 --numeric-owner --mtime=@${SOURCE_DATE_EPOCH}"
fi
GZIP_N="-n"
gzip -n -c </dev/null >/dev/null 2>&1 || GZIP_N=""

if [ -n "$TAR_DET_FLAGS" ]; then
  # tar intermediário sem compressão: o rc do tar é checado DIRETAMENTE (num
  # pipe, um tar falhando não derruba o gzip e publicaria payload truncado).
  (cd "$STAGE_PARENT" && tar $TAR_DET_FLAGS -cf "$TMP_TAR" "${APP_NAME}-${VERSION}") \
    || { rm -f "$TMP_TAR"; die "falha ao gerar o tar determinístico"; }
  gzip $GZIP_N -c "$TMP_TAR" >"$TMP_OUT" \
    || { rm -f "$TMP_TAR" "$TMP_OUT"; die "falha ao comprimir o tarball"; }
  rm -f "$TMP_TAR"
else
  (cd "$STAGE_PARENT" && {
    # Em alguns AIX o `tar` não suporta -z. Preferimos tar+gzip quando necessário.
    if tar -czf "$TMP_OUT" "${APP_NAME}-${VERSION}" >/dev/null 2>&1; then
      :
    else
      rm -f "$TMP_OUT"
      if command -v gzip >/dev/null 2>&1; then
        tar -cf - "${APP_NAME}-${VERSION}" | gzip $GZIP_N -c >"$TMP_OUT"
      else
        die "tar não suporta -z e gzip não encontrado. Instale gzip ou use um tar com suporte a -z."
      fi
    fi
  })
fi

# ── Validação pós-build (FASE 2) ────────────────────────────────────────────
# Extrai o tarball recém-gerado, roda sanity checks (VERSION presente e igual,
# server.py presente, nenhum *.key/*.db/segredo/estado local) e, em modo
# release, exige que o hash da árvore extraída (calculado pelo tree_hash.py
# DO PRÓPRIO PACOTE) seja idêntico ao do aceite. Divergência = build falha e
# o payload inválido é removido antes da publicação.
if [ -n "$TREE_HASH" ]; then
  python3 "$ROOT_DIR/scripts/build_validate.py" verify-tarball "$TMP_OUT" \
    --version "$VERSION" --expected-hash "$TREE_HASH" --root "$ROOT_DIR" \
    || { rm -f "$TMP_OUT"; die "o tarball gerado diverge da árvore validada no aceite — build abortado"; }
else
  python3 "$ROOT_DIR/scripts/build_validate.py" verify-tarball "$TMP_OUT" \
    --version "$VERSION" \
    || { rm -f "$TMP_OUT"; die "o tarball gerado não passou nos sanity checks — build abortado"; }
fi

mv -f "$TMP_OUT" "$OUT"

info "OK: $OUT"

#!/bin/sh
# =============================================================================
# dakota-replay2 — Self-installing archive (auto install / auto update)
#
# Este arquivo é um script shell + tarball (.tar.gz) concatenados. Executá-lo
# instala o Replay2 do zero OU atualiza uma instalação existente:
#
#   Update (prefixo já tem gateway/control/server.py):
#     para serviços (control plane + capture-daemon), faz backup do replay.db,
#     sobrepõe o código (sem tocar em gateway/state nem .local-secrets),
#     corrige permissões e reinicia os serviços com health check.
#
#   Install novo:
#     extrai o payload e delega ao install.sh do pacote; gera os segredos
#     locais (.local-secrets) se ausentes.
#
# Uso:
#   sh dakota-replay2-<versao>.run [--prefix DIR] [--no-restart] [--no-deps]
#                                 [--service-user USER] [--service-group GRP]
#
# Opções:
#   --prefix DIR       Diretório de instalação (default: /opt/dakota/replay2)
#   --no-restart       Não reinicia os serviços após o update
#   --no-deps          Não instala dependências (só no install novo)
#   --service-user U   Usuário dos serviços (default: results)
#   --service-group G  Grupo dos serviços (default: cpd)
#
# Env: DAKOTA_CONTROL_PORT (default 8080), DAKOTA_ADMIN (bootstrap admin)
# =============================================================================
set -eu

APP_NAME="dakota-replay2"
PREFIX="/opt/dakota/replay2"
NO_RESTART=0
NO_DEPS=0
SERVICE_USER="results"
SERVICE_GROUP="cpd"
CONTROL_PORT="${DAKOTA_CONTROL_PORT:-8080}"
BOOTSTRAP="${DAKOTA_ADMIN:-admin:Admin123!}"

info() { printf '%s\n' "$*"; }
warn() { printf '%s\n' "AVISO: $*" >&2; }
die() { printf '%s\n' "ERRO: $*" >&2; exit 1; }

usage() {
  sed -n '2,32p' "$0" | sed 's/^# \{0,1\}//'
  exit 0
}

while [ $# -gt 0 ]; do
  case "$1" in
    --prefix) shift; [ $# -gt 0 ] || die "falta valor para --prefix"; PREFIX="$1" ;;
    --no-restart) NO_RESTART=1 ;;
    --no-deps) NO_DEPS=1 ;;
    --service-user) shift; [ $# -gt 0 ] || die "falta valor para --service-user"; SERVICE_USER="$1" ;;
    --service-group) shift; [ $# -gt 0 ] || die "falta valor para --service-group"; SERVICE_GROUP="$1" ;;
    -h|--help) usage ;;
    *) die "opção desconhecida: $1 (use --help)" ;;
  esac
  shift
done

is_root() { [ "$(id -u 2>/dev/null || echo 1)" = "0" ]; }

# ── Extração do payload ──────────────────────────────────────────────────────
ARCHIVE_LINE=$(awk '/^__ARCHIVE_BELOW__$/ { print NR + 1; exit 0; }' "$0")
[ -n "$ARCHIVE_LINE" ] || die "arquivo corrompido: marcador do payload não encontrado"

make_workdir() {
  if command -v mktemp >/dev/null 2>&1; then
    mktemp -d 2>/dev/null || mktemp -d -t replay2-selfinstall.XXXXXX
    return
  fi
  # AIX não tem mktemp: diretório por PID com umask fechado
  i=0
  while [ $i -lt 99 ]; do
    d="/tmp/replay2-selfinstall.$$.$i"
    if (umask 077 && mkdir "$d" 2>/dev/null); then
      echo "$d"
      return 0
    fi
    i=$((i + 1))
  done
  return 1
}

WORKDIR=$(make_workdir) || die "não consegui criar diretório temporário em /tmp"
cleanup() { rm -rf "$WORKDIR"; }
trap cleanup EXIT INT TERM

info "Extraindo payload..."
tail -n +"$ARCHIVE_LINE" "$0" | gzip -dc | tar -xf - -C "$WORKDIR" \
  || die "falha ao extrair o payload (arquivo corrompido?)"

SRC=""
for d in "$WORKDIR/$APP_NAME"-*; do
  [ -d "$d" ] && SRC="$d" && break
done
[ -n "$SRC" ] && [ -f "$SRC/VERSION" ] || die "payload inválido (VERSION não encontrado)"
NEW_VERSION=$(sed -n '1p' "$SRC/VERSION" | tr -d '\r\n')
info "Pacote: $APP_NAME $NEW_VERSION → $PREFIX"

# ── Helpers de serviço (AIX não tem pkill) ───────────────────────────────────
pids_of() {
  ps -ef | grep "$1" | grep -v grep | awk '{print $2}'
}

stop_services() {
  for pat in 'control/[s]erver.py' 'dakota-gateway capture-[d]aemon'; do
    PIDS=$(pids_of "$pat")
    if [ -n "$PIDS" ]; then
      info "Parando: $pat (PIDs: $PIDS)"
      kill $PIDS 2>/dev/null || true
    fi
  done
  sleep 2
  for pat in 'control/[s]erver.py' 'dakota-gateway capture-[d]aemon'; do
    PIDS=$(pids_of "$pat")
    [ -n "$PIDS" ] && kill -9 $PIDS 2>/dev/null || true
  done
}

start_services() {
  # Os su são destacados de stdin/stdout/stderr: sem isso os wrappers `bash -c`
  # dos daemons herdam os descritores do chamador e uma sessão SSH de deploy
  # (`ssh host "sh pacote.run"`) só encerra quando o último fd fecha — deploy
  # travava com os serviços já saudáveis (homologação Linux, v0.8.9).
  # Configuração operacional persistente (DAKOTA_SOURCE_ROOT, etc.): o env
  # file é do servidor, não vem no tarball — sobrevive a deploys/updates.
  su "$SERVICE_USER" -c "cd '$PREFIX/gateway' && if [ -f ./control.env ]; then . ./control.env; fi; DAKOTA_ADMIN=\"$BOOTSTRAP\" PYTHONPATH='$PREFIX/gateway':\$PYTHONPATH nohup python3 control/server.py --listen 0.0.0.0:$CONTROL_PORT --cookie-secret-file '$PREFIX/.local-secrets/cookie-secret' --hmac-key-file '$PREFIX/.local-secrets/hmac-key' --gateway-auto-activate --db '$PREFIX/gateway/state/replay.db' > /tmp/replay2-control.log 2>&1 &" </dev/null >/dev/null 2>&1
  sleep 1
  su "$SERVICE_USER" -c "cd '$PREFIX/gateway' && PYTHONPATH='$PREFIX/gateway':\$PYTHONPATH nohup python3 '$PREFIX/gateway/dakota-gateway' capture-daemon --db '$PREFIX/gateway/state/replay.db' --hmac-key-file '$PREFIX/.local-secrets/hmac-key' > /tmp/replay2-capture-daemon.log 2>&1 &" </dev/null >/dev/null 2>&1
  sleep 3
  python3 -c "import urllib.request; print(urllib.request.urlopen('http://localhost:$CONTROL_PORT/health',timeout=5).read().decode())" \
    || die "control plane não respondeu no /health (ver /tmp/replay2-control.log)"
  if [ -S "$PREFIX/gateway/state/daemon/capture.sock" ]; then
    info "capture-daemon socket OK"
  else
    warn "capture-daemon sem socket (ver /tmp/replay2-capture-daemon.log)"
  fi
}

fix_perms() {
  if is_root && id "$SERVICE_USER" >/dev/null 2>&1; then
    chown -R "$SERVICE_USER:$SERVICE_GROUP" "$PREFIX/gateway/" 2>/dev/null || true
    mkdir -p "$PREFIX/gateway/state/captures"
    chown -R "$SERVICE_USER:$SERVICE_GROUP" "$PREFIX/gateway/state/" 2>/dev/null || true
    [ -d "$PREFIX/.local-secrets" ] && chown -R "$SERVICE_USER:$SERVICE_GROUP" "$PREFIX/.local-secrets" || true
    chmod 0600 "$PREFIX/.local-secrets/hmac-key" 2>/dev/null || true
    chmod 0600 "$PREFIX/.local-secrets/cookie-secret" 2>/dev/null || true
    chmod 0660 "$PREFIX/gateway/state/replay.db" "$PREFIX/gateway/state/replay.db-wal" "$PREFIX/gateway/state/replay.db-shm" 2>/dev/null || true
  fi
}

ensure_secrets() {
  SECRETS_DIR="$PREFIX/.local-secrets"
  if [ -f "$SECRETS_DIR/hmac-key" ] && [ -f "$SECRETS_DIR/cookie-secret" ]; then
    return 0
  fi
  info "Gerando segredos locais em $SECRETS_DIR ..."
  mkdir -p "$SECRETS_DIR"
  python3 - "$SECRETS_DIR" <<'PYEOF'
import os
import secrets
import sys

secrets_dir = sys.argv[1]
for name in ("hmac-key", "cookie-secret"):
    path = os.path.join(secrets_dir, name)
    if not os.path.exists(path):
        with open(path, "wb") as fh:
            fh.write(secrets.token_bytes(32))
        os.chmod(path, 0o600)
PYEOF
}

install_wrapper() {
  # Wrapper do ForceCommand (captura SSH) — atualiza se o template veio no pacote
  TPL="$PREFIX/gateway/dakota_gateway/templates/dakota-capture-session.sh"
  if is_root && [ -f "$TPL" ] && [ -d /usr/local/bin ] && [ -w /usr/local/bin ]; then
    cp "$TPL" /usr/local/bin/dakota-capture-session
    chmod +x /usr/local/bin/dakota-capture-session
    info "Wrapper SSH atualizado: /usr/local/bin/dakota-capture-session"
  fi
}

# ── Update ou install ────────────────────────────────────────────────────────
WAS_RUNNING=0
if [ -f "$PREFIX/gateway/control/server.py" ]; then
  # ── UPDATE ──
  info "Instalação existente detectada — modo UPDATE."
  [ -n "$(pids_of 'control/[s]erver.py')" ] && WAS_RUNNING=1
  is_root || die "update precisa de root (parar/iniciar serviços e corrigir permissões)"

  stop_services

  DB="$PREFIX/gateway/state/replay.db"
  if [ -f "$DB" ]; then
    # Rotação ANTES do backup novo: mantém os 2 mais recentes (cada backup
    # tem o tamanho do banco; sem rotação o /opt encheu 2x em produção).
    KEEP="${REPLAY_DB_BACKUP_KEEP:-2}"
    ls -t "$DB".bak.* 2>/dev/null | tail -n +$((KEEP)) | while IFS= read -r old; do
      rm -f "$old" && info "Backup antigo removido: $old"
    done
    BAK="$DB.bak.$(date +%Y%m%d-%H%M%S)"
    cp "$DB" "$BAK" && info "Backup do banco: $BAK"
  fi

  # Sobrepõe o código (o payload nunca contém gateway/state nem segredos)
  info "Sobrepondo código em $PREFIX ..."
  ITEMS=""
  for item in bin lib screens examples gateway scripts tests install.sh uninstall.sh VERSION README.md conftest.py pytest.ini; do
    [ -e "$SRC/$item" ] && ITEMS="$ITEMS $item"
  done
  (cd "$SRC" && tar -cf - $ITEMS) | (cd "$PREFIX" && tar -xf -) \
    || die "falha ao copiar arquivos para $PREFIX"

  # Artefatos de benchmark (evidência de release, §33): o overlay acima não
  # cobre artifacts/ inteiro para não tocar nas evidências locais do
  # servidor, mas artifacts/benchmarks/ precisa chegar — o control plane
  # adota os experimentos no banco no boot (v0.8.8).
  if [ -d "$SRC/artifacts/benchmarks" ]; then
    mkdir -p "$PREFIX/artifacts"
    (cd "$SRC/artifacts" && tar -cf - benchmarks) | (cd "$PREFIX/artifacts" && tar -xf -) \
      || die "falha ao copiar artifacts/benchmarks para $PREFIX"
  fi

  # Limpa caches que possam ter ficado/vindo
  find "$PREFIX" -type d -name '__pycache__' -prune -exec rm -rf {} \; 2>/dev/null || true
  find "$PREFIX" -type f \( -name '*.pyc' -o -name '*.pyo' \) -exec rm -f {} \; 2>/dev/null || true

  ensure_secrets
  fix_perms
  install_wrapper

  if [ "$NO_RESTART" -eq 0 ] && [ "$WAS_RUNNING" -eq 1 ]; then
    info "Reiniciando serviços..."
    start_services
  else
    info "Serviços não reiniciados (--no-restart ou não estavam rodando)."
    info "Para iniciar: $PREFIX/gateway/control/server.py + capture-daemon (ver deploy)."
  fi
else
  # ── INSTALL NOVO ──
  info "Nenhuma instalação em $PREFIX — modo INSTALL."
  DEPS_FLAG=""
  [ "$NO_DEPS" -eq 1 ] && DEPS_FLAG="--no-deps"
  sh "$SRC/install.sh" --prefix "$PREFIX" --force $DEPS_FLAG
  ensure_secrets
  fix_perms
  install_wrapper
  info ""
  info "Install concluído. Para subir os serviços como $SERVICE_USER:"
  info "  control plane:  $PREFIX/gateway/control/server.py --listen 0.0.0.0:$CONTROL_PORT --gateway-auto-activate \\"
  info "                  --cookie-secret-file $PREFIX/.local-secrets/cookie-secret \\"
  info "                  --hmac-key-file $PREFIX/.local-secrets/hmac-key \\"
  info "                  --db $PREFIX/gateway/state/replay.db"
  info "  capture-daemon: $PREFIX/gateway/dakota-gateway capture-daemon \\"
  info "                  --db $PREFIX/gateway/state/replay.db \\"
  info "                  --hmac-key-file $PREFIX/.local-secrets/hmac-key"
fi

info ""
info "✓ $APP_NAME $NEW_VERSION processado com sucesso em $PREFIX"
exit 0

__ARCHIVE_BELOW__

# AGENTS.md — Guia para Agentes de Código

Este arquivo orienta agentes de IA (e novos desenvolvedores) que vão trabalhar no
**Dakota Replay2**. Ele descreve o que o projeto é, como está organizado, como
buildar, testar, contribuir e o que **não** fazer. Leia-o por completo antes de
alterar código.

---

## 1. Visão Geral do Projeto

O **Dakota Replay2** é uma plataforma de **validação de migração** do sistema
legado Recital 8 para o Recital 24 (ambiente Dakota). Não é apenas automação de
telas: a base combina captura auditável de sessões de terminal, replay sequencial
com ordem global preservada, verificação de integridade criptográfica, análise de
falhas e teste de estresse.

Objetivos centrais:

- **Capturar** fielmente sessões reais do Recital 8 (via gateway SSH auditável);
- **Reproduzir** no Recital 24 na mesma sequência observada (`strict-global`),
  com checkpoints e rastreabilidade;
- **Registrar falhas estruturadas** (`replay_failures`) com tipo, severidade,
  mensagem e evidência, investigáveis por API/UI;
- **Suportar carga** com `parallel-sessions`, `concurrency`, `speed`,
  `ramp_up_per_sec` e `jitter_ms`;
- **Gerar dados sintéticos e jornadas** a partir da análise do código-fonte
  legado (P2-A — Synthetic Knowledge Base).

Versão atual: ver arquivo `VERSION` (atualmente `0.7.9`). Linux é o alvo
operacional principal; AIX é contemplado no desenho (scripts POSIX, Expect/Tcl)
mas a homologação AIX é item operacional pendente, não capacidade comprovada.

A documentação e os comentários do projeto são em **português (pt-BR)** —
mantenha esse idioma em código novo, comentários e documentação.

---

## 2. Stack Tecnológica

| Camada | Tecnologia | Localização |
|---|---|---|
| Core engine (captura/automação de telas) | Expect/Tcl | `bin/`, `lib/`, `screens/`, `examples/` |
| Gateway (captura auditável, replay, CLI) | Python 3.10+ (stdlib) | `gateway/dakota_gateway/` |
| Terminal engine canônico | Python | `gateway/dakota_terminal/` |
| Control plane (API HTTP + UI operacional) | Python stdlib (`http.server.ThreadingHTTPServer`) + SQLite | `gateway/control/` |
| UI | HTML + CSS/JS vanilla + Tailwind (build via npx) | `gateway/control/templates/`, `gateway/control/static/` |
| Scripts | Shell POSIX (`sh`/`bash`) | `scripts/`, `dev.sh` |
| Build | Shell script → tarball `.tar.gz` | `scripts/build-tarball.sh` |
| Runtime | Processo direto no host (**sem containers**) | — |

Requisitos de ferramentas:

- `python3` (3.10+; CI testa 3.10, 3.11, 3.12) — dependências em
  `gateway/requirements.txt` (flask/bottle/werkzeug declarados, mas o servidor
  HTTP em produção usa stdlib; `watchfiles` só para hot-reload em dev;
  `websocket-client` e `Pillow` usados nos testes de aceitação/visual;
  pylint/flake8/black para qualidade de código);
- `node` >= 18 (testes JS com `node --test` e build do Tailwind);
- `tclsh` + pacote `tcltest`, e `expect` (engine Tcl e testes Tcl);
- cliente `ssh` (cenários de proxy/replay remoto);
- Chromium (apenas para evidência visual de aceitação e testes Selenium).

Não há `pyproject.toml`, `Cargo.toml` ou framework web externo no runtime.
Arquivos de configuração chave na raiz:

- `VERSION` — fonte única da versão (lida por `build-tarball.sh` e `bump.sh`);
- `pytest.ini` — `testpaths = tests gateway/tests`, `pythonpath = gateway`,
  markers: `unit`, `p2`, `control`, `integration`, `slow`, `selenium`, `external`;
- `Makefile` — targets de dev/test/build (ver seção 4);
- `package.json` (raiz) — apenas npm scripts de conveniência (`npm run dev`,
  `npm run test`, ...); `gateway/package.json` — só devDependency `tailwindcss`;
- `tailwind.config.cjs` — content scan em `gateway/control/templates/**`,
  `gateway/control/static/js/**` e `gateway/control/**/*.py`;
- `.github/workflows/ci.yml` — CI (GitHub Actions).

---

## 3. Arquitetura e Organização do Código

### 3.1 Mapa de diretórios

```
replay2/
├── bin/                      # Entrypoints Expect: main.exp, replay2.exp
├── lib/                      # Engine Tcl: capture, normalize, signature,
│                             #   state_machine, control, record, config,
│                             #   action, dump, events, log, plugins
├── screens/                  # Diretório de handlers de telas Tcl (convenção:
│                             #   registram na state machine; hoje só README +
│                             #   plugins.tcldict.txt — demos em examples/)
├── examples/                 # demo.exp + legacy_sim.tcl (simulador local)
├── gateway/
│   ├── dakota-gateway        # Wrapper executável (python3 → dakota_gateway.cli)
│   ├── dakota_gateway/       # Núcleo Python do gateway (ver 3.2)
│   ├── dakota_terminal/      # Engine de terminal canônica (ver 3.3)
│   ├── control/              # Control plane: server.py + routes/ + services/
│   │                         #   + templates/ + static/ (ver 3.4)
│   ├── tests/                # Testes Python do gateway
│   ├── docs/                 # ops.md, threat_model.md
│   ├── state/                # RUNTIME (gitignored): replay.db, captures/
│   └── requirements.txt
├── tests/                    # Suíte principal (Python + Tcl + fixtures)
│   ├── acceptance/           # Testes de aceitação/contrato (fases do release)
│   ├── fixtures/             # terminal_vectors/ (cp850, cp437, utf8, ...) etc.
│   ├── js/acceptance/        # Testes JS de aceitação (payload/playback)
│   ├── oracles/              # Oráculo JS do terminal virtual
│   ├── contracts/            # Contratos de telas
│   └── all.tcl               # Runner tcltest
├── scripts/                  # Utilitários POSIX (build, testes, smoke, deploy)
│   └── acceptance/           # Gates de aceitação run-phase-01..08
├── artifacts/                # Evidências de aceitação (necessárias p/ build)
├── dist/                     # Tarballs gerados (gitignored)
├── docs/                     # Referências Recital, navegação, servidor MIG24
│   └── historico/            # Relatórios congelados da v0.1.0 (GAPS, auditoria, análises)
├── log/                      # Logs locais (gitignored)
├── .local-secrets/           # hmac.key, cookie_secret.key (gitignored)
└── dev/                      # Sandbox de dev (gitignored)
```

### 3.2 `gateway/dakota_gateway/` — núcleo do gateway

- `gateway.py` — proxy SSH auditável; captura `bytes in/out`, `checkpoint`,
  `session_start/end` e eventos `deterministic_input` (tela estável + input);
- `audit_writer.py` — ordem global (`seq_global`), hash-chain, HMAC e manifest;
- `crypto.py` — primitivas criptográficas (HMAC, hash-chain);
- `verifier.py` — verificação de integridade da trilha;
- `replay.py` — replay no destino (modos `raw` e `deterministic`);
- `replay_control.py` — runner de runs, concorrência, métricas, falhas
  estruturadas, reprocessamento por faixa/sessão/checkpoint;
- `replay_failures.py` / `replay_run_state.py` — taxonomia de falhas e estado
  de runs;
- `screen.py` — normalização e assinatura de tela (fonte central do gateway);
- `canonical.py` — canônicos compartilhados entre camadas;
- `compliance.py` — policies de target (`gateway_required`,
  `direct_ssh_policy`, `capture_start_mode`, `capture_compliance_mode`);
- `auth.py` — autenticação/usuários do control plane;
- `assessment.py` — AI Assessment (análise consolidada do sistema legado);
- `terminal_config.py` — configuração de terminal (geometria, encoding);
- `state_db.py` — **helpers de acesso a SQLite** (`connect`, `now_ms`,
  `query_one`, `query_all`, `exec1`): é a API de persistência de facto, usada
  por todo o control plane (`server.py`, `auth_support.py`, services). O
  schema, o pool de conexões e as migrações vivem em `db/` (`schema.py`,
  `connection.py`, `migrations.py`) — código novo com regras de schema vai em
  `dakota_gateway/db/`. Há também um `schema.py` na raiz do pacote (legado) —
  prefira sempre `db/schema.py`;
- `cli.py` + `cli_commands/` (`catalog.py`, `runtime.py`, `env_profiles.py`) —
  CLI: `start`, `verify`, `replay`, `targets`, `profiles`, `runs`
  (create/start), `user add`, `env-profiles` e `synthetic` com muitos
  subcomandos (`analyze-source`, `screens`, `generate`, `stress`, `journey ...`,
  `schedule ...`, `record`, `explore`, `quickstart`, `pipeline`, `benchmark`,
  `assess`, `knowledge-base`, `export-junit`, `export-csv`, `watch`, `metrics`,
  `diff-quickstart`);
- `source_analyzer/` — P2-A Discovery: extratores SQL/ISAM/DBF/Recital, telas,
  menus, CRUD, relacionamentos, catálogo de programas/entidades, auditoria;
- `synthetic/` — P2-A Synthetic: planejador de dataset (grafo de dependências),
  sintetizador de dados, jornadas (inferência, geração CRUD, validação,
  verificação, dry-run), `journey_mix`, scheduler, executor remoto, stress
  runner, explorador de telas, relatórios de evidência/homologação;
- `benchmark/` — pacote de benchmark (AIX vs Linux);
- `templates/` — templates internos do gateway.

### 3.3 `gateway/dakota_terminal/` — terminal engine canônica

Desde v0.3.19, o **TerminalEngine Python é a fonte única oficial** de emulação
de terminal (parser ANSI/UTF-8, geometria, snapshots, `text_sig`/`visual_sig`).
Módulos: `parser.py`, `decoder.py`, `engine.py`, `model.py`, `geometry.py`,
`attributes.py`, `snapshot.py`, `serializer.py`, `signatures.py`,
`comparison.py`, `diffs.py`. O JS de produção **não** contém parser de
terminal — isso é garantido pelo teste
`production_no_terminal_parser.test.mjs`. Vetores de decodificação vivem em
`tests/fixtures/terminal_vectors/`.

### 3.4 `gateway/control/` — control plane (superfície oficial)

- `server.py` — entry point HTTP (stdlib `ThreadingHTTPServer`), shell leve
  (~900 linhas): auth/cookies, helpers e despacho;
- `routes/` — acoplamento HTTP por domínio (`run_routes`, `capture_routes`,
  `gateway_routes`, `observability_routes`, `catalog_routes`,
  `operational_routes`, `journey_routes`, `synthetic_routes`, `ui_routes`,
  `admin_routes`);
- `services/` — regras e payloads reutilizáveis (reports, cenários, captura,
  sessão/replay, observabilidade, analytics, ambiente);
- Módulos de suporte na raiz de `control/`: `auth_support.py`,
  `server_support.py`, `audit_scan_support.py`, `engineering_route_support.py`,
  `error_middleware.py`, `page_state_builders.py`, `runtime_supervision.py`,
  `websocket_support.py`;
- `ui_templates.py` — loader fino; `templates/` — HTMLs da UI;
- `static/js/` — JS vanilla (`core/`, `components/`, `pages/`, `vendor/`);
  testes `*.test.mjs` ao lado dos módulos;
- `openapi.yaml`, `synthetic-openapi.yaml` — contratos de API.

Padrão arquitetural do control plane: `server.py` despacha → `routes/` parseiam
HTTP → `services/` executam regras → `dakota_gateway/db/` persiste. **Não
inflar `server.py`**: rota nova vai em `routes/`, regra nova em `services/`.

Semântica visual da UI (convenção obrigatória): rose/pink = identidade/CTA;
emerald = sucesso/running; amber = queued/warning; red = erro/falha; neutral =
inativo/desabilitado.

### 3.5 Engine Tcl (`bin/`, `lib/`, `screens/`)

`bin/main.exp` é o loop principal: captura incremental (`lib/capture.tcl`),
normalização (`lib/normalize.tcl`), assinatura estável (`lib/signature.tcl`),
roteamento por estado (`lib/state_machine.tcl`), controle local
`pause/resume/step/send/dump` (`lib/control.tcl`) e gravação simplificada
(`lib/record.tcl`). Handlers de tela são módulos `.tcl` em `screens/` que se
registram via `::state_machine::register <assinatura> <estado> <proc>`
(`bin/main.exp` carrega os handlers via `::plugins::load_screens` de
`lib/plugins.tcl`, filtrados pelo estado em `screens/plugins.tcldict.txt`; o
diretório hoje só tem a convenção documentada — handlers de exemplo vivem em
`examples/`). Todo
entrypoint Tcl executa `encoding system utf-8` **antes** de qualquer `source`
(regra P0).

Atenção: a captura fiel consolidada é a do **gateway SSH**; `record.tcl` é um
gravador simplificado da engine e não substitui a trilha auditável.

---

## 4. Comandos de Build, Dev e Testes

### Setup e ambiente de desenvolvimento

```bash
make setup                 # cria .venv e instala deps (flask, bottle, werkzeug, watchfiles, pytest)
make dev                   # = ./dev.sh — sobe control server em http://127.0.0.1:8090
                           #   com hot-reload (watchfiles), admin padrão admin:Admin123!
make dev-stop / dev-logs   # para o servidor / tail -f log/replay2-control.log
```

Equivamente: `npm run dev`, `npm run setup`, etc. (npm scripts espelham o make).

Variáveis de ambiente de dev: `LISTEN` (default `127.0.0.1:8090`), `DB_PATH`
(default `gateway/state/replay.db`), `DAKOTA_ENV` (`lab` default |
`homologation` | `production`), `DAKOTA_ADMIN` (`user:senha` p/ bootstrap),
`SECRETS_DIR`, `COOKIE_SECRET_FILE`, `HMAC_KEY_FILE`, `WATCH_MODE` (0 desliga
hot-reload). O `dev.sh` gera os segredos em `.local-secrets/` se ausentes e
sobe o servidor com `--gateway-auto-activate`.

Execução manual do control plane:

```bash
python3 gateway/control/server.py \
  --listen 127.0.0.1:8090 \
  --db gateway/state/replay.db \
  --cookie-secret-file .local-secrets/cookie_secret.key \
  --hmac-key-file .local-secrets/hmac.key \
  --bootstrap-admin 'admin:Admin123!'
```

### Testes

Orquestrador principal: `./scripts/test.sh` (documentação completa em `TESTES.md`).

```bash
./scripts/test.sh --quick        # JS apenas — loop de dev
./scripts/test.sh --unit         # JS + Python + Tcl — antes de commit
./scripts/test.sh --all          # tudo (default)
./scripts/test.sh --ci           # tudo menos Tcl
./scripts/test.sh --js|--python|--tcl            # suítes individuais
./scripts/test.sh --capture --replay             # foco em captura/replay
./scripts/test.sh --smoke --remote --host 10.5.8.24 --port 8080
# modificadores: --verbose, --fail-fast
# DAKOTA_TEST_SH_TIMEOUT=450 (timeout por bloco), DAKOTA_TEST_SH_DRY_RUN=1
```

Por camada:

```bash
# Python (pytest.ini já define pythonpath=gateway e testpaths)
python3 -m pytest tests/ gateway/tests/ -q
python3 -m pytest -m "not slow and not selenium and not external" -q
python3 -m pytest -m p2 -q                      # P2-A Knowledge Base

# JavaScript (node:test, 7 arquivos oficiais listados em scripts/test.sh)
node --test gateway/control/static/js/virtual_terminal.test.mjs
node --test gateway/control/static/js/components/capture_replay_timeline.test.mjs
# ... + terminal_snapshot_renderer, replay_snapshot_state, checkpoint_seek,
#     template_syntax, production_no_terminal_parser

# Tcl
tclsh tests/all.tcl

# Make
make test          # subconjunto principal + gateway/tests
make test-all      # compileall + pytest (sem selenium) + syntax check Tcl
make test-p2       # testes P2-A
make check         # compileall + smoke + build check
make smoke-test    # scripts/smoke-test.sh (9 checks end-to-end locais)
```

Smoke remoto (requer acesso SSH ao host): `scripts/smoke-test-capture.sh` e
`scripts/smoke-test-replay.sh` (wrappers de `smoke-test-capture.py` /
`smoke-test-replay.py`) validam health/ready, login, captures, replay,
geometria, encoding, timeline e playback contra o servidor (default
`10.5.8.24:8080`).

Scripts auxiliares de teste em `scripts/`: `test-fast.sh`, `test-all.sh`,
`test-p2.sh`, `test-best-effort.sh`, `validate_acceptance_results.py`,
`process_tree.py` (runner com detecção de processos vazados, usado pelo
`test.sh`).

### Build e release

```bash
bash scripts/final-acceptance.sh   # pipeline de aceitação completo (fases 01–08);
                                   #   gera artifacts/ exigidos pelo build
./scripts/build-tarball.sh         # gera dist/dakota-replay2-<VERSION>-<ts>.tar.gz
make tailwind                      # rebuilda gateway/control/static/tailwind.css
bash scripts/bump.sh [patch|minor|major]   # incrementa VERSION
```

**Importante:** `build-tarball.sh` **falha** se os artefatos de aceitação em
`artifacts/` não existirem — rode `scripts/final-acceptance.sh` antes. O build
remove automaticamente do artefato: segredos (`*.key`, `*.pem`, `.env*`, chaves
SSH), bancos (`*.db*`, `*.sqlite*`), `gateway/state/`, `__pycache__`, `.venv`,
`node_modules`, `dist/`, `log/`. Ver `CHECKLIST_EMPACOTAMENTO.md` para a
verificação pós-build e o processo de release completo (build → copiar para
`remoto_dakota/artifacts/` → homologação → `git tag v$(cat VERSION)`).

### Instalação/deploy

```bash
./install.sh [--prefix /opt/dakota-replay2] [--no-deps] [--link-dir /usr/local/bin] [--force]
```

Instala em `/opt/dakota-replay2` (default), cria os wrappers `replay2` e
`dakota-gateway`, instala `expect`/`tcl` via apt/dnf/yum/zypper (Linux) ou AIX
Toolbox. Servidor de homologação/produção documentado: MIG24 AIX 7
(`10.5.8.25`, ver `docs/servidor-dakota-mig24.md`). Operação do gateway
(rotação, verificação, replay local de smoke): `gateway/docs/ops.md`.
`uninstall.sh` remove a instalação.

### CI

`.github/workflows/ci.yml`: matrix Python 3.10–3.12, instala Tcl/Expect, roda
`tclsh tests/all.tcl`, `gateway/tests/test_integrity.py`,
`tests/quick-test-api.py`, `tests/benchmark.tcl`, lint (pylint/flake8 com
`--exit-zero`), coverage (`gateway/tests/test_integrity.py`) e Selenium
(`continue-on-error`).

---

## 5. Convenções de Código

De `CONTRIBUTING.md` + prática observada:

### Python
- PEP 8; **docstrings em português**;
- Type hints com `from __future__ import annotations` no topo dos módulos;
- Ordem de imports: stdlib → third-party → `dakota_gateway` → `control`;
- Persistência nova em `dakota_gateway/db/` (não adicionar a `state_db.py`,
  que é shim legado);
- Rotas novas em `gateway/control/routes/`, regras em `services/` — manter
  `server.py` enxuto.

### Shell
- POSIX compatível (sem bash-isms nos scripts de produção; `test.sh`/`dev.sh`
  usam bash deliberadamente);
- `set -eu` (ou `set -euo pipefail` em bash);
- Variáveis com fallback: `${VAR:-default}`.

### Tcl
- `encoding system utf-8` antes de qualquer `source` (regra de segurança P0);
- Compatível com Linux e AIX (tcltest).

### JavaScript/UI
- Vanilla JS, módulos `.js`/`.cjs`; testes `.test.mjs` com `node:test`;
- Não reintroduzir parser de terminal no JS de produção (Python é a fonte
  canônica);
- Respeitar a semântica de cores da UI (seção 3.4); após alterar templates/JS,
  rebuildar o CSS com `make tailwind`.

### Fluxo de PR
1. Branch `feature/nome-da-feature`;
2. Implementar + testar (`make test` / `./scripts/test.sh --unit`);
3. PR para `develop`;
4. Squash merge.

---

## 6. Segurança

- **Segredos locais**: `.local-secrets/hmac.key` e `cookie_secret.key` (gerados
  pelo `dev.sh`); em operação, `/etc/dakota-gateway/` com `0600`. Nunca
  commitar — `.gitignore` já cobre `*.key`, `*.pem`, `.env*`, chaves SSH,
  `*.db*`, `.local-secrets/`, `gateway/state/`.
- **Trilha auditável**: o gateway grava eventos com `seq_global`/`seq_session`/
  `ts_ms`, hash-chain e HMAC. Sempre rodar `verify` antes de replay/migração.
  Não apontar `verify`/`replay` para diretório misto de capturas (eventos
  passivos de porta 22 não compartilham a cadeia HMAC da sessão PTY).
- **Bootstrap admin**: preferir `DAKOTA_ADMIN` a `--bootstrap-admin` (o argumento
  expõe senha em histórico de shell e process list). Em `DAKOTA_ENV=production`,
  `DAKOTA_ADMIN` é **obrigatório** e o servidor aborta sem ele.
- **Cookies**: `HttpOnly`, `SameSite=Lax`, `Secure` em produção; `/metrics` com
  autenticação em produção.
- **Credenciais de destino**: usar `credential_ref` (ex.: `env:VAR`) nos
  connection profiles — nunca gravar segredo bruto no banco.
- **Gateway-only**: targets com `gateway_required=true` exigem evidência de
  entrada via gateway (`entry_mode`, `via_gateway`, `gateway_session_id`);
  `capture_compliance_mode=strict` bloqueia start de runs não conformes.
- **Build**: o tarball é higienizado pelo `build-tarball.sh`; confira com o
  checklist de `CHECKLIST_EMPACOTAMENTO.md` antes de distribuir.
- **`scripts/install-local-ssh-capture.sh`** altera o `sshd_config` do sistema
  (instala `ForceCommand` para rotear SSH pelo gateway) — mudança fora do
  diretório do projeto; só executar com intenção explícita. Desfazer com
  `scripts/uninstall-local-ssh-capture.sh`.
- Hosts internos (`10.5.8.24`, `10.5.8.25`) aparecem em docs/scripts de smoke;
  não introduzir novos hosts/segredos hard-coded em código commitado.

---

## 7. Fronteiras Arquiteturais (o que NÃO fazer)

De `FRONTEIRAS.md` e `CONTRIBUTING.md`:

- ❌ Prometheus / Grafana / OpenTelemetry / observabilidade externa
- ❌ PostgreSQL ou outro banco — **SQLite apenas**
- ❌ Docker / Kubernetes / containers — processo direto no host
- ❌ Multi-tenancy (`tenant`, `tenant_id`)
- ❌ Monitoramento de infra (`host_status`, `service_check`) — isso é do
  projeto separado `r-observe/`
- ❌ Portas 3000/3001/9090 (stack r-observe); control plane usa 8090 (dev) /
  8080 (produção)
- ❌ Misturar com os projetos irmãos: `remoto_dakota/` (camada operacional de
  deploy/healthcheck) e `r-observe/` (observabilidade de infra externa)

O que **faz** sentido evoluir: Discovery Engine (`source_analyzer/`),
Synthetic Engine (`synthetic/`), replay determinístico, métricas internas via
`/metrics`, endpoints REST na API existente, `/health` e `/ready`.

---

## 8. Estratégia de Testes (resumo)

Pirâmide documentada em `TESTES.md`:

```
Smoke (remoto)        → scripts/smoke-test-*.sh  (requer SSH)
Integração (HTTP)     → gateway/tests/test_ui_routes.py
Unitários (Py+JS+Tcl) → tests/ + gateway/tests/ + static/js/*.test.mjs + tests/all.tcl
```

- Ao corrigir bug: escreva o teste de regressão **antes** da correção
  (fluxo em `TESTES.md`, seção "Fluxo de Desenvolvimento");
- Novo módulo Python → teste em `tests/test_<nome>_unit.py` (ou
  `gateway/tests/` se for específico do gateway);
- Contratos de tela/terminal: `tests/test_screen_contracts.py`,
  `tests/test_dakota_terminal_canonical.py`, fixtures em
  `tests/fixtures/terminal_vectors/`;
- Aceitação/release: `scripts/acceptance/run-phase-01..08-*.sh` orquestradas
  por `scripts/final-acceptance.sh`; resultados em `artifacts/` (baseline
  `acceptance-test-baseline.sha256` — alterações em arquivos de teste de
  aceitação exigem regerar a baseline via pipeline completo);
- Gaps de cobertura conhecidos estão listados em `TESTES.md` (seção "Gaps
  Conhecidos") — consulte antes de assumir que algo já é testado.

## 8.5. Deploy no Servidor (REGRA OBRIGATÓRIA)

**Sempre usar o script de deploy. NUNCA fazer deploy manual com `scp`/`ssh` soltos.**

### Deploy no MIG24 (AIX 10.5.8.25):
```bash
cd /home/jmachado/projetos/dakota/remoto_dakota
bash scripts/deploy.sh --target aix
```

### Deploy no Linux (10.5.8.24):
```bash
SSH_PASSWORD="$SSH_PASSWORD" bash scripts/deploy.sh --target linux
```

O script cuida de: build do tarball, backup do banco, parada do serviço, sincronização, chown, restart e health check.

### Hotfix (apenas emergência, 1-2 arquivos):
```bash
cd replay2
for f in gateway/control/services/arquivo.py gateway/control/templates/algum.html; do
  scp -o StrictHostKeyChecking=accept-new "$f" root@10.5.8.25:/opt/dakota/replay2/"$f"
done
ssh dakota-mig24-root "chown -R results:cpd /opt/dakota/replay2/gateway/ && pkill -f server.py; sleep 2; cd /opt/dakota/replay2/gateway && su results -c '...'"
```
Hotfixes devem ser seguidos de deploy completo via `deploy.sh` na próxima oportunidade.

## 9. Lacunas Conhecidas (não tratar como bug novo)

- `record.tcl` é gravador simplificado; a captura oficial é o gateway SSH;
- Taxonomia de falhas (`timeout`, `screen_divergence`, `navigation_error`,
  `concurrency_error`) ainda é heurística e pendente de refinamento por fluxo;
- Não existe catálogo formal de cenários de carga;
- Telnet suportado na camada de replay, mas autenticação automática prefere SSH;
- Portabilidade AIX pendente de homologação operacional dedicada.

## 10. Documentação de Referência

- `README.md` — visão funcional completa, API e CLI
- `TESTES.md` — catálogo e fluxo de testes
- `DESENVOLVIMENTO.md` — guia de dev (setup, env vars, troubleshooting)
- `CONTRIBUTING.md` — stack, convenções, fluxo de PR
- `FRONTEIRAS.md` — fronteiras arquiteturais
- `CHECKLIST_EMPACOTAMENTO.md` — release e exclusões do artefato
- `gateway/README.md` — gateway, deterministic record, replay local
- `gateway/docs/ops.md` — operação (rotação, verificação, replay)
- `gateway/docs/threat_model.md` — modelo de ameaças
- `ROADMAP.md`, `DEBT_MAP.md` — planejamento e dívida técnica
- `docs/` — referências do sistema Recital/Dakota e do servidor MIG24;
  `docs/historico/` — relatórios congelados da v0.1.0 (GAPS, auditoria,
  análises), mantidos só como referência histórica

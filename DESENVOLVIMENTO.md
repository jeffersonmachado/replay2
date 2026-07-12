# Desenvolvimento - Dakota Replay2

Guia rápido para iniciar o ambiente de desenvolvimento.

## Quick Start

### Opção 1: npm (recomendado)
```bash
npm run dev
```

### Opção 2: make
```bash
make dev
```

### Opção 3: script direto
```bash
./dev.sh
```

## Primeiros Passos

### 1. Configuração Inicial (primeira vez apenas)
```bash
npm run setup
# ou
make setup
```

Isto vai:
- Criar virtualenv Python (`.venv`)
- Instalar dependências (Flask, Bottle, Werkzeug)
- Gerar secrets locais (`HMAC_KEY`, `COOKIE_SECRET`)

### 2. Iniciar Ambiente de Desenvolvimento
```bash
npm run dev
```

O servidor inicia em **http://127.0.0.1:8090**

#### Login padrão
- **Usuário**: `admin`
- **Senha**: `Admin123!`

(pode ser customizado com `BOOTSTRAP_ADMIN=user:senha npm run dev`)

## Scripts Disponíveis

| Comando | Descrição |
|---------|-----------|
| `npm run dev` | Inicia servidor em modo desenvolvimento |
| `npm run dev:stop` | Para o servidor |
| `npm run dev:logs` | Mostra logs em tempo real |
| `npm run test` | Executa todos os testes |
| `npm run test:python` | Executa apenas testes Python |
| `npm run test:tcl` | Executa testes Tcl/Expect |
| `npm run setup` | Configuração inicial (venv + deps) |
| `npm run install` | Instala dependências Python |
| `npm run clean` | Remove artefatos (`__pycache__`, etc) |
| `npm run lint` | Análise estática de código |

## Variáveis de Ambiente

Customize o comportamento:

```bash
# Porta e LISTEN
LISTEN=127.0.0.1:8090 npm run dev

# Diretório de logs
LOG_DIR=/var/log/dakota npm run dev

# Banco de dados
DB_PATH=gateway/state/replay.db npm run dev

# Admin inicial (equivale a DAKOTA_ADMIN)
BOOTSTRAP_ADMIN=user:senha npm run dev

# Admin via env var (alternativa a --bootstrap-admin)
DAKOTA_ADMIN='admin:senha-segura' npm run dev

# Auto-ativação do gateway no boot (após reset)
DAKOTA_GATEWAY_AUTO_ACTIVATE=true npm run dev

# Modo de operação (lab, production, homologation)
DAKOTA_ENV=production npm run dev

# Combinação completa para bootstrap após reset
DAKOTA_ADMIN='admin:Dakota@2026!' \
  DAKOTA_GATEWAY_AUTO_ACTIVATE=true \
  npm run dev
```

### Bootstrap e Reset do Banco

Após `rm -f gateway/state/replay.db`, o servidor recria o banco do zero e executa
o **bootstrap automático**:

| Variável | Efeito |
|---|---|
| `DAKOTA_ADMIN=user:senha` | Cria admin inicial se não existir |
| `DAKOTA_GATEWAY_AUTO_ACTIVATE=true` | Ativa o gateway no boot |

O bootstrap também cria automaticamente:
- **Perfil de conexão padrão**: `SSH Direto (padrão)` — SSH porta 22, auth externa
- **Captura ativa**: se o gateway foi auto-ativado, uma captura é iniciada

**Exemplo completo de bootstrap após reset:**
```bash
# 1. Zerar banco
rm -f gateway/state/replay.db gateway/state/replay.db-shm gateway/state/replay.db-wal

# 2. Iniciar com bootstrap completo
DAKOTA_ADMIN='admin:Dakota@2026!' DAKOTA_GATEWAY_AUTO_ACTIVATE=true ./scripts/start-control.sh
```

**Log esperado:**
```
Modo: lab
Admin criado: admin
[bootstrap] perfil de conexao 'default' criado (SSH porta 22)
[startup] gateway auto-ativado por admin (DAKOTA_GATEWAY_AUTO_ACTIVATE=true)
listening on http://127.0.0.1:8090
```

## Estrutura de Desenvolvimento

```
dakota/replay2/
├── gateway/
│   ├── control/          # API + UI (Python/Jinja2)
│   │   ├── server.py     # Entry point
│   │   ├── routes/       # Endpoints HTTP
│   │   ├── services/     # Lógica de negócio
│   │   ├── templates/    # HTML (Jinja2)
│   │   └── static/       # JS, CSS
│   └── dakota_gateway/   # Gateway + Auditoria
│       ├── gateway.py    # Proxy SSH auditável
│       ├── audit_writer.py # Hash-chain + HMAC
│       ├── replay.py     # Runner de sessões
│       └── ...
├── lib/                  # Tcl (core capture)
├── bin/                  # Binários compilados
├── tests/                # Testes Python + Tcl
├── scripts/              # Shells utilities
├── dev.sh               # Script de dev (novo!)
├── Makefile             # Targets make (novo!)
└── package.json         # npm scripts (novo!)
```

## Fluxo Típico de Dev

### 1️⃣ Configuração Inicial
```bash
git clone ...
cd dakota/replay2
npm run setup
```

### 2️⃣ Iniciar Servidor
```bash
npm run dev
# Terminal exibe:
# → Dakota Replay2 - Ambiente de Desenvolvimento
# ✓ Diretórios criados: ./log, .local-secrets
# ✓ Virtualenv ativado
# ✓ Dependências instaladas
# ✓ Servidor iniciado (PID: 12345)
#
# Dashboard: http://127.0.0.1:8090
# Log:       ./log/replay2-control.log
# Admin:     admin:Admin123!
```

### 3️⃣ Acessar Dashboard
Abra **http://127.0.0.1:8090** no navegador.

Login com `admin:Admin123!`

### 4️⃣ Executar Testes
Em outro terminal:
```bash
npm run test
# ou apenas testes Python
npm run test:python
```

### 5️⃣ Ver Logs em Tempo Real
```bash
npm run dev:logs
# ou
tail -f log/replay2-control.log
```

### 6️⃣ Parar Servidor
```bash
npm run dev:stop
# ou Ctrl+C no terminal onde rodou
```

## Troubleshooting

### "virtualenv not found"
```bash
python3 -m venv .venv
source .venv/bin/activate
```

### "ModuleNotFoundError: No module named 'flask'"
```bash
npm run install
# ou manualmente
source .venv/bin/activate
pip install flask bottle werkzeug
```

### "Port already in use"
```bash
LISTEN=127.0.0.1:8091 npm run dev
```

### "Permission denied"
```bash
chmod +x dev.sh
chmod +x scripts/*.sh
```

### Logs fora do esperado
```bash
# Verificar arquivo de log
cat log/replay2-control.log
# ou 
npm run dev:logs

# Aumentar verbosidade
FLASK_DEBUG=1 npm run dev
```

## Integração SSH (Gateway Capture)

Para permitir que SSH automático capture via gateway:

```bash
./scripts/install-local-ssh-capture.sh --match-user myuser
```

Isto configura `ForceCommand` para interceptar logins e rotear pelo gateway auditável.

## Modo Hot-Reload

Se `flask-reload` estiver instalado, mudanças em `.py`, `.html`, `.css` e `.js` recarregam automaticamente.

Instalar:
```bash
source .venv/bin/activate
pip install flask-reload
```

## Build Tcl/Expect (opcional)

Para compilação de `bin/main.exp` e `bin/replay2.exp`:

```bash
./scripts/build-tarball.sh
```

(Não é obrigatório para desenvolvimento do Control Plane)

## Estrutura de Dados

Ao iniciar:
- `.local-secrets/` → Chaves HMAC e secrets de cookie
- `gateway/state/replay.db` → SQLite (usuários, runs, configurações)
- `log/` → Logs de execução

Limpar estado:
```bash
make clean
rm -rf gateway/state/replay.db .local-secrets
npm run dev
```

## Próximas Etapas

1. **Leia**: [ANALISE_PROFUNDA.md](ANALISE_PROFUNDA.md) - Visão técnica completa
2. **Explore**: [gateway/control/](gateway/control/) - API e UI
3. **Entenda**: [Filtros de Auditoria](FILTROS_AUDITORIA.md) - Sistema de filtros
4. **Teste**: `npm run test` - Suite de testes

# Análise Profunda: Dakota Replay2 (v0.1.0)

## Sumário Executivo

**Dakota Replay2** é uma plataforma robusta e modular para automação de sistemas legados em modo texto (Recital/Clipper) com capacidades avançadas de auditoria, integridade criptográfica e replay determinístico. Implementada em **Expect/Tcl** (core) com extensões em **Python/Go** (gateway), a solução é portável entre Linux e AIX.

**Objetivo Principal:** Capturar, registrar com integridade verificável, e reproduzir sessões interativas em máquinas legadas, mantendo ordem global total e conformidade auditória.

---

## 1. Arquitetura Geral

### 1.1 Componentes Principais

```
┌─────────────────────────────────────────────────────────────┐
│                   REPLAY2 ARCHITECTURE                       │
├─────────────────────────────────────────────────────────────┤
│                                                               │
│  ┌──────────────────┐         ┌──────────────────────┐      │
│  │  CORE ENGINE     │         │   GATEWAY LAYER      │      │
│  │  (Expect/Tcl)   │         │  (Python + Go)       │      │
│  │                  │         │                      │      │
│  │ ✓ Capture        │         │ ✓ AuditLog (JSONL)  │      │
│  │ ✓ Normalize      │         │ ✓ SSH Proxy         │      │
│  │ ✓ Signature      │         │ ✓ Integrity Hash-Chain│    │
│  │ ✓ State Machine  │         │ ✓ Replay Control    │      │
│  │ ✓ Handlers/Plugins│        │ ✓ Checkpoints       │      │
│  └──────────────────┘         └──────────────────────┘      │
│                                                               │
│              ┌──────────────────────┐                        │
│              │   CONTROL PLANE      │                        │
│              │   (Python + SQLite)  │                        │
│              │                      │                        │
│              │ ✓ Dashboard Web      │                        │
│              │ ✓ User/Roles (RBAC)  │                        │
│              │ ✓ Run Scheduling     │                        │
│              │ ✓ Metadata Store     │                        │
│              └──────────────────────┘                        │
│                                                               │
│  ┌────────────────────────────────────────────────┐          │
│  │        SUPPORTING INFRASTRUCTURE               │          │
│  │ ✓ Tests (tcltest + Python)                    │          │
│  │ ✓ Tarball Distribution (Linux + AIX)          │          │
│  │ ✓ Installation (POSIX scripts)                 │          │
│  │ ✓ Examples & Fixtures                         │          │
│  └────────────────────────────────────────────────┘          │
│                                                               │
└─────────────────────────────────────────────────────────────┘
```

### 1.2 Fluxo de Dados (Session Lifecycle)

```
USER SSH SESSION
       │
       ├─→ Gateway (ForcedCommand via SSHD)
       │       │
       │       ├─→ Opens SSH to LEGACY_HOST
       │       │
       │       ├─→ Proxy bytes (both directions)
       │       │
       │       ├─→ Capture via PTY
       │       │
       │       └─→ Write Audit Log (JSONL + Integrity)
       │           │
       │           ├─ seq_global (global order)
       │           ├─ hash-chain (prev_hash → hash)
       │           ├─ HMAC (verified with key)
       │           └─ Checkpoints (screen signatures)
       │
       └─→ Audit Log Stored in /var/log/dakota-gateway/
           │
           ├─ Verifiable: hash-chain + HMAC detection of tampering
           ├─ Replayable: seq_global ordering maintained
           └─ Auditable: actor, timestamps, direction (in/out)
```

---

## 2. Core Engine (bin/ + lib/)

### 2.1 Captura de Tela (lib/capture.tcl)

**Responsabilidade:** Extrair buffer completo do PTY com determinismo.

- **Método:** Lê buffer via `expect` com timeouts calibrados
- **Encoding:** UTF-8 desde o início (evita corrupção de caracteres)
- **Namespace:** `::capture`
- **Saída:** String bruta com ANSI + box-drawing preservado

```tcl
# Exemplo conceitual
set screen_raw [::capture::get_screen $spawn_id]
# → "\x1b[H\x1b[2J┌─────────┐\n│ Login   │\n└─────────┘"
```

**Desafios Solucionados:**
- TTY echo loop prevention (flags `-nottycopy`, `-nottyinit`)
- Non-blocking read with deadline
- Incremental buffer accumulation

### 2.2 Normalização (lib/normalize.tcl)

**Responsabilidade:** Clean ANSI, Unicode box-drawing, whitespace inconsistencies.

**Transformações aplicadas:**

1. **Remove ANSI Escape Sequences:** `\x1b[31m`, `\x1b[1A`, etc.
2. **Box-Drawing Unicode → ASCII:** Converte `─`, `│`, `┌`, `└`, etc. para `-`, `|`, `+`
3. **Whitespace Trimming:** Remove linhas excedentes em branco
4. **Line Breaks Normalization:** Garante `\n` consistente

```tcl
set screen_raw "\x1b[31m┌─────┐\n\x1b[0m│ Menu│\n└─────┘"
set norm [::normalize::screen $screen_raw]
# → "+-----+\n| Menu|\n+-----+"
```

**Namespace:** `::normalize`

**Saída:** Texto limpo, portável, sem ANSI/Unicode especiais.

### 2.3 Assinatura de Tela (lib/signature.tcl)

**Responsabilidade:** Gerar identificador estável e determinístico de uma tela.

**Estratégia:** Não depende de posições absolutas, mas de **estrutura**:

```
Format: "L=<lines>;W=<max_width>;TIT=<titles>;LBL=<labels>"

Exemplo:
  "L=8;W=38;TIT=Login System;LBL=Usuario:;Senha:"
```

**Componentes da Assinatura:**

| Campo | Descrição |
|-------|-----------|
| `L` | Número de linhas |
| `W` | Largura máxima |
| `TIT` | Títulos (linhas com decoração + frame) |
| `LBL` | Labels estáticos (padrão `palavra:`) |

**Namespace:** `::signature`

**Exemplo de Funcionamento:**

```tcl
set norm "┌──────┐\n│Login │\n├──────┤\n│User: │\n│Pass: │\n└──────┘"
set sig [::signature::from_screen $norm]
# → "L=6;W=8;TIT=Login;LBL=User:;Pass:"
```

**Vantagens:**
- Resistente a mudanças cosméticas (cores, posição micro-ajustada)
- Portável entre AIX e Linux
- Determinístico (hash estável)

### 2.4 Máquina de Estados (lib/state_machine.tcl)

**Responsabilidade:** Despacho de handlers com base em assinatura de tela.

**Conceitos:**

- **Estado**: Nome atual (ex: `LOGIN`, `MENU`, `CADASTRO`)
- **Regra**: Mapeamento `signature → handler_proc + novo_estado`
- **Handler**: Função que reage à tela (ex: digita usuario/senha)

**API pública:**

```tcl
# Registra uma regra: se vê assinatura → executa handler e muda estado
::state_machine::register_rule $sig $new_state $handler_proc

# Despacha: verifica se tela atual casa com alguma regra
::state_machine::dispatch $spawn_id $sig $norm_screen
# → retorna 1 se handler executou, 0 se nenhuma regra

# Get/set estado
::state_machine::set_current_state "LOGIN"
set current [::state_machine::get_current_state]
```

**Namespace:** `::state_machine`

**Exemplo (Login Handler):**

```tcl
# Em screens/screen_login.tcl
proc handle_login {spawn_id norm_screen} {
    send "usuario\r"
    after 100
    send "senha123\r"
    return 1
}

::state_machine::register_rule \
    "L=8;W=38;TIT=Login*;LBL=*Usuario:*" \
    "LOGADO" \
    handle_login
```

### 2.5 Plugins/Handlers (screens/*.tcl)

**Responsabilidade:** Lógica específica por tela (isolada em módulos).

**Padrão:**

- 1 arquivo `.tcl` por tela lógica
- Registra suas regras em `::state_machine`
- Procedure `handle_XXXX` que reage à tela

**Exemplo: screens/screen_menu.tcl**

```tcl
proc handle_menu_option {spawn_id norm_screen} {
    # Lógica do menu...
    send "1\r"  ;# Seleciona opção 1
    return 1
}

::state_machine::register_rule \
    "L=10;W=40;TIT=MENU*" \
    "MENU_SELECIONADO" \
    handle_menu_option
```

---

## 3. Gateway Layer (gateway/)

### 3.1 Terminal Gateway (gateway/dakota_gateway/)

**Responsabilidade:** Proxy SSH com auditoria determinística.

**Arquitetura:**

```
USER SSH CONNECT
       ↓
  SSHD (ForceCommand)
       ↓
  dakota_gateway.cli start
       ├─ Opens SSH to LEGACY_HOST (via Popen + PTY)
       ├─ Proxy bytes (both directions) via select()
       ├─ Capture screen buffers periodically
       ├─ Write AuditLog (JSONL)
       └─ Verify integrity (hash-chain + HMAC)
```

**Componentes Chave:**

#### 3.1.1 AuditWriter (audit_writer.py)

**Responsabilidade:** Escrever eventos com integridade.

```python
# Schema simplificado
class AuditEvent:
    v: str                    # "v1"
    seq_global: int          # Ordem global total
    ts_ms: int               # Epoch millis
    type: str                # "bytes" | "checkpoint" | "session_start" | "session_end"
    actor: str               # Usuário no gateway
    session_id: str          # UUID da sessão
    seq_session: int         # Seq por sessão
    
    # Para "bytes":
    dir: str                 # "in" or "out"
    data_b64: str            # Base64 do chunk
    n: int                   # Tamanho
    
    # Para "checkpoint":
    sig: str                 # Assinatura da tela
    norm_sha256: str         # SHA256 do texto normalizado
    norm_len: int            # Tamanho do texto
    
    # Integridade:
    prev_hash: str           # SHA256 do evento anterior
    hash: str                # SHA256 do evento atual
    hmac: str                # HMAC-SHA256
```

**Formato no Disco (JSONL):**

```json
{"v":"v1","seq_global":1,"ts_ms":1711000000000,"type":"session_start","actor":"legacyuser","session_id":"abc123","seq_session":1,"prev_hash":"","hash":"sha256_hex...","hmac":"hmac_hex..."}
{"v":"v1","seq_global":2,"ts_ms":1711000000050,"type":"bytes","actor":"legacyuser","session_id":"abc123","seq_session":2,"dir":"in","data_b64":"dXNlcm5hbWU=","n":8,"prev_hash":"sha256_hex...","hash":"sha256_hex...","hmac":"hmac_hex..."}
{"v":"v1","seq_global":3,"ts_ms":1711000000100,"type":"checkpoint","actor":"legacyuser","session_id":"abc123","seq_session":3,"sig":"L=8;W=38;TIT=Login","norm_sha256":"sha256_hex...","norm_len":256,"prev_hash":"sha256_hex...","hash":"sha256_hex...","hmac":"hmac_hex..."}
```

#### 3.1.2 Hash-Chain Integrity

**Conceito:** Detecção de tampering via encadeamento de hashes.

```
Event 1: prev_hash = ""
         hash = SHA256(canonicalized_event_1)

Event 2: prev_hash = hash_1  ← linked to previous
         hash = SHA256(canonicalized_event_2)

Event 3: prev_hash = hash_2
         hash = SHA256(canonicalized_event_3)

Verificação:
  - Se alguém alterar Event 2 → hash muda
  - Event 3's prev_hash não casa mais
  - Detecção automática ✓
```

**Canonical Format (determinístico):**

```
v=v1
seq_global=2
ts_ms=1711000000050
type=bytes
actor=legacyuser
session_id=abc123
seq_session=2
dir=in
n=8
data_b64=dXNlcm5hbWU=
sig=
norm_sha256=
norm_len=
prev_hash=sha256_event_1
```

#### 3.1.3 Screen Normalization & Checkpoints (screen.py)

**Responsabilidade:** Normalizar telas no lado do gateway (mesmo algoritmo que lib/normalize.tcl).

```python
def normalize_screen(raw_bytes: bytes) -> str:
    # Remove ANSI, converte box-drawing, limpa whitespace
    
def signature_from_screen(norm_text: str) -> str:
    # Gera assinatura (L=; W=; TIT=; LBL=)
```

**Checkpoint Logic:**

```
Durante sessão, quando:
  - N_BYTES >= checkpoint_min_bytes (default 512)
  - E quiet_ms passaram desde último checkpoint
  
→ Capture screen atual
  - Normalize
  - Gera signature + SHA256 do texto
  - Escreve checkpoint event
  - Reset contador de bytes
```

**Checklist Detecta Desvios:**

Se replayer chegar em estado diferente → assinatura não casa → erro detectado.

#### 3.1.4 Verifier (verifier.py)

**Responsabilidade:** Validar log com integridade.

```python
def verify_log(log_dir: str, hmac_key: bytes) -> bool:
    # Para cada evento:
    #   1. Valida prev_hash (aponta para anterior)
    #   2. Recalcula hash (canonicalization)
    #   3. Verifica HMAC (HMAC-SHA256 com chave)
    #   4. Verifica seq_global (sem gaps)
    # 
    # Se qualquer check falhar → VerificationError
```

### 3.2 Replay Control (replay_control.py)

**Responsabilidade:** Reproduzir log em destino, mantendo ordem global.

```python
# Passo 1: Lê log em ordem (seq_global)
# Passo 2: Para cada arquivo de tela legado:
#   - Abre SSH no TARGET_HOST
#   - Envia input_bytes (dir == "in")
#   - Periodicamente valida checkpoints
#   - Compara signature/norm_sha256 com esperado
#
# Resultado: Reprodução determinística + validação de chegada
```

**Modes:**

- `sequential`: Ordem global estrita (default, mais seguro)
- `parallel-sessions`: Múltiplas sessões em paralelo (menos ordem global)

---

## 4. Control Plane (gateway/control/)

### 4.1 Dashboard + API (control/server.py)

**Responsabilidade:** Interface web para:

- Login/RBAC (admin, operator, viewer)
- Visualização de runs (execuções de replay)
- Status e logs
- Scheduling de replayss

**Stack:**

- **Framework:** Python `http.server` (stdlib puro, sem Django/Flask)
- **Banco:** SQLite (em `gateway/state/replay.db`)
- **Auth:** PBKDF2 hash + cookie assinado

**Endpoints Principais:**

| Endpoint | Método | Descrição |
|----------|--------|-----------|
| `/` | GET | Dashboard HTML |
| `/api/runs` | GET | Lista runs |
| `/api/runs/<id>/start` | POST | Inicia replay |
| `/api/runs/<id>/stop` | POST | Para replay |
| `/api/users` | GET/POST | Manage users |
| `/login` | POST | Authenticate |
| `/logout` | GET | Clear session |

**Exemplo de Schema SQL:**

```sql
CREATE TABLE users (
    id INTEGER PRIMARY KEY,
    username TEXT UNIQUE,
    password_hash TEXT,
    role TEXT,  -- admin|operator|viewer
    created_at_ms INTEGER
);

CREATE TABLE replay_runs (
    id INTEGER PRIMARY KEY,
    name TEXT,
    status TEXT,  -- queued|running|completed|failed
    log_dir TEXT,
    target_host TEXT,
    target_user TEXT,
    created_at_ms INTEGER,
    started_at_ms INTEGER,
    completed_at_ms INTEGER,
    result TEXT
);
```

### 4.2 Dashboard Web (dashboard/server.py)

**Responsabilidade:** Visualizar eventos da engine (opcional).

**Entrada:** JSONL de eventos (do replay2 com `--log-format json`).

```bash
expect bin/main.exp ... --log-format json --log-stream stdout > events.jsonl
python3 dashboard/server.py --events-file events.jsonl --listen 127.0.0.1:8080
```

---

## 5. Artefatos & Configuração

### 5.1 Configuração (lib/config.tcl)

**Responsabilidade:** Parsing de CLI + defaults seguros.

**Variáveis de Configuração:**

```tcl
dict create \
    legacy_cmd "tclsh examples/legacy_sim.tcl"  # Comando do legado
    encoding "utf-8"                            # Encoding PTY
    translation "auto"                          # \r\n handling
    timeout_seconds 30                          # Timeout geral
    stable_required 3                           # N leituras estáveis
    dump_dir ""                                 # Debug dumps (se vazio, desabled)
    record_file ""                              # Audit log (se vazio, disabled)
    log_format "text"                           # text|json
    log_stream "stderr"                         # stderr|stdout
```

**Parsing de CLI:**

```bash
expect bin/main.exp \
    --legacy-cmd "ssh user@host cmdhere" \
    --encoding utf-8 \
    --dump-dir /tmp/dumps \
    --record-file /tmp/audit.jsonl
```

### 5.2 Dumping & Diagnostics (lib/dump.tcl)

**Responsabilidade:** Artefatos de diagnóstico (raw, normalized, signature, estado).

```
dump-dir/
  ├─ 0001_raw.txt         # Captura bruta
  ├─ 0001_norm.txt        # Após normalização
  ├─ 0001_sig.txt         # Assinatura gerada
  ├─ 0001_meta.tcldict.txt # {"state": "LOGIN", "sig": "L=8;..."}
  ├─ 0002_raw.txt
  ...
```

**Ativação:**

```bash
expect bin/main.exp ... --dump-dir /tmp/dump_debug --dump-on-unknown
```

### 5.3 Recording & Replay (lib/record.tcl)

**Responsabilidade:** Gravar inputs do usuário e reproduzir.

```bash
# Gravar
expect bin/main.exp --legacy-cmd "..." --record-file session.rec

# Reproduzir
expect bin/main.exp --legacy-cmd "..." --replay-file session.rec
```

---

## 6. Testing & Quality

### 6.1 Unit Tests (tests/*.test.tcl)

**Framework:** `tcltest` (puro Tcl, portável).

**Cobertura:**

- `capture.test.tcl`: Leitura de buffer
- `normalize.test.tcl`: Limpeza de ANSI/box-drawing
- `signature.test.tcl`: Geração de assinatura
- `config.test.tcl`: Parsing de CLI

**Exemplo:**

```tcl
test normalize_ansi_01 "remove escape sequences" -body {
    set raw "\x1b[31mHello\x1b[0m"
    set norm [::normalize::screen $raw]
    string equal $norm "Hello"
} -result 1
```

### 6.2 Integration Tests (integration_legacy_sim.test.tcl)

**Objetivo:** Testar fluxo end-to-end com simulador.

```tcl
test integration_legacy_sim_login_01 "simulador gera tela de login" -body {
    # Spawn simulador
    # Captura tela
    # Valida assinatura
    # Despacha handler
    # Verifica que estado mudou
}
```

### 6.3 Integrity Tests (gateway/tests/test_integrity.py)

**Objetivo:** Verificar hash-chain e HMAC.

```python
def test_writer_and_verify_ok():
    # Escreve eventos
    # Verifica log
    # Deve passar

def test_verify_detects_tamper():
    # Altera um evento no arquivo
    # Tenta verificar
    # Deve lançar VerificationError
```

---

## 7. Distribuição & Deployment

### 7.1 Tarball Build (scripts/build-tarball.sh)

**Processo:**

```bash
./scripts/build-tarball.sh
# Gera dist/dakota-replay2-0.1.0.tar.gz

# Estrutura do tarball:
# dakota-replay2-0.1.0/
#   ├─ bin/
#   ├─ lib/
#   ├─ gateway/  (sem __pycache__)
#   ├─ dashboard/
#   ├─ install.sh
#   ├─ uninstall.sh
#   ├─ VERSION
```

**Features:**

- Remove `__pycache__` (tamanho), preserva `.py`
- Suporta AIX (tar sem -z, fallback com gzip)
- Portável (POSIX shell)

### 7.2 Instalação (install.sh)

**Comportamento:**

```bash
tar -xzf dakota-replay2-0.1.0.tar.gz
cd dakota-replay2-0.1.0
sudo ./install.sh --prefix /opt/dakota-replay2 [--no-deps]
```

**Fases:**

1. Detecta/instala deps: Expect, Tcl (se --no-deps não passado)
2. Copia arquivos para `--prefix`
3. Cria wrapper `/opt/dakota-replay2/bin/replay2` (shell)
4. Symlink para `/usr/local/bin/replay2` (se possível)

**Desinstalação:**

```bash
sudo /opt/dakota-replay2/uninstall.sh
```

---

## 8. Segurança & Auditoria

### 8.1 Design Principles

| Princípio | Implementação |
|-----------|---------------|
| **Integridade** | Hash-chain + HMAC-SHA256 |
| **Non-Repudiation** | Timestamps (ts_ms) + actor field |
| **Auditoria** | Log append-only (JSONL) |
| **Determinismo** | seq_global (sem gaps) |
| **Validação Destino** | Screen signature checkpoints |
| **Isolamento** | Handlers em namespace separado |

### 8.2 Threat Model (gateway/docs/threat_model.md)

**Cenários Cobertos:**

- ✓ Tampering do log (hash-chain + HMAC)
- ✓ Replay attack (seq_global + timestamps)
- ✓ Screen mismatch no destino (checkpoints)
- ✓ Unauthorized access (RBAC + authentication)

**Out of Scope:**

- Network eavesdropping (use SSH tunneling)
- Physical access ao disco (assume secure disk)

### 8.3 Configuration Segura

```bash
# HMAC key (gerar)
head -c 32 /dev/urandom > /etc/dakota-gateway/hmac.key
chmod 600 /etc/dakota-gateway/hmac.key

# Cookie secret (gerar)
head -c 32 /dev/urandom > /etc/dakota-gateway/cookie_secret.key
chmod 600 /etc/dakota-gateway/cookie_secret.key

# Log dir (append-only se possível)
mkdir -p /var/log/dakota-gateway
chmod 750 /var/log/dakota-gateway
```

---

## 9. Fluxos Principais

### 9.1 Fluxo de Captura & Automação (main.exp)

```
1. Parse CLI arguments
   └─ legacy_cmd, encoding, timeouts, plugins, etc.

2. Load all lib/*.tcl
   └─ Capture, Normalize, Signature, StateMachine, Actions

3. Load screens/*.tcl
   └─ Register handlers

4. Spawn legacy process
   └─ Wait for stable screen

5. Loop:
   a. Capture screen (blocking read with timeout)
   b. Normalize (remove ANSI, box-draw)
   c. Generate signature
   d. Check stability (N consistent reads)
   e. Dispatch to state_machine
      - Match signature → find handler
      - Execute handler (send input, etc.)
      - Record event (if recording enabled)
   f. Check for exit conditions
   g. Sleep N ms

6. Close spawn_id
   └─ Exit
```

### 9.2 Fluxo de Gateway (TerminalGateway)

```
1. ForcedCommand via SSHD
   └─ python3 -m dakota_gateway.cli start ...

2. Initialize:
   - Generate session_id (UUID)
   - Get actor from env (SUDO_USER, LOGNAME, USER)
   - Open AuditWriter (JSONL)

3. PTY Setup:
   - master_fd, slave_fd = pty.openpty()
   - Popen(["ssh", "-tt", "user@legacy"], slave_fd)

4. Main Loop (select on master_fd):
   - Read from master (legacy → user)
   - Write to user's stdin
   - Capture with boundary detection
   - Write checkpoint if bytes threshold hit
   - Read from user's stdin
   - Write to master (user → legacy)
   - Write event

5. Verify Integrity:
   - Hash-chain validation
   - HMAC check

6. Cleanup
   - Write session_end event
   - Close streams
```

### 9.3 Fluxo de Replay (replay_control.py)

```
1. Verify log
   └─ Hash-chain + HMAC OK?

2. Open PTY & SSH to target
   └─ Popen(["ssh", "-tt", "user@target"], slave_fd)

3. Iterate events (sorted by seq_global):
   a. If type == "bytes" AND dir == "in":
      - Decode data_b64
      - Write to target's stdin
   
   b. If type == "checkpoint":
      - Capture current screen
      - Normalize
      - Generate signature
      - Compare with expected (sig + norm_sha256)
      - If mismatch → log warning/error
   
   c. If type == "session_end":
      - Wait a bit
      - Close SSH session

4. Close
```

---

## 10. Pontos de Extensibilidade

### 10.1 Novos Handlers/Plugins

Adicionar `screens/screen_novo.tcl`:

```tcl
proc handle_novo_state {spawn_id norm_screen} {
    # Sua lógica aqui
    send "comando\r"
    return 1
}

::state_machine::register_rule \
    "L=X;W=Y;TIT=*;LBL=*" \
    "NOVO_STATE" \
    handle_novo_state
```

### 10.2 Novos Encoding ou Normalizações

Editar `lib/normalize.tcl`:

```tcl
# Adicione mais mapeamentos box-drawing, ANSI, etc.
dict set box_map "㎜" "mm"
```

### 10.3 Novos Event Types

Editar `gateway/dakota_gateway/schema.py`:

```python
# Adicione novo type em schema
# Update canonical format em audit_writer.py
```

### 10.4 Importar Logs em Dashboard

Dashboard consome JSON-lines do replay2:

```bash
expect bin/main.exp ... --log-format json | 
    python3 dashboard/server.py --events-file /dev/stdin
```

---

## 11. Conhecidos Trade-offs & Limitações

| Limitação | Razão | Alternativa |
|-----------|-------|-----------|
| Expect/Tcl (não Python/Go) | Portabilidade AIX | Reimplementar em C |
| Assinatura baseada em texto | Frágil a layouts radicais | ML-based screen matching |
| Checkpoints periódicos | Pode perder estado no meio | Checkpoint após cada interação |
| SSH para proxy | Requer OpenSSH | Implementar protocol próprio |
| No replay "interativo" | Replay é determinístico | Adicionar pause/step |
| SQLite (not PostgreSQL) | Simplicidade deploy | Suportar PostgreSQL |

---

## 12. Roadmap Implícito

Com base na estrutura, features em desenvolvimento provável:

- [ ] TUI de debug (lib/control.tcl já existe parcialmente)
- [ ] Suporte a múltiplos encodings dinamicamente (now fixed)
- [ ] Dashboard mais rico (charts, analytics)
- [ ] Integração com sistemas de eventos (syslog, e-mail)
- [ ] Backup automático de logs
- [ ] Clustering (múltiplos gateways)

---

## 13. Como Começar a Usar

### 13.1 Quick Start (Demo Local)

```bash
cd /home/jmachado/projetos/dakota/replay2

# Executar com simulador
expect examples/demo.exp

# Você verá:
# - Tela de login simulada
# - Máquina de estados despacha handler
# - Menu exibido
# - Automação acontecendo
```

### 13.2 Operacional (Production)

```bash
# 1. Build tarball
./scripts/build-tarball.sh

# 2. Instalar no servidor
sudo ./install.sh --prefix /opt/dakota-replay2

# 3. Gerar chaves
mkdir -p /etc/dakota-gateway
head -c 32 /dev/urandom > /etc/dakota-gateway/hmac.key
chmod 600 /etc/dakota-gateway/hmac.key

# 4. Conf ForcedCommand no SSHD
# sshd_config:
# 
# Match Group legacy-users
#   ForceCommand python3 -m dakota_gateway.cli start \
#     --log-dir /var/log/dakota-gateway \
#     --hmac-key-file /etc/dakota-gateway/hmac.key \
#     --source-host legacy-server \
#     --source-user legacyuser

# 5. Restart SSHD
sudo systemctl restart sshd

# 6. Usuário conecta, é capturado e auditado
ssh legacyuser@gateway-host

# 7. Depois, replay determinístico
python3 -m dakota_gateway.cli replay \
  --log-dir /var/log/dakota-gateway \
  --hmac-key-file /etc/dakota-gateway/hmac.key \
  --target-host legacy-backup \
  --target-user legacyuser
```

---

## 14. Conclusão

**Dakota Replay2** é uma solução sofisticada e production-ready para automação+auditoria de sistemas legados. Seus pontos fortes são:

✓ **Robustez:** Expect/Tcl testado, portável Linux+AIX  
✓ **Integridade:** Hash-chain + HMAC, verificável  
✓ **Auditoria:** Logs append-only, determinísticos  
✓ **Extensível:** Handlers isolados em plugins  
✓ **Produção:** ForcedCommand SSH, distribuição tarball  

A arquitetura modular permite crescimento incremental: do core automation à auditoria, replay e control plane web.

---

**Análise realizada em:** 27 de março de 2026  
**Versão:** 0.1.0  
**Autor da análise:** GitHub Copilot (Claude Haiku 4.5)

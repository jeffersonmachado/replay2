# Análise Profunda: Camada Web de Gerenciamento (Dakota Replay2)

## 1. Visão Geral da Arquitetura Web

### 1.1 Componentes Principais

```
┌─────────────────────────────────────────────────────────────────┐
│           CAMADA WEB - DAKOTA REPLAY2                            │
├─────────────────────────────────────────────────────────────────┤
│                                                                   │
│  ┌──────────────────┐              ┌──────────────────┐          │
│  │  Control Server  │              │  Dashboard (opt) │          │
│  │ (Port 8090)      │              │  (Port 8080)     │          │
│  │                  │              │                  │          │
│  │ ✓ Run management │              │ ✓ Event viewer   │          │
│  │ ✓ Scheduling     │              │ ✓ Real-time logs │          │
│  │ ✓ RBAC + Auth    │              │ ✓ Filtering      │          │
│  │ ✓ User mgmt      │              │ ✓ No auth req    │          │
│  │ ✓ Metrics view   │              │                  │          │
│  └────────┬─────────┘              └────────┬─────────┘          │
│           │                                 │                     │
│           ├─ HTTP/1.1                       ├─ HTTP/1.1          │
│           │  ThreadingHTTPServer            │  ThreadingHTTPServer│
│           │                                 │                     │
│           ├─ SQLite (replay.db)             ├─ JSONL (real-time) │
│           │  (Persistent state)             │  (Event tailing)   │
│           │                                 │                     │
│           └─────────────────────────────────┘                     │
│                         │                                         │
│                    ┌────▼─────────────┐                          │
│                    │  Shared Services  │                          │
│                    │                   │                          │
│                    │ ✓ auth.py         │                          │
│                    │ ✓ state_db.py     │                          │
│                    │ ✓ replay_control.py                         │
│                    │                   │                          │
│                    └───────────────────┘                          │
│                                                                   │
└─────────────────────────────────────────────────────────────────┘
```

### 1.2 Stack Tecnológico

| Camada | Tecnologia | Por quê |
|--------|-----------|---------|
| **HTTP Server** | Python `http.server.ThreadingHTTPServer` | Stdlib puro, sem deps |
| **Frontend** | HTML5 + Vanilla JavaScript | Sem framework, portável |
| **Authentication** | PBKDF2-SHA256 + Cookie assinado | PKCS2 standard, seguro |
| **Session Management** | SQLite + token_hash | Verificável, sem XSS |
| **Database** | SQLite com WAL | Portável, ACID |
| **Real-time Events** | JSONL tailing (dashboard) | Simples, append-only |

---

## 2. Control Server (gateway/control/server.py)

### 2.1 Propósito

**Gerenciamento centralizado de replay sessions com interface web, RBAC e estado persistente.**

```
Caso de Uso:
┌─ Admin acessa http://localhost:8090
│  └─ Cria novo "replay run"
│     (log_dir, target_host, target_user, mode, params)
│
├─ Sistema inicia replay em thread separada
│  └─ Proxies SSH, valida checkpoints, registra progresso
│
├─ Usuário monitora progresso em real-time
│  └─ Dashboard atualiza a cada 1s via /api/runs
│
└─ Operator pode pausar/resumar/cancelar a qualquer momento
```

### 2.2 Endpoints da API

#### 2.2.1 Públicos (sem autenticação)

| Endpoint | Método | Descrição |
|----------|--------|-----------|
| `/login` | GET | Página de login (HTML) |
| `/api/login` | POST | Autenticar (`{username, password}`) |
| `/api/logout` | POST | Limpar sessão (cookie) |

**Fluxo de Login:**

```
1. User POST /api/login {username, password}
   ├─ Valida credenciais contra pbkdf2_hash no BD
   ├─ Cria nova "session" (token_hash + exp)
   └─ Retorna cookie assinado com HMAC

2. Cookie incluso em todas as próximas requisições
   ├─ Verificado via _auth() em cada handler
   └─ Renova se necessário
```

#### 2.2.2 Autenticados (requer login)

**GET /api/me**
- Retorna sessão atual: `{id, username, role}`
- Usado para preencher UI ("user=john role=admin")

**GET /api/runs?limit=200**
- Lista runs (recentes primeiro)
- Retorna: `{runs: [{id, status, log_dir, target_host, created_at_ms, params_json, metrics_json}, ...]}`

**GET /api/runs/<id>/events**
- Lista eventos de uma run (últimos 200)
- Retorna: `{events: [{ts_ms, kind, message, data_json}, ...]}`

#### 2.2.3 Autenticados + RBAC: admin, operator

**POST /api/runs**
- Criar novo run
- Payload:
  ```json
  {
    "log_dir": "/var/log/dakota-gateway",
    "target_host": "legacy-backup.example.com",
    "target_user": "legacyuser",
    "target_command": "",  // optional
    "mode": "strict-global",
    "params": {
      "concurrency": 20,
      "ramp_up_per_sec": 2,
      "speed": 4,
      "jitter_ms": 500,
      "target_user_pool": ["replay01", "replay02"],
      "on_checkpoint_mismatch": "continue"
    }
  }
  ```
- Retorna: `{id: 42}`

**POST /api/runs/<id>/start**
- Inicia replay (muda status: queued → running)
- Spawna thread `Runner.start_run_async(run_id)`

**POST /api/runs/<id>/pause**
- Pausa execução (running → paused)

**POST /api/runs/<id>/resume**
- Retoma execução (paused → running)

**POST /api/runs/<id>/cancel**
- Cancela replay (qualquer status → cancelled)

**POST /api/runs/<id>/retry**
- Cria nova run a partir da anterior (copia config, status=queued)

#### 2.2.4 Autenticados + RBAC: admin

**GET /api/users**
- Lista todos usuários
- Retorna: `{users: [{id, username, role, created_at_ms}, ...]}`

**POST /api/users**
- Criar novo usuário
- Payload:
  ```json
  {
    "username": "operator1",
    "password": "senha123",
    "role": "operator"  // admin|operator|viewer
  }
  ```

### 2.3 Interface de Usuário (INDEX_HTML)

#### 2.3.1 Layout

```html
┌─────────────────────────────────────────────────────────┐
│ Replay Control          [user=john role=admin] [Logout] │
├─────────────────────────────────────────────────────────┤
│                                                           │
│ Criar Run:                                               │
│ ┌─────────────┬─────────────┬──────────┬──────────────┐ │
│ │ log_dir     │ target_host │ user     │ mode selector│ │
│ │ /var/log... │ legacy.com  │ legacyu  │ ▼ strict-... │ │
│ └─────────────┴─────────────┴──────────┴──────────────┘ │
│ ┌──────────────┬──────────────┬────────────┬──────────┐  │
│ │ concurrency  │ ramp-up      │ speed      │ jitter   │  │
│ │ 20           │ 2/s          │ 4          │ 500ms    │  │
│ └──────────────┴──────────────┴────────────┴──────────┘  │
│ ┌────────────────────────┬──────────────────────────┐    │
│ │ user pool (CSV)        │ on_mismatch: continue ▼ │    │
│ │ replay01,replay02,...   │                          │    │
│ └────────────────────────┴──────────────────────────┘    │
│ [Criar run]                                    runs:42   │
│                                                           │
├─────────────────────────────────────────────────────────┤
│                                                           │
│ Todas as Runs:                                           │
│ ┌───┬──────────┬────────────┬──────────────┬──────────┐ │
│ │id │ status   │ created_at │ params       │ ações   │ │
│ ├───┼──────────┼────────────┼──────────────┼─────────┤ │
│ │42 │ running  │ 10:30 am   │ concurrency=20│start...│ │
│ │   │          │            │ ramp=2/s     │pause..│ │
│ │   │          │            │ speed=4      │resume.│ │
│ │41 │ success  │ 10:00 am   │ (...)        │retry  │ │
│ │40 │ failed   │ 09:45 am   │ (...)        │retry  │ │
│ └───┴──────────┴────────────┴──────────────┴──────────┘ │
│                                                           │
├─────────────────────────────────────────────────────────┤
│                                                           │
│ Detalhe:                                                 │
│ Run ID: [42___] [Carregar]                              │
│ ┌──────────────────────────────────────────────────────┐ │
│ │ {                                                    │ │
│ │   "id": 42,                                          │ │
│ │   "status": "running",                               │ │
│ │   "log_dir": "/var/log/dakota-gateway",              │ │
│ │   "target_host": "legacy-backup.com",                │ │
│ │   "created_at_ms": 1711354200000,                     │ │
│ │   "started_at_ms": 1711354205000,                     │ │
│ │   "params_json": {...},                              │ │
│ │   "metrics_json": {"progress": 5000, ...}            │ │
│ │ }                                                    │ │
│ └──────────────────────────────────────────────────────┘ │
│                                                           │
│ Events (últimos 200):                                    │
│ [...json array...]                                       │
│                                                           │
└─────────────────────────────────────────────────────────┘
```

#### 2.3.2 JavaScript Dinâmico

**Reload automático:**
```javascript
loadMe();           // Carrega usuário atual
loadRuns();         // Carrega lista de runs
setInterval(loadRuns, 1000);  // Atualiza a cada 1s
```

**Ações de run:**
```javascript
async startRun(id) { await api(`/api/runs/${id}/start`, {method:'POST'}); await loadRuns(); }
async pauseRun(id) { await api(`/api/runs/${id}/pause`, {method:'POST'}); await loadRuns(); }
async resumeRun(id) { await api(`/api/runs/${id}/resume`, {method:'POST'}); await loadRuns(); }
async cancelRun(id) { await api(`/api/runs/${id}/cancel`, {method:'POST'}); await loadRuns(); }
async retryRun(id) { await api(`/api/runs/${id}/retry`, {method:'POST'}); await loadRuns(); }
```

**Parsing de campos JSON (params_json, metrics_json):**
```javascript
if (run.params_json) {
    const pj = JSON.parse(run.params_json);
    if (pj.concurrency) extra += `<br/>concurrency=${pj.concurrency}`;
    if (pj.ramp_up_per_sec) extra += `<br/>ramp=${pj.ramp_up_per_sec}/s`;
}
if (run.metrics_json) {
    const mj = JSON.parse(run.metrics_json);
    extra += `<br/>progress=${mj.last_seq_global_applied||0}`;
}
```

### 2.4 Banco de Dados (state_db.py)

#### 2.4.1 Schema

**Users Table:**
```sql
CREATE TABLE users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  username TEXT NOT NULL UNIQUE,
  password_hash TEXT,  -- pbkdf2_sha256$iters$salt_b64$dk_b64
  role TEXT NOT NULL CHECK(role IN ('admin','operator','viewer')),
  created_at_ms INTEGER NOT NULL
);
```

**Sessions Table:**
```sql
CREATE TABLE sessions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  token_hash TEXT NOT NULL UNIQUE,  -- sha256_hex(token)
  created_at_ms INTEGER NOT NULL,
  expires_at_ms INTEGER NOT NULL
);
```

**Replay Runs Table:**
```sql
CREATE TABLE replay_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at_ms INTEGER NOT NULL,
  created_by INTEGER NOT NULL REFERENCES users(id),
  
  -- Configuração
  log_dir TEXT NOT NULL,
  target_host TEXT NOT NULL,
  target_user TEXT NOT NULL,
  target_command TEXT NOT NULL,
  mode TEXT NOT NULL CHECK(mode IN ('strict-global','parallel-sessions')),
  
  -- Parâmetros dinâmicos
  params_json TEXT,        -- {concurrency, ramp_up_per_sec, speed, ...}
  metrics_json TEXT,       -- {progress, sessions_ok, sessions_failed, ...}
  
  -- Verificação de integridade
  run_fingerprint TEXT NOT NULL,  -- hash(log_dir, target_host, user, cmd, mode)
  verify_ok INTEGER,              -- 1 = integridade OK
  verify_error TEXT,              -- mensagem de erro se verificação falhou
  
  -- Status
  status TEXT NOT NULL CHECK(status IN ('queued','running','paused','failed','success','cancelled')),
  started_at_ms INTEGER,
  finished_at_ms INTEGER,
  
  -- Progresso
  last_seq_global_applied INTEGER NOT NULL DEFAULT 0,
  last_checkpoint_sig TEXT,
  
  -- Retry
  parent_run_id INTEGER REFERENCES replay_runs(id),
  error TEXT
);

CREATE UNIQUE INDEX replay_runs_fingerprint_unique
ON replay_runs(run_fingerprint) WHERE status IN ('queued','running','paused');
```

**Run Events Table:**
```sql
CREATE TABLE replay_run_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id INTEGER NOT NULL REFERENCES replay_runs(id) ON DELETE CASCADE,
  ts_ms INTEGER NOT NULL,
  kind TEXT NOT NULL,        -- "api", "verify", "replay_start", "checkpoint", "error"
  message TEXT NOT NULL,
  data_json TEXT             -- {event_type, session_id, ...}
);

CREATE INDEX replay_run_events_run_ts
ON replay_run_events(run_id, ts_ms);
```

#### 2.4.2 Inicialização do Banco

```python
def init_db(con: sqlite3.Connection) -> None:
    con.executescript(SCHEMA_SQL)  # Cria tabelas
    
    # Migração backward-compatible (novos campos)
    cols = {row["name"] for row in con.execute("PRAGMA table_info(replay_runs)").fetchall()}
    if "params_json" not in cols:
        con.execute("ALTER TABLE replay_runs ADD COLUMN params_json TEXT")
    if "metrics_json" not in cols:
        con.execute("ALTER TABLE replay_runs ADD COLUMN metrics_json TEXT")
```

**Otimizações:**
- `PRAGMA journal_mode=WAL` → Write-Ahead Logging (melhor concorrência)
- `PRAGMA foreign_keys=ON` → Integridade referencial
- `isolation_level=None` → Autocommit
- `timeout=30s` → Espera lock por até 30s

---

## 3. Autenticação & Segurança (auth.py)

### 3.1 Password Hashing

**Algoritmo: PBKDF2-SHA256**

```python
def pbkdf2_hash_password(password: str, salt_b64: str | None = None) -> str:
    """Retorna: pbkdf2_sha256$<iters>$<salt_b64>$<dk_b64>"""
    iters = 200_000  # NIST recomenda ≥ 100k
    if salt_b64 is None:
        salt = os.urandom(16)  # Novo salt (aleatorio)
        salt_b64 = base64.b64encode(salt).decode("ascii")
    # Deriva chave com PBKDF2
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iters, dklen=32)
    dk_b64 = base64.b64encode(dk).decode("ascii")
    return f"pbkdf2_sha256${iters}${salt_b64}${dk_b64}"
```

**Verificação (timing-safe):**

```python
def verify_password(password: str, stored: str) -> bool:
    algo, iters_s, salt_b64, dk_b64 = stored.split("$", 3)
    if algo != "pbkdf2_sha256":
        return False
    iters = int(iters_s)
    salt = base64.b64decode(salt_b64)
    want = base64.b64decode(dk_b64)
    got = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iters, dklen=len(want))
    return hmac.compare_digest(got, want)  # ← Timing-safe comparison
```

**Exemplo de Hash Armazenado:**

```
pbkdf2_sha256$200000$lFQwGWAHw7qTy5sJHJWJpQ==$DgB+kWR0P8W1KWGvHBBvf/wK2YWGWvHBBvf/wK2YWg==
```

### 3.2 Cookie Assinado

**Geração (sign_cookie):**

```python
def sign_cookie(secret: bytes, username: str, token: str, expires_at_ms: int) -> str:
    # Payload: username|token|exp_ms
    payload = f"{username}|{token}|{expires_at_ms}".encode("utf-8")
    
    # HMAC-SHA256 para assinatura
    sig = hmac.new(secret, payload, hashlib.sha256).hexdigest()
    
    # Raw: username|token|exp_ms|sig
    raw = f"{username}|{token}|{expires_at_ms}|{sig}".encode("utf-8")
    
    # Encode base64url
    return base64.urlsafe_b64encode(raw).decode("ascii")
```

**Verificação (verify_cookie):**

```python
def verify_cookie(secret: bytes, cookie_val: str) -> tuple[str, str, int] | None:
    raw = base64.urlsafe_b64decode(cookie_val)
    username, token, exp_s, sig = raw.decode("utf-8").split("|")
    exp = int(exp_s)
    
    # Re-computa HMAC esperado
    payload = f"{username}|{token}|{exp}".encode("utf-8")
    want = hmac.new(secret, payload, hashlib.sha256).hexdigest()
    
    # Valida assinatura (timing-safe)
    if not hmac.compare_digest(want, sig):
        return None
    
    # Valida expiração
    if int(time.time() * 1000) > exp:
        return None
    
    return username, token, exp
```

### 3.3 Session Flow

```
┌─ User POST /api/login {user, pass}
│
├─ Server valida password vs pbkdf2_hash
│  └─ Se OK:
│
├─ Server gera novo token (secrets.token_urlsafe(32)) + hash dele
│
├─ Server insere em DB:
│  INSERT INTO sessions(user_id, token_hash, exp) VALUES(...)
│
├─ Server assina cookie:
│  sign_cookie(cookie_secret, username, token, exp)
│
├─ Server SET-COOKIE com valor assinado
│  └─ HTTP-Only idealmente (mas controle é interno)
│
└─ Client armazena cookie, envia em próximas requisições
   Cada requisição:
   ├─ Extract cookie
   ├─ verify_cookie_signature
   ├─ Lookup token_hash no DB
   ├─ Valida exp vs now
   └─ Continua se tudo OK
```

### 3.4 Ameaças Mitigadas

| Ameaça | Mitigação |
|--------|-----------|
| **Brute-force password** | 200k iterações PBKDF2 + salt aleatório |
| **Cookie tampering** | HMAC-SHA256 assinado |
| **Session fixation** | Token novo por login |
| **Timing attack** | `hmac.compare_digest()` |
| **XSS (cookie theft)** | HTTP-Only (quando em HTTPS) |
| **CSRF** | Cookie same-site + user confirmation |

---

## 4. Dashboard Opcional (dashboard/server.py)

### 4.1 Propósito

**Visualização simples, em tempo real, de eventos da engine de automação (sem RBAC).**

```
Caso de Uso:
┌─ Engine roda com --log-format json --log-stream stdout
│
├─ Piped para arquivo ou dashboard-server
│
└─ Dashboard consome JSONL em tempo real via tail
   └─ Usuários veem eventos de captura, normalização, despacho, etc.
```

### 4.2 Endpoints

**GET /login** - Sem login! (opcional)

**GET /** - Página principal (HTML)

**GET /api/events?limit=200&type=unknown_screen**
- Retorna: `{source, events: [...], buffer_size}`
- Filtrável por type
- Atualiza a cada 1s (JavaScript `setInterval(load, 1000)`)

### 4.3 JSONL Tailing

**Algoritmo:**

```python
def tail_jsonl(path: str, buf: EventBuffer, stop_evt: threading.Event):
    last_inode = None
    f = None
    while not stop_evt.is_set():
        try:
            st = os.stat(path)
            inode = (st.st_ino, st.st_dev)
            if inode != last_inode:  # Arquivo rotacionado?
                if f:
                    f.close()
                f = open(path, "r", encoding="utf-8", errors="replace")
                f.seek(0, os.SEEK_END)  # Pula para o final
                last_inode = inode
        except FileNotFoundError:
            time.sleep(0.25)
            continue

        line = f.readline()
        if not line:
            time.sleep(0.1)
            continue
        
        line = line.strip()
        if not line:
            continue
        
        try:
            ev = json.loads(line)
            if isinstance(ev, dict):
                buf.add(ev)
        except Exception:
            pass  # Ignora JSON inválido
```

**EventBuffer (thread-safe):**

```python
class EventBuffer:
    def __init__(self, max_events: int = 5000):
        self.max_events = max_events
        self._lock = threading.Lock()
        self._events = []

    def add(self, ev: dict):
        with self._lock:
            self._events.append(ev)
            # Mantém última N eventos (circular buffer)
            if len(self._events) > self.max_events:
                self._events = self._events[-self.max_events:]

    def snapshot(self):
        with self._lock:
            return list(self._events)
```

### 4.4 Interface de Usuário

```html
┌──────────────────────────────────────────┐
│ dakota-replay2 dashboard                  │
│ fonte: /tmp/replay2.events.jsonl         │
├──────────────────────────────────────────┤
│                                           │
│ Filtro type: [unknown_screen_______]    │
│ Limite: [200___] [Atualizar]             │
│         eventos: 180 (buffer=5000)       │
│                                           │
├─────────────────────────────────────────┤
│                                          │
│ ts_ms            │ level      │ type    │ dados │
├──────────────────┼────────────┼─────────┼────────┤
│ 1711354205000    │ info       │ capture │ <ver>  │
│ 1711354205050    │ info       │ normalize│<ver>  │
│ 1711354205100    │ info       │ signature│<ver>  │
│ 1711354205150    │ warning    │unknown  │<ver>  │
│ 1711354205200    │ info       │dispatch │<ver>  │
│                                          │
└──────────────────────────────────────────┘
```

---

## 5. Fluxos de Operação

### 5.1 Fluxo: Criar e Executar um Replay

```
1. Admin acessa http://localhost:8090
   └─ Se não autenticado → /login
   └─ POST /api/login (username, password)
   └─ Verifica pbkdf2 no BD
   └─ Retorna cookie assinado
   └─ Redireciona para /

2. Admin vê formulário de "Criar Run"
   ├─ log_dir: /var/log/dakota-gateway
   ├─ target_host: legacy-backup.com
   ├─ target_user: legacyuser
   ├─ mode: strict-global
   └─ params: {concurrency: 20, ramp_up_per_sec: 2, ...}

3. Admin clica [Criar run]
   ├─ POST /api/runs (JSON payload)
   ├─ Handler valida RBAC (role ∈ {admin, operator})
   ├─ Insere em replay_runs (status=queued)
   └─ Retorna {id: 42}

4. Admin vê run 42 na lista (status=queued)

5. Admin clica [start]
   ├─ POST /api/runs/42/start
   ├─ UPDATEs status: queued → running
   ├─ Chama self.server.runner.start_run_async(42)
   ├─ Runner thread inicia em background
   └─ Retorna 200 OK

6. Runner thread:
   ├─ Lê log_dir (auditlog JSONL)
   ├─ Verifica integridade (hash-chain + HMAC)
   ├─ Abre SSH para target_host@target_user
   ├─ Itera eventos (sorted by seq_global)
   ├─ Envia inputs, valida checkpoints
   ├─ Atualiza last_seq_global_applied (metrics_json)
   ├─ Trata mismatches (benchmark pausa/continua)
   └─ Finaliza (status → success|failed)

7. Frontend recarrega a cada 1s
   ├─ GET /api/runs?limit=200
   ├─ Vê status=running (depois success)
   ├─ Mostra progress: progress=5000 sess_ok=18 fail=2
   └─ Atualiza em tempo real
```

### 5.2 Fluxo: Inspect & Debug de uma Run

```
1. Admin preenche "Run ID: 42" em "Detalhe"
   └─ Clicks [Carregar]

2. Frontend JavaScript:
   ├─ GET /api/runs?limit=200
   ├─ Encontra run com id=42
   ├─ JSON.stringify e exibe detalhes completos
   │  (id, status, log_dir, target_host, params_json, metrics_json, etc.)
   │
   ├─ GET /api/runs/42/events
   ├─ JSON.stringify eventos (últimos 200)
   │  (ts_ms, kind, message, data_json)
   │
   └─ Usuário inspeciona no navegador

3. Exemplos de eventos:
   ├─ kind: "api", message: "start solicitado", data: {by: "admin"}
   ├─ kind: "verify", message: "log verificação OK", data: {events: 1500, ...}
   ├─ kind: "replay_start", message: "SSH conectado", data: {session_id: "xyz"}
   ├─ kind: "checkpoint", message: "mismatch!", data: {expected: "L=8;...", actual: "L=8;..."}
   └─ kind: "error", message: "SSH timeout", data: {target: "legacy.com"}
```

### 5.3 Fluxo: Pausar/Retomar/Cancelar

```
Pausar:
  POST /api/runs/42/pause
  └─ pause_run(con, run_id)
  └─ UPDATE status: running → paused
  └─ Runner thread honra sinalizador e pausa

Retomar:
  POST /api/runs/42/resume
  ├─ resume_run(con, run_id)
  └─ UPDATE status: paused → running
  └─ start_run_async(42) novamente

Cancelar:
  POST /api/runs/42/cancel
  └─ cancel_run(con, run_id)
  ├─ UPDATE status → cancelled
  └─ Runner thread detecta e para gracefully

Retry:
  POST /api/runs/42/retry
  ├─ retry_run(con, run_id, created_by=user_id)
  ├─ INSERT INTO replay_runs (parent_run_id=42, status=queued, ...)
  └─ Retorna {id: 43} (nova run com mesma config)
```

---

## 6. Arquitetura de Threads

### 6.1 ThreadingHTTPServer

```python
class ControlServer(ThreadingHTTPServer):
    def __init__(self, addr, handler, *, db_path: str, cookie_secret: bytes, hmac_key: bytes):
        super().__init__(addr, handler)  # ← Cria thread pool
        self.db_path = db_path
        self.cookie_secret = cookie_secret
        self.hmac_key = hmac_key
        self.runner = Runner(db_path, hmac_key)
```

**Comportamento:**

```
HTTP Request 1 → Handler thread 1
  ├─ POST /api/login
  └─ Valida BD, retorna cookie

HTTP Request 2 → Handler thread 2 (simultâneo)
  ├─ GET /api/runs
  └─ Lista runs

POST /api/runs/42/start → Handler thread 3
  └─ self.server.runner.start_run_async(42)
  └─ Spawna nova thread de background: Runner.run()

Runner thread (background, separado)
  ├─ Executa replay
  ├─ UPDATEs progresso no BD
  └─ HTTP handlers continuam respondendo
```

### 6.2 Runner Thread

**Pseudocódigo:**

```python
class Runner:
    def start_run_async(self, run_id: int):
        t = threading.Thread(target=self.run, args=(run_id,), daemon=False)
        t.start()

    def run(self, run_id: int):
        # Thread-safe: cada runner tem sua conexão BD
        try:
            con = connect(self.db_path)
            run = get_run(con, run_id)
            
            # Verifica log
            verify_ok = verify_log(run.log_dir, self.hmac_key)
            if not verify_ok:
                update_run_status(con, run_id, 'failed', 'log verification failed')
                return
            
            # Abre SSH e reproduz
            for ev in iter_events(run.log_dir):
                if ev['type'] == 'bytes' and ev['dir'] == 'in':
                    send_to_ssh(ev['data_b64'])
                elif ev['type'] == 'checkpoint':
                    if not validate_checkpoint(ev['sig']):
                        if run.on_checkpoint_mismatch == 'fail-fast':
                            raise CheckpointMismatch()
                
                # Atualiza progresso
                update_run_progress(con, run_id, ev['seq_global'])
            
            # Finaliza
            update_run_status(con, run_id, 'success')
        except Exception as e:
            update_run_status(con, run_id, 'failed', str(e))
        finally:
            con.close()
```

---

## 7. RBAC (Role-Based Access Control)

### 7.1 Roles

| Role | Permissões |
|------|-----------|
| **admin** | Criar/deletar usuários, gerenciar runs, ver tudo |
| **operator** | Criar/gerir runs próprias, pausar/resumar/cancelar, ver histórico |
| **viewer** | Ver runs, ver eventos, ver detalhe (sem criar/modificar) |

### 7.2 Enforcement

```python
def _require(self, roles: set[str] | None = None):
    u = self._auth()
    if not u:
        self.send_response(HTTPStatus.UNAUTHORIZED)
        self.end_headers()
        return None
    if roles and u["role"] not in roles:
        self.send_response(HTTPStatus.FORBIDDEN)
        self.end_headers()
        return None
    return u
```

**Exemplo de uso:**

```python
def do_POST(self):
    if p.path == "/api/users":
        u = self._require(roles={"admin"})  # ← Apenas admin
        if not u:
            return
        # ... criar usuário ...
    
    if p.path == "/api/runs":
        u = self._require(roles={"admin", "operator"})  # ← Admin ou operator
        if not u:
            return
        # ... criar run ...
```

---

## 8. Deployment & Configuração

### 8.1 Iniciar Control Server

```bash
mkdir -p /etc/dakota-gateway

# Gerar secrets
head -c 32 /dev/urandom > /etc/dakota-gateway/hmac.key
head -c 32 /dev/urandom > /etc/dakota-gateway/cookie_secret.key

chmod 600 /etc/dakota-gateway/*.key

# Bootstrap admin
python3 gateway/control/server.py \
  --listen 127.0.0.1:8090 \
  --db /var/lib/dakota-gateway/replay.db \
  --cookie-secret-file /etc/dakota-gateway/cookie_secret.key \
  --hmac-key-file /etc/dakota-gateway/hmac.key \
  --bootstrap-admin admin:admin123

# Output:
# Admin criado: admin
# listening on http://127.0.0.1:8090
```

### 8.2 Systemd Service

```ini
[Unit]
Description=Dakota Replay2 Control Server
After=network.target

[Service]
Type=simple
User=dakota
WorkingDirectory=/opt/dakota-replay2
ExecStart=/usr/bin/python3 -m dakota_gateway.control.server \
  --listen 127.0.0.1:8090 \
  --db /var/lib/dakota-gateway/replay.db \
  --cookie-secret-file /etc/dakota-gateway/cookie_secret.key \
  --hmac-key-file /etc/dakota-gateway/hmac.key
Restart=always
RestartSec=5s

[Install]
WantedBy=multi-user.target
```

### 8.3 Nginx Reverse Proxy (HTTPS)

```nginx
server {
    listen 443 ssl http2;
    server_name replay-control.example.com;
    
    ssl_certificate /etc/letsencrypt/live/replay-control.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/replay-control.example.com/privkey.pem;
    
    location / {
        proxy_pass http://127.0.0.1:8090;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
    }
}
```

---

## 9. Segurança & Hardening

### 9.1 Checklist

- [ ] Cookie Secret: 32 bytes random
- [ ] HMAC Key: 32 bytes random, separado do cookie_secret
- [ ] DB file: permissões 600 (apenas root/dakota user)
- [ ] Log directory: permissões 750, append-only se possível
- [ ] HTTPS via nginx reverse proxy
- [ ] HTTP-Only cookies (automático com HTTPS proxy)
- [ ] CSRF token para actions destrutivas
- [ ] Audit logging de logins/ações sensíveis
- [ ] Rate limiting no /api/login
- [ ] Alertar sobre tentativas de brute-force

### 9.2 Limites de Concorrência

```python
# ThreadingHTTPServer por padrão não limita threads
# Solução: usar ThreadPoolExecutor wrapper

import concurrent.futures

class BoundedThreadingHTTPServer(ThreadingHTTPServer):
    def __init__(self, *args, max_workers=50, **kwargs):
        super().__init__(*args, **kwargs)
        self.executor = concurrent.futures.ThreadPoolExecutor(max_workers=max_workers)

    def process_request(self, request, client_address):
        self.executor.submit(self.process_request_thread, request, client_address)
```

---

## 10. Monitoramento & Observabilidade

### 10.1 Métricas Chave

| Métrica | Fonte | Frequência |
|---------|-------|-----------|
| **Runs ativas** | `SELECT COUNT(*) FROM runs WHERE status='running'` | 1s |
| **Taxa de sucesso** | `COUNT(success) / COUNT(all)` | 1m |
| **Progresso** | `SUM(last_seq_global_applied)` | 1s |
| **Tempo médio** | `AVG(finished_at - started_at)` | 1h |
| **Login failures** | Contar 401s num período | 1m |

### 10.2 Health Check

```
GET /_health/ready
  └─ Verifica BD conexão
  └─ Retorna 200 se OK, 503 se falha

GET /_health/live
  └─ Ping simples
  └─ Sempre 200 OK
```

---

## 11. Comparação: Control Server vs Dashboard

| Aspecto | Control Server | Dashboard |
|---------|----------------|-----------|
| **Propósito** | Gerenciar replays | Visualizar eventos |
| **Autenticação** | RBAC + login obrigatório | Sem login (opcional) |
| **Interface** | Criar runs, ver progresso | Visualizar logs em tempo real |
| **Dados** | Runs + metadata (BD) | Eventos (JSONL) |
| **Real-time** | Atualiza 1s (polling) | Atualiza 1s (tailing + polling) |
| **Uso** | Operações + scheduling | Troubleshooting |
| **Porta** | 8090 | 8080 |

---

## 12. Exemplos de Uso

### 12.1 CLI: Criar Run via API

```bash
curl -X POST http://localhost:8090/api/login \
  -H "Content-Type: application/json" \
  -d '{"username":"operator1","password":"pass123"}' \
  -c cookies.txt

# Pega cookie de cookies.txt

curl -X POST http://localhost:8090/api/runs \
  -H "Content-Type: application/json" \
  -b cookies.txt \
  -d '{
    "log_dir": "/var/log/dakota-gateway",
    "target_host": "legacy-backup.com",
    "target_user": "legacyuser",
    "mode": "strict-global",
    "params": {"concurrency": 50}
  }' | jq .
```

### 12.2 Dashboard: Monitorar Eventos

```bash
# Terminal 1: engine automação
expect bin/main.exp --log-format json --log-stream stdout > replay2.jsonl &

# Terminal 2: dashboard
python3 dashboard/server.py \
  --events-file ./replay2.jsonl \
  --listen 127.0.0.1:8080

# Browser: http://localhost:8080
```

---

## 13. Roadmap Futuro da Camada Web

- [ ] **Webhooks** para notificações (Slack, email)
- [ ] **Charts** D3.js ou Chart.js (progresso, taxa de sucesso)
- [ ] **API Key authentication** (para integração CI/CD)
- [ ] **Audit log** de logins e operações sensíveis
- [ ] **2FA** (TOTP)
- [ ] **OpenID Connect** integrado
- [ ] **PostgreSQL** como backend (em vez de só SQLite)
- [ ] **gRPC** para performance alta
- [ ] **WebSocket** para real-time updates (em vez de polling)

---

**Análise realizada em:** 27 de março de 2026  
**Versão:** 0.1.0  
**Foco:** Control Plane Web + Dashboard

# Execução de Replay — Fluxo Completo e Gargalos

## Fluxo Completo

```
┌─────────────────────────────────────────────────────────────────────┐
│                     REPLAY EXECUTION PIPELINE                        │
│                                                                      │
│  FASE 1: CAPTURA (Ambiente Origem — Recital 8)                      │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │ Usuário → Gateway SSH → AuditWriter                          │   │
│  │                                                                │   │
│  │ Eventos capturados (AuditEvent):                                  │   │
│  │  type=session_start (actor, ts_ms, session_id, logname, uid, gid)│   │
│  │  type=bytes (dir=in/out, data_b64, n bytes)                       │   │
│  │  type=checkpoint (screen_sig, norm_sha256, screen_sample,        │   │
│  │       key_b64, key_text, key_kind, input_len, screen_source)     │   │
│  │  type=session_end (ts_ms)                                         │   │
│  │                                                                │   │
│  │ Garantias:                                                     │   │
│  │  ✓ seq_global monotonic (lock-based)                          │   │
│  │  ✓ hash-chain (SHA-256)                                       │   │
│  │  ✓ HMAC integrity                                             │   │
│  │  ✓ JSONL format                                               │   │
│  └──────────────────────────────────────────────────────────────┘   │
│                           │                                          │
│                           ▼                                          │
│  FASE 2: ARMAZENAMENTO                                              │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │ audit-YYYYMMDD-HHMMSS.partNNN.jsonl                           │   │
│  │ audit.state        (seq_global, prev_hash, current_log)       │   │
│  │ audit.lock         (fcntl-based, multi-process safe)          │   │
│  │                                                                │   │
│  │ Rotação: quando arquivo atinge rotate_bytes                    │   │
│  └──────────────────────────────────────────────────────────────┘   │
│                           │                                          │
│                           ▼                                          │
│  FASE 3: VERIFICAÇÃO (pré-replay)                                   │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │ Verifier.verify_log(log_dir)                                  │   │
│  │  └→ Valida hash-chain                                        │   │
│  │  └→ Valida HMAC                                              │   │
│  │  └→ Valida seq_global sem gaps                               │   │
│  │  └→ Rejeita se adulterado                                    │   │
│  └──────────────────────────────────────────────────────────────┘   │
│                           │                                          │
│                           ▼                                          │
│  FASE 4: REPLAY (Ambiente Destino — Recital 24)                     │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │ Replay Engine                                                 │   │
│  │                                                                │   │
│  │ Modos:                                                         │   │
│  │  strict-global      → ordem global total                      │   │
│  │  parallel-sessions  → ordem por sessão, concorrência          │   │
│  │                                                                │   │
│  │ Para cada evento:                                              │   │
│  │  1. Lê evento do log (seq_global order)                       │   │
│  │  2. Se bytes_in: envia input para target SSH                  │   │
│  │  3. Se checkpoint: aguarda tela estabilizar                   │   │
│  │  4. Compara assinatura (strict/contains/regex/fuzzy)          │   │
│  │  5. Se mismatch: registra falha                               │   │
│  │  6. Continua ou aborta (configurável)                         │   │
│  │                                                                │   │
│  │ Controle:                                                      │   │
│  │  concurrency     → sessões paralelas                          │   │
│  │  ramp_up_per_sec → aceleração progressiva                     │   │
│  │  speed           → fator de aceleração                        │   │
│  │  jitter_ms       → variação aleatória                         │   │
│  └──────────────────────────────────────────────────────────────┘   │
│                           │                                          │
│                           ▼                                          │
│  FASE 5: VALIDAÇÃO                                                  │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │ Para cada checkpoint:                                          │   │
│  │  expected_signature (do log de origem)                        │   │
│  │  vs                                                           │   │
│  │  observed_signature (do destino)                              │   │
│  │                                                                │   │
│  │ Matching modes:                                                │   │
│  │  strict   → igualdade exata                                   │   │
│  │  contains → expected contido em observed                      │   │
│  │  regex    → expected é regex                                  │   │
│  │  fuzzy    → SequenceMatcher ratio ≥ threshold                 │   │
│  │                                                                │   │
│  │ Falhas registradas em replay_failures:                        │   │
│  │  failure_type: functional, timeout, screen_divergence,         │   │
│  │      technical_error, navigation_error, concurrency_error,     │   │
│  │      checkpoint_mismatch, integrity_error, cancelled           │   │
│  │  (journey_verifier acrescenta: validation_error,               │   │
│  │   data_error, permission_error)                                │   │
│  │  severity: low, medium, high, critical                        │   │
│  │  expected_value, observed_value, message, evidence            │   │
│  └──────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────┘
```

## Estados de uma Run

```
  created → running → completed
                    → failed
                    → cancelled
           → paused → running
                    → cancelled
```

## Operações Disponíveis

| Operação | API | Descrição |
|----------|-----|-----------|
| Criar | POST /api/runs | Cria nova run |
| Iniciar | POST /api/runs/{id}/start | Inicia execução |
| Pausar | POST /api/runs/{id}/pause | Pausa execução |
| Retomar | POST /api/runs/{id}/resume | Retoma execução |
| Cancelar | POST /api/runs/{id}/cancel | Cancela execução |
| Repetir | POST /api/runs/{id}/repeat | Cria run filha |
| Status | GET /api/runs/{id} | Status e progresso |
| Falhas | GET /api/runs/{id}/failures | Falhas estruturadas |
| Relatório | GET /api/runs/{id}/report/export | Export md/json/csv |

## Gargalos Identificados

### 1. Single-Threaded Replay (Crítico)

**Problema:** O replay engine processa uma sessão por vez no modo `strict-global`.
**Impacto:** Baixo throughput para cenários com muitas sessões.
**Solução:** `parallel-sessions` já existe; otimizar dispatch.

### 2. Checkpoint por Assinatura (Alto)

**Problema:** Validação puramente por assinatura de tela; diferenças sutis passam.
**Impacto:** Falsos negativos (divergências não detectadas).
**Solução:** Adicionar validadores semânticos por fluxo de negócio.

### 3. Timeout Fixo (Alto)

**Problema:** `checkpoint_timeout_ms` é fixo por configuração.
**Impacto:** Timeout prematuro em telas lentas; espera desnecessária em telas rápidas.
**Solução:** Adaptive timeout baseado em baseline do ambiente de origem.

### 4. Encoding Assumido (Médio)

**Problema:** Encoding do terminal não é detectado dinamicamente.
**Impacto:** Caracteres acentuados podem gerar falsos positivos.
**Solução:** Detecção de encoding via LANG/terminfo.

### 5. Ausência de Retry Automático (Médio)

**Problema:** Falha de SSH não tem retry automático.
**Impacto:** Runs abortam por problemas transitórios de rede.
**Solução:** Retry com backoff exponencial.

### 6. I/O Bound (Médio)

**Problema:** Leitura sequencial de JSONL para cada evento.
**Impacto:** Latência acumulada em logs grandes.
**Solução:** Indexação por `seq_global`; pre-load em memória.

### 7. Single SQLite Writer (Baixo)

**Problema:** Múltiplas threads escrevendo no mesmo SQLite.
**Impacto:** Contenção de lock em concorrência alta.
**Solução:** WAL mode já ajuda; batch writes futuros.

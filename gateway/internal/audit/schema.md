# Audit event schema (v1)

Cada linha no arquivo `*.jsonl` é um JSON object (UTF-8) com estes campos.

## Campos comuns
- `v`: `"v1"`
- `seq_global`: inteiro monotônico global (sem gaps)
- `ts_ms`: epoch millis
- `type`: `"bytes"` | `"checkpoint"` | `"session_start"` | `"session_end"`
- `actor`: string (usuário autenticado no gateway)
- `session_id`: string (UUID-like)
- `seq_session`: inteiro monotônico por sessão (sem gaps)

## Evento bytes
- `dir`: `"in"` (user->legacy) ou `"out"` (legacy->user)
- `data_b64`: base64 do chunk de bytes
- `n`: tamanho em bytes do chunk

## Evento checkpoint
- `sig`: assinatura de tela (mesma lógica do replay2: normalize+signature)
- `norm_sha256`: sha256 hex do texto normalizado
- `norm_len`: tamanho do texto normalizado

## Integridade (hash-chain + HMAC)
- `prev_hash`: sha256 hex do evento anterior (string vazia no primeiro)
- `hash`: sha256 hex do hash do evento atual
- `hmac`: hmac-sha256 hex do mesmo payload do hash

### Hash input (determinístico)
O hash/hmac são calculados sobre uma string canônica:

```
v=<v>\n
seq_global=<seq_global>\n
ts_ms=<ts_ms>\n
type=<type>\n
actor=<actor>\n
session_id=<session_id>\n
seq_session=<seq_session>\n
dir=<dir>\n
n=<n>\n
data_b64=<data_b64>\n
sig=<sig>\n
norm_sha256=<norm_sha256>\n
norm_len=<norm_len>\n
prev_hash=<prev_hash>\n
```

Campos ausentes entram como vazio.

## Rotação + manifest
O writer pode rotacionar por tamanho para `audit-YYYYMMDD-HHMMSS.partNNN.jsonl`.
Para cada arquivo gerado, criar um `*.manifest.json` com:
- `path`, `bytes`, `seq_start`, `seq_end`, `first_hash`, `last_hash`, `file_sha256`


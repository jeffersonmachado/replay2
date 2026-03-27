# Operação (gateway)

## Diretórios e permissões recomendadas

- `hmac.key`: `/etc/dakota-gateway/hmac.key` com permissão `0600` (root) e backup seguro.
- `log-dir`: `/var/log/dakota-gateway` com permissão restrita (`0700`), idealmente com política *append-only* (filesystem/WORM quando disponível).

## Rotação

O gateway pode rotacionar por tamanho via `--rotate-bytes`.\n
Ao rotacionar, ele cria um sidecar `*.manifest.json` com hash do arquivo e range de sequência.

Exemplo:

```bash
python3 -m dakota_gateway.cli start ... --rotate-bytes 268435456
```

## Verificação e replay como rotina

1) Verifique integridade (sempre) antes de replay/migração:

```bash
python3 -m dakota_gateway.cli verify --log-dir ... --hmac-key-file ...
```

2) Rode replay no destino:

```bash
python3 -m dakota_gateway.cli replay --log-dir ... --hmac-key-file ... --target-host ... --target-user ...
```

## Observabilidade

Como o log é JSONL, você pode:
- enviar para SIEM (Splunk/ELK) em modo somente leitura
- gerar métricas por `type`, `actor`, `session_id`

## Control plane (SQLite + dashboard)

### Arquivos
- DB: `gateway/state/replay.db` (ou `--db` no server/CLI)
- Dashboard: `gateway/control/server.py`

### Bootstrap

```bash
python3 gateway/control/server.py \
  --listen 127.0.0.1:8090 \
  --db gateway/state/replay.db \
  --cookie-secret-file /etc/dakota-gateway/cookie_secret.key \
  --hmac-key-file /etc/dakota-gateway/hmac.key \
  --bootstrap-admin admin:admin123
```

### Operação
- `operator` cria runs e aciona start/pause/resume/cancel/retry pelo dashboard.\n
- O runner atualiza `last_seq_global_applied` e registra `replay_run_events`.\n

## Backups

- Backup do log + manifests é suficiente para auditoria.
- Sem o `hmac.key`, você perde a capacidade de provar autenticidade; mantenha a chave em cofre.


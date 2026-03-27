# TerminalGateway + AuditLog + Replay (multiusuário)

Este diretório implementa um **gateway obrigatório** para sessões SSH de usuários no legado, com:

- **Log append-only** (`*.jsonl`) com **ordem global total** (`seq_global`)
- **Integridade verificável** (hash-chain + HMAC)
- **Replay** no servidor destino (mesmo sistema) mantendo a mesma sequência global
- **Checkpoints** por tela (normalize + signature) para validar que o destino está “no mesmo lugar”

## Pré-requisitos

- `python3` (stdlib apenas)
- `ssh` client instalado (o gateway abre sessões no servidor origem/destino via `ssh -tt`)

## Como funciona (visão geral)

1. O usuário conecta via SSH no **gateway**.\n
2. O `sshd` do gateway executa um **ForcedCommand** (este programa) para cada sessão.\n
3. O gateway abre um `ssh -tt` para o servidor **origem**, faz proxy dos bytes e grava eventos.\n
4. O replayer lê o log e reproduz os `input_bytes` em sessões `ssh -tt` no servidor **destino**.\n

## Uso

### 1) Rodar gateway por sessão (ForcedCommand)

Exemplo (manual, para teste):

```bash
python3 -m dakota_gateway.cli start \
  --log-dir /var/log/dakota-gateway \
  --hmac-key-file /etc/dakota-gateway/hmac.key \
  --source-host legacy-origem \
  --source-user legacyuser
```

Por padrão o gateway abre `ssh -tt legacyuser@legacy-origem` (shell interativo).

Alternativa (wrapper local, bom para `ForceCommand`):

```bash
/opt/dakota-replay2/gateway/dakota-gateway start ...
```

### 2) Verificar integridade

```bash
python3 -m dakota_gateway.cli verify --log-dir /var/log/dakota-gateway --hmac-key-file /etc/dakota-gateway/hmac.key
```

### 3) Replay no destino (ordem global estrita)

```bash
python3 -m dakota_gateway.cli replay \
  --log-dir /var/log/dakota-gateway \
  --hmac-key-file /etc/dakota-gateway/hmac.key \
  --target-host legacy-destino \
  --target-user legacyuser
```

Modo alternativo (por sessão, sem interleaving global):

```bash
python3 -m dakota_gateway.cli replay ... --mode parallel-sessions
```

## Configuração do SSHD (gateway)

Você normalmente configura `ForceCommand` no `sshd_config` do host gateway, por exemplo:

```
Match Group legacy-users
  ForceCommand /usr/bin/python3 -m dakota_gateway.cli start --log-dir /var/log/dakota-gateway --hmac-key-file /etc/dakota-gateway/hmac.key --source-host legacy-origem --source-user legacyuser
  PermitTTY yes
  X11Forwarding no
  AllowTcpForwarding no
```

## Segurança

- Proteja `hmac.key` (600 root) e o `log-dir` (append-only idealmente).
- O HMAC garante que ninguém **forja** eventos sem a chave, e a hash-chain detecta **alteração/reordenação/remoção**.

## Schema do log

Veja `gateway/internal/audit/schema.md` (v1).

## Control plane (controle de replay + dashboard)

O control plane adiciona **metadados operacionais** em SQLite e um dashboard interno com login/RBAC.

### Subir o dashboard

Você precisa de 2 chaves:
- `hmac.key`: a mesma usada para verificar o audit log
- `cookie_secret`: segredo para assinar cookies de sessão do dashboard

Exemplo:

```bash
mkdir -p /etc/dakota-gateway
head -c 32 /dev/urandom > /etc/dakota-gateway/hmac.key
head -c 32 /dev/urandom > /etc/dakota-gateway/cookie_secret.key

python3 gateway/control/server.py \
  --listen 127.0.0.1:8090 \
  --db gateway/state/replay.db \
  --cookie-secret-file /etc/dakota-gateway/cookie_secret.key \
  --hmac-key-file /etc/dakota-gateway/hmac.key \
  --bootstrap-admin admin:admin123
```

Abra `http://127.0.0.1:8090/`.

### CLI para operar runs (SQLite)

- Criar usuário:

```bash
python3 -m dakota_gateway.cli user --db gateway/state/replay.db add --username op --password op123 --role operator
```

- Criar run:

```bash
python3 -m dakota_gateway.cli runs --db gateway/state/replay.db create --created-by op --log-dir /var/log/dakota-gateway --target-host legacy-destino --target-user legacyuser --mode strict-global
```

- Rodar (foreground):

```bash
python3 -m dakota_gateway.cli runs --db gateway/state/replay.db start --run-id 1 --hmac-key-file /etc/dakota-gateway/hmac.key
```

Mais detalhes: `gateway/docs/ops.md`.


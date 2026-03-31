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

### Replay local de smoke

Para validar na mesma maquina, com o mesmo host e credenciais conhecidas:

```bash
tmpdir="$(mktemp -d)"
cp gateway/state/captures/<capture-session-uuid>/audit-<sessao-pty>.part001.jsonl "$tmpdir/"

sshwrap="$(mktemp -d)"
printf '%s\n' '#!/usr/bin/env bash' 'exec sshpass -e /usr/bin/ssh "$@"' > "$sshwrap/ssh"
chmod +x "$sshwrap/ssh"

PATH="$sshwrap:$PATH" SSHPASS='teste' python3 gateway/dakota-gateway verify \
  --log-dir "$tmpdir" \
  --hmac-key-file .local-secrets/hmac.key

PATH="$sshwrap:$PATH" SSHPASS='teste' python3 gateway/dakota-gateway replay \
  --log-dir "$tmpdir" \
  --hmac-key-file .local-secrets/hmac.key \
  --target-host 127.0.0.1 \
  --target-user teste \
  --mode strict-global \
  --input-mode raw
```

Se quiser testar o deterministico no mesmo host:

```bash
PATH="$sshwrap:$PATH" SSHPASS='teste' python3 gateway/dakota-gateway replay \
  --log-dir "$tmpdir" \
  --hmac-key-file .local-secrets/hmac.key \
  --target-host 127.0.0.1 \
  --target-user teste \
  --mode strict-global \
  --input-mode deterministic \
  --on-deterministic-mismatch fail-fast
```

Leitura do resultado:

- `raw`: reproduz `bytes in`, entao costuma passar mesmo se o prompt remoto nao for identico;
- `deterministic + fail-fast`: aborta se `screen_sig` do destino nao bater;
- `deterministic + send-anyway`: registra mismatch por timeout e segue com a injecao.

Atencao:

- nao use o `log_dir` misto inteiro da captura para `verify/replay` quando ele contiver junto eventos do sampler passivo de porta 22;
- para replay operacional da sessao PTY, aponte para o arquivo `audit-*.jsonl` da propria sessao `capture-session` ou copie-o para um diretorio temporario dedicado.

## Gateway-only e endurecimento operacional

Quando um target estiver com `gateway_required=true`, trate a seguinte regra como obrigatoria:

- a captura oficial comeca no login;
- a sessao operacional precisa entrar pelo gateway;
- SSH direto operacional deve ficar bloqueado ou explicitamente fora de conformidade;
- eventual acesso direto deve ser reservado para administracao e registrado fora do fluxo normal de captura.

O projeto agora implementa:

- policy por `target_environment`;
- evidencia de conformidade por sessao e por `replay_run`;
- bloqueio de start da run quando a policy estiver em `strict` e a origem nao for conforme.

O que ainda depende do host/infra:

- `sshd_config` para limitar usuarios/chaves e forcar bastion/gateway;
- firewall/ACL para impedir SSH direto aos targets controlados;
- segregacao de contas administrativas versus contas operacionais capturaveis.

Checklist operacional recomendado:

1. Marque o target como `gateway_required=true`.
2. Ajuste `direct_ssh_policy` para `gateway_only` ou `disabled`.
3. Bloqueie SSH direto operacional no host real via `sshd_config`, firewall ou ACL.
4. Permita acesso administrativo direto so se `allow_admin_direct_access=true`.
5. Verifique no control plane se sessoes e runs mostram `compliance_status=compliant`.
6. Trate `warning`, `non_compliant` ou `rejected` como bypass ou quebra de processo.

### Captura completa de sessão (digitado + saída)

Para gravar tudo que o usuário faz após conectar na porta 22 (view/replay), use o gateway como ponto de entrada SSH com `ForceCommand`.

Comando recomendado no host de entrada SSH:

```bash
python3 /caminho/do/repo/gateway/dakota-gateway capture-session \
  --db /caminho/do/repo/gateway/state/replay.db \
  --hmac-key-file /etc/dakota-gateway/hmac.key
```

Com esse fluxo:

- tudo que o usuário digita é registrado como `bytes` com `dir=in`;
- tudo que o sistema retorna é registrado como `bytes` com `dir=out`;
- a sessão gera `session_start` e `session_end` no mesmo `log_dir` da captura ativa.

Observação: monitoramento passivo da porta 22 (open/close) não captura payload da sessão SSH criptografada; ele serve apenas para metadados de conexão.

### Integracao local do sshd com o gateway

Para um host de laboratorio, o projeto entrega scripts para fazer `ssh <usuario>@localhost` entrar automaticamente no `capture-session`:

```bash
./scripts/install-local-ssh-capture.sh --match-user teste
```

Esse fluxo instala:

- um wrapper local em `/usr/local/bin/dakota-capture-session`;
- um snippet em `/etc/ssh/sshd_config.d/90-dakota-capture.conf`;
- um `ForceCommand` para o usuario informado.

Depois disso, o fluxo fica:

1. `ssh teste@localhost`
2. `sshd` chama o wrapper do gateway
3. se houver captura ativa, o wrapper executa `dakota-gateway capture-session`
4. se nao houver captura ativa, o wrapper faz fallback para o shell/login normal do usuario
5. quando entra pelo gateway, a captura passa a registrar `bytes`, `checkpoint` e `deterministic_input`

Para remover a integracao:

```bash
./scripts/uninstall-local-ssh-capture.sh
```

## Parametros de replay via gateway

Para target controlado no destino, configure o bastion/gateway do replay com:

- `gateway_host`
- `gateway_user`
- `gateway_port`

Implementacao atual:

- o runner usa SSH com `ProxyJump` para chegar ao target;
- o control plane tenta reaproveitar `gateway_host` do metadata do target;
- se o target exigir gateway e esse metadata nao existir, o sistema tenta derivar o host a partir do `gateway_endpoint` da captura de origem.

Recomendacao operacional:

- prefira configurar explicitamente o bastion no `target_environment`, em vez de depender apenas da derivacao da captura.

Exemplo rapido de cadastro:

```bash
python3 -m dakota_gateway.cli targets add \
  --db gateway/state/replay.db \
  --name "Recital 24 PRD" \
  --host recital24-prd \
  --transport-hint ssh \
  --gateway-required \
  --direct-ssh-policy gateway_only \
  --capture-start-mode login_required \
  --capture-compliance-mode strict \
  --gateway-host gw-recital.example \
  --gateway-user bastion \
  --gateway-port 2200
```

## Observabilidade

Como o log é JSONL, você pode:
- enviar para SIEM (Splunk/ELK) em modo somente leitura
- gerar métricas por `type`, `actor`, `session_id`

## Control plane (SQLite + UI operacional)

### Arquivos
- DB: `gateway/state/replay.db` (ou `--db` no server/CLI)
- UI/API: `gateway/control/server.py`

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
- `operator` cria runs e aciona start/pause/resume/cancel/retry pela UI operacional.\n
- O runner atualiza `last_seq_global_applied` e registra `replay_run_events`.\n
- Em target controlado, o start da run pode ser recusado se a origem capturada nao provar passagem pelo gateway e inicio conforme no login.

## Backups

- Backup do log + manifests é suficiente para auditoria.
- Sem o `hmac.key`, você perde a capacidade de provar autenticidade; mantenha a chave em cofre.

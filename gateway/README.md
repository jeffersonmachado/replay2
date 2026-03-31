# Gateway (audit + replay + control)

Este diretorio contem os componentes Python de gateway para:

- captura/auditoria de sessao
- verificacao de integridade
- replay
- control plane com SQLite + UI

## O que existe neste pacote

- `dakota-gateway` (wrapper)
- `dakota_gateway/` (modulos Python)
- `control/server.py` (API + UI de controle)
- `docs/` (operacao e threat model)
- `state/` (estado runtime; banco e criado no primeiro uso)

## Fronteiras do pacote

- superficie oficial principal: `control/server.py` e a UI/API operacional suportada;
- control plane modularizado: `control/routes/` concentra acoplamento HTTP por dominio, `control/services/` concentra regras e payloads, `control/ui_templates.py` faz apenas o carregamento, e `control/templates/` guarda os HTMLs da UI;
- codigo experimental: `internal/audit/` em Go nao integra o runtime Python por padrao nesta fase.
- persistencia: `dakota_gateway/db/` passa a concentrar schema, conexao e migracoes leves; `dakota_gateway/state_db.py` continua exposto como shim de compatibilidade para o restante da base.

## Pre-requisitos

- `python3`
- cliente `ssh` para cenarios de proxy/replay remoto

## Subir o control server

```bash
mkdir -p /etc/dakota-gateway
head -c 32 /dev/urandom > /etc/dakota-gateway/hmac.key
head -c 32 /dev/urandom > /etc/dakota-gateway/cookie_secret.key

python3 gateway/control/server.py \
  --listen 127.0.0.1:8090 \
  --db gateway/state/replay.db \
  --cookie-secret-file /etc/dakota-gateway/cookie_secret.key \
  --hmac-key-file /etc/dakota-gateway/hmac.key
```

Abra `http://127.0.0.1:8090/`.

## Bootstrap inicial do admin

Opcao 1 (argumento):

```bash
python3 gateway/control/server.py \
  --listen 127.0.0.1:8090 \
  --db gateway/state/replay.db \
  --cookie-secret-file /etc/dakota-gateway/cookie_secret.key \
  --hmac-key-file /etc/dakota-gateway/hmac.key \
  --bootstrap-admin admin:UseUmaSenhaForte123
```

Opcao 2 (variavel de ambiente):

```bash
export DAKOTA_ADMIN='admin:UseUmaSenhaForte123'
python3 gateway/control/server.py \
  --listen 127.0.0.1:8090 \
  --db gateway/state/replay.db \
  --cookie-secret-file /etc/dakota-gateway/cookie_secret.key \
  --hmac-key-file /etc/dakota-gateway/hmac.key
```

Comportamento do bootstrap:

- cria o banco se nao existir
- cria admin apenas se ainda nao houver admin
- se admin ja existir, nao sobrescreve
- emite aviso para senha fraca

## Seguranca operacional

- proteja `hmac.key` e `cookie_secret.key` com permissoes restritas
- use senha forte no bootstrap inicial
- trate `--bootstrap-admin` e `DAKOTA_ADMIN` como mecanismos de inicializacao
- prefira `DAKOTA_ADMIN` ao argumento `--bootstrap-admin`, porque a variavel de ambiente reduz exposicao em historico de shell e process list

## Semantica visual do control plane

- rose/pink = identidade principal, CTA, foco e destaque de marca
- emerald = sucesso, running e estado ativo saudavel
- amber = queued, warning e atencao operacional
- red/rose forte = erro, falha, cancelamento
- neutral = desligado, inativo, indisponivel ou desabilitado

Essa convencao evita misturar cor de marca com cor de estado e mantem a interface pink-first sem perder a leitura operacional.

## Operacoes por CLI

Exemplos:

```bash
python3 -m dakota_gateway.cli targets add --db gateway/state/replay.db --name "Recital 24 HML" --host recital24-hml --transport-hint ssh --gateway-required --direct-ssh-policy gateway_only --capture-start-mode login_required --capture-compliance-mode strict --gateway-host gw-recital.example --gateway-user bastion --gateway-port 2200
python3 -m dakota_gateway.cli profiles add --db gateway/state/replay.db --name "SSH Batch" --transport ssh --username replayuser --credential-ref env:RECITAL24_SSH_KEY
python3 -m dakota_gateway.cli verify --log-dir /var/log/dakota-gateway --hmac-key-file /etc/dakota-gateway/hmac.key
python3 -m dakota_gateway.cli replay --log-dir /var/log/dakota-gateway --hmac-key-file /etc/dakota-gateway/hmac.key --target-host legacy-destino --target-user legacyuser
python3 -m dakota_gateway.cli runs create --db gateway/state/replay.db --created-by admin --log-dir /var/log/dakota-gateway --target-env-id 1 --connection-profile-id 1 --mode strict-global --match-mode fuzzy --match-threshold 0.9
```

Exemplo de payload de target `gateway_only` via API:

```json
{
  "name": "Recital 24 HML",
  "host": "recital24-hml",
  "transport_hint": "ssh",
  "gateway_required": true,
  "direct_ssh_policy": "gateway_only",
  "capture_start_mode": "login_required",
  "capture_compliance_mode": "strict",
  "gateway_host": "gw-recital.example",
  "gateway_user": "bastion",
  "gateway_port": 2200
}
```

## Cadastros operacionais

- `target_environments`: ambiente alvo reutilizavel, com host, plataforma, porta e `transport_hint`.
- `target_environments`: agora tambem carregam policy explicita de acesso e conformidade:
  - `gateway_required`
  - `direct_ssh_policy`
  - `capture_start_mode`
  - `capture_compliance_mode`
  - `allow_admin_direct_access`
- `connection_profiles`: perfil de conexao reutilizavel, com `transport`, usuario, porta, comando e `credential_ref`.
- `replay_runs`: agora pode apontar para `target_env_id` e `connection_profile_id`, mantendo tambem os campos resolvidos para auditoria da execucao.
- `replay_runs`: tambem registram `entry_mode`, `via_gateway`, `gateway_session_id`, `gateway_endpoint`, `compliance_status`, `compliance_reason` e `validated_at_ms`.

## Gateway-only e SSH direto

Para target controlado, o fluxo oficial de captura passa a ser:

1. login no ambiente via gateway;
2. captura do `session_start` e da sequencia real de `bytes/checkpoints`;
3. validacao de conformidade da origem antes da run;
4. bloqueio ou marcacao de nao conformidade conforme `capture_compliance_mode`.

Interpretacao operacional:

- `gateway_required=true`: sessoes operacionais capturaveis nao podem bypassar o gateway.
- `direct_ssh_policy=gateway_only|disabled`: SSH direto operacional deve ficar fora de conformidade ou indisponivel no host.
- `direct_ssh_policy=admin_only`: acesso direto passa a ser excecao administrativa, nao fluxo oficial de captura.
- `capture_start_mode=login_required`: o projeto exige evidencia de inicio no login shell; `source_command` direto invalida a captura oficial.

Dependencias de infraestrutura continuam fora do codigo:

- endurecer `sshd_config`, firewall, ACL ou bastion no host real;
- restringir chaves/usuarios para que o gateway seja o unico ponto de entrada operacional;
- manter eventual acesso direto apenas para administracao autorizada.

## Replay via gateway

O replay agora suporta rota SSH via bastion/gateway usando `ProxyJump` quando os parametros abaixo estiverem resolvidos:

- `gateway_host`
- `gateway_user`
- `gateway_port`
- `gateway_route_mode=proxyjump`

Esses valores podem vir:

- do metadata do `target_environment`;
- ou, na falta disso, do `gateway_endpoint` observado na captura de origem quando a policy exigir gateway.

## Matching de replay

Os checkpoints do replay aceitam:

- `strict`
- `contains`
- `regex`
- `fuzzy`

As falhas estruturadas passam a registrar evidencia do matcher e taxonomia ampliada (`timeout`, `screen_divergence`, `navigation_error`, `concurrency_error`), ainda sujeita a refinamento por fluxo de negocio.

## Deterministic Record

O gateway agora adiciona uma camada integrada de `deterministic record` sem remover a trilha bruta existente.

Semantica:

- a captura continua gravando `bytes in/out`, `checkpoint`, `session_start/end` e cadeia de integridade;
- em cada input do usuario o gateway registra antes um evento `deterministic_input`;
- esse evento associa a ultima tela observada ao input enviado.

Campos principais do `deterministic_input`:

- `screen_sig`: assinatura estavel da tela normalizada;
- `screen_sample`: amostra textual legivel da tela;
- `norm_sha256` e `norm_len`: evidencia da tela normalizada;
- `key_b64`, `key_text`, `key_kind`: acao capturada;
- `input_len`, `contains_newline`, `contains_escape`, `is_probable_paste`, `is_probable_command`: heuristicas de classificacao do input;
- `screen_source`, `screen_snapshot_ts_ms`, `screen_snapshot_age_ms`: origem e idade do snapshot associado;
- `screen_raw_b64`: snapshot bruto opcional exatamente do mesmo snapshot usado para `screen_sig`, `screen_sample`, `norm_sha256` e `norm_len`;
- `source="gateway_record"`.

Como funciona internamente:

- o gateway reaproveita o mesmo `screen_buf` usado para `checkpoint`;
- a normalizacao e a assinatura continuam centralizadas em `dakota_gateway/screen.py`;
- quando a saida do terminal entra em quietude, o gateway atualiza um `last_stable_snapshot`;
- o `deterministic_input` passa a preferir essa tela estavel;
- se ainda nao houver tela estavel, o evento cai explicitamente para `screen_source=buffer` ou `screen_source=empty`;
- `screen_source=buffer` significa: ainda nao houve quietude suficiente, entao o evento usou o buffer instantaneo atual;
- `screen_source=empty` significa: nao havia tela estavel e tambem nao havia buffer observavel para associar ao input;
- quando `screen_raw_b64` existe, ele sempre corresponde ao mesmo snapshot efetivamente escolhido; no caso `screen_source=empty`, o raw pode vir vazio/ausente por definicao;
- o replay bruto continua usando `bytes dir=in`;
- o replay deterministico pode ser habilitado com `--input-mode deterministic`, fazendo o motor esperar `screen_sig` antes de injetar a acao.

Granularidade do input:

- chunks curtos e claramente imprimiveis podem ser refinados em unidades menores para o `deterministic_input`;
- sequencias ANSI, controles, paste provavel e chunks ambiguos permanecem atomicos;
- quando um chunk parece um comando terminando em enter, o gateway tenta separar os caracteres do `enter` para aproximar o comportamento do recorder legado;
- a trilha bruta continua preservada em `bytes in`, entao nenhum detalhe de auditoria e perdido.

View e observabilidade:

- `GET /api/gateway/monitor` agora resume tambem quantos `deterministic_input` existem na janela;
- `GET /api/gateway/sessions/{session_id}` expoe esses eventos na timeline da sessao;
- `GET /api/captures/{id}/replay?session_id=...` retorna `deterministic_events`, `timeline`, metadados do snapshot estavel e os modos disponiveis de playback.
- a tela `/captures/{id}/replay?...` permite iniciar uma run operacional em modo bruto ou deterministico quando a captura tiver contexto suficiente de ambiente/perfil.

CLI:

```bash
python3 -m dakota_gateway.cli replay \
  --log-dir /var/log/dakota-gateway \
  --hmac-key-file /etc/dakota-gateway/hmac.key \
  --target-host legacy-destino \
  --target-user legacyuser \
  --input-mode deterministic \
  --on-deterministic-mismatch fail-fast
```

Replay local na mesma maquina:

```bash
# 1. isole o arquivo audit-*.jsonl da sessao PTY/capture-session
tmpdir="$(mktemp -d)"
cp gateway/state/captures/<capture-session-uuid>/audit-<sessao-pty>.part001.jsonl "$tmpdir/"

# 2. se o alvo usar senha, injete um wrapper local de ssh com sshpass so para o teste
sshwrap="$(mktemp -d)"
printf '%s\n' '#!/usr/bin/env bash' 'exec sshpass -e /usr/bin/ssh "$@"' > "$sshwrap/ssh"
chmod +x "$sshwrap/ssh"

# 3. replay bruto
PATH="$sshwrap:$PATH" SSHPASS='teste' python3 gateway/dakota-gateway replay \
  --log-dir "$tmpdir" \
  --hmac-key-file .local-secrets/hmac.key \
  --target-host 127.0.0.1 \
  --target-user teste \
  --mode strict-global \
  --input-mode raw

# 4. replay deterministico estrito
PATH="$sshwrap:$PATH" SSHPASS='teste' python3 gateway/dakota-gateway replay \
  --log-dir "$tmpdir" \
  --hmac-key-file .local-secrets/hmac.key \
  --target-host 127.0.0.1 \
  --target-user teste \
  --mode strict-global \
  --input-mode deterministic \
  --on-deterministic-mismatch fail-fast
```

Interpretacao do replay local:

- `input_mode=raw`: tende a funcionar mesmo quando o prompt ou o usuario remoto mudam, porque reproduz a trilha de `bytes in`;
- `input_mode=deterministic --on-deterministic-mismatch fail-fast`: falha se a tela atual do destino nao bater com `screen_sig`;
- `input_mode=deterministic --on-deterministic-mismatch send-anyway`: espera a tela por timeout, registra o mismatch e ainda injeta a acao.

Observacao operacional importante:

- o `log_dir` completo de uma `capture_session` pode conter tanto arquivos da sessao PTY (`capture-session`) quanto arquivos auxiliares do sampler de porta 22;
- para `verify` e `replay` de sessao PTY, prefira isolar apenas o `audit-*.jsonl` da sessao capturada pelo gateway;
- se voce apontar `verify/replay` para um diretorio misto, pode haver falha de integridade porque os eventos passivos de porta 22 nao compartilham a mesma cadeia HMAC da sessao PTY.

SSH local entrando pelo gateway:

```bash
./scripts/install-local-ssh-capture.sh --match-user teste
```

Esse script instala um `ForceCommand` do `sshd` apontando para o `capture-session`. Depois disso:

- `ssh teste@localhost` passa pelo PTY auditavel do gateway;
- a sessao deixa de aparecer apenas como `port22_connection_open/close`;
- a UI de `Captures -> View` passa a mostrar `IN`, `OUT` e `DET` da sessao SSH.
- se o gateway estiver desativado ou sem captura ativa, o wrapper faz fallback para o login normal do usuario em vez de falhar com `nenhuma captura ativa encontrada`.

Para desfazer:

```bash
./scripts/uninstall-local-ssh-capture.sh
```

UI operacional:

- abra a sessao em `/captures/{id}/replay?capture_id={id}&session_id={session_id}`;
- use `Criar replay bruto` para manter o comportamento historico;
- use `Criar replay determinístico` para criar e iniciar uma run com `input_mode=deterministic`;
- se a captura nao tiver `target_env_id`/`connection_profile_id`, a UI redireciona para `runs/new` com os campos relevantes pre-preenchidos.

Limitacoes atuais:

- o modo deterministico ainda usa a assinatura textual do gateway atual, nao um banco externo de IDs como no projeto legado;
- a classificacao de input usa heuristicas conservadoras; paste e bursts ambiguos continuam sendo gravados como chunk unico no deterministico;
- a tela estavel depende da janela de quietude configurada no gateway, entao entradas extremamente rapidas logo apos saida podem cair em `screen_source=buffer`;
- a UI exibe a timeline enriquecida e dispara a run operacional, mas o playback visual no navegador continua sendo apenas uma revisao local da trilha capturada.

Testes e artefato:

- os testes Python da feature ficam em `tests/test_deterministic_record_unit.py`, `tests/test_gateway_status_unit.py`, `tests/test_screen_contracts.py`, `tests/test_runtime_capture_session_unit.py` e `tests/test_terminal_gateway_unit.py`;
- o tarball gerado por `scripts/build-tarball.sh` agora inclui o diretorio de topo `tests/`;
- a instalacao via `install.sh` tambem preserva `tests/` no prefixo instalado para auditoria e regressao local;
- comando validado nesta rodada:

```bash
python3 -m unittest \
  tests.test_screen_contracts \
  tests.test_runtime_capture_session_unit \
  tests.test_terminal_gateway_unit \
  tests.test_gateway_status_unit \
  tests.test_deterministic_record_unit
```

Mais detalhes: `docs/ops.md`.

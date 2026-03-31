Dakota Replay2
==============

Plataforma de captura de producao/live, replay sequencial, validacao de migracao, analise de falhas e teste de estresse para garantir com seguranca a transicao do Recital 8 para o Recital 24.

## Visao Geral

O projeto nao deve ser tratado apenas como automacao de telas. A base atual combina:

- core `Expect/Tcl` para captura de tela, normalizacao, assinatura de estado e automacao screen-oriented;
- gateway `Python` para captura auditavel de sessoes, ordem global de eventos, integridade criptografica e replay;
- control plane `Python + SQLite` para operar execucoes, acompanhar runs, registrar falhas e expor API/UI operacional.

## Objetivo Principal

Registrar fielmente tudo o que aconteceu no ambiente live do Recital 8 e reproduzir no Recital 24 exatamente na mesma sequencia observada originalmente, preservando contexto, checkpoints e rastreabilidade de falhas.

## Requisito Central de Sequencia

A ordem real dos acontecimentos e sagrada.

O projeto precisa:

- registrar `session_start`, `bytes in/out`, `checkpoint` e `session_end` com `seq_global`, `seq_session` e `ts_ms`;
- preservar interleaving global entre sessoes no gateway;
- permitir replay em `strict-global` para respeitar a ordem global original;
- permitir `parallel-sessions` e concorrencia controlada quando o objetivo for stress/load test.

## Regra Arquitetural de Gateway-Only

Quando um `target_environment` controlado estiver com `gateway_required=true`, a sessao operacional capturavel:

- deve comecar no login;
- deve passar obrigatoriamente pelo gateway;
- deve registrar evidencia explicita de entrada (`entry_mode`, `via_gateway`, `gateway_session_id`, `gateway_endpoint`);
- so pode ser tratada como fonte confiavel de replay auditavel se a `capture_compliance_mode` permitir.

Neste modo, o projeto distingue:

- acesso operacional capturavel: precisa passar pelo gateway;
- acesso administrativo direto: so existe se a policy permitir explicitamente;
- acesso nao conforme: bypass, rejeitado ou marcado conforme a policy do target.

## Controle de Falhas no Ambiente Novo

Toda execucao no Recital 24 precisa ser investigavel.

Hoje a base registra:

- ponto de progresso por `last_seq_global_applied`;
- checkpoint esperado x observado;
- falhas estruturadas em `replay_failures` com tipo, severidade, mensagem e evidencia;
- eventos operacionais em `replay_run_events`;
- estado e metricas por run em `replay_runs`.

## Objetivos Especificos

- captura fiel de sessoes reais;
- replay auditavel e repetivel;
- identificacao precisa de divergencias funcionais e tecnicas;
- suporte a multiplos usuarios e processos acelerados;
- operacao principal em Linux com estrategia documentada para AIX.

## Escopo Funcional

- capturar entrada, saida e checkpoints de sessoes via gateway SSH;
- normalizar telas e gerar assinaturas estaveis;
- reproduzir logs auditados no destino em ordem estrita ou por sessao;
- pausar, retomar, cancelar, repetir e monitorar runs;
- reprocessar parcialmente por faixa de `seq_global`, por `session_id` ou a partir de um `checkpoint sig`;
- gerar reprocessamento guiado a partir de uma falha registrada, incluindo sessao da falha e checkpoint associado;
- rastrear a familia de execucoes e o vinculo entre run de reprocessamento, run de origem e falha que disparou a nova tentativa;
- resolver runs a partir de `target_environment` e `connection_profile`, sem depender apenas de `target_host` digitado manualmente;
- aplicar matching configuravel (`strict`, `contains`, `regex`, `fuzzy`) para checkpoints no replay funcional e no stress;
- consolidar na `/observability` a taxa de sucesso de reprocessamento por fluxo e as assinaturas de falha que mais se repetem;
- destacar na `/observability` recuperacao por ambiente, candidatas a automacao e fila pendente/reincidente de reprocessamentos;
- consolidar falhas por execucao via API/UI.

## Escopo Tecnico

- `bin/main.exp` e `lib/*.tcl`: engine de captura/automacao;
- `gateway/dakota_gateway/gateway.py`: captura auditavel com `seq_global` e hash-chain;
- `gateway/dakota_gateway/replay_control.py`: runner, replay, carga concorrente e persistencia operacional;
- `gateway/control/server.py`: API HTTP + UI operacional;
- `gateway/dakota_gateway/state_db.py`: persistencia SQLite;
- `tests/` e `gateway/tests/`: validacoes Tcl e Python.

## Fronteiras Arquiteturais

- runtime principal: core `Expect/Tcl`, gateway Python, control plane Python e SQLite;
- superficie oficial de operacao/observabilidade: `gateway/control/server.py`;
- composicao do control plane: `gateway/control/server.py` atua como shell HTTP leve, `gateway/control/routes/` concentra os dominios de rota, `gateway/control/services/` concentra payloads e regras reaproveitaveis, `gateway/control/ui_templates.py` virou loader fino e `gateway/control/templates/` concentra os HTMLs da UI;
- componente experimental/futuro: `gateway/internal/audit` em Go, sem integracao obrigatoria no runtime atual.
- persistencia Python em transicao incremental: `gateway/dakota_gateway/db/` concentra schema, conexao e migracoes leves; `gateway/dakota_gateway/state_db.py` permanece como camada de compatibilidade.

## Criterios de Sucesso

- uma sessao real pode ser registrada com ordem confiavel dos eventos;
- o replay consegue respeitar a sequencia original no modo `strict-global`;
- o operador identifica o evento, a sessao, o esperado, o observado, a severidade e a evidencia da falha;
- a plataforma executa cenarios concorrentes com controle de `concurrency`, `speed`, `ramp_up_per_sec` e `jitter_ms`;
- Linux funciona como alvo principal e AIX permanece considerado no desenho e na documentacao operacional.

## Beneficios Esperados

- reduzir risco funcional na migracao Recital 8 -> Recital 24;
- transformar replay em evidencia auditavel, nao apenas em automacao oportunista;
- acelerar diagnostico de divergencias e regressao;
- reutilizar o mesmo acervo de capturas tanto para validacao quanto para carga.

## Definicao Executiva

Dakota Replay2 e a base operacional para capturar a realidade do Recital 8, reproduzi-la com rastreabilidade no Recital 24 e mostrar com precisao onde a migracao divergiu, falhou ou degradou.

## O Que Existe Hoje

### Core Expect/Tcl

- `bin/main.exp`: loop principal de captura, normalizacao, assinatura e despacho por estado.
- `lib/capture.tcl`: leitura incremental de tela com buffer por sessao.
- `lib/normalize.tcl` + `lib/signature.tcl`: reducao de ruido e identificacao estavel de tela.
- `lib/state_machine.tcl`: roteamento de handlers por assinatura e estado.
- `lib/control.tcl`: controle local `pause/resume/step/send/dump`.
- `lib/record.tcl`: gravacao simples de eventos da engine.

### Gateway / Replay

- `gateway/dakota_gateway/gateway.py`: proxy SSH com captura `bytes in/out` + `checkpoint`.
- `gateway/dakota_gateway/audit_writer.py`: ordem global, hash-chain, HMAC e manifest.
- `gateway/dakota_gateway/verifier.py`: verificacao de integridade.
- `gateway/dakota_gateway/replay.py`: replay no destino.
- `gateway/dakota_gateway/replay_control.py`: operacao de runs, concorrencia, metricas e falhas estruturadas.
- `target_environments` + `connection_profiles`: cadastro de alvo e perfil de conexao para o gateway resolver `target_env_id`, transporte, porta e referencia credencial.

### Operacao

- `gateway/control/server.py`: API/UI para criar, iniciar, pausar, retomar, cancelar, repetir e inspecionar runs.
- a criacao de run agora tambem aceita recorte operacional de reprocessamento no ambiente novo, sem exigir edicao manual de `params_json`.
- a mesma aplicacao agora expoe a visao `/observability`, combinando gateway live, runs recentes e falhas consolidadas sem alternar para outra interface.
- visualizacao de sessoes do gateway por metadados (`actor`, `session_id`, `event_type`, busca livre) diretamente no painel operacional.
- detalhe de sessao com timeline destacando checkpoints, sinais de atencao e falhas relacionadas por `session_id`, com salto direto ao evento associado.
- filtros de timeline por faixa de `seq_global` e `ts_ms`, com agrupamento de falhas repetidas por assinatura operacional.
- relatorio consolidado por execucao com agrupamento por sessao, tipo de falha e severidade para apoiar validacao de migracao e analise de ambiente.
- comparacao entre runs via `parent_run_id` ou baseline equivalente anterior, destacando falhas novas, recorrentes e resolvidas para acelerar leitura de regressao.
- visao `/observability` com cards visuais de regressao por run recente, incluindo baseline, delta de falhas e novas recorrencias.
- leitura de regressao agora tambem segmentada por ambiente derivado da run e por `flow_name` das falhas, para localizar onde a piora apareceu primeiro.
- relatorio exportavel por execucao em `md`, `json` e `csv` via `/api/runs/{id}/report/export`, com cortes por ambiente, fluxo e severidade.
- relatorio transversal de tendencia por multiplas runs via `/api/reports/runs/trend`, destacando ambientes com mais regressao e fluxos mais sensiveis ao longo do tempo.
- esse relatorio transversal agora aceita recorte por `environment`, `created_from_ms`, `created_to_ms` e `run_limit`, tanto na API quanto na `/observability`.
- a `/observability` agora permite salvar esses recortes como cenarios analiticos nomeados, reutilizando investigacoes como `HML ultima semana` sem remontar filtros manualmente.
- esses cenarios agora aceitam `visibility` (`private`/`shared`) e `tags`, permitindo compartilhamento controlado por equipe e organizacao por area operacional.
- a lista de cenarios agora pode ser filtrada por `visibility` e `tag`, e cada usuario pode marcar favoritos para priorizar recortes recorrentes.
- o projeto agora tambem possui base para um catalogo formal de cenarios operacionais executaveis (`replay`/`stress`), com API para instanciar uma run diretamente a partir do cenario salvo.
- o control plane principal agora expoe esse catalogo operacional na UI, permitindo salvar a configuracao atual como cenario e criar uma run diretamente a partir dele.
- esse catalogo na UI agora permite reaplicar/editar configuracoes rapidamente e filtrar cenarios por tipo e ambiente para uso diario do time.
- o editor do catalogo operacional agora suporta descricao/observacoes, e a listagem separa visualmente cenarios `replay` e `stress`.
- cada cenario operacional agora mostra historico resumido de uso, ultimo executor, volume total de runs e taxa observada de falha.
- o catalogo operacional agora tambem aceita recorte por executor e janela temporal, alem de ordenacao por uso, instabilidade, uso recente ou nome.
- o catalogo operacional agora tambem suporta favoritos por usuario, labels por squad/area/tags e score composto de criticidade para priorizacao operacional.
- cada cenario operacional agora tambem pode declarar responsavel explicito e limites de SLA para falha/criticidade, com alerta visual quando o limite e ultrapassado.
- a `/observability` agora tambem destaca no topo os cenarios operacionais com SLA estourado ou em alerta, reaproveitando a mesma regra do catalogo.
- a API principal agora expoe `/api/targets` e `/api/connection-profiles` para cadastro/listagem de ambientes de destino e perfis de conexao reutilizaveis.
- `scripts/start-*.sh` e `scripts/stop-*.sh`: bootstrap local da camada web.

## Lacunas Relevantes Ainda Abertas

- a captura fiel principal hoje esta consolidada no gateway SSH; o `record.tcl` ainda e um gravador simplificado da engine e nao substitui a trilha auditavel do gateway;
- a taxonomia de falhas de replay foi ampliada para `timeout`, `screen_divergence`, `navigation_error` e `concurrency_error`, mas ainda depende de heuristicas de terminal e precisa ser refinada por fluxo de negocio;
- nao existe ainda catalogo formal de cenarios de carga;
- o suporte a `telnet` entrou na camada de replay/orquestracao, mas autenticacao automatica continua preferencialmente via SSH ou mecanismos externos ao processo;
- a portabilidade AIX esta considerada no desenho e nos scripts POSIX, mas ainda depende de homologacao operacional dedicada.

## Target Environment e Connection Profile

Cada run pode ser criada:

- diretamente com `target_host`, `target_user` e `target_command`;
- ou resolvida via `target_env_id` + `connection_profile_id`.

O `target_environment` guarda:

- `env_id`, nome e host;
- plataforma (`linux` ou homologacao AIX);
- porta e `transport_hint`.
- policy de acesso/compliance:
  - `gateway_required`
  - `direct_ssh_policy` (`gateway_only`, `admin_only`, `unrestricted`, `disabled`)
  - `capture_start_mode` (`login_required`, `session_start_required`)
  - `capture_compliance_mode` (`strict`, `warn`, `off`)
  - `allow_admin_direct_access`

O `connection_profile` guarda:

- `transport` (`ssh` ou `telnet`);
- usuario, porta e comando default;
- `credential_ref` para integrar credenciais sem gravar segredo bruto no banco.

Na hora da criacao da run, o control plane resolve esses cadastros e materializa no `params_json`:

- `target_environment` / `environment`;
- `transport` / `target_port`;
- `connection_profile_name`;
- `credential_ref` e opcoes de conexao.

Cada `replay_run` agora tambem persiste evidencia de conformidade da origem:

- `entry_mode`
- `via_gateway`
- `gateway_session_id`
- `gateway_endpoint`
- `compliance_status`
- `compliance_reason`
- `validated_at_ms`

Se o target estiver em modo `strict` e a origem nao for compativel com a policy, a run fica registrada mas o start e bloqueado.

Quando o target controlado exige gateway no destino, o replay agora aceita rota via bastion/gateway com:

- `gateway_host`
- `gateway_user`
- `gateway_port`
- `gateway_route_mode=proxyjump`

Sem essa rota, targets `gateway_only`, `admin_only` ou `disabled` continuam bloqueando replay direto.

## Matching Inteligente

O replay agora aceita:

- `match_mode=strict`
- `match_mode=contains`
- `match_mode=regex`
- `match_mode=fuzzy`

Parametros suportados:

- `match_threshold`
- `match_ignore_case`

Quando um checkpoint nao fecha, a falha passa a carregar evidencia de matching com similaridade observada e classificacao heuristica de falha.

## Instalacao e Uso Rapido

### Distribuicao

```bash
./scripts/build-tarball.sh
ls -la dist/
```

### Core

```bash
/opt/dakota-replay2/bin/replay2 run --legacy-cmd "{tclsh examples/legacy_sim.tcl}"
```

### Control Plane

```bash
python3 gateway/control/server.py \
  --listen 127.0.0.1:8090 \
  --db gateway/state/replay.db \
  --cookie-secret-file /caminho/cookie.secret \
  --hmac-key-file /caminho/hmac.key
```

### Replay Operacional

```bash
python3 -m dakota_gateway.cli targets add \
  --db gateway/state/replay.db \
  --env-id recital24-hml \
  --name "Recital 24 HML" \
  --host recital24-hml-host \
  --platform linux \
  --transport-hint ssh \
  --gateway-required \
  --direct-ssh-policy gateway_only \
  --capture-start-mode login_required \
  --capture-compliance-mode strict \
  --gateway-host gw-recital.example \
  --gateway-user bastion \
  --gateway-port 2200

python3 -m dakota_gateway.cli profiles add \
  --db gateway/state/replay.db \
  --profile-id ssh-batch \
  --name "SSH Batch" \
  --transport ssh \
  --username replayuser \
  --port 22 \
  --credential-ref env:RECITAL24_SSH_KEY

python3 -m dakota_gateway.cli runs create \
  --db gateway/state/replay.db \
  --created-by admin \
  --log-dir /var/log/dakota-gateway \
  --target-env-id 1 \
  --connection-profile-id 1 \
  --mode strict-global \
  --match-mode fuzzy \
  --match-threshold 0.9
```

Exemplo equivalente via API:

```json
POST /api/targets
{
  "env_id": "recital24-hml",
  "name": "Recital 24 HML",
  "host": "recital24-hml-host",
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

```json
POST /api/runs
{
  "log_dir": "/var/log/dakota-gateway",
  "target_env_id": 1,
  "connection_profile_id": 1,
  "mode": "strict-global",
  "params": {
    "gateway_host": "gw-recital.example",
    "gateway_user": "bastion",
    "gateway_port": 2200,
    "gateway_route_mode": "proxyjump"
  }
}
```

## Linux e AIX

- Linux e o alvo operacional principal do projeto atual.
- AIX continua contemplado na escolha de `Expect/Tcl`, scripts POSIX e normalizacao de tela.
- Limitacoes remanescentes de AIX devem ser tratadas como item de homologacao e documentacao operacional, nao como capacidade ja comprovada sem teste.

## Documentacao Complementar

- `DIAGNOSTICO_TECNICO.md`
- `ROADMAP.md`
- `gateway/README.md`
- `gateway/docs/ops.md`

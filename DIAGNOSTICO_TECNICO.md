# Diagnostico Tecnico Atual

Data de referencia: 2026-03-29

Este diagnostico foi produzido a partir do codigo real em `bin/`, `lib/`, `gateway/`, `dashboard/`, `tests/` e scripts do repositorio. Ele nao assume capacidades nao comprovadas no codigo.

## 1. Visao Geral da Arquitetura Atual

### 1.1 Nucleo Expect/Tcl

- `bin/main.exp` executa o loop de captura, normalizacao, assinatura e despacho de handlers.
- `lib/capture.tcl` mantem buffer por `spawn_id` e tenta reconstruir a tela atual.
- `lib/normalize.tcl` e `lib/signature.tcl` estabilizam a comparacao de telas.
- `lib/state_machine.tcl` despacha handlers por assinatura.
- `lib/control.tcl` oferece controle local de debug e operacao.

### 1.2 Gateway Auditavel

- `gateway/dakota_gateway/gateway.py` intercepta uma sessao via SSH e grava `session_start`, `bytes in/out`, `checkpoint` e `session_end`.
- `gateway/dakota_gateway/audit_writer.py` garante ordem global (`seq_global`) compartilhada entre processos, hash-chain e HMAC.
- `gateway/dakota_gateway/verifier.py` valida integridade antes do replay.

### 1.3 Replay / Operacao

- `gateway/dakota_gateway/replay.py` reproduz logs em `strict-global` ou `parallel-sessions`.
- `gateway/dakota_gateway/replay_control.py` administra runs, pausa/retomada/cancelamento, concorrencia e metricas.
- `gateway/control/server.py` expoe API HTTP, interface operacional, visualizacao de sessoes por metadados e agora uma visao integrada de observabilidade.
- `gateway/dakota_gateway/state_db.py` persiste usuarios, runs, eventos e agora falhas estruturadas.

## 2. Modulos Atuais e Responsabilidades

- `bin/replay2.exp`: CLI da engine, diagnostico, assinatura, record/replay simplificado e plugins.
- `lib/record.tcl`: grava eventos selecionados da engine em formato Tcl list.
- `gateway/dakota_gateway/cli.py`: CLI do gateway e do control plane.
- `gateway/control/server.py`: UI/API operacional oficial do control plane.
- `scripts/start-*.sh` e `scripts/stop-*.sh`: operacao local da camada web.

## 3. Capacidades Existentes Ja Comprovadas

### Captura de sessao

- O gateway grava entrada e saida da sessao com `seq_global`, `seq_session`, `ts_ms`, `session_id` e `actor`.
- O core Expect/Tcl captura a tela atual e consegue gerar assinaturas e dumps.

### Preservacao de sequencia

- A trilha auditavel do gateway preserva ordem global total em disco.
- O replay `strict-global` reexecuta a ordem observada no log.
- O replay `parallel-sessions` preserva ordem por sessao para uso controlado.

### Replay e operacao

- Existe motor de replay, validacao por checkpoint, pausa/retomada/cancelamento/retry e acompanhamento de progresso.
- A orquestracao agora tambem possui cadastro formal de `target_environments` e `connection_profiles`, permitindo criar runs por `target_env_id` e `connection_profile_id`.
- O replay agora aceita `transport` resolvido por perfil (`ssh` e `telnet`) e matching configuravel (`strict`, `contains`, `regex`, `fuzzy`).
- A base agora tambem suporta reprocessamento parcial por faixa de `seq_global`, por sessao e a partir de `checkpoint sig`, preservando trilha auditavel da nova run.
- O control plane agora tambem consegue derivar uma nova run parcial diretamente de uma falha estruturada, reduzindo montagem manual de recortes no Recital 24.
- A trilha operacional agora passa a mostrar familia de runs e origem do reprocessamento, permitindo investigar se a nova tentativa resolveu ou repetiu a divergencia anterior.
- A camada de observabilidade agora tambem consolida padroes de reprocessamento por fluxo e por assinatura de falha repetida, aproximando a analise de efetividade do retry parcial.
- A gestao ativa de reprocessamentos agora tambem inclui recuperacao por ambiente, candidatas a automacao e fila pendente/reincidente no overview operacional.
- O control plane persiste status, progresso e eventos operacionais por run.
- Agora tambem permite visualizar sessoes do gateway filtrando por `actor`, `session_id`, `event_type` e texto livre.
- O painel tambem consegue abrir o detalhe de uma sessao, com timeline de eventos auditados e falhas relacionadas no banco.
- A timeline agora marca checkpoints, eventos de atencao e falhas com navegacao direta para o ponto da sequencia associado.
- O detalhe da sessao agora suporta recorte por `seq_global`/`ts_ms` e agrupamento de falhas repetidas para acelerar investigacao.
- A camada operacional agora tem base para relatorio consolidado por run, resumindo falhas por sessao, tipo e severidade.
- O control plane agora tambem agrega uma visao `/observability` com resumo do gateway live, status de runs e falhas recentes no mesmo frontend.
- A API agora tambem consegue comparar uma run com a run pai ou com a ultima execucao equivalente, separando falhas novas, recorrentes e resolvidas.
- A interface de observabilidade agora destaca regressao diretamente nas runs recentes, sem exigir leitura manual de JSON para identificar piora entre execucoes.
- A analise de regressao agora comeca a ficar segmentada por ambiente derivado da run e por `flow_name`, reduzindo a investigacao cega entre execucoes parecidas.
- O relatorio por run agora tambem pode ser exportado em formatos operacionais simples, o que ajuda auditoria, compartilhamento e consolidacao externa.
- A camada operacional agora tambem comeca a consolidar tendencia entre multiplas runs, reduzindo a dependencia de analise manual caso a caso.
- Essa tendencia agora pode ser recortada por ambiente e janela temporal, aproximando a visao operacional de investigacoes reais de degradacao.
- O control plane agora passa a ter base para cenarios analiticos persistidos, o que reduz perda de contexto entre investigacoes operacionais repetidas.
- Esses cenarios passam a admitir compartilhamento controlado e taxonomia simples por tags, aproximando a plataforma do uso colaborativo do time.
- O uso colaborativo fica mais operacional com favoritos por usuario e filtros de descoberta por tag/visibilidade.
- A base agora comeca a separar cenarios analiticos de cenarios operacionais executaveis, aproximando o projeto de um catalogo real de replay e stress.
- Esse catalogo operacional deixa de ser apenas backend e passa a aparecer no control plane principal, encurtando o caminho entre definicao e execucao.
- O uso do catalogo operacional fica mais realista com reaproveitamento de configuracao, filtros de descoberta e preparacao para manutencao recorrente pelo time.
- A UX do catalogo operacional melhora ao separar visualmente replay/stress e permitir observacoes diretamente no cenario salvo.
- O catalogo operacional agora tambem ganha leitura de uso real, com ultimo executor, volume de runs e taxa observada de falha por cenario.
- Esse catalogo agora tambem suporta leitura governavel por executor e janela temporal, com ordenacao por uso e instabilidade para priorizacao operacional.
- A camada de governanca do catalogo operacional agora inclui favoritos por usuario, taxonomia simples por squad/area/tags e score composto de criticidade.
- O catalogo operacional agora tambem passa a carregar dono operacional explicito e limites simples de SLA, permitindo destacar cenarios em violacao no proprio painel.
- A `/observability` passa a consolidar esses SLA no overview operacional, reduzindo a chance de um cenario estourado ficar escondido apenas no catalogo.

### Integridade e auditoria

- Hash-chain + HMAC estao implementados e testados.
- O verificador rejeita adulteracao de log e gaps de sequencia.

### Stress / concorrencia

- Existe suporte a concorrencia por sessao, `concurrency`, `ramp_up_per_sec`, `speed`, `jitter_ms` e `target_user_pool`.
- Metricas de sessoes e checkpoints sao persistidas em `metrics_json`.

### Compatibilidade

- O core usa `Expect/Tcl`, uma escolha adequada para Linux e AIX.
- Os scripts de instalacao/distribuicao seguem abordagem POSIX simples.

## 4. Pontos Incompletos

### Captura fiel

- A captura fiel de producao esta madura no gateway, nao no `record.tcl`.
- O `record.tcl` ainda grava um subconjunto de eventos da engine e nao substitui a trilha auditavel do gateway para migracao.

### Esperado vs observado

- Ate esta evolucao, o replay falhava com mensagem textual; agora ha persistencia estruturada de falhas.
- Ainda falta ampliar a comparacao alem de assinatura de checkpoint, por exemplo com taxonomia funcional mais detalhada e comparacao de evidencia por fluxo.

### Classificacao de falhas

- A base agora persiste `timeout`, `screen_divergence`, `navigation_error`, `concurrency_error`, `integrity_error`, `cancelled` e `technical_error`.
- A classificacao de checkpoint ainda e heuristica; falta calibracao por fluxo de negocio para separar melhor erro funcional, navegacao e ambiente.

### Stress/load orchestration

- Ja existe mecanismo de carga concorrente, mas ainda nao existe catalogo formal de cenarios nem relatorio consolidado por cenario.

### AIX

- A arquitetura considera AIX, mas nao ha suite dedicada de homologacao AIX no repositorio.

## 5. Fragilidades Tecnicas

- `gateway/dakota_gateway/replay.py` e `replay_control.py` validam checkpoints principalmente por assinatura; isso e util, mas pode ser insuficiente para diferencas funcionais sutis.
- O replay depende de SSH/TTY e de assinatura de tela; variacoes de terminal, encoding ou latencia podem gerar falsos desvios.
- O control plane e single-instance in-process; nao ha coordenacao distribuida nem fila externa.
- O banco e SQLite, adequado para MVP operacional, mas com limites claros para escala e multi-instancia pesada.

## 6. Riscos de Migracao

- Confiar apenas em automacao de tela sem trilha auditavel completa pode mascarar divergencias de ordem; o gateway evita isso, mas a documentacao principal ainda nao refletia bem essa centralidade.
- Divergencias que nao alterem fortemente a assinatura podem passar despercebidas se nao houver checkpoints mais ricos.
- Stress concorrente no destino pode produzir falhas intermitentes ainda classificadas de forma generica.

## 7. Lacunas para Cumprir o Objetivo Oficial

Prioridade alta:

- consolidar o posicionamento oficial do projeto na documentacao;
- persistir falhas estruturadas com esperado x observado, severidade e evidencia;
- expor essas falhas na API/UI operacional.

Prioridade media:

- enriquecer classificacao de falhas com taxonomia funcional e de ambiente;
- criar catalogo de cenarios e suites de carga;
- ampliar testes automatizados de replay controlado e falhas.

Prioridade estrategica:

- homologacao Linux completa e matriz operacional AIX;
- observabilidade externa e monitoramento continuo;
- maior robustez de checkpoints e comparadores.

## 8. Priorizacao por Ondas

### Onda 1 - Alinhamento de objetivo e arquitetura

- README principal reposicionado para o objetivo Recital 8 -> Recital 24.
- Diagnostico tecnico consolidado em documento proprio.

### Onda 2 - Captura fiel e preservacao de sequencia

- Base real ja existente no gateway validada.
- Proxima evolucao recomendada: enriquecer metadados de checkpoint por fluxo e estado operacional.

### Onda 3 - Replay sequencial no Recital 24

- Base ja existente e funcional em `strict-global` e `parallel-sessions`.
- Proxima evolucao: maior controle de diferencas toleraveis e resumibilidade fina.

### Onda 4 - Controle detalhado de falhas

- Implementado nesta rodada: persistencia `replay_failures`, API detalhada e resumo por run.
- Proxima evolucao: taxonomia mais ampla e agrupamento semantico de falhas repetidas.

### Onda 5 - Stress test e concorrencia

- Ja ha concorrencia e aceleracao.
- Faltam catalogo, cenarios nomeados e relatorios de benchmark/capacidade.

### Onda 6 - Orquestracao e operacao

- API/UI ja criam, iniciam, pausam, retomam, cancelam e repetem runs.
- Faltam recursos operacionais como catalogo de cenarios e filtros analiticos mais ricos.

### Onda 7 - Portabilidade e robustez final

- Linux esta melhor encaminhado.
- AIX ainda precisa de trilha objetiva de homologacao e limitacoes explicitas por ambiente.

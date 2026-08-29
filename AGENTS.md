# AGENTS.md — Guia para Agentes de Código

Este arquivo orienta agentes de IA (e novos desenvolvedores) que vão trabalhar no
**Dakota Replay2**. Ele descreve o que o projeto é, como está organizado, como
buildar, testar, contribuir e o que **não** fazer. Leia-o por completo antes de
alterar código.

---

## 1. Visão Geral do Projeto

O **Dakota Replay2** é uma plataforma de **validação de migração** do sistema
legado Recital 8 para o Recital 24 (ambiente Dakota). Não é apenas automação de
telas: a base combina captura auditável de sessões de terminal, replay sequencial
com ordem global preservada, verificação de integridade criptográfica, análise de
falhas e teste de estresse.

Objetivos centrais:

- **Capturar** fielmente sessões reais do Recital 8 (via gateway SSH auditável);
- **Reproduzir** no Recital 24 na mesma sequência observada (`strict-global`),
  com checkpoints e rastreabilidade;
- **Registrar falhas estruturadas** (`replay_failures`) com tipo, severidade,
  mensagem e evidência, investigáveis por API/UI;
- **Suportar carga** com `parallel-sessions`, `concurrency`, `speed`,
  `ramp_up_per_sec` e `jitter_ms`;
- **Gerar dados sintéticos e jornadas** a partir da análise do código-fonte
  legado (P2-A — Synthetic Knowledge Base).

Versão atual: ver arquivo `VERSION` (atualmente `0.7.9`). Linux é o alvo
operacional principal; AIX é contemplado no desenho (scripts POSIX, Expect/Tcl)
mas a homologação AIX é item operacional pendente, não capacidade comprovada.

A documentação e os comentários do projeto são em **português (pt-BR)** —
mantenha esse idioma em código novo, comentários e documentação.

---

## 2. Stack Tecnológica

| Camada | Tecnologia | Localização |
|---|---|---|
| Core engine (captura/automação de telas) | Expect/Tcl | `bin/`, `lib/`, `screens/`, `examples/` |
| Gateway (captura auditável, replay, CLI) | Python 3.10+ (stdlib) | `gateway/dakota_gateway/` |
| Terminal engine canônico | Python | `gateway/dakota_terminal/` |
| Control plane (API HTTP + UI operacional) | Python stdlib (`http.server.ThreadingHTTPServer`) + SQLite | `gateway/control/` |
| UI | HTML + CSS/JS vanilla + Tailwind (build via npx) | `gateway/control/templates/`, `gateway/control/static/` |
| Scripts | Shell POSIX (`sh`/`bash`) | `scripts/`, `dev.sh` |
| Build | Shell script → tarball `.tar.gz` | `scripts/build-tarball.sh` |
| Runtime | Processo direto no host (**sem containers**) | — |

Requisitos de ferramentas:

- `python3` (3.10+; CI testa 3.10, 3.11, 3.12) — dependências em
  `gateway/requirements.txt` (flask/bottle/werkzeug declarados, mas o servidor
  HTTP em produção usa stdlib; `watchfiles` só para hot-reload em dev;
  `websocket-client` e `Pillow` usados nos testes de aceitação/visual;
  pylint/flake8/black para qualidade de código);
- `node` >= 18 (testes JS com `node --test` e build do Tailwind);
- `tclsh` + pacote `tcltest`, e `expect` (engine Tcl e testes Tcl);
- cliente `ssh` (cenários de proxy/replay remoto);
- Chromium (apenas para evidência visual de aceitação e testes Selenium).

Não há `pyproject.toml`, `Cargo.toml` ou framework web externo no runtime.
Arquivos de configuração chave na raiz:

- `VERSION` — fonte única da versão (lida por `build-tarball.sh` e `bump.sh`);
- `pytest.ini` — `testpaths = tests gateway/tests`, `pythonpath = gateway`,
  markers: `unit`, `p2`, `control`, `integration`, `slow`, `selenium`, `external`;
- `Makefile` — targets de dev/test/build (ver seção 4);
- `package.json` (raiz) — apenas npm scripts de conveniência (`npm run dev`,
  `npm run test`, ...); `gateway/package.json` — só devDependency `tailwindcss`;
- `tailwind.config.cjs` — content scan em `gateway/control/templates/**`,
  `gateway/control/static/js/**` e `gateway/control/**/*.py`;
- `.github/workflows/ci.yml` — CI (GitHub Actions).

---

## 3. Arquitetura e Organização do Código

### 3.1 Mapa de diretórios

```
replay2/
├── bin/                      # Entrypoints Expect: main.exp, replay2.exp
├── lib/                      # Engine Tcl: capture, normalize, signature,
│                             #   state_machine, control, record, config,
│                             #   action, dump, events, log, plugins
├── screens/                  # Diretório de handlers de telas Tcl (convenção:
│                             #   registram na state machine; hoje só README +
│                             #   plugins.tcldict.txt — demos em examples/)
├── examples/                 # demo.exp + legacy_sim.tcl (simulador local)
├── gateway/
│   ├── dakota-gateway        # Wrapper executável (python3 → dakota_gateway.cli)
│   ├── dakota_gateway/       # Núcleo Python do gateway (ver 3.2)
│   ├── dakota_terminal/      # Engine de terminal canônica (ver 3.3)
│   ├── control/              # Control plane: server.py + routes/ + services/
│   │                         #   + templates/ + static/ (ver 3.4)
│   ├── tests/                # Testes Python do gateway
│   ├── docs/                 # ops.md, threat_model.md
│   ├── state/                # RUNTIME (gitignored): replay.db, captures/
│   └── requirements.txt
├── tests/                    # Suíte principal (Python + Tcl + fixtures)
│   ├── acceptance/           # Testes de aceitação/contrato (fases do release)
│   ├── fixtures/             # terminal_vectors/ (cp850, cp437, utf8, ...) etc.
│   ├── js/acceptance/        # Testes JS de aceitação (payload/playback)
│   ├── oracles/              # Oráculo JS do terminal virtual
│   ├── contracts/            # Contratos de telas
│   └── all.tcl               # Runner tcltest
├── scripts/                  # Utilitários POSIX (build, testes, smoke, deploy)
│   └── acceptance/           # Gates de aceitação run-phase-01..08
├── artifacts/                # Evidências de aceitação (necessárias p/ build)
├── dist/                     # Tarballs gerados (gitignored)
├── docs/                     # Referências Recital, navegação, servidor MIG24
│   └── historico/            # Relatórios congelados da v0.1.0 (GAPS, auditoria, análises)
├── log/                      # Logs locais (gitignored)
├── .local-secrets/           # hmac.key, cookie_secret.key (gitignored)
└── dev/                      # Sandbox de dev (gitignored)
```

### 3.2 `gateway/dakota_gateway/` — núcleo do gateway

- `gateway.py` — proxy SSH auditável; captura `bytes in/out`, `checkpoint`,
  `session_start/end` e eventos `deterministic_input` (tela estável + input);
- `audit_writer.py` — ordem global (`seq_global`), hash-chain, HMAC e manifest;
- `capture_daemon.py` — daemon privilegiado de captura (AF_UNIX, JSON-lines):
  resolve a captura ativa e assina/grava os eventos como usuário de serviço;
- `audit_client.py` — cliente/sink remoto do daemon, usado pelo
  `capture-session` (roda como o usuário SSH final, sem chave HMAC nem DB);
- `crypto.py` — primitivas criptográficas (HMAC, hash-chain);
- `verifier.py` — verificação de integridade da trilha;
- `replay.py` — replay no destino (modos `raw` e `deterministic`);
  `ReplayConfig.term_override` (param `term` da run) vence o TERM gravado no
  `session_start` da captura — terminais com sequências de porta auxiliar
  (ex.: `dk100` do TeraTerm, `ESC[5i`) travam a sessão de replay headless,
  então o replay sintético usa `xterm` por default. Fim de sessão remota
  (slave do PTY fechado após `exit`) é tratado como EOF limpo, sem derrubar
  a run com `OSError EIO`. Desde v0.8.66, runs determinísticas gravam a
  sessão observada (saída real do destino) como trilha auditável assinada
  em `gateway/state/observed_runs/run-<id>/<session_id>/`
  (`ObservedTrailRecorder`; `params.record_observed=0` desliga; falha na
  gravação nunca derruba a run). A trilha é reproduzível via
  `GET /api/runs/{id}/replay` (seek por `seek_seq`, mesma janela da rota de
  captures) e comparável com a captura na página `/runs/{id}/compare`
  (captura × observada lado a lado, com seek direto no ponto da falha);
- `replay_control/` — pacote (decomposto do módulo monolítico em 2026-08-03,
  dívida G2): runner de runs, concorrência, métricas, falhas estruturadas,
  reprocessamento por faixa/sessão/checkpoint. Submódulos: `window.py`
  (helpers de janela/hash/params), `deterministic.py` (comparação
  determinística), `executors.py` (executores strict-global/parallel-sessions/
  concurrent + `LoadTestParams`), `runner.py` (ciclo de vida de runs + classe
  `Runner`) e `__init__.py` (fachada que reexporta toda a superfície do
  módulo antigo);
- `replay_failures.py` / `replay_run_state.py` — taxonomia de falhas e estado
  de runs;
- `screen.py` — normalização e assinatura de tela (fonte central do gateway);
- `canonical.py` — canônicos compartilhados entre camadas;
- `compliance.py` — policies de target (`gateway_required`,
  `direct_ssh_policy`, `capture_start_mode`, `capture_compliance_mode`);
- `auth.py` — autenticação/usuários do control plane;
- `assessment.py` — AI Assessment (análise consolidada do sistema legado);
- `terminal_config.py` — configuração de terminal (geometria, encoding);
- `host_metrics.py` — coletor de recursos do host (CPU/memória/load/disco;
  Linux via `/proc`, AIX via `vmstat`/`lsattr`/`lsps`/`iostat` — disco no AIX
  cobre taxas, IOPS, % tm_act, iowait e latência via `iostat`/`iostat -D`) + `HostMetricsSampler`
  (thread do control plane que grava na tabela `host_metrics`);
- `state_db.py` — **helpers de acesso a SQLite** (`connect`, `now_ms`,
  `query_one`, `query_all`, `exec1`): é a API de persistência de facto, usada
  por todo o control plane (`server.py`, `auth_support.py`, services). O
  schema, o pool de conexões e as migrações vivem em `db/` (`schema.py`,
  `connection.py`, `migrations.py`) — código novo com regras de schema vai em
  `dakota_gateway/db/`. Há também um `schema.py` na raiz do pacote (legado) —
  prefira sempre `db/schema.py`;
- `cli.py` + `cli_commands/` (`catalog.py`, `runtime.py`, `env_profiles.py`) —
  CLI: `start`, `verify`, `replay`, `targets`, `profiles`, `runs`
  (create/start), `user add`, `env-profiles` e `synthetic` com muitos
  subcomandos (`analyze-source`, `screens`, `generate`, `stress`, `journey ...`,
  `schedule ...`, `record`, `explore`, `quickstart`, `pipeline`, `benchmark`,
  `assess`, `knowledge-base`, `export-junit`, `export-csv`, `watch`, `metrics`,
  `diff-quickstart`) e o subcomando top-level `benchmark` (benchmark real AIX ×
  Linux: `create`, `preflight`, `run`, `status`, `compare`, `report`, `import`
  — este último adota no banco experimentos presentes em
  `artifacts/benchmarks/`, mesma rotina do boot do control plane);
- `source_analyzer/` — P2-A Discovery: extratores SQL/ISAM/DBF/Recital, telas,
  menus, CRUD, relacionamentos, catálogo de programas/entidades, auditoria;
- `synthetic/` — P2-A Synthetic: planejador de dataset (grafo de dependências),
  sintetizador de dados, jornadas (inferência, geração CRUD, validação,
  verificação, dry-run), `journey_mix`, scheduler, executor remoto, stress
  runner, explorador de telas, relatórios de evidência/homologação.
  Síntese a partir de captura real: `capture_parametrizer.py` registra a
  posição do cursor de cada input a partir do fluxo bruto OUT (o screen_raw
  da tela estável estaciona o cursor no canto — não serve), funde teclas em
  campos e quebra o campo quando o cursor "teleporta" (auto-avanço sem
  ENTER); `screen_layout.py` extrai os `@ row,col GET` do .prg (inclusive
  labels `SAY fTraduz(...)`) e as grades `dbedit(top,left,...)` (colunas
  `vCam*[n]="campo"` + PICTURE `vPict*` + cabeçalho `vCol*` fTraduz — a
  largura da coluna é max(cabeçalho, PICTURE), separador 1; desempate
  entre grades sobrepostas por compatibilidade valor×PICTURE e célula mais
  estreita; '+' é tecla de confirmação de grade, nunca dado) e
  `capture_knowledge_integrator.py` vincula a
  tela ao fonte pelo código de menu (3.6.1 → est361.prg) + labels
  posicionados e mapeia input→campo por `by_cursor_position` e
  `by_grid_column` (célula de dbedit; PICTURE do
  GET vira constraint de geração do dado sintético e **vence o range
  heurístico** — `journey_synthesizer.synthesize` aplica a PICTURE antes de
  `_value_range_for_field`; original inteiro puro gera `number`, não
  `decimal`; célula com PICTURE de função `@` e original alfanumérico curto
  gera `format="pattern:<original>"`, que o `DatasetBuilder` resolve
  preservando o shape — letra→letra, dígito→dígito). Célula de grade cuja
  tabela de origem é conhecida (`grid_source`) é mapeada por
  `by_grid_source` para a **entidade da tabela da grade** (est361=itens,
  est366=pagamento), não para a entidade da tela — mesmo sem o campo na KB
  (a KB da captura 13 tem est361/est366 sem campos); a entidade da grade
  entra em `entities_involved` e o dataset passa a ser multi-entidade
  (`_first_session_dataset_row` mescla o 1º registro de cada entidade com
  chave prefixada `entidade.campo` + bare, espelhando o `session_data`;
  `_dataset_lookup` resolve o valor pela entidade efetiva do input, com
  fallback bare para datasets antigos). Valores de 1-2 dígitos
  com cara de opção de menu que o cursor não consegue vincular a um GET são
  preservados com evidência explícita: `menu_option_kept` (fora de GET
  conhecido) e `kept_layout_field` (cursor num GET cujo campo não existe na
  entidade da KB — `layout_field` carrega o nome do campo do fonte). O
  de→para (`_build_depara_screens`) lista esses preservados na seção
  "mantidos" das telas com substituições e nomeia cada tela pelo código de
  menu do título gravado (`_screen_display_name`: "3.6.1 PEDIDO E-COMMERCE"),
  não pelo entity_name — que pode ser espúrio (entidade "arq" da KB vem de
  um alias genérico `arq.` de outro programa e atrai telas com campos
  genéricos). Cada campo do de→para carrega `origin` ("formulario" = GET
  clássico, "grade" = célula de dbedit) e, para grades, `grid_source` — a
  tabela real que alimenta o alias temporário da grade, detectada pelo
  `replace ... with <tabela>->` mais frequente na função que define os
  arrays de coluna (est361.prg: itens ← est361, pagamento ← est366); a UI
  mostra o badge "formulário" / "grade · <tabela>" por campo.
  Replay sintético em 1 clique (v0.8.15): `synthetic_trail.py`
  (`build_synthetic_trail`) regrava a trilha da captura com os valores
  substituídos pelos dados sintéticos (respeitando `skip_fields`, que vira
  substituição identidade para preservar a posição do cursor), remove o
  banner pré-sessão e re-assina hash-chain + HMAC. Quando a captura começou
  fora do sistema (preâmbulo de login/shell — ex.: profile quebrado derrubou
  o usuário no shell e ele navegou manualmente até o ERP, capturas 13/62), o
  replay no ambiente são começaria em outro estado (menu wrapper
  auto-iniciado) e a trilha desalinha desde a primeira tecla:
  `detect_session_entry` reconhece o padrão (prompt shell/erro de
  /etc/profile + wrapper antes do runtime Recital subir — `ESC[?7l`), corta
  o preâmbulo (`start_seq`) e deriva das próprias teclas gravadas o
  `entry_preamble` (menu wrapper → shell → ERP, com âncoras de espera),
  gravado nos params da run e executado por `_run_entry_preamble`
  (`replay_control/executors.py`) uma vez por sessão, antes do primeiro
  checkpoint; default ligado no 1-clique (`auto_entry`, `0` desliga). Se a
  âncora final do preamble falha (ex.: o comando de entrada gravado depende
  de artefato que não existe mais no servidor — o `k` da captura 62 roda
  `dbrt ferblo` e o `ferblo.dbo` sumiu, caindo num Confirm de FATAL ERROR),
  o `_run_entry_preamble` drena o Confirm (ENTER quando o tail tem
  "onfirm"), volta ao prompt do shell e dispara o `entry_fallback` derivado
  por `derive_module_entry`: os códigos de menu das telas OUT (3.6.1 → 361 →
  est361.prg) apontam o diretório do módulo sob `source_dir` e a presença de
  `<mod>.dbo`/`config.<mod>` decide o comando (`cd <dados>; dbrt
  <prg_mod>/<mod>` quando o config está no diretório de dados irmão de prg,
  senão `cd <prg_mod>; dbrt <mod>`), com espera pela mesma âncora do
  preamble. O fallback vai em `params.entry_fallback` e na resposta
  `entry_point.fallback` do `POST .../synthetic-replay`. A
  substituição casa o
  valor original como evento único ou como **run de teclas de 1 caractere**
  (campo digitado tecla a tecla — dígitos de máscara e também
  alfanuméricos/decimais de grade, ex.: 'g2511'/'229,9' da captura 13;
  ENTER/ESC/TAB quebram o run): o valor novo é distribuído 1 caractere por
  evento, o último evento carrega o restante quando o valor é mais longo
  (input multi-caractere é válido no replay) e os excedentes ficam vazios
  quando é mais curto; campos-âncora (chave de
  consulta: compõe índice da entidade — parseado do fonte ou lido dos
  arquivos de índice Recital `i<TABELA>.00N` (`index_file_reader.py`: a
  expressão da chave fica em texto claro no primeiro bloco, ex. `rede +
  loja + dtos(data)`; `discover_data_dirs` resolve os diretórios de dados
  via `DAKOTA_DATA_ROOT` (lista separada por `:`/`;`) ou descobre todos os
  irmãos de `source_dir` com índices — cada módulo do legado tem o seu:
  `/dakota11/{cad,est,fin,loj,...}`),
  operação de busca seek/locate/dbseek,
  campo único, `lookup_table` (FK) ou tipo semântico identificador —
  `source_analyzer/semantic_types.py` centraliza `IDENTIFIER_TYPES`/
  `identifies_record`, hoje cpf/cnpj, extensível por declaração) são
  detectados automaticamente por `suggest_key_fields` e mantidos com o
  valor original — o `skip_fields` manual é só para exceções. Campos de
  grade sem entrada na KB (a KB da captura 13/62 tem est361/est366 sem
  campos) também são ancorados quando casam com algum campo das expressões
  de chave lidas dos `i<TABELA>.00N` (`_indexed_field_names` +
  `_matches_indexed`, com casamento por prefixo ≥3 — comb↔combinacao,
  tam↔tamanho): códigos sintéticos de modelo/comb/codigo não existem no
  cadastro de produtos (seek no cad2d1 do est361.prg) e o ERP rejeita o
  item ("Codigo nao cadastrado"), impedindo a persistência do pedido.
  Células numéricas de grade (`is_grid`) têm a magnitude limitada pela
  quantidade de dígitos do valor original (`journey_synthesizer.synthesize`
  — a PICTURE define a largura máxima, mas qtd=7042 ou 4755 parcelas
  quebrariam validações do ERP): qtd "2"→1..9, valor "229,9"→≤999,99; e o
  float gerado é formatado com o MESMO nº de casas decimais do original
  (`_format_synthetic_value` em `capture_synthesis_service.py` — "229,9"
  (1 casa) → "763,0", não "763,05": o GET do Recital com PICTURE de 2 casas
  não comita valor de 1 casa no ENTER e a grade de pagamento fica pendente,
  desalinhando a sequência de ESCs do replay — captura 62, run 40); a
  rota
  `POST /api/captures/{id}/synthetic-replay` (botão "Replay sintético" no
  detalhe da captura, `capture_synthesis_service.start_synthetic_replay`)
  encadeia síntese → trilha → run real determinística `send-anyway`. Runs
  sintéticas carregam `params.synthetic=true` + `source_capture_id` — a UI
  exibe o badge "sintético • captura #N" (lista e detalhe da run,
  `run_views.runSyntheticBadgeHtml`), a lista de Execuções tem filtro de
  origem (todas/sintéticas/reais) e o detalhe da captura lista as runs
  geradas dela (`GET /api/captures/{id}/runs`,
  `run_service.list_capture_runs_payload` — o params_json é parseado em
  Python, a captura não tem FK para as runs). Nessas runs, checkpoint não
  estabilizado com tela observada é classificado `screen_divergence`/
  `medium` (divergência de conteúdo esperada), não `timeout`/`high`, e a
  assinatura do grupo de falhas não inclui o `observed_value` (muda a cada
  sessão) para a comparação entre runs reconhecer recorrência. A comparação
  determinística tolera o eco da tecla recém-enviada (a tela esperada é a
  estável ANTES do input — `apply_input_echo_fallback` em
  `replay_compare.py`, alimentado pela janela de `recent_keys` dos
  executores) e rebaixa para `low` divergências cuja tela de referência da
  captura está envelhecida (`stale_reference_override` em
  `replay_control/deterministic.py`: `screen_snapshot_age_ms` ≥ 10s E telas
  sem nenhuma linha em comum — divergência de contexto, não funcional) e, sem
  exigência de idade, mudanças de contexto app ↔ shell
  (`context_switch_override`: telas disjuntas + prompt ksh/`not found` em um
  dos lados) e avanços além da referência (`content_present_override`: toda
  linha não-vazia da tela esperada presente na observada, verbatim ou como
  prefixo com eco ≥ 4 chars — eco/rolagem, a sessão avançou sem divergir). Nas
  runs sintéticas, a divergência explicada pelo de→para vira
  `synthetic_data_swap`/`low` (`replay_compare.py`): placeholder de par longo
  só conta como eco quando presente nas DUAS linhas (só na esperada = campo
  ausente na observada: divergência estrutural, não troca — regressão da
  run 40, em que a linha da grade do item casava com a linha de menu
  "0. Finalizacao"). A
  falha de checkpoint de um `deterministic_input` é registrada UMA vez só
  (o `wait_checkpoint` do strict-global recebe `record_failure=False`; o
  registro definitivo com a ação skip/send-anyway é o do
  `_deterministic_failure` no except). A trilha
  sintética grava o manifest `trail/de-para.json` (original → sintético por
  tela, com os mantidos marcados); a página de replay da sessão exibe o
  badge "sintético • captura #N" com link para o replay de origem e o botão
  "De→para por tela" (modal alimentado por
  `GET /api/captures/{id}/synthetic-substitutions?log_dir=...`,
  `capture_synthesis_service.synthetic_substitutions_payload` — lê o
  manifest ou reconstrói de `report.json` + `dataset.jsonl` recalculando
  os campos-âncora na KB, para trilhas antigas). O "Manter originais
  (replay)" do detalhe da captura é um dropdown multi-select dos campos da
  trilha agrupados por tela (componente `skip_fields_select.js`), alimentado
  por `GET /api/captures/{id}/synthetic-fields?source_dir=...`
  (`capture_synthesis_service.synthetic_fields_payload` — reusa o
  `report.json` da síntese mais recente ou parametriza a captura na hora com
  KB + índices; chaves de consulta vêm marcadas e desabilitadas). O painel
  não tem mais "semente": o select "Dados" escolhe
  `variation=synthetic` (cada sessão com dados diferentes — default) ou
  `variation=equal` (todas com os mesmos dados, 1ª linha do dataset —
  `journey_synthesizer.synthesize`). O "Gerar" (`POST
  /api/captures/{id}/synthesize`) retorna `depara` (original → sintético da
  1ª sessão, via `_build_depara_screens` sobre `screen_mappings` + 1ª linha
  do dataset) e a UI abre automaticamente o modal "De→para por tela"
  formatado (mesmo visual do modal da página de replay), reabrível pelo
  botão "Ver de→para por tela" no bloco de resultado.
  Fluxo Synthetic → Replay real (X5): `POST /api/synthetic/stress/real` →
  `control/services/synthetic_replay_service.py` → `replay_adapter.py`
  materializa a trilha auditável (hash-chain + HMAC) e cria run real via
  `run_service.create_run_request_payload` + `Runner.start_run_async`;
- `benchmark/` — pacote de benchmark (AIX vs Linux);
- `templates/` — templates internos do gateway.

### 3.3 `gateway/dakota_terminal/` — terminal engine canônica

Desde v0.3.19, o **TerminalEngine Python é a fonte única oficial** de emulação
de terminal (parser ANSI/UTF-8, geometria, snapshots, `text_sig`/`visual_sig`).
Módulos: `parser.py`, `decoder.py`, `engine.py`, `model.py`, `geometry.py`,
`attributes.py`, `snapshot.py`, `serializer.py`, `signatures.py`,
`comparison.py`, `diffs.py`. O JS de produção **não** contém parser de
terminal — isso é garantido pelo teste
`production_no_terminal_parser.test.mjs`. Vetores de decodificação vivem em
`tests/fixtures/terminal_vectors/`.

### 3.4 `gateway/control/` — control plane (superfície oficial)

- `server.py` — entry point HTTP (stdlib `ThreadingHTTPServer`), shell leve
  (~900 linhas): auth/cookies, helpers e despacho;
- `routes/` — acoplamento HTTP por domínio (`run_routes`, `capture_routes`,
  `gateway_routes`, `observability_routes`, `catalog_routes`,
  `operational_routes`, `journey_routes`, `synthetic_routes`,
  `benchmark_routes` (benchmark real §21), `ui_routes`,
  `admin_routes`);
- `services/` — regras e payloads reutilizáveis (reports, cenários, captura,
  sessão/replay, observabilidade, analytics, ambiente). Inclui
  `replay_state_cache.py` — cache em disco de estados da TerminalEngine
  (janela profunda de replay em sessões enormes, dívida X6; kill-switch
  `REPLAY_STATE_CACHE=0`; estado gravado a cada 1000 eventos bytes, ou a
  cada 100 em sessões de até 5000 eventos — intervalo dinâmico v0.8.67) e `session_index_cache.py` — índice em disco dos
  eventos da sessão (tipo/seq/arquivo/offset + direção/tamanho decodificado
  dos "bytes"): a janela de replay é materializada por seek e os totais de
  playback saem de somas de arrays, sem reparsear os audit-*.jsonl a cada
  request (kill-switch `REPLAY_SESSION_INDEX=0`). Ambos são ligados
  automaticamente acima de `MAX_FULL_REPLAY_EVENTS` (20000) ou quando a
  request vem com `stream=1` — a página de replay da captura marca assim
  as janelas sequenciais do player/timeline, senão cada janela reprocessa
  do evento 0 e o player fica faminto (v0.8.63). O pacing do player em si
  (lote por frame, delays proporcionais ao timestamp, piso sensível à
  velocidade) vive em `static/js/components/playback_pacing.js`. O
  `replay_state_cache.py` também fornece o janitor de caches órfãos
  (`cleanup_orphan_caches` + `CacheJanitor`, thread ligada no boot do
  control plane: kill-switch `REPLAY_CACHE_JANITOR=0`, intervalo
  `REPLAY_CACHE_JANITOR_INTERVAL_S` default 3600) e o serviço de replay
  aceita `abort_check` para cancelar o processamento quando o cliente
  abandona a request (sonda a cada 64 linhas/eventos). Inclui também
  `synthetic_replay_service.py` — fluxo Synthetic → Replay real (dívida X5):
  materializa a jornada como trilha auditável efêmera e dispara run real
  via replay_control. O `benchmark_service.py` também faz a adoção de
  experimentos de benchmark no boot do control plane
  (`import_experiments_from_artifacts`): experimentos cujos artefatos vieram
  no tarball (`artifacts/benchmarks/`, §33) são registrados em
  `benchmark_experiments`/`benchmark_runs` de forma idempotente — sem isso a
  lista `/api/benchmarks` ficava vazia após deploy (v0.8.8);
- Módulos de suporte na raiz de `control/`: `auth_support.py`,
  `server_support.py`, `audit_scan_support.py`, `engineering_route_support.py`,
  `error_middleware.py`, `page_state_builders.py`, `runtime_supervision.py`,
  `websocket_support.py`;
- `ui_templates.py` — loader fino; `templates/` — HTMLs da UI;
- `static/js/` — JS vanilla (`core/`, `components/`, `pages/`, `vendor/`);
  testes `*.test.mjs` ao lado dos módulos;
- `openapi.yaml`, `synthetic-openapi.yaml` — contratos de API.

Padrão arquitetural do control plane: `server.py` despacha → `routes/` parseiam
HTTP → `services/` executam regras → `dakota_gateway/db/` persiste. **Não
inflar `server.py`**: rota nova vai em `routes/`, regra nova em `services/`.

Semântica visual da UI (convenção obrigatória): rose/pink = identidade/CTA;
emerald = sucesso/running; amber = queued/warning; red = erro/falha; neutral =
inativo/desabilitado.

### 3.5 Engine Tcl (`bin/`, `lib/`, `screens/`)

`bin/main.exp` é o loop principal: captura incremental (`lib/capture.tcl`),
normalização (`lib/normalize.tcl`), assinatura estável (`lib/signature.tcl`),
roteamento por estado (`lib/state_machine.tcl`), controle local
`pause/resume/step/send/dump` (`lib/control.tcl`) e gravação simplificada
(`lib/record.tcl`). Handlers de tela são módulos `.tcl` em `screens/` que se
registram via `::state_machine::register <assinatura> <estado> <proc>`
(`bin/main.exp` carrega os handlers via `::plugins::load_screens` de
`lib/plugins.tcl`, filtrados pelo estado em `screens/plugins.tcldict.txt`; o
diretório hoje só tem a convenção documentada — handlers de exemplo vivem em
`examples/`). Todo
entrypoint Tcl executa `encoding system utf-8` **antes** de qualquer `source`
(regra P0).

Atenção: a captura fiel consolidada é a do **gateway SSH**; `record.tcl` é um
gravador simplificado da engine e não substitui a trilha auditável.

---

## 4. Comandos de Build, Dev e Testes

### Setup e ambiente de desenvolvimento

```bash
make setup                 # cria .venv e instala deps (flask, bottle, werkzeug, watchfiles, pytest)
make dev                   # = ./dev.sh — sobe control server em http://127.0.0.1:8090
                           #   com hot-reload (watchfiles), admin padrão admin:Admin123!
make dev-stop / dev-logs   # para o servidor / tail -f log/replay2-control.log
```

Equivamente: `npm run dev`, `npm run setup`, etc. (npm scripts espelham o make).

Variáveis de ambiente de dev: `LISTEN` (default `127.0.0.1:8090`), `DB_PATH`
(default `gateway/state/replay.db`), `DAKOTA_ENV` (`lab` default |
`homologation` | `production`), `DAKOTA_ADMIN` (`user:senha` p/ bootstrap),
`SECRETS_DIR`, `COOKIE_SECRET_FILE`, `HMAC_KEY_FILE`, `WATCH_MODE` (0 desliga
hot-reload), `HOST_METRICS_ENABLED` (0 desliga o sampler de recursos do host),
`HOST_METRICS_INTERVAL_S` (default `5`), `HOST_METRICS_RETENTION_DAYS`
(default `7`). `DAKOTA_RATE_LIMIT_RPM`
(default `600`) e `DAKOTA_RATE_LIMIT` (`0` desliga) controlam o rate limiting por IP
de `/api/*` (`gateway/control/rate_limit.py`); `/api/login` tem throttle
próprio mais estrito em `admin_routes.py` e não passa pelo limiter genérico. O `dev.sh` gera os segredos em `.local-secrets/` se ausentes e
sobe o servidor com `--gateway-auto-activate`.

Execução manual do control plane:

```bash
python3 gateway/control/server.py \
  --listen 127.0.0.1:8090 \
  --db gateway/state/replay.db \
  --cookie-secret-file .local-secrets/cookie_secret.key \
  --hmac-key-file .local-secrets/hmac.key \
  --bootstrap-admin 'admin:Admin123!'
```

### Testes

Orquestrador principal: `./scripts/test.sh` (documentação completa em `TESTES.md`).

```bash
./scripts/test.sh --quick        # JS apenas — loop de dev
./scripts/test.sh --unit         # JS + Python + Tcl — antes de commit
./scripts/test.sh --all          # tudo (default)
./scripts/test.sh --ci           # tudo menos Tcl
./scripts/test.sh --js|--python|--tcl            # suítes individuais
./scripts/test.sh --capture --replay             # foco em captura/replay
./scripts/test.sh --smoke --remote --host 10.5.8.24 --port 8080
# modificadores: --verbose, --fail-fast
# DAKOTA_TEST_SH_TIMEOUT=450 (timeout por bloco), DAKOTA_TEST_SH_DRY_RUN=1
```

Por camada:

```bash
# Python (pytest.ini já define pythonpath=gateway e testpaths)
python3 -m pytest tests/ gateway/tests/ -q
python3 -m pytest -m "not slow and not selenium and not external" -q
python3 -m pytest -m p2 -q                      # P2-A Knowledge Base

# JavaScript (node:test, 7 arquivos oficiais listados em scripts/test.sh)
node --test gateway/control/static/js/virtual_terminal.test.mjs
node --test gateway/control/static/js/components/capture_replay_timeline.test.mjs
# ... + terminal_snapshot_renderer, replay_snapshot_state, checkpoint_seek,
#     template_syntax, production_no_terminal_parser

# Tcl
tclsh tests/all.tcl

# Make
make test          # subconjunto principal + gateway/tests
make test-all      # compileall + pytest (sem selenium) + syntax check Tcl
make test-p2       # testes P2-A
make check         # compileall + smoke + build check
make smoke-test    # scripts/smoke-test.sh (9 checks end-to-end locais)
```

Smoke remoto (requer acesso SSH ao host): `scripts/smoke-test-capture.sh` e
`scripts/smoke-test-replay.sh` (wrappers de `smoke-test-capture.py` /
`smoke-test-replay.py`) validam health/ready, login, captures, replay,
geometria, encoding, timeline e playback contra o servidor (default
`10.5.8.24:8080`).

Scripts auxiliares de teste em `scripts/`: `test-fast.sh`, `test-all.sh`,
`test-p2.sh`, `test-best-effort.sh`, `validate_acceptance_results.py`,
`process_tree.py` (runner com detecção de processos vazados, usado pelo
`test.sh`).

### Build e release

```bash
bash scripts/final-acceptance.sh   # pipeline de aceitação completo (fases 01–08);
                                   #   gera artifacts/ exigidos pelo build
./scripts/build-tarball.sh         # gera dist/dakota-replay2-<VERSION>-<ts>.tar.gz
bash scripts/build-selfinstall.sh  # gera dist/...run — self-installing archive
                                   #   (stub selfinstall-stub.sh + tarball); no
                                   #   servidor: `sh <pkg>.run` instala ou
                                   #   atualiza (stop→backup db→overlay→perms→
                                   #   restart+health). Opções: --build, --tarball
make tailwind                      # rebuilda gateway/control/static/tailwind.css
bash scripts/bump.sh [patch|minor|major]   # incrementa VERSION
```

**Importante:** `build-tarball.sh` **falha** se os artefatos de aceitação em
`artifacts/` não existirem — rode `scripts/final-acceptance.sh` antes. O build
remove automaticamente do artefato: segredos (`*.key`, `*.pem`, `.env*`, chaves
SSH), bancos (`*.db*`, `*.sqlite*`), `gateway/state/`, `__pycache__`, `.venv`,
`node_modules`, `dist/`, `log/`. Quando existe, `artifacts/benchmarks/`
(evidência do benchmark real AIX×Linux: contrato, runs, agregados, relatório)
é incluído no pacote. Ver `CHECKLIST_EMPACOTAMENTO.md` para a
verificação pós-build e o processo de release completo (build → copiar para
`remoto_dakota/artifacts/` → homologação → `git tag v$(cat VERSION)`).

### Instalação/deploy

```bash
./install.sh [--prefix /opt/dakota-replay2] [--no-deps] [--link-dir /usr/local/bin] [--force]
```

Instala em `/opt/dakota-replay2` (default), cria os wrappers `replay2` e
`dakota-gateway`, instala `expect`/`tcl` via apt/dnf/yum/zypper (Linux) ou AIX
Toolbox. Servidor de homologação/produção documentado: MIG24 AIX 7
(`10.5.8.25`, ver `docs/servidor-dakota-mig24.md`). Operação do gateway
(rotação, verificação, replay local de smoke): `gateway/docs/ops.md`.
`uninstall.sh` remove a instalação.

### CI

`.github/workflows/ci.yml`: matrix Python 3.10–3.12, instala Tcl/Expect, roda
`tclsh tests/all.tcl`, `gateway/tests/test_integrity.py`,
`tests/quick-test-api.py`, `tests/benchmark.tcl`, lint (pylint/flake8 com
`--exit-zero`), coverage (`gateway/tests/test_integrity.py`) e Selenium
(`continue-on-error`).

---

## 5. Convenções de Código

De `CONTRIBUTING.md` + prática observada:

### Python
- PEP 8; **docstrings em português**;
- Type hints com `from __future__ import annotations` no topo dos módulos;
- Ordem de imports: stdlib → third-party → `dakota_gateway` → `control`;
- Persistência nova em `dakota_gateway/db/` (não adicionar a `state_db.py`,
  que é shim legado);
- Rotas novas em `gateway/control/routes/`, regras em `services/` — manter
  `server.py` enxuto.

### Paridade UI × CLI (princípio obrigatório)
- Todo recurso operacional deve existir **sempre via UI** — o usuário final é
  leigo e não pode depender de terminal;
- CLI é bem-vinda e desejada para os mesmos recursos (automação, servidores
  headless, homologação), mas **nunca como substituto da UI**;
- Regra nova de UI sem equivalente CLI é aceitável; recurso "só CLI" para
  operação de usuário final, não — nesse caso a UI é obrigatória.

### Shell
- POSIX compatível (sem bash-isms nos scripts de produção; `test.sh`/`dev.sh`
  usam bash deliberadamente);
- `set -eu` (ou `set -euo pipefail` em bash);
- Variáveis com fallback: `${VAR:-default}`.

### Tcl
- `encoding system utf-8` antes de qualquer `source` (regra de segurança P0);
- Compatível com Linux e AIX (tcltest).

### JavaScript/UI
- Vanilla JS, módulos `.js`/`.cjs`; testes `.test.mjs` com `node:test`;
- Não reintroduzir parser de terminal no JS de produção (Python é a fonte
  canônica);
- Respeitar a semântica de cores da UI (seção 3.4); após alterar templates/JS,
  rebuildar o CSS com `make tailwind`.

### Fluxo de PR
1. Branch `feature/nome-da-feature`;
2. Implementar + testar (`make test` / `./scripts/test.sh --unit`);
3. PR para `develop`;
4. Squash merge.

---

## 6. Segurança

- **Segredos locais**: `.local-secrets/hmac.key` e `cookie_secret.key` (gerados
  pelo `dev.sh`); em operação, `/etc/dakota-gateway/` com `0600`. Nunca
  commitar — `.gitignore` já cobre `*.key`, `*.pem`, `.env*`, chaves SSH,
  `*.db*`, `.local-secrets/`, `gateway/state/`.
- **Trilha auditável**: o gateway grava eventos com `seq_global`/`seq_session`/
  `ts_ms`, hash-chain e HMAC. Sempre rodar `verify` antes de replay/migração.
  Não apontar `verify`/`replay` para diretório misto de capturas (eventos
  passivos de porta 22 não compartilham a cadeia HMAC da sessão PTY).
- **Capture daemon**: em operação, a escrita auditável é do `capture-daemon`
  (usuário de serviço) via socket Unix `gateway/state/daemon/capture.sock`;
  o `capture-session` (ForceCommand, usuário SSH final) só envia eventos.
  Chave HMAC `0600` e `replay.db` `0660`, sem depender do grupo do usuário.
  Fallback local (daemon fora) só com `--hmac-key-file`; senão fail-closed.
- **Bootstrap admin**: preferir `DAKOTA_ADMIN` a `--bootstrap-admin` (o argumento
  expõe senha em histórico de shell e process list). Em `DAKOTA_ENV=production`,
  `DAKOTA_ADMIN` é **obrigatório** e o servidor aborta sem ele.
- **Cookies**: `HttpOnly`, `SameSite=Lax`, `Secure` em produção; `/metrics` com
  autenticação em produção.
- **Credenciais de destino**: usar `credential_ref` (ex.: `env:VAR`) nos
  connection profiles — nunca gravar segredo bruto no banco.
- **Gateway-only**: targets com `gateway_required=true` exigem evidência de
  entrada via gateway (`entry_mode`, `via_gateway`, `gateway_session_id`);
  `capture_compliance_mode=strict` bloqueia start de runs não conformes.
- **Build**: o tarball é higienizado pelo `build-tarball.sh`; confira com o
  checklist de `CHECKLIST_EMPACOTAMENTO.md` antes de distribuir.
- **`scripts/install-local-ssh-capture.sh`** altera o `sshd_config` do sistema
  (instala `ForceCommand` para rotear SSH pelo gateway) — mudança fora do
  diretório do projeto; só executar com intenção explícita. Desfazer com
  `scripts/uninstall-local-ssh-capture.sh`.
- Hosts internos (`10.5.8.24`, `10.5.8.25`) aparecem em docs/scripts de smoke;
  não introduzir novos hosts/segredos hard-coded em código commitado.

---

## 7. Fronteiras Arquiteturais (o que NÃO fazer)

De `FRONTEIRAS.md` e `CONTRIBUTING.md`:

- ❌ Prometheus / Grafana / OpenTelemetry / observabilidade externa
- ❌ PostgreSQL ou outro banco — **SQLite apenas**
- ❌ Docker / Kubernetes / containers — processo direto no host
- ❌ Multi-tenancy (`tenant`, `tenant_id`)
- ❌ Monitoramento de infra **externa** (`host_status`, `service_check`,
  probes de outros hosts) — isso é do projeto separado `r-observe/`.
  Exceção aprovada: o replay2 coleta métricas de recursos do **próprio host**
  (`host_metrics.py`, painel `/observability/resources`) para correlação com
  runs de estresse e comparação entre ambientes (ver FRONTEIRAS.md)
- ❌ Portas 3000/3001/9090 (stack r-observe); control plane usa 8090 (dev) /
  8080 (produção)
- ❌ Misturar com os projetos irmãos: `remoto_dakota/` (camada operacional de
  deploy/healthcheck) e `r-observe/` (observabilidade de infra externa)

O que **faz** sentido evoluir: Discovery Engine (`source_analyzer/`),
Synthetic Engine (`synthetic/`), replay determinístico, métricas internas via
`/metrics`, endpoints REST na API existente, `/health` e `/ready`.

---

## 8. Estratégia de Testes (resumo)

Pirâmide documentada em `TESTES.md`:

```
Smoke (remoto)        → scripts/smoke-test-*.sh  (requer SSH)
Integração (HTTP)     → gateway/tests/test_ui_routes.py
Unitários (Py+JS+Tcl) → tests/ + gateway/tests/ + static/js/*.test.mjs + tests/all.tcl
```

- Ao corrigir bug: escreva o teste de regressão **antes** da correção
  (fluxo em `TESTES.md`, seção "Fluxo de Desenvolvimento");
- Novo módulo Python → teste em `tests/test_<nome>_unit.py` (ou
  `gateway/tests/` se for específico do gateway);
- Contratos de tela/terminal: `tests/test_screen_contracts.py`,
  `tests/test_dakota_terminal_canonical.py`, fixtures em
  `tests/fixtures/terminal_vectors/`;
- Aceitação/release: `scripts/acceptance/run-phase-01..08-*.sh` orquestradas
  por `scripts/final-acceptance.sh`; resultados em `artifacts/` (baseline
  `acceptance-test-baseline.sha256` — alterações em arquivos de teste de
  aceitação exigem regerar a baseline via pipeline completo);
- Gaps de cobertura conhecidos estão listados em `TESTES.md` (seção "Gaps
  Conhecidos") — consulte antes de assumir que algo já é testado.

## 8.5. Deploy no Servidor (REGRA OBRIGATÓRIA)

**Sempre usar o script de deploy/instalador. NUNCA fazer deploy manual com `scp`/`ssh` soltos.**

### Deploy no MIG24 (AIX 10.5.8.25):
```bash
cd /home/jmachado/projetos/dakota/remoto_dakota
bash scripts/deploy.sh --target aix
```
O deploy AIX usa o **self-installing archive** (`.run`): o script gera o
instalador em `dist/`, copia via scp e executa `sh <pkg>.run --prefix
/opt/dakota/replay2` no servidor. O instalador para os serviços, faz backup do
banco, sobrepõe o código, corrige permissões, atualiza o wrapper SSH, reinicia
e faz health check.

Configuração operacional persistente (v0.8.12): o start do control plane
carrega `$PREFIX/gateway/control.env` se existir — é o lugar de variáveis do
servidor como `DAKOTA_SOURCE_ROOT=/dakota11/prg` (sem ela os endpoints
synthetic respondem 500 em produção). Opcional: `DAKOTA_DATA_ROOT` aponta os
diretórios de dados (lista separada por `:`) de onde o `index_file_reader`
lê as chaves dos índices Recital — sem ela, os irmãos de `source_dir` com
índices são descobertos automaticamente. O arquivo é do servidor (não vem no
tarball, como `.local-secrets/`) e sobrevive a deploys.

Manualmente (sem o deploy.sh), o fluxo equivalente é:
```bash
cd /home/jmachado/projetos/dakota/replay2
bash scripts/build-selfinstall.sh
scp dist/dakota-replay2-<VERSAO>-<ts>.run root@10.5.8.25:/tmp/
ssh root@10.5.8.25 "sh /tmp/dakota-replay2-<VERSAO>-<ts>.run --prefix /opt/dakota/replay2 && rm -f /tmp/dakota-replay2-<VERSAO>-<ts>.run"
```

### Deploy no Linux (10.5.8.24):
```bash
SSH_PASSWORD="$SSH_PASSWORD" bash scripts/deploy.sh --target linux
```
O deploy Linux usa o mesmo **self-installing archive** (`.run`) do AIX
(homologado na 0.8.9; o stub destaca stdin/stdout/stderr dos `su` de
`start_services` para não travar a sessão SSH do deploy).

O script cuida de: build do tarball/instalador, backup do banco, parada do
serviço, sincronização, chown, restart e health check.

### Hotfix (apenas emergência, 1-2 arquivos):
```bash
cd replay2
for f in gateway/control/services/arquivo.py gateway/control/templates/algum.html; do
  scp -o StrictHostKeyChecking=accept-new "$f" root@10.5.8.25:/opt/dakota/replay2/"$f"
done
ssh dakota-mig24-root "chown -R results:cpd /opt/dakota/replay2/gateway/ && pkill -f server.py; sleep 2; cd /opt/dakota/replay2/gateway && su results -c '...'"
```
Hotfixes devem ser seguidos de deploy completo via `deploy.sh` na próxima oportunidade.

## 9. Lacunas Conhecidas (não tratar como bug novo)

- `record.tcl` é gravador simplificado; a captura oficial é o gateway SSH;
- Taxonomia de falhas (`timeout`, `screen_divergence`, `navigation_error`,
  `concurrency_error`) ainda é heurística e pendente de refinamento por fluxo
  (exceção já refinada: run sintética `send-anyway` classifica checkpoint
  não estabilizado com tela observada como `screen_divergence`/`medium`);
- Não existe catálogo formal de cenários de carga;
- Telnet suportado na camada de replay, mas autenticação automática prefere SSH;
- Portabilidade AIX pendente de homologação operacional dedicada.

## 10. Documentação de Referência

- `README.md` — visão funcional completa, API e CLI
- `TESTES.md` — catálogo e fluxo de testes
- `DESENVOLVIMENTO.md` — guia de dev (setup, env vars, troubleshooting)
- `CONTRIBUTING.md` — stack, convenções, fluxo de PR
- `FRONTEIRAS.md` — fronteiras arquiteturais
- `CHECKLIST_EMPACOTAMENTO.md` — release e exclusões do artefato
- `gateway/README.md` — gateway, deterministic record, replay local
- `gateway/docs/ops.md` — operação (rotação, verificação, replay)
- `gateway/docs/threat_model.md` — modelo de ameaças
- `ROADMAP.md`, `DEBT_MAP.md` — planejamento e dívida técnica
- `docs/` — referências do sistema Recital/Dakota e do servidor MIG24;
  `docs/historico/` — relatórios congelados da v0.1.0 (GAPS, auditoria,
  análises), mantidos só como referência histórica

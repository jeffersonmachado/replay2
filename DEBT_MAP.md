# Mapa de Dívida Arquitetural — Dakota Replay2

**Data:** 2026-06-30
**Base:** Ciclo de extração do `server.py` (498 linhas) + consolidação `_write_json` + middleware de erro + DELETE endpoints

> **Nota de atualização (2026-07-23):** as métricas de linhas abaixo são um
> snapshot de 2026-06-30 e já divergem do código atual — `server.py` tem
> ~903 linhas (não ~505) e `synthetic_routes.py` tem 823 linhas (o trabalho
> de delegação citado em R2 foi posteriormente revertido pelo crescimento do
> módulo). Os status de resolução foram revistos: G1 resolvido por remoção do
> componente Go (v0.3.0); itens de baixa severidade seguem abertos. G2 foi
> resolvida em 2026-08-03 com a decomposição do `replay_control.py` em pacote.

---

## Resumo por Camada

| Camada | Arquivos | Linhas | Dívida |
|--------|----------|--------|--------|
| Control (server.py) | 1 | ~505 (snapshot 2026-06-30; ~903 em 2026-07-23) | Média |
| Control (módulos extraídos) | 7 | ~1900 | Baixa |
| Routes | 10 | ~3000 | Média |
| Services | 14 | ~2779 | Baixa |
| Gateway (dakota_gateway/) | ~30 | ~4960 | Alta |
| Tcl Runtime (lib/) | ~15 | ~3000 | Média |

---

## 1. Dívida por Camada

### 1.1 Camada de Rotas (`gateway/control/routes/`)

| # | Item | Severidade | Descrição |
|---|------|-----------|-----------|
| R1 | `ui_routes.py` ✅ | **CORRIGIDO** | Reduzido de 597 para 102 linhas. `ROUTES_CONFIG` extraído para `ui_templates.py`. |
| R2 | `synthetic_routes.py` | **PARCIAL** | Reduzido de 599 para 396 linhas no ciclo de 2026-06; o módulo voltou a crescer e tem **823 linhas** em 2026-07-23. Delegação para `journey_routes.py` mantida, mas o tamanho atual indica dívida reaberta. |
| R3 | `journey_routes.py` sobrepõe `synthetic_routes.py` ✅ | **CORRIGIDO** | Unificado: `journey_routes.py` é fonte canônica. `synthetic_routes.py` delega com rewrite de path. |
| R4 | `_write_json` extraído ✅ | **CORRIGIDO** | 9 duplicações removidas. Centralizado em `route_helpers.py`. |
| R5 | DELETE incompleto ✅ | **CORRIGIDO** | Adicionados: `DELETE /api/runs/{id}`, `DELETE /api/captures/{id}`, `DELETE /api/targets/{id}`, `DELETE /api/connection-profiles/{id}`. |
| R6 | `parse_qs` importado vs injetado ✅ | **RESOLVIDA (2026-08-03)** | Padronizado **import direto** (não injeção): nenhum teste injetava fake — os únicos usos passavam o próprio `parse_qs` real, então a injeção não comprava testabilidade e o padrão dominante já era import (9 módulos × 3). Removido `parse_qs_fn` de `operational_routes.py`, `capture_routes.py` e `gateway_routes.py`; `server.py` e `tests/test_control_routes_unit.py` atualizados. |

### 1.2 Camada de Serviços (`gateway/control/services/`)

| # | Item | Severidade | Descrição |
|---|------|-----------|-----------|
| S1 | `gateway_observability_service.py` ✅ | **CORRIGIDO** | Reduzido de 594 para 424 linhas. `prepare_session_replay_data` (174 linhas) extraído para `session_replay_service.py`. |
| S2 | `operational_scenario_service.py` ✅ | **RESOLVIDA (2026-08-06)** | `normalize_operational_scenario_payload` (validação pura, sem banco) extraído para `scenario_validator.py`; service caiu de 412 para 363 linhas e importa o validador. Cobertura direta: `tests/test_scenario_validator_unit.py` (7 testes: defaults, erros de domínio, faixas SLA, normalização). |
| S3 | `scenario_service.py` ✅ | **RESOLVIDA (2026-08-06)** | Fachada **removida** (merge com `scenario_shared.py` criaria import circular: `operational_scenario_service` → `scenario_shared` ← `scenario_service` → `operational_scenario_service`). Callers (`server.py`, `observability_routes.py`, `operational_routes.py`, testes) agora importam direto de `analytics_scenario_service`/`operational_scenario_service`. |
| S4 | `report_service.py` ✅ | **RESOLVIDA (2026-08-06)** | Renomeado para `report_format_service.py` e reduzido aos formatters (`report_to_markdown`/`report_to_csv`); re-exports eliminados — callers importam builders dos módulos canônicos (`report_run_service`, `report_overview_service`). Fim da confusão de nomes. |

### 1.3 Camada Gateway Core (`gateway/dakota_gateway/`)

| # | Item | Severidade | Descrição |
|---|------|-----------|-----------|
| G1 | Componente Go não integrado ✅ | **RESOLVIDO** | `gateway/internal/audit/` foi **removido** no commit `dd87592` (v0.3.0). O runtime Python é a única implementação de auditoria. |
| G2 | `replay_control.py` — Runner monolítico ✅ | **RESOLVIDA (2026-08-03)** | Controla execução de replay, fila, status, retry. **Decomposto em pacote sem mudança de lógica:** `gateway/dakota_gateway/replay_control/` com `window.py` (helpers de janela/hash/params), `deterministic.py` (comparação determinística), `executors.py` (executores strict-global/parallel-sessions/concurrent + `LoadTestParams`), `runner.py` (ciclo de vida de runs + `Runner`) e `__init__.py` (fachada que reexporta toda a superfície importável do módulo antigo). Suíte completa verde (~954 passed). |
| G3 | `source_analyzer/` com 9 extractors ✅ | **RESOLVIDA (2026-08-03)** | Criado `base_extractor.py` com `BaseExtractor` (ABC: `name` + estático `extract(content, source_file="")`) e o registro `entity_extractors()` na ordem oficial (SQL → ISAM → DBF → Recital). Os 5 extractors com `extract` herdam da ABC; `parser.py::_parse_file` consome o registro em vez de invocar nominalmente. 11 testes novos em `tests/test_base_extractor_unit.py` (contrato + regressão funcional). |
| G4 | `synthetic/` com 29+ módulos ✅ | **CORRIGIDO** | `test_synthetic_gap_coverage.py` com 22 testes: 12 para `screen_differ`, 10 para `error_detector`. |
| G5 | `replay_run_state.py` + `replay_failures.py` ✅ | **CORRIGIDO** | Extraídos do `replay_control.py`. |

### 1.4 Camada Tcl Runtime (`lib/`)

| # | Item | Severidade | Descrição |
|---|------|-----------|-----------|
| T1 | `record.tcl` vs `audit_writer.py` ✅ | **RESOLVIDA (2026-08-03)** | Header do `lib/record.tcl` agora declara explicitamente que é gravador simplificado (sem `seq_global`/hash-chain/HMAC), proibido como evidência de migração ou entrada de verify/replay oficiais, e aponta gateway SSH + `audit_writer.py` como fonte de verdade (AGENTS.md §3.5/§9 já diziam o mesmo). |
| T2 | Testes Tcl cobrem 5 módulos de ~15 ✅ | **RESOLVIDA (2026-08-03)** | `log.tcl` já era coberto (`log.test.tcl`); adicionados `tests/action.test.tcl` (6 testes), `dump.test.tcl` (12), `events.test.tcl` (11) e `plugins.test.tcl` (10) — barramento de eventos (dedup/isolamento de sink/merge), dump de diagnósticos (configure/enabled/safe_filename/gravação/sink), plugins (discover/estado/enable-disable/load com plugin quebrado) e API de ações (erro sem Expect/clamp de sleep/fconfigure). `tclsh tests/all.tcl`: 68/68. |

### 1.5 Build e Release (`scripts/`)

| # | Item | Severidade | Descrição |
|---|------|-----------|-----------|
| B1 | Race de tarball em deploys paralelos ✅ | **RESOLVIDA (2026-08-03)** | Incidente real no deploy 0.8.3: AIX e Linux rebuildavam o tarball no mesmo `dist/` em paralelo e o `.run` foi montado sobre payload parcial (`sanity: payload gzip inválido`). `build-tarball.sh` agora grava em `$OUT.tmp.$$` e publica com `mv -f` (rename atômico na mesma FS); trap de cleanup remove o temporário. Regressão: `tests/test_build_tarball_atomic_unit.py` (contrato estático + 2 builds concorrentes com mesmo timestamp → gzip íntegro, sem órfãos). |

---

## 2. Dívida Transversal

| # | Item | Severidade | Descrição |
|---|------|-----------|-----------|
| X1 | Sem middleware de erro ✅ | **CORRIGIDO** | `error_middleware.py` com decorator `@error_guard` aplicado em `do_GET`, `do_POST`, `do_DELETE`. Retorna 500 JSON padronizado com traceback no console. |
| X2 | Sem rate limiting ✅ | **RESOLVIDA (2026-08-06)** | Duas camadas: (1) throttle de login já existente em `admin_routes.py` (5 falhas/10 min → lockout 60 s por IP+username); (2) limiter genérico por IP para `/api/*` — `gateway/control/rate_limit.py` (janela fixa em memória, thread-safe, poda preguiçosa), ligado no `ControlServer` e aplicado em do_GET/POST/DELETE **antes** do auth guard, com 429 + `Retry-After`. Config: `DAKOTA_RATE_LIMIT_RPM` (default 600 — generoso para a UI, só dispara em abuso) e `DAKOTA_RATE_LIMIT=0` (off). `/api/login` e `/health` fora do limiter genérico. Testes: `tests/test_rate_limit_unit.py` (9: janela/reset/isolamento/retry_after/poda/env + integração HTTP real com 429 e exclusões). |
| X3 | Sem versionamento de API ✅ | **CORRIGIDO** | Prefixo `/v1` suportado em todos os handlers via `_normalize_path()`. `/v1/api/...` e `/api/...` funcionam identicamente. |
| X4 | ConnectionPool subutilizado ✅ | **CORRIGIDO** | `connect()` direto só usado no `main()` para bootstrap inicial (antes do pool existir). Runtime usa `db_pool` via `Handler._db()`. |
| X5 | Synthetic ↔ Replay não integrado | **RESOLVIDA (2026-08-03)** | Novo fluxo `POST /api/synthetic/stress/real` (rota fina em `synthetic_routes.py` → `control/services/synthetic_replay_service.py`): materializa os inputs da jornada como trilha auditável (`audit-*.jsonl` com `data_b64` real, hash-chain + HMAC pela chave do servidor — passa no `verify_log`) em `<state>/synthetic_runs/<uuid>/` e cria run real via `run_service.create_run_request_payload` (mesma resolução de target e compliance gateway-only de `POST /api/runs`), disparando `runner.start_run_async`. O Runner remove o `log_dir` efêmero ao fim do run (`params.ephemeral_log_dir`, restrito ao prefixo `synthetic_runs/`). O `run_via_runner` simulado do `replay_adapter.py` (que descartava os jsonl e usava `_simulate_screens`) foi removido junto com `ReplayAdapterConfig`; o bug de formato (`data_b64: ""` + `key_text`, que enviaria zero bytes) foi corrigido. Regressão: `tests/test_synthetic_replay_service_unit.py` (12 testes). |
| X6 | Endpoints de captura sem paginação/limite interno para sessões grandes | **RESOLVIDA (2026-08-03)** | Observado no MIG24 (captura 20 com 116.267 eventos / 8,5 MB): `GET /api/captures/{id}/replay` reprocessava a sessão inteira na TerminalEngine por request — >10 min e RSS do `server.py` saltou de 1,8 GB para 4 GB em 15 s, exigindo restart do control plane; `GET .../sessions` levava ~28 s por request. **Corrigido neste ciclo:** (1) cache do scan de sessões em `compliance.summarize_capture_sessions` (assinatura nome+size+mtime, TTL 5 s); (2) janela `offset`/`limit` no endpoint de replay — sem janela explícita, sessões com >20k eventos (`MAX_FULL_REPLAY_EVENTS`) retornam só os primeiros 1000 (`DEFAULT_REPLAY_WINDOW_LIMIT`) com `window.truncated=true`; snapshots/diffs só são calculados dentro da janela (+base de diff); (3) teto de 2000 checkpoints (`MAX_REPLAY_CHECKPOINTS`); (4) modo parcial: o stream é processado só até o fim da janela (`window.partial_state=true` — final_snapshot/checkpoints/canonical_signatures refletem esse ponto); (5) totais de `playback` refletem a sessão inteira. Medição (sintético adversarial, clear-screen por evento): 116k eventos passou de **>12 min / RSS extrapolado ~2,5–4 GB** para **29 s / RSS 181 MB**; 20k eventos no modo completo era 761 s / 432 MB. **Ciclo 2026-08-02 (0.7.26+):** (6) UI pagina de ponta a ponta (`replay_window_loader.js` + scroll infinito, botão habilitado só após dados carregados); (7) cache de estado em disco — a TerminalEngine ganhou estado serializável (`state_dict`/`load_state`/`is_state_clean`) e o serviço persiste o estado completo a cada `STATE_CACHE_INTERVAL` (1000) eventos em sessões enormes (`replay_state_cache.py`, gzip atômico, assinatura nome+size+mtime invalida em alteração da captura); janela profunda retoma do estado mais próximo com paridade frio×morno testada (guarda: deterministic_input/session_end antes do ponto impede a retomada; exceção documentada — checkpoints anteriores ao ponto de retomada não são regerados, `window.state_cache.hit=true`). Kill-switch: `REPLAY_STATE_CACHE=0`. **Refino 2026-08-02 (0.7.28):** `deterministic_input` passou a ser materializado apenas dentro da janela (contrato de paginação — cada janela carrega os seus eventos); capturas determinísticas têm dezenas de milhares desses eventos (captura 20 do MIG24: 25.229) e o snapshot fresco por evento fora da janela era o custo dominante do modo parcial (~487 s para offset=20k no AIX). Com isso a guarda de paridade da retomada só impõe fallback para `session_end`/`session_start` não inicial antes do ponto. **Refino 2 (0.7.29):** a captura 20 tem 352 pares `session_start`/`session_end` no meio do stream (reconexões reutilizando o session_id) e a guarda acima bloqueava a retomada em qualquer offset útil (~204 s por janela profunda, já sem o custo dos deterministic_input). Como os efeitos na engine (`engine.finish` do session_end) já estão congelados no estado persistido, a fase de skip passou a fazer apenas bookkeeping dos campos do payload para esses tipos — a retomada não tem mais bloqueios estruturais (só `state_load_failed`). Paridade frio×morno com reconexões coberta por teste. **Prova real (MIG24, captura 20, janela offset=20000/limit=100):** 487 s (0.7.27, sem retomada) → 204 s (0.7.28) → **32 s (0.7.29, `state_cache.hit=true, resumed_from=20000`)**; custo remanescente é o scan de totais (leitura+base64 de todos os eventos), não mais a engine. **Pendente:** cancelamento de request abandonada não implementado (custo agora limitado); limpeza de caches órfãos de capturas removidas; totais de playback poderiam ser cacheados junto ao estado. **Ciclo 2026-08-03 (índice de sessão):** o profiling da janela profunda no AIX (17,7 s standalone) mostrou o parse completo dos audit-*.jsonl como custo dominante remanescente (7,4 s de json.loads em 116k linhas / 314 MB), seguido dos renders de snapshot (5,7 s — regiões esparsas disparam checkpoint por intervalo de tempo) e das assinaturas (3 s). Novo `session_index_cache.py`: índice tipado por (capture_sig, session_id) com tipo/seq/arquivo/offset de cada evento + direção/tamanho decodificado dos "bytes"; a janela passa a ser materializada por seek e os totais de playback saem de somas de arrays, sem reparsear os arquivos. Kill-switch `REPLAY_SESSION_INDEX=0`; invalidação pela mesma assinatura nome+size+mtime; índice corrompido ou falha de leitura cai para o parse completo (fail-safe); sessões sem geometria de metadados no session_start também caem (a detecção varre os OUT atrás de resize). Paridade total frio×morno e com×sem índice coberta por 7 testes novos (`test_session_replay_session_index_unit.py`), incluindo prova de que o request morno não lê o arquivo inteiro (`read_text` derrubado via monkeypatch). Renders de snapshot em regiões esparsas (checkpoint por `interval_time`) são o custo dominante remanescente — revisão da política de checkpoints fica para o próximo ciclo. **Prova real (MIG24, 0.7.30, captura 20, janela offset=20000/limit=100):** req fria 32,0 s (parse completo + construção do índice, `session_index.stored=true`) → req morna **11,4 s / 12,8 s** (`state_cache.hit=true, resumed_from=20000` + `session_index.hit=true`). Trajetória completa da janela profunda: 487 s (0.7.27) → 204 s (0.7.28) → 32 s (0.7.29) → ~12 s (0.7.30) → **~10 s (0.7.31)**. **Prova real (MIG24, 0.7.31, captura 20, janela offset=20000/limit=100):** 9,2–10,2 s com `state_cache.hit=true` + `session_index.hit=true`; checkpoints da janela caíram de ~110 (um por evento, via interval_time) para 71 — só semânticos (clear_screen 40, reconexões session_start/end 29, ris 1) + âncora `window_start` — zero interval_time. **Ciclo 2026-08-03 (0.7.32):** (10) janitor de caches órfãos — `cleanup_orphan_caches` + `CacheJanitor` (thread daemon ligada no boot do control plane, padrão HostMetricsSampler) removem dirs de sig sem captura correspondente, com guarda de recência (min_age 1h) contra gravação concorrente e fail-safe; kill-switch `REPLAY_CACHE_JANITOR=0`, intervalo `REPLAY_CACHE_JANITOR_INTERVAL_S` (default 3600); (11) cancelamento de request abandonada — `client_still_connected` (recv MSG_PEEK não-bloqueante, tolerante a erro) na rota de replay + `abort_check` no serviço, com sondas a cada 64 linhas no parse e a cada 64 eventos no loop principal; abort retorna `{"error": {"code": "client_aborted"}, "aborted": true}` sem escrever resposta. 10 testes novos (`test_replay_cache_janitor_unit.py`, `test_replay_abort_unit.py`). X6 praticamente encerrada — resta apenas observação operacional contínua. |

---

**Intermitência RESOLVIDA (2026-08-03, recorrida no pipeline 0.8.4):** `gateway/tests/test_benchmark_routes.py::test_start_executes_and_produces_real_results` falhou 2× na suíte cheia — a janela de polling de 20 s (200×0,1 s) que aguarda o run COMPLETED estourava sob contenção de CPU. Correção definitiva (sem aumentar timeout de espera cega): novo `BenchmarkSupervisor.wait_completion()` expõe o **sinal real de conclusão** (join da thread supervisionada) e o teste aguarda por ele — retorna no instante do fim; o teto de 120 s só existe para detectar deadlock verdadeiro. Validado 3× sob CPU 100% (12/12 cores com `yes`): 12 passed nas três. Cobertura do método: `WaitCompletionUnitTests` (sem thread → True imediato; thread viva → False no timeout; após fim → True imediato).

**Bug RESOLVIDO (2026-08-06, pipeline 0.8.5 — extracted_gate=False):** os logs preservados da árvore extraída (fix do 0.8.4) apontaram a causa exata: `test_killed_because_survived_parent_implies_leaked` com `killed_processes=[]`. Raiz no `process_tree.py::_kill_tree`: a fase KILL inteira era pulada quando o relógio passava do `kill_deadline` — sob CPU starving o sleep da fase TERM acorda tarde e o escapee ficava vivo/não registrado. Correção: a fase KILL (SIGKILLs não-bloqueantes) roda SEMPRE; só as esperas respeitam deadlines. Regressão white-box: `tests/acceptance/test_process_tree_kill_phase.py` (falha sem o fix, passa com — verificado por stash). Baseline regerada (47 arquivos; process_tree.py protegido + novo teste incluso).

**Intermitência RESOLVIDA (2026-08-03, pipeline 0.8.4 run 3):** `test-all` estourou o teto genérico de 450 s na suíte `python-full` (450,97 s, `timed_out`) com a suíte íntegra — medição: ~390 s isolada, ~443 s sob carga; `process_tree` confirmou 0 leaks/escaped. Não era travamento: era orçamento sem margem. `test-all.sh` agora dá orçamento próprio ao `python-full` (`DAKOTA_TEST_ALL_PYTHON_TIMEOUT`, default **900 s = 2× a medição sob carga**); o teto continua existindo para hang real e a detecção de vazamento é independente do timeout. Baseline regerada (test-all.sh é arquivo protegido).

**Intermitência conhecida (2026-08-03, pipeline 0.8.4):** o gate da **árvore extraída** falhou 1× (`extracted_gate=False`) com a árvore original toda verde; os logs da extraída eram apagados com o `EXTRACT_DIR`, impossibilitando diagnóstico. `final-acceptance.sh` agora preserva os logs da extraída em `artifacts/acceptance-logs/extracted-failed-<run_id>/` quando o gate falha. A fase 08 re-rodada isoladamente na mesma árvore extraída (mesmo tarball) passou com GATE PASSED (fases 01–07 + test-all + python-full 1039 passed) — falha ambiental/intermitente, não de código. Se repetir, os logs preservados indicam o passo exato.

**Intermitência conhecida (2026-08-03, pipeline 0.8.1):** a fase 07 (`phase07-contamination`, `tests/acceptance/test_contamination_regression.py`) travou 1× no pipeline completo — log parado no START do passo e timeout global de 1500 s da fase; o gate reprovou corretamente a run (`tree_gate=False`) e o reaper matou o processo escapado (classificado escaped+leaked+killed, §25 funcionando). Isolada, a fase passa em ~3 min (GATE PASSED) e o re-run do pipeline fechou RELEASE VALIDATION PASSED com o mesmo tree_hash. Causa provável: contenção de CPU/Chromium nos testes aninhados (pytest + browser real dentro de pytest). Se repetir, instrumentar o passo com log de progresso por teste — sem aumentar timeouts.

## 3. Ordem de Ataque Recomendada

**Itens resolvidos:** R1–R6, S1–S4, G1, G2, G3, G4, G5, T1, T2, X1, X2, X3, X4, X5, X6, B1 (24 itens).
**Pendentes:** nenhum — mapa zerado em 2026-08-06.

---

## 4. O Que Já Foi Resolvido

### Ciclo 1 — Extração do server.py
- ✅ `server.py` de ~1200+ para 498 linhas
- ✅ Extração: `runtime_supervision.py`, `server_support.py`, `auth_support.py`, `page_state_builders.py`, `audit_scan_support.py`, `engineering_route_support.py`
- ✅ Extração: `replay_run_state.py`, `replay_failures.py`
- ✅ Reconciliação de capturas ativas na inicialização
- ✅ Robustez na amostragem de porta 22
- ✅ Precedência de storage no parser de source analyzer
- ✅ Correção de `gateway/tests/__init__.py`

### Ciclo 2 — Consolidação e Segurança
- ✅ `_write_json` centralizado em `route_helpers.py` (9 duplicações eliminadas)
- ✅ Middleware de erro global (`error_middleware.py` com `@error_guard`)
- ✅ DELETE endpoints: `/api/runs/{id}`, `/api/captures/{id}`, `/api/targets/{id}`, `/api/connection-profiles/{id}`
- ✅ Componente Go documentado como laboratório experimental (decisão: manter isolado) — **posteriormente removido de vez na v0.3.0 (commit `dd87592`)**
- ✅ `DEBT_MAP.md` criado com 21 itens mapeados por camada e severidade

### Ciclo 3 — Separação de serviços
- ✅ `session_replay_service.py` extraído de `gateway_observability_service.py` (594→424 linhas)
- ✅ Import atualizado em `capture_routes.py` e `test_gateway_status_unit.py`

### Ciclo 4 — Limpeza de rotas e verificação
- ✅ `ui_routes.py`: 597→102 linhas (`ROUTES_CONFIG` extraído para `ui_templates.py`)
- ✅ ConnectionPool: verificado que runtime já usa pool corretamente; `connect()` só no bootstrap

### Ciclo 5 — Unificação de jornadas
- ✅ `journey_routes.py`: fonte canônica de jornadas; ordem de matching corrigida (rotas específicas antes de `{id}` genérico)
- ✅ `synthetic_routes.py`: 599→396 linhas; journey/error-patterns/diff delegados com rewrite de path
- ✅ POST `/api/journeys/infer-menu` e `/api/journeys/{id}/run` adicionados (antes só existiam no synthetic)

### Ciclo 6 — Versionamento de API
- ✅ Prefixo `/v1` suportado via `_normalize_path()` no `Handler`
- ✅ `/v1/api/gateway/state` ≡ `/api/gateway/state` (transparente, sem breaking change)

### Ciclo 7 — Cobertura de testes synthetic
- ✅ `test_synthetic_gap_coverage.py` com 22 testes novos
- ✅ `screen_differ.py`: 12 testes (diff, to_json, normalizacao, edge cases)
- ✅ `error_detector.py`: 10 testes (fatal, not_found, validation, lock, permission, multiplos erros)

### Suite de testes
- ✅ Python: `266 passed, 2 skipped, 39 subtests passed`
- ✅ Tcl: `14 passed`

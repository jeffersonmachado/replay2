# Mapa de Dívida Arquitetural — Dakota Replay2

**Data:** 2026-06-30
**Base:** Ciclo de extração do `server.py` (498 linhas) + consolidação `_write_json` + middleware de erro + DELETE endpoints

> **Nota de atualização (2026-07-23):** as métricas de linhas abaixo são um
> snapshot de 2026-06-30 e já divergem do código atual — `server.py` tem
> ~903 linhas (não ~505) e `synthetic_routes.py` tem 823 linhas (o trabalho
> de delegação citado em R2 foi posteriormente revertido pelo crescimento do
> módulo). Os status de resolução foram revistos: G1 resolvido por remoção do
> componente Go (v0.3.0); G2 e itens de baixa severidade seguem abertos.

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
| R6 | `parse_qs` importado vs injetado | **BAIXA** | Alguns handlers recebem `parse_qs_fn` como parâmetro, outros importam `parse_qs` direto. Padronizar injeção. |

### 1.2 Camada de Serviços (`gateway/control/services/`)

| # | Item | Severidade | Descrição |
|---|------|-----------|-----------|
| S1 | `gateway_observability_service.py` ✅ | **CORRIGIDO** | Reduzido de 594 para 424 linhas. `prepare_session_replay_data` (174 linhas) extraído para `session_replay_service.py`. |
| S2 | `operational_scenario_service.py` (412 linhas) | **BAIXA** | Tamanho aceitável mas tem lógica de validação inline. Extrair `scenario_validator.py`. |
| S3 | `scenario_service.py` (41 linhas) | **BAIXA** | Thin facade. Poderia ser merging com `scenario_shared.py` (73 linhas). |
| S4 | `report_service.py` (97 linhas) | **BAIXA** | Re-exporta funções de `report_run_service.py` e `report_overview_service.py`. Padrão okay, mas nomes confusos (report_service vs report_run_service). |

### 1.3 Camada Gateway Core (`gateway/dakota_gateway/`)

| # | Item | Severidade | Descrição |
|---|------|-----------|-----------|
| G1 | Componente Go não integrado ✅ | **RESOLVIDO** | `gateway/internal/audit/` foi **removido** no commit `dd87592` (v0.3.0). O runtime Python é a única implementação de auditoria. |
| G2 | `replay_control.py` — Runner monolítico | **ALTA** | Controla execução de replay, fila, status, retry. Extrair: `run_queue.py`, `run_executor.py`, `run_status.py`. |
| G3 | `source_analyzer/` com 9 extractors | **MÉDIA** | Extractors independentes mas sem interface comum. Criar `BaseExtractor` ABC. |
| G4 | `synthetic/` com 29+ módulos ✅ | **CORRIGIDO** | `test_synthetic_gap_coverage.py` com 22 testes: 12 para `screen_differ`, 10 para `error_detector`. |
| G5 | `replay_run_state.py` + `replay_failures.py` ✅ | **CORRIGIDO** | Extraídos do `replay_control.py`. |

### 1.4 Camada Tcl Runtime (`lib/`)

| # | Item | Severidade | Descrição |
|---|------|-----------|-----------|
| T1 | `record.tcl` vs `audit_writer.py` | **BAIXA** | Duplicação funcional. `record.tcl` é simplificado e não substitui trilha auditável. Documentar claramente que `audit_writer.py` é a fonte de verdade. |
| T2 | Testes Tcl cobrem 5 módulos de ~15 | **MÉDIA** | Faltam testes para `action.tcl`, `dump.tcl`, `events.tcl`, `log.tcl`, `plugins.tcl`. |

---

## 2. Dívida Transversal

| # | Item | Severidade | Descrição |
|---|------|-----------|-----------|
| X1 | Sem middleware de erro ✅ | **CORRIGIDO** | `error_middleware.py` com decorator `@error_guard` aplicado em `do_GET`, `do_POST`, `do_DELETE`. Retorna 500 JSON padronizado com traceback no console. |
| X2 | Sem rate limiting | **BAIXA** | Nenhuma proteção contra abuso de endpoints. |
| X3 | Sem versionamento de API ✅ | **CORRIGIDO** | Prefixo `/v1` suportado em todos os handlers via `_normalize_path()`. `/v1/api/...` e `/api/...` funcionam identicamente. |
| X4 | ConnectionPool subutilizado ✅ | **CORRIGIDO** | `connect()` direto só usado no `main()` para bootstrap inicial (antes do pool existir). Runtime usa `db_pool` via `Handler._db()`. |
| X5 | Synthetic ↔ Replay não integrado | **MÉDIA** | `replay_adapter.py` existe mas não exposto na API REST. Fluxo ponta-a-ponta requer intervenção manual. |
| X6 | Endpoints de captura sem paginação/limite interno para sessões grandes | **PARCIAL (2026-08-02)** | Observado no MIG24 (captura 20 com 116.267 eventos / 8,5 MB): `GET /api/captures/{id}/replay` reprocessava a sessão inteira na TerminalEngine por request — >10 min e RSS do `server.py` saltou de 1,8 GB para 4 GB em 15 s, exigindo restart do control plane; `GET .../sessions` levava ~28 s por request. **Corrigido neste ciclo:** (1) cache do scan de sessões em `compliance.summarize_capture_sessions` (assinatura nome+size+mtime, TTL 5 s); (2) janela `offset`/`limit` no endpoint de replay — sem janela explícita, sessões com >20k eventos (`MAX_FULL_REPLAY_EVENTS`) retornam só os primeiros 1000 (`DEFAULT_REPLAY_WINDOW_LIMIT`) com `window.truncated=true`; snapshots/diffs só são calculados dentro da janela (+base de diff); (3) teto de 2000 checkpoints (`MAX_REPLAY_CHECKPOINTS`); (4) modo parcial: o stream é processado só até o fim da janela (`window.partial_state=true` — final_snapshot/checkpoints/canonical_signatures refletem esse ponto); (5) totais de `playback` refletem a sessão inteira. Medição (sintético adversarial, clear-screen por evento): 116k eventos passou de **>12 min / RSS extrapolado ~2,5–4 GB** para **29 s / RSS 181 MB**; 20k eventos no modo completo era 761 s / 432 MB. **Ciclo 2026-08-02 (0.7.26+):** (6) UI pagina de ponta a ponta (`replay_window_loader.js` + scroll infinito, botão habilitado só após dados carregados); (7) cache de estado em disco — a TerminalEngine ganhou estado serializável (`state_dict`/`load_state`/`is_state_clean`) e o serviço persiste o estado completo a cada `STATE_CACHE_INTERVAL` (1000) eventos em sessões enormes (`replay_state_cache.py`, gzip atômico, assinatura nome+size+mtime invalida em alteração da captura); janela profunda retoma do estado mais próximo com paridade frio×morno testada (guarda: deterministic_input/session_end antes do ponto impede a retomada; exceção documentada — checkpoints anteriores ao ponto de retomada não são regerados, `window.state_cache.hit=true`). Kill-switch: `REPLAY_STATE_CACHE=0`. **Refino 2026-08-02 (0.7.28):** `deterministic_input` passou a ser materializado apenas dentro da janela (contrato de paginação — cada janela carrega os seus eventos); capturas determinísticas têm dezenas de milhares desses eventos (captura 20 do MIG24: 25.229) e o snapshot fresco por evento fora da janela era o custo dominante do modo parcial (~487 s para offset=20k no AIX). Com isso a guarda de paridade da retomada só impõe fallback para `session_end`/`session_start` não inicial antes do ponto. **Refino 2 (0.7.29):** a captura 20 tem 352 pares `session_start`/`session_end` no meio do stream (reconexões reutilizando o session_id) e a guarda acima bloqueava a retomada em qualquer offset útil (~204 s por janela profunda, já sem o custo dos deterministic_input). Como os efeitos na engine (`engine.finish` do session_end) já estão congelados no estado persistido, a fase de skip passou a fazer apenas bookkeeping dos campos do payload para esses tipos — a retomada não tem mais bloqueios estruturais (só `state_load_failed`). Paridade frio×morno com reconexões coberta por teste. **Prova real (MIG24, captura 20, janela offset=20000/limit=100):** 487 s (0.7.27, sem retomada) → 204 s (0.7.28) → **32 s (0.7.29, `state_cache.hit=true, resumed_from=20000`)**; custo remanescente é o scan de totais (leitura+base64 de todos os eventos), não mais a engine. **Pendente:** cancelamento de request abandonada não implementado (custo agora limitado); limpeza de caches órfãos de capturas removidas; totais de playback poderiam ser cacheados junto ao estado. |

---

## 3. Ordem de Ataque Recomendada

**Itens resolvidos:** R1–R5, S1, G1, G4, G5, X1, X3, X4 (12 itens).
**Pendentes:** G2 (`replay_control.py` monolítico, **ALTA**), X6 (endpoints de captura para sessões grandes, **PARCIAL** — janela+cache de sessões+teto de checkpoints em 2026-08-01; UI paginada e cache de estado em disco (seek O(1) em janela profunda) em 2026-08-02; falta cancelamento de request abandonada e limpeza de caches órfãos), X5 (Synthetic ↔ Replay não exposto na API), R6, S2–S4, T1–T2, X2 (severidade baixa/média).

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

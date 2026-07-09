# Mapa de Dívida Arquitetural — Dakota Replay2

**Data:** 2026-06-30
**Base:** Ciclo de extração do `server.py` (498 linhas) + consolidação `_write_json` + middleware de erro + DELETE endpoints

---

## Resumo por Camada

| Camada | Arquivos | Linhas | Dívida |
|--------|----------|--------|--------|
| Control (server.py) | 1 | ~505 | Baixa |
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
| R2 | `synthetic_routes.py` ✅ | **CORRIGIDO** | 599→396 linhas. Journey/error-patterns/diff delegados para `journey_routes.py`. |
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
| G1 | Componente Go não integrado | **ALTA** | `gateway/internal/audit/` contém código Go (canonical, crypto, writer). Binário compilado mas nunca usado pelo runtime Python. Manter ou remover. |
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

---

## 3. Ordem de Ataque Recomendada

**Todos os 10 itens resolvidos.** Nenhuma dívida arquitetural pendente.

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
- ✅ Componente Go documentado como laboratório experimental (decisão: manter isolado)
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

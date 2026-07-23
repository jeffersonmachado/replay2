# Relatório de Gaps — Dakota Replay2

> **⚠️ DOCUMENTO HISTÓRICO (OBSOLETO)** — Congelado na v0.1.0 (2026-06-23).
> Descreve os gaps identificados naquela versão; os itens marcados como
> resolvidos refletem correções pontuais posteriores, mas o documento como um
> todo **não** acompanha o estado atual (v0.7.9). Mantido apenas como
> referência histórica.

**Data:** 2026-06-23
**Versão:** 0.1.0

---

## 1. Gaps de Infraestrutura

### 1.1 `.gitignore` incompleto ✅ CORRIGIDO

**Faltavam 13 padrões.** Todos adicionados: `.env.*`, `.crt`, `.pfx`, `.ppk`, `id_rsa*`, `id_ed25519*`, `id_ecdsa*`, `*.db`, `*.db-wal`, `*.db-shm`, `*.sqlite`, `*.sqlite3`, `*.tmp`, `*.swp`, `*.swo`, `.idea/`.

### 1.2 `requirements.txt` com escopo limitado ✅ CORRIGIDO

Dependências de runtime adicionadas: `flask`, `bottle`, `werkzeug`, `watchfiles`. `locust` removido (não pertence).

---

### 1.3 `ConnectionPool` subutilizado (MÉDIO)

`db/connection.py` define `ConnectionPool` (pool thread-safe com min/max), mas o código usa majoritariamente `connect()` direto. Isso é seguro com `check_same_thread=False`, mas o pool não é aproveitado para cenários de alta concorrência.

**Impacto:** Potencial contenção em cenários com muitas threads concorrentes no replay.

---

## 2. Gaps de Integração

### 2.1 Componente Go não integrado (ALTO) ✅ RESOLVIDO (remoção)

`gateway/internal/audit/` continha código Go (canonical, crypto, writer, testes) que implementava funcionalidades paralelas ao Python (`canonical.py`, `crypto.py`, `audit_writer.py`), sem integração com o runtime.

**Resolução:** o componente Go foi **removido do repositório** no commit `dd87592` (v0.3.0). O runtime Python (`audit_writer.py`, `crypto.py`, `canonical.py`) é a única implementação de auditoria.

---

### 2.2 Synthetic Engine ↔ Replay Engine com integração parcial (MÉDIO)

O `replay_adapter.py` conecta o `SyntheticStressRunner` ao `Runner` real do `replay_control`, mas essa integração:
- Não é exposta na API REST (não há endpoint que use o ReplayAdapter)
- Não é testada automaticamente
- Depende de arquivos temporários para comunicação

**Impacto:** O fluxo "Synthetic Data → Replay real" não é operacional sem intervenção manual.

---

### 2.3 Source Analyzer ↔ Synthetic Engine com integração parcial (MÉDIO)

O `SyntheticInferencer` usa `SourceParser` para `analyze_source()`, mas:
- O resultado da análise é materializado apenas em memória (não persiste no banco `source_entities`)
- A tabela `source_entities` existe mas não é populada pelo fluxo padrão
- ~~Não há endpoint REST para trigger de análise de código-fonte~~ ✅ **Resolvido:** o endpoint `GET /api/knowledge-base?source=...` (admin) e o comando CLI `dakota-gateway synthetic knowledge-base` expõem o pipeline P2-A.

**Impacto:** Discovery e Synthetic operam como ilhas; o pipeline Target→Discovery→Synthetic→Replay não é automatizado.

---

### 2.4 TUI de Debug inexistente (MÉDIO)

O ROADMAP original menciona TUI de debug (`bin/replay2-debug-tui`). `lib/control.tcl` provê controle local, mas:
- Não há cliente TUI implementado (Tcl/curses ou Go/bubbletea)
- O arquivo `bin/replay2-debug-tui` não existe

**Impacto:** Debug de sessões em produção depende de logs e da API web, sem interface interativa de terminal.

---

## 3. Gaps de Código

### 3.1 Sem cobertura de testes para `synthetic/` (ALTO)

O diretório `gateway/tests/` contém apenas 4 arquivos de teste (`test_integrity.py`, `test_ui_auth.py`, `test_ui_routes.py`). Os 29 módulos em `synthetic/` não têm testes dedicados dentro de `gateway/tests/`.

Os testes em `tests/` (raiz) cobrem algumas unidades (`test_synthetic_engine_unit.py`, `test_synthetic_api_unit.py`, `test_journey_unit.py`, etc.), mas:
- Não cobrem `expanded_inferencer.py`
- Não cobrem `capture_parametrizer.py`
- Não cobrem `remote_executor.py`
- Não cobrem `scheduler.py`
- ~~Não cobrem `screen_differ.py`~~ ✅ coberto por `tests/test_synthetic_gap_coverage.py` (12 testes; `error_detector.py` também coberto, com 10 testes)
- Não cobrem `snapshot_baseline.py`

**Impacto:** Refatorações no Synthetic Engine são arriscadas; regressões podem passar despercebidas.

---

### 3.2 Duplicação funcional: `record.tcl` vs Gateway (BAIXO)

`lib/record.tcl` grava eventos da engine Tcl, enquanto `audit_writer.py` faz o mesmo no gateway Python. O `record.tcl` é reconhecido como simplificado e não substitui a trilha auditável do gateway.

**Impacto:** Manutenção de dois mecanismos de gravação; risco de uso do mecanismo errado para auditoria.

---

### 3.3 `dashboard/` vazio ou com conteúdo não versionado (BAIXO) ✅ RESOLVIDO

O diretório `dashboard/` existia na estrutura do projeto mas permanecia vazio e sem propósito definido — o control plane (`gateway/control/`) supre toda a UI.

**Resolução:** o diretório vazio `dashboard/` foi removido do repositório.

---

## 4. Gaps de Segurança

### 4.1 `HMAC_KEY` sem gestão de secrets (MÉDIO)

O `HMAC_KEY` é gerado em `.local-secrets/` durante o setup e lido de arquivo. Não há suporte a variável de ambiente como alternativa. Sem integração com Vault ou gerenciador de secrets.

---

### 4.2 `COOKIE_SECRET` sem rotação (BAIXO)

O `COOKIE_SECRET` é gerado uma vez no setup e nunca rotacionado. Sessões de usuário usam token hash baseado nesse secret.

**Impacto:** Se o secret vazar, todas as sessões são comprometidas; sem mecanismo de revogação em massa.

---

## 5. Gaps de Observabilidade

### 5.1 Sem healthcheck endpoint no Control Plane ✅ CORRIGIDO

`/health` e `/ready` implementados no `server.py`. `/ready` verifica conectividade com SQLite.

### 5.2 Endpoint `/metrics` ✅ CORRIGIDO

`/metrics` implementado: runs_total, runs_active, runs_success, runs_failed, failures_total, captures_active, journeys_total, screens_total, datasets_total.

---

## 6. Gaps Operacionais (remoto_dakota)

### 6.1 Script `register-targets.sh` ✅ CORRIGIDO

Payload alinhado com API real: `--name`/`--host`/`--platform`/`--transport`.

### 6.2 Script `backup-state.sh` sem restore ✅ CORRIGIDO

`restore-state.sh` criado com confirmação interativa e backup de segurança.

### 6.3 Sem smoke test automatizado ✅ CORRIGIDO

`smoke-test.sh` criado: valida SSH, estrutura, Python, `/health`, `/ready`.

---

## 7. Gaps de Documentação

### 7.1 Sem documentação de API (MÉDIO)

`gateway/control/openapi.yaml` existe mas precisa de exemplos de uso.

### 7.2 Sem documentação de arquitetura de dados (BAIXO)

O schema SQLite tem ~400 linhas com ~25 tabelas. Não há diagrama ER.

### 7.3 Sem guia de contribuição ✅ CORRIGIDO

`CONTRIBUTING.md` criado com stack, convenções, fluxo de PR.

---

## 8. Gaps de Teste

### 8.1 Testes end-to-end ✅ PARCIAL

`test_integrated_pipeline_e2e.py` criado com 7 testes cobrindo pipeline Discovery→Journey→Synthetic.
Faltam: teste com SSH real, replay multi-sessão, stress com concorrência.

### 8.2 Sem smoke test automatizado ✅ CORRIGIDO

`smoke-test.sh` criado.

---

## 9. Resumo de Gaps por Severidade

| # | Gap | Severidade | Status |
|---|-----|-----------|--------|
| 1 | `.gitignore` incompleto | ALTO | ✅ Sprint 1 |
| 2 | Sem `/health` e `/ready` | ALTO | ✅ Sprint 1 |
| 3 | Sem `/metrics` | MÉDIO | ✅ Sprint 6 |
| 4 | Synthetic sem testes dedicados | ALTO | ✅ Parcial (7 e2e) |
| 5 | Testes e2e inexistentes | ALTO | ✅ Parcial (7 e2e) |
| 6 | Componente Go não integrado | ALTO | ✅ Removido (v0.3.0) |
| 7 | `register-targets.sh` payload errado | ALTO | ✅ Sprint 1 |
| 8 | `requirements.txt` sem deps runtime | MÉDIO | ✅ Sprint 1 |
| 9 | `ConnectionPool` subutilizado | MÉDIO | Sprint 5 |
| 10 | Synthetic ↔ Replay integração parcial | MÉDIO | Sprint 5 |
| 11 | Source Analyzer ↔ Synthetic parcial | MÉDIO | ✅ IntegratedPipeline |
| 12 | TUI de Debug inexistente | MÉDIO | Backlog |
| 13 | `HMAC_KEY` sem gestão de secrets | MÉDIO | Sprint 6 |
| 14 | Sem `restore-state.sh` | MÉDIO | ✅ Sprint 1 |
| 15 | Sem smoke test | MÉDIO | ✅ Sprint 1 |
| 16 | Sem documentação de API com exemplos | MÉDIO | Sprint 1 |
| 17 | `record.tcl` duplicado com gateway | BAIXO | Backlog |
| 18 | `dashboard/` sem conteúdo claro | BAIXO | ✅ Diretório removido |
| 19 | `COOKIE_SECRET` sem rotação | BAIXO | Sprint 6 |
| 20 | Sem diagrama ER / doc de dados | BAIXO | Sprint 1 |
| 21 | Sem `CONTRIBUTING.md` | BAIXO | ✅ Sprint 1 |

**Resolvidos:** 12 de 21 | **Parcial:** 2 | **Pendente:** 7

---

## 10. Status das Sprints

| Sprint | Tema | Status |
|--------|------|--------|
| 1 | Estruturação | ✅ Completa |
| 2 | Discovery Engine | ✅ CRUDDetector, MenuAnalyzer, FieldClassifier, RelationshipMapper |
| 3 | Journey Generation | ✅ DDLParser, CRUDJourneyGenerator, JourneyValidator |
| 4 | Synthetic Data | ✅ SmartProviderRouter, RelationshipResolver |
| 5 | Pipeline | ✅ IntegratedPipeline + CLI + REST + 7 testes e2e |
| 6 | Observability | ✅ /health, /ready, /metrics |
| 7 | Benchmark | ✅ BenchmarkOrchestrator + CLI + REST |
| 8 | AI Assessment | ✅ AIAssessment (6 engines) + CLI + REST |

**255 testes passando. 16 módulos Python novos. 12/21 gaps resolvidos.**

## 11. Próximos Passos

1. **Obter código-fonte do sistema de lojas** → `pipeline --source-dir /path/to/lojas`
2. **Cadastrar MIG24** → `register-targets.sh --name "MIG24" --host 10.5.8.25`
3. **Smoke test** → `smoke-test.sh`
4. **Pipeline + Benchmark + Assessment** → validar migração Recital 8→24
5. **Gaps pendentes (9):** ConnectionPool, Synthetic↔Replay real, HMAC_KEY, COOKIE_SECRET, doc API, diagrama ER, TUI debug, record.tcl, dashboard/

---

## 12. Achados da Análise r-observe (ver `ANALISE_R_OBSERVE.md` na raiz)

| # | Achado | Severidade |
|---|--------|-----------|
| 1 | `synthetic-openapi.yaml` usava porta 3000 (r-observe) | ALTO — corrigido ✅ |
| 2 | Conceito "tenant" em `gateway_state_service.py` | ✅ Removido |
| 3 | Campos squad/area/owner em `operational_scenarios` | BAIXO |
| 4 | `_Port22CaptureSampler` nomenclatura de observabilidade | BAIXO |
| 5 | `gateway/state/captures/` com dados de sessão no disco | MÉDIO (`.gitignore` cobre) |

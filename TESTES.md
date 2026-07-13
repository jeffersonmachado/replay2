# Tipos de Teste — Replay2

## Visão Geral

Os testes do Replay2 estão organizados em camadas, da mais interna (unidade) à mais externa (smoke/integração remota).

```
┌──────────────────────────────────────────────┐
│                  Smoke (remoto)              │  ← scripts/smoke-test-*.sh
├──────────────────────────────────────────────┤
│              Integração (HTTP real)          │  ← gateway/tests/test_ui_routes.py
├──────────────────────────────────────────────┤
│           Unitários (Python + JS + Tcl)      │  ← tests/ + gateway/tests/
└──────────────────────────────────────────────┘
```

## Script Principal

```bash
./scripts/test.sh [OPÇÕES]
```

### Opções

| Opção | Descrição |
|---|---|
| `--all` | Todas as suítes (padrão) |
| `--unit` | JS + Python + Tcl |
| `--js` | Testes JavaScript (terminal virtual + timeline) |
| `--python` | Testes Python (`tests/` + `gateway/tests/`) |
| `--tcl` | Testes Tcl (`tests/all.tcl`) |
| `--smoke` | Smoke tests remotos (requer `--remote`) |
| `--capture` | Testes específicos de captura |
| `--replay` | Testes específicos de replay |
| `--integration` | Testes de integração |
| `--quick` | JS apenas (~2s) |
| `--ci` | Modo CI: tudo menos Tcl |
| `--verbose` | Saída detalhada (`pytest -v`) |
| `--fail-fast` | Parar no primeiro erro (`pytest -x`) |
| `--remote` | Smoke contra servidor remoto |
| `--host HOST` | Servidor para smoke (padrão: `10.5.8.24`) |
| `--port PORT` | Porta (padrão: `8080`) |

### Exemplos

```bash
# Desenvolvimento rápido
./scripts/test.sh --quick

# Antes de commit
./scripts/test.sh --unit

# Smoke remoto no AIX
./scripts/test.sh --smoke --remote --host 10.5.8.25

# Foco em replay
./scripts/test.sh --replay --verbose

# CI/CD pipeline
./scripts/test.sh --ci --fail-fast
```

---

## Catálogo de Testes

### 1. JavaScript — Terminal Virtual

| Arquivo | Nº Testes | O que cobre |
|---|---|---|
| `gateway/control/static/js/virtual_terminal.test.mjs` | 22 | feed, renderPlainText, renderHtml, SGR (bold/dim/underline/reverse/hidden), DEC graphics, CSI split, ESC isolado, wrapPending+LF, RIS, IND, NEL, RI, clear screen, screenSig, visualSig, snapshot not re-fed |

**Comando:** `node --test gateway/control/static/js/virtual_terminal.test.mjs`

---

### 2. JavaScript — Timeline

| Arquivo | Nº Testes | O que cobre |
|---|---|---|
| `gateway/control/static/js/components/capture_replay_timeline.test.mjs` | 7 | Agrupamento OUT por proximidade, IN como boundary, geometria do snapshot, clear-screen preservado, text_sig/visual_sig |

**Comando:** `node --test gateway/control/static/js/components/capture_replay_timeline.test.mjs`

---

### 3. Python — Unitários (`tests/`)

| Arquivo | Foco |
|---|---|
| `test_capture8_replay_integration.py` | Fluxo completo: geometry, encoding, timeline, playback, screen content, DEC graphics, UTF-8 |
| `test_capture_realtime_counting.py` | Contagem de sessions/events do disco para capturas ativas |
| `test_runtime_capture_session_unit.py` | Resolução de log_dir, metadata, reconciliação no startup |
| `test_gateway_status_unit.py` | Status do gateway, prepare_session_replay_data, timeline determinística |
| `test_gateway_compliance_unit.py` | Compliance, normalize_target_policy, summarize_capture_sessions |
| `test_control_routes_unit.py` | Rotas do control plane (parsers, helpers) |
| `test_control_plane_gateway_route_unit.py` | Rota de gateway no control plane |
| `test_deterministic_record_unit.py` | Gravação determinística, screen_sig, screen_source |
| `test_screen_contracts.py` | Contratos de screen: normalize_screen, signature_from_screen, build_screen_snapshot |
| `test_screen_registry_unit.py` | Registro de telas, screen_signature, título, programa |
| `test_error_middleware_unit.py` | Middleware de erro, try/except global |
| `test_db_layer_unit.py` | Camada de banco: init_db, connect, now_ms |
| `test_final_features_unit.py` | Features finais: recorder, eventos, pipeline |
| `test_advanced_features_unit.py` | Features avançadas |

**Comando:** `PYTHONPATH=gateway python3 -m pytest tests/`

---

### 4. Python — Gateway Unitários (`gateway/tests/`)

| Arquivo | Foco |
|---|---|
| `test_ui_routes.py` | Ciclo de vida UI: activate → capture → stop → deactivate, autenticação |
| `test_ui_auth.py` | Autenticação, login, sessão |
| `test_capture_knowledge_integrator.py` | Integração de conhecimento de captura: is_command, data_field_index, comandos |
| `test_knowledge_base_api.py` | API da base de conhecimento |
| `test_data_synthesizer.py` | Síntese de dados |
| `test_roteiro_synthesizer.py` | Síntese de roteiros |
| `test_integrity.py` | Integridade dos dados |

**Comando:** `PYTHONPATH=gateway python3 -m pytest gateway/tests/`

---

### 5. Tcl

| Arquivo | Nº Testes | O que cobre |
|---|---|---|
| `tests/capture.test.tcl` | 3 | `apply_screen_boundaries`: sem boundary, ESC[2J ESC[H, ESC[H ESC[2J |
| `tests/config.test.tcl` | 4 | Config: defaults, overrides, env override encoding, find tclsh |
| `tests/normalize.test.tcl` | 4 | Normalização: strip ANSI, boxes, whitespace, pipeline |
| `tests/signature.test.tcl` | 2 | Assinatura: login screen, menu screen |
| `tests/integration_legacy_sim.test.tcl` | 1 | Integração legacy: login |

**Comando:** `tclsh tests/all.tcl`

---

### 6. Smoke — Captura (Remoto)

| Script | O que verifica |
|---|---|
| `scripts/smoke-test-capture.sh` | Health/ready, login, listagem, detalhe, sessões, replay, eventos |

**Comando:** `./scripts/smoke-test-capture.sh --host 10.5.8.24 --port 8080`

**Endpoints validados:**
- `GET /health` → 200
- `GET /ready` → 200
- `POST /api/login` → 200
- `GET /api/captures` → 200 + total
- `GET /api/captures/{id}` → 200 + status/sessions/events
- `GET /api/captures/{id}/sessions` → 200 + lista
- `GET /api/captures/{id}/replay?session_id=` → 200 + geometry/timeline/playback
- `GET /api/captures/{id}/events` → 200 + eventos

---

### 7. Smoke — Replay (Remoto)

| Script | O que verifica |
|---|---|
| `scripts/smoke-test-replay.sh` | Geometria, encoding, timeline (timestamp_ms), playback (data_b64), snapshots (text_sig, visual_sig), session_start |

**Comando:** `./scripts/smoke-test-replay.sh --host 10.5.8.24 --port 8080`

**Validações:**
- `geometry.rows` e `geometry.cols` presentes
- `geometry.geometry_source` definido
- `geometry.encoding` presente
- `timeline[].timestamp_ms` em todos os eventos
- `playback.events[].data_b64` em todos os eventos
- Snapshots com `content_kind: "terminal_snapshot"`
- `text_sig` e `visual_sig` nos snapshots (quando disponível)
- `session_start` com rows, cols, term, encoding

---

## Gaps Conhecidos

| Gap | Prioridade | Observação |
|---|---|---|
| Teste de concorrência (múltiplas capturas simultâneas) | Média | Não implementado |
| Teste de synthesize endpoint | Média | `POST /api/captures/{id}/synthesize` sem cobertura |
| Teste de delete de captura | Baixa | `DELETE /api/captures/{id}` sem cobertura |
| Teste de filtros avançados (search, ts_from, ts_to) | Baixa | Parcialmente coberto por smoke |
| Teste de PTY real (gateway + captura) | Alta | Complexo — requer terminal real |
| Teste de sessão inexistente no replay | Média | `prepare_session_replay_data` com session_id inválido |
| Teste de JSONL malformado/corrompido | Média | Erro silencioso atualmente |

---

## Fluxo de Desenvolvimento

```
1. Escrever teste de regressão        → falha esperada
2. Implementar correção               → teste passa
3. Rodar ./scripts/test.sh --quick    → JS verde
4. Rodar ./scripts/test.sh --unit     → tudo verde
5. Rodar ./scripts/test.sh --all      → suite completa
6. Build + smoke remoto               → ./scripts/test.sh --smoke --remote
7. Commit
```

# Relatório de Auditoria — Dakota Replay2 v0.1.0

**Data:** 2026-06-23
**Versão auditada:** 0.1.0
**Escopo:** Análise completa de componentes, dependências, fluxos e débitos técnicos

---

## 1. Estrutura Atual

```
replay2/
├── bin/
│   ├── main.exp            # Loop principal Expect/Tcl (captura, normalização, assinatura)
│   └── replay2.exp         # CLI da engine, diagnóstico, record/replay simplificado, plugins
├── lib/
│   ├── action.tcl          # Handlers de ação
│   ├── capture.tcl         # Leitura incremental de tela com buffer por spawn_id
│   ├── config.tcl          # Configuração da engine
│   ├── control.tcl         # Controle local pause/resume/step/send/dump
│   ├── dump.tcl            # Dump de tela
│   ├── events.tcl          # Eventos da engine
│   ├── log.tcl             # Logging
│   ├── normalize.tcl       # Redução de ruído de tela
│   ├── plugins.tcl         # Sistema de plugins
│   ├── record.tcl          # Gravação simplificada de eventos (não substitui gateway)
│   ├── signature.tcl       # Identificação estável de tela
│   └── state_machine.tcl   # Roteamento de handlers por assinatura
├── gateway/
│   ├── dakota-gateway       # Binário Go do gateway (componente experimental)
│   ├── go.mod / go.sum      # Dependências Go
│   ├── dakota_gateway/      # Core Python do gateway
│   │   ├── gateway.py       # Proxy SSH com captura auditável
│   │   ├── audit_writer.py  # Ordem global, hash-chain, HMAC
│   │   ├── verifier.py      # Verificação de integridade
│   │   ├── replay.py        # Runner de replay determinístico
│   │   ├── replay_control.py # Operação de runs, concorrência, falhas
│   │   ├── state_db.py      # Persistência SQLite (camada compatibilidade)
│   │   ├── schema.py        # Schema de eventos de auditoria
│   │   ├── canonical.py     # Serialização canônica
│   │   ├── crypto.py        # SHA-256, HMAC
│   │   ├── screen.py        # Snapshot de tela
│   │   ├── auth.py          # Autenticação
│   │   ├── compliance.py    # Compliance de captura
│   │   ├── cli.py           # CLI principal
│   │   ├── cli_commands/    # Comandos CLI organizados
│   │   │   ├── catalog.py   # Catálogo operacional
│   │   │   └── runtime.py   # Runtime commands
│   │   ├── db/              # Nova camada de persistência
│   │   │   ├── connection.py
│   │   │   ├── migrations.py
│   │   │   └── schema.py
│   │   ├── synthetic/       # Engine de dados sintéticos (29 arquivos)
│   │   └── source_analyzer/ # Analisador de código-fonte legado (8 arquivos)
│   ├── control/             # Control Plane (API + UI)
│   │   ├── server.py        # Entry point HTTP
│   │   ├── routes/          # 10 módulos de rota
│   │   ├── services/        # 14 serviços
│   │   ├── templates/       # 15 templates HTML + partials
│   │   ├── static/          # CSS, JS
│   │   └── openapi.yaml     # Spec OpenAPI
│   ├── internal/audit/      # Componente Go experimental (sem integração)
│   ├── state/               # Estado local (NÃO versionar)
│   ├── tests/               # Testes do gateway
│   └── docs/                # Documentação interna
├── dashboard/               # Dashboard (a confirmar conteúdo)
├── dev/qa-waves-ui/         # UI de QA Waves
├── tests/                   # Testes Python + Tcl (40+ arquivos)
├── scripts/                 # Scripts shell (build, start, stop, clean)
├── screens/                 # Plugins e dicionários de tela
├── examples/                # Exemplos (.exp, .tcl)
├── log/                     # Logs locais
├── Makefile                 # Build system
├── package.json             # npm scripts
├── dev.sh                   # Script de desenvolvimento
├── install.sh / uninstall.sh # Instalação/desinstalação
├── VERSION                  # 0.1.0
└── *.md                     # Documentação (8 arquivos)
```

---

## 2. Componentes Principais

### 2.1 Discovery Engine

**Status:** PARCIALMENTE IMPLEMENTADO

**O que existe:**
- `source_analyzer/parser.py` — SourceParser orquestrador
- `source_analyzer/sql_extractor.py` — Extração de entidades SQL
- `source_analyzer/isam_extractor.py` — Extração de entidades ISAM
- `source_analyzer/dbf_extractor.py` — Extração de entidades DBF
- `source_analyzer/recital_extractor.py` — Extração de entidades Recital
- `source_analyzer/screen_extractor.py` — Extração de definições de tela
- `source_analyzer/validation_extractor.py` — Extração de regras de validação
- `source_analyzer/entity_catalog.py` — Catálogo de entidades
- `synthetic/screen_explorer.py` — ScreenExplorer (descoberta de telas)

**O que falta:**
- Descoberta automática de formulários (campos, tipos, validações)
- Descoberta de menus e hierarquia de navegação
- Descoberta de workflows (sequências de telas)
- Descoberta de CRUDs completos
- Integração com execução real (modo "active" do ScreenExplorer)

### 2.2 Journey Engine

**Status:** PARCIALMENTE IMPLEMENTADO

**O que existe:**
- `synthetic/journey.py` — JourneyDefinition, JourneyStep, JourneyDataset
- `synthetic/journey_inferencer.py` — Inferência de jornadas por código-fonte
- `synthetic/journey_builder.py` — Construção de jornadas
- `synthetic/journey_verifier.py` — Verificação de jornadas
- `synthetic/macro_journey.py` — Macro-jornadas multi-módulo
- `synthetic/expanded_inferencer.py` — Inferência expandida (condicionais, dependências, transações)

**O que falta:**
- Geração automática de jornadas a partir de metadados Sequelize/DDL
- Construção automática a partir de schemas de banco
- Templates de jornada por domínio de negócio
- Validação de completude de jornada

### 2.3 Synthetic Engine

**Status:** IMPLEMENTADO (experimental, necessita generalização)

**O que existe (29 arquivos):**
- `engine.py` — SyntheticEngine (orquestrador: analyze → infer → register → generate → template → replay)
- `inferencer.py` — SyntheticInferencer (código-fonte → ScreenSchema)
- `expanded_inferencer.py` — ExpandedInferencer (condicionais, dependências, transações)
- `providers.py` — 20+ provedores de dados (CPF, CNPJ, nomes, endereços, etc.)
- `dataset_builder.py` — DatasetBuilder (geração de datasets)
- `template_engine.py` — TemplateEngine (templates de entrada)
- `schema.py` — Schemas (ScreenSchema, FieldSchema, SyntheticSchema)
- `constraints.py` — Validação de constraints
- `screen_registry.py` — Registro de telas no banco
- `journey.py`, `journey_inferencer.py`, `journey_builder.py`, `journey_verifier.py`
- `macro_journey.py` — Orquestração multi-módulo
- `error_detector.py` — Detecção de erros em tela (50+ padrões)
- `capture_parametrizer.py` — Parametrização de capturas
- `stress_runner.py` — Runner de stress test
- `replay_adapter.py` — Adaptador para replay
- `homologation_report.py` — Relatório HTML/JSON
- `screen_differ.py` — Diff de telas
- `remote_executor.py` — Execução remota
- `scheduler.py` — Agendamento
- `session_recorder.py` — Gravação de sessões
- `screen_explorer.py` — Exploração de telas
- `snapshot_baseline.py` — Baseline de snapshots
- `csv_exporter.py`, `junit_exporter.py` — Exportadores

**O que falta:**
- Generalização para não exigir scripts específicos por entidade
- IA para analisar código fonte e gerar massa automaticamente
- Detecção automática de relacionamentos entre entidades
- Geração de dados que respeitem regras de negócio implícitas

### 2.4 Replay Engine

**Status:** IMPLEMENTADO (funcional, necessita refinamento)

**O que existe:**
- `gateway/dakota_gateway/replay.py` — Replay determinístico (strict-global, parallel-sessions)
- `gateway/dakota_gateway/replay_control.py` — Controle de runs, concorrência, falhas
- `gateway/dakota_gateway/audit_writer.py` — Ordem global, hash-chain, HMAC
- `gateway/dakota_gateway/verifier.py` — Verificação de integridade
- Matching configurável: strict, contains, regex, fuzzy
- Suporte a SSH e Telnet
- Concorrência: `concurrency`, `ramp_up_per_sec`, `speed`, `jitter_ms`
- Reprocessamento parcial por `seq_global`, `session_id`, `checkpoint_sig`

**O que falta:**
- Otimização de performance para scale
- Melhor detecção de "tela estável"
- Suporte a encoding dinâmico por sessão
- Comparação funcional além de assinatura de checkpoint
- Refinamento da taxonomia de falhas por fluxo de negócio

### 2.5 Gateway

**Status:** IMPLEMENTADO

**O que existe:**
- `gateway.py` — Proxy SSH com captura bytes in/out + checkpoint
- `audit_writer.py` — Hash-chain + HMAC + ordem global
- `verifier.py` — Verificação de integridade pré-replay
- `compliance.py` — Política de compliance de captura

### 2.6 Control Plane

**Status:** IMPLEMENTADO (maduro)

**O que existe:**
- `server.py` — API HTTP + UI operacional
- 10 módulos de rota: admin, capture, catalog, gateway, journey, observability, operational, run, synthetic, ui
- 14 serviços: analytics_scenario, capture, environment, gateway_observability, gateway_state, operational_scenario, report (4 arquivos), run, scenario (2 arquivos), scenario_shared
- 15 templates HTML (Jinja2)
- OpenAPI spec
- Suporte a target_environments e connection_profiles
- Catálogo operacional com SLA
- Observabilidade integrada
- Relatórios exportáveis (md, json, csv)

### 2.7 Observability

**Status:** PARCIALMENTE IMPLEMENTADO

**O que existe:**
- `/observability` no control plane
- Consolidação de falhas por run
- Comparação entre runs (baseline)
- Regressão segmentada por ambiente e flow_name
- Relatórios de tendência
- Cenários analíticos salvos
- SLA tracking no catálogo operacional

**O que falta:**
- Endpoint `/metrics` com métricas internas
- Endpoints `/health` e `/ready`
- Log aggregation estruturada

---

## 3. Dependências

### Python

| Dependência | Uso | Status |
|-------------|-----|--------|
| flask | Web framework (control plane) | Produção |
| bottle | Web framework alternativo | Produção |
| werkzeug | WSGI utilities | Produção |
| watchfiles | Hot reload (dev) | Desenvolvimento |
| pytest | Testes | Desenvolvimento |

### Tcl/Expect

| Dependência | Uso | Status |
|-------------|-----|--------|
| Expect | Automação de terminal | Produção |
| Tcl 8.6+ | Runtime | Produção |
| tcltest | Testes | Desenvolvimento |

### Go (experimental)

| Dependência | Uso | Status |
|-------------|-----|--------|
| Go 1.x | gateway/internal/audit | Experimental |

### Sistema

| Dependência | Uso | Status |
|-------------|-----|--------|
| ssh | Transporte de replay | Produção |
| telnet | Transporte alternativo | Produção |
| bash | Scripts operacionais | Produção |
| python3 | Runtime Python | Produção |

---

## 4. Fluxo de Execução

```
┌──────────────────────────────────────────────────────────────────┐
│                     FLUXO COMPLETO DO REPLAY2                     │
│                                                                   │
│  ┌─────────┐    ┌──────────┐    ┌──────────┐    ┌─────────────┐  │
│  │ Captura │ →  │Armazena- │ →  │Transforma│ →  │   Replay    │  │
│  │  (Live) │    │  mento   │    │   ção    │    │ (Destino)   │  │
│  └─────────┘    └──────────┘    └──────────┘    └─────────────┘  │
│       │              │               │                │           │
│       ▼              ▼               ▼                ▼           │
│  ┌─────────┐    ┌──────────┐    ┌──────────┐    ┌─────────────┐  │
│  │Gateway  │    │Audit     │    │Checkpoint│    │Validação    │  │
│  │SSH      │    │Writer    │    │Normalize │    │+ Falhas     │  │
│  │Proxy    │    │JSONL     │    │+ Signature│   │+ Relatório  │  │
│  └─────────┘    └──────────┘    └──────────┘    └─────────────┘  │
│                                                                   │
│  ┌─────────────────────────────────────────────────────────────┐  │
│  │                    CONTROL PLANE                             │  │
│  │  ┌────────┐  ┌────────┐  ┌──────────┐  ┌────────────────┐  │  │
│  │  │API HTTP│  │UI Web  │  │Observa-  │  │Catálogo        │  │  │
│  │  │REST    │  │Jinja2  │  │bilidade  │  │Operacional     │  │  │
│  │  └────────┘  └────────┘  └──────────┘  └────────────────┘  │  │
│  └─────────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────┘
```

### Subfluxos

**Captura:**
1. Usuário inicia sessão via gateway SSH
2. Gateway intercepta bytes in/out
3. AuditWriter gera eventos com `seq_global`, `seq_session`, `ts_ms`
4. Hash-chain + HMAC garantem integridade
5. Checkpoints são capturados quando tela estabiliza

**Replay:**
1. Verifier valida integridade do log
2. Replay engine carrega eventos em ordem `seq_global`
3. Para cada evento: envia input, aguarda checkpoint, compara assinatura
4. Matching: strict, contains, regex, fuzzy
5. Falhas são registradas em `replay_failures`

**Reprocessamento:**
1. Operador seleciona falha na UI
2. Control plane cria nova run com recorte (`seq_global` range, `session_id`, `checkpoint_sig`)
3. Run filha herda `parent_run_id`
4. Comparação entre runs identifica regressão/resolução

---

## 5. Componentes Incompletos

| Componente | Completude | Ação Necessária |
|------------|-----------|-----------------|
| TUI de Debug | 50% | `lib/control.tcl` existe; falta cliente TUI |
| Observabilidade | 60% | Falta /metrics, /health, /ready |
| HA/Clustering | 0% | Single-instance; sem failover |
| Catálogo de cenários de carga | 60% | Estrutura existe; falta população |
| Homologação AIX | 20% | Desenho existe; sem execução real |
| Discovery ativo | 30% | ScreenExplorer modo passivo OK; falta modo ativo |
| Geração automática de jornadas | 50% | Inferência de código existe; falta DDL/ORM |
| Testes de performance | 10% | Sem benchmarks de escala |
| Documentação de API | 70% | OpenAPI existe; falta exemplos |
| Integração CI/CD | 0% | Sem pipeline automatizado |

---

## 6. Componentes Experimentais

| Componente | Localização | Status |
|------------|-------------|--------|
| Gateway Go | `gateway/internal/audit/` | Experimental, sem integração |
| QA Waves UI | `dev/qa-waves-ui/` | Em desenvolvimento |
| Go runtime | `gateway/go.mod` | Binário `dakota-gateway` compilado |

---

## 7. Débitos Técnicos

### Críticos
1. **Single-instance:** Control plane é in-process, sem coordenação distribuída
2. **SQLite:** OK para MVP, limite claro para escala multi-instância
3. **Heurísticas de checkpoint:** Classificação ainda depende de heurísticas de terminal; precisa calibração por fluxo
4. **Falsos positivos:** Variações de terminal/encoding/latência podem gerar falsos desvios

### Altos
5. **Hardcoding de HMAC_KEY:** Gerada em setup, mas sem gestão de secrets adequada
6. **Log rotation:** Implementada mas não testada em produção
7. **Tratamento de encoding:** Encoding assumido; sem detecção dinâmica
8. **Timeout de replay:** Timeout fixo; sem adaptive timeout

### Médios
9. **record.tcl vs gateway:** Dois mecanismos de gravação; `record.tcl` é redundante
10. **Testes de integração:** Cobertura parcial; faltam testes end-to-end com ambiente real
11. **Documentação de código:** Docstrings inconsistentes entre módulos
12. **Gerenciamento de dependências:** Sem `requirements.txt` lock; apenas `pip install`

### Baixos
13. **Makefile vs package.json:** Duplicação de comandos
14. **Python cache:** `__pycache__/` diretórios versionados acidentalmente
15. **Scripts shell:** Mistura de bash-isms; nem todos POSIX

---

## 8. Oportunidades de Melhoria

### Arquiteturais
1. **Separar gateway do control plane:** Containers/processos independentes
2. **Message queue:** Substituir SQLite direto por fila (Redis/NATS) para coordenação
3. **Microserviços:** Separar synthetic engine, source analyzer, replay engine
4. **API Gateway:** Unificar acesso ao control plane e observabilidade

### Funcionais
5. **Generalização do Synthetic Engine:** IA-driven ao invés de provider-driven
6. **Discovery ativo:** Navegação automática em sistemas legados
7. **Journey Generation automática:** A partir de DDL, ORM, metadados
8. **AI Assessment:** Análise inteligente de resultados de replay

### Operacionais
9. **Métricas internas:** endpoint `/metrics` com stats de runs e falhas
10. **Healthcheck endpoints:** `/health`, `/ready` no control plane
11. **Pipeline CI/CD:** Build, test, package, deploy automatizado
12. **Gestão de secrets:** Vault ou similar

---

## 9. Matriz de Riscos

| Risco | Probabilidade | Impacto | Mitigação |
|-------|---------------|---------|-----------|
| Falso positivo em checkpoint | Alta | Médio | Matching configurável; calibração por fluxo |
| SQLite corrompido | Baixa | Alto | Backup automático; migrar para PostgreSQL |
| Single point of failure | Média | Alto | HA/Clustering no roadmap |
| Divergência AIX/Linux não detectada | Média | Crítico | Benchmark dedicado; homologação AIX |
| Vazamento de credenciais | Baixa | Crítico | .gitignore; .example; sem hardcoding |

---

## 10. Conclusão

O Replay2 v0.1.0 é um MVP funcional com arquitetura sólida e cobertura abrangente dos fluxos core. Os principais gaps estão em:

1. **Generalização do Synthetic Engine** — hoje requer conhecimento prévio das entidades
2. **Escalabilidade** — single-instance, SQLite
3. **Homologação AIX** — desenho existe, execução pendente
4. **Métricas e healthcheck** — endpoints `/metrics`, `/health`, `/ready`

A base técnica é robusta e o roadmap está bem definido. O projeto está pronto para o primeiro ciclo de validação no ambiente Dakota (MIG24).

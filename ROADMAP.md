# Roadmap — Dakota Replay2

**Versão atual:** 0.1.0
**Data:** 2026-06-23
**Horizonte:** 8 Sprints (16 semanas)

---

## Sprint 1 — Estruturação e Preparação (2 semanas)

### Objetivo
Organizar a estrutura operacional, auditar o projeto e preparar o ambiente Dakota para o primeiro ciclo de validação.

### Escopo
- [ ] Finalizar estrutura `remoto_dakota/`
- [ ] Auditoria completa do Replay2
- [ ] Hardening do build (checklist de exclusão)
- [ ] Inventário do MIG24 documentado
- [ ] Healthcheck funcional
- [ ] Primeiro deploy no MIG24
- [ ] Teste de conectividade e smoke test

### Arquivos Impactados
- `remoto_dakota/docs/*` — documentação operacional
- `remoto_dakota/scripts/healthcheck.sh` — melhoria do healthcheck
- `replay2/scripts/build-tarball.sh` — hardening de exclusões
- `replay2/AUDITORIA_REPLAY2.md` — relatório de auditoria
- `replay2/CHECKLIST_EMPACOTAMENTO.md` — checklist de release

### Riscos
- Acesso SSH ao MIG24 pode não estar funcional
- Dependências Python podem faltar no MIG24
- Encoding do terminal AIX pode divergir

### Critérios de Aceite
- [ ] Healthcheck retorna 100% PASS no MIG24
- [ ] Artefato builda sem incluir itens proibidos
- [ ] Control plane inicia no MIG24 sem erros
- [ ] Documentação operacional completa

---

## Sprint 2 — Discovery Engine (2 semanas)

### Objetivo
Capacidade de analisar automaticamente o código-fonte do sistema de lojas e extrair entidades, telas, menus e regras de negócio.

### Escopo
- [ ] Aprimorar Source Analyzer (SQL, ISAM, DBF, Recital extractors)
- [ ] Implementar CRUD Detector (identificar CRUDs completos)
- [ ] Implementar Menu Analyzer (hierarquia de menus)
- [ ] Implementar Field Classifier (tipos de campo)
- [ ] Implementar Relationship Mapper (FK, lookup)
- [ ] Executar discovery no código-fonte do sistema de lojas
- [ ] Gerar Entity Catalog + Screen Map

### Arquivos Impactados
- `gateway/dakota_gateway/source_analyzer/crud_detector.py` — NOVO
- `gateway/dakota_gateway/source_analyzer/menu_analyzer.py` — NOVO
- `gateway/dakota_gateway/source_analyzer/field_classifier.py` — NOVO
- `gateway/dakota_gateway/source_analyzer/relationship_mapper.py` — NOVO
- `gateway/dakota_gateway/source_analyzer/parser.py` — melhoria
- `gateway/dakota_gateway/source_analyzer/sql_extractor.py` — FK detection
- `gateway/dakota_gateway/source_analyzer/screen_extractor.py` — menu hierarchy

### Riscos
- Código fonte pode usar padrões não cobertos pelos extractors
- Qualidade do código fonte pode dificultar parsing

### Critérios de Aceite
- [ ] Cobertura de entidades detectadas > 90%
- [ ] Precisão na classificação de campos > 85%
- [ ] CRUDs completos identificados > 80%
- [ ] Tempo de discovery < 10 min para 500 programas

---

## Sprint 3 — Journey Generation (2 semanas)

### Objetivo
A partir do Discovery, gerar automaticamente jornadas de validação: CRUD, negócio, stress.

### Escopo
- [ ] DDL Parser (CREATE TABLE → ScreenSchema)
- [ ] CRUD Journey Generator (por entidade)
- [ ] Business Journey Generator (por domínio)
- [ ] Stress Journey Generator (volume/concorrência)
- [ ] Journey Validator (completude, cobertura)
- [ ] Integração com catálogo operacional
- [ ] Gerar jornadas para o sistema de lojas

### Arquivos Impactados
- `gateway/dakota_gateway/synthetic/crud_journey_generator.py` — NOVO
- `gateway/dakota_gateway/synthetic/business_journey_generator.py` — NOVO
- `gateway/dakota_gateway/synthetic/stress_journey_generator.py` — NOVO
- `gateway/dakota_gateway/synthetic/journey_validator.py` — NOVO
- `gateway/dakota_gateway/synthetic/journey_inferencer.py` — DDL support

### Riscos
- Jornadas geradas podem não refletir fluxos reais de negócio
- Mapeamento tela→entidade pode ser impreciso

### Critérios de Aceite
- [ ] Jornadas geradas automaticamente > 70% das entidades
- [ ] Cobertura de CRUD > 80% das operações detectadas
- [ ] Precisão dos passos > 90% executáveis sem ajuste

---

## Sprint 4 — Synthetic Data Engine (2 semanas)

### Objetivo
Transformar o Synthetic Engine em um gerador genérico orientado por IA.

### Escopo
- [ ] Smart Provider Router (field → provider automático)
- [ ] Relationship Resolver (FK values from parent datasets)
- [ ] Consistency Validator (cross-entity rules)
- [ ] Constraint Inference Engine (VALID, PICTURE, RANGE → rules)
- [ ] Business Rule Extractor (código → constraints)
- [ ] Gerar datasets para todas as entidades do sistema de lojas

### Arquivos Impactados
- `gateway/dakota_gateway/synthetic/smart_provider_router.py` — NOVO
- `gateway/dakota_gateway/synthetic/relationship_resolver.py` — NOVO
- `gateway/dakota_gateway/synthetic/consistency_validator.py` — NOVO
- `gateway/dakota_gateway/synthetic/constraint_inference.py` — NOVO
- `gateway/dakota_gateway/synthetic/business_rule_extractor.py` — NOVO

### Riscos
- Inferência de regras de negócio é complexa
- Performance com datasets grandes (1M+ registros)

### Critérios de Aceite
- [ ] Entidades cobertas sem script manual > 80%
- [ ] Precisão dos dados gerados > 95%
- [ ] Respeito a FK/relationships > 90%

---

## Sprint 5 — Replay Engine (2 semanas)

### Objetivo
Executar o primeiro ciclo real de validação no ambiente Dakota (MIG24).

### Escopo
- [ ] Configurar target_environment e connection_profiles para MIG24
- [ ] Criar jornadas de validação no catálogo operacional
- [ ] Executar replay das jornadas no MIG24
- [ ] Coletar e analisar falhas
- [ ] Relatório de validação do sistema de lojas
- [ ] Adaptive timeout para checkpoint
- [ ] Retry automático em falha de SSH

### Arquivos Impactados
- `gateway/dakota_gateway/replay.py` — adaptive timeout, retry
- `gateway/dakota_gateway/replay_control.py` — melhorias
- `remoto_dakota/scripts/register-targets.sh` — cadastro no MIG24

### Critérios de Aceite
- [ ] 100% das jornadas do sistema de lojas executadas
- [ ] Taxa de sucesso de replay > 95%
- [ ] Relatório de validação gerado

---

## Sprint 6 — Observability (2 semanas)

### Objetivo
Aprimorar a camada de observabilidade interna (já existente via /observability).

### Escopo
- [ ] Endpoint `/metrics` com métricas internas (runs ativas, falhas, taxa de sucesso)
- [ ] Métricas: TPS, latência, falhas, checkpoints
- [ ] Healthcheck endpoints (`/health`, `/ready`)
- [ ] Log aggregation estruturada

### Arquivos Impactados
- `gateway/control/metrics.py` — NOVO (métricas internas)
- `gateway/control/routes/observability_routes.py` — /metrics, /health

### Critérios de Aceite
- [ ] `/metrics` expõe métricas principais em JSON
- [ ] `/health` e `/ready` respondem corretamente

---

## Sprint 7 — Benchmark AIX x Linux (2 semanas)

### Objetivo
Executar benchmark comparativo entre AIX e Linux.

### Escopo
- [ ] Benchmark Orchestrator (definição, execução, coleta)
- [ ] System Metrics Collector (CPU, memória, I/O, locks)
- [ ] Comparison Engine (delta analysis)
- [ ] Executive Report Generator
- [ ] Executar benchmark no sistema de lojas

### Arquivos Impactados
- `gateway/dakota_gateway/benchmark/` — NOVO diretório

### Critérios de Aceite
- [ ] Benchmark executado em AIX e Linux
- [ ] Métricas coletadas para todos os indicadores
- [ ] Relatório executivo gerado

---

## Sprint 8 — AI Assessment (2 semanas)

### Objetivo
Implementar o diferencial estratégico: IA que analisa, diagnostica e recomenda.

### Escopo
- [ ] Garbage Collector (código morto, tabelas órfãs)
- [ ] Bottleneck Detector (gargalos AIX vs Linux)
- [ ] Risk Identifier (áreas de maior risco)
- [ ] Inconsistency Finder (divergências sutis)
- [ ] Regression Detector (já parcialmente existente)
- [ ] Environment Comparator (AIX vs Linux side-by-side)
- [ ] Recommendation Engine
- [ ] Executive Report completo

### Arquivos Impactados
- `gateway/dakota_gateway/assessment/` — NOVO diretório (6 analysis engines)

### Critérios de Aceite
- [ ] Garbage Collector identifica > 80% do código morto
- [ ] Bottleneck Detector encontra gargalos reais
- [ ] Recomendações são acionáveis e específicas

---

## Resumo de Entregas por Sprint

| Sprint | Tema | Entregas Principais |
|--------|------|---------------------|
| 1 | Estruturação | remoto_dakota, auditoria, build hardening, healthcheck |
| 2 | Discovery | Source Analyzer 2.0, CRUD detector, menu analyzer |
| 3 | Journey | Journey generators (DDL, CRUD, business, stress) |
| 4 | Synthetic | Smart router, relationship resolver, AI-driven generation |
| 5 | Replay | Primeiro ciclo real MIG24, adaptive timeout, retry |
| 6 | Observability | Métricas internas, /health, /ready, log aggregation |
| 7 | Benchmark | AIX vs Linux, metrics collection, comparison engine |
| 8 | AI Assessment | 6 analysis engines, recommendations, executive report |

## Dependências entre Sprints

```
Sprint 1 (Estruturação)
  └→ Sprint 2 (Discovery)
       └→ Sprint 3 (Journey Generation)
            └→ Sprint 4 (Synthetic Data)
                 └→ Sprint 5 (Replay — 1º ciclo real)
                      ├→ Sprint 6 (Observability)
                      ├→ Sprint 7 (Benchmark AIX vs Linux)
                      └→ Sprint 8 (AI Assessment)
```

---

## Roadmap Anterior (v0.1.0 — mantido como referência histórica)

### v0.1.x — Bug Fixes & Polish (Q2 2026)

#### Objetivos
- Corrigir issues encontrados em produção
- Melhorar user experience
- Aumentar cobertura de testes

#### Tasks

**Core Engine**
- [ ] Melhorar detecção de "tela estável" (mais robusta)
- [ ] Suportar encoding dinâmico por sessão
- [ ] Otimizar captura para telas > 100KB
- [ ] Fix edge cases em box-drawing normalization

**Gateway**
- [ ] Adicionar retry automático em falha de SSH
- [ ] Melhorar rotação de logs (com backup automático)
- [ ] Detecção de "conexão morta" e reconexão

**Control Plane**
- [ ] Fix race conditions em concurrent runs
- [ ] Melhorar performance de listagem (paginação)
- [ ] Cache de resultados frequentes

---

### v0.2.0 — Observabilidade (Q3 2026)

#### Objetivos
- Permitir monitoramento em produção
- Detectar problemas cedo
- Integração com ferramentas de observabilidade

#### Features

**Métricas**

```json
{
  "dashboard:replay_runs_active": 3,
  "dashboard:replay_runs_success": 1500,
  "dashboard:replay_runs_failed": 50,
  "gateway:events_written": 500000,
  "gateway:verify_failures": 2,
  "gateway:checkpoint_mismatches": 15,
  "engine:screen_unrecognized": 23,
  "engine:capture_timeout": 1
}
```

- [ ] Endpoint `/metrics` (JSON)
- [ ] Métricas internas de runs e falhas
- [ ] Custom metrics via events

**Alertas**

- [ ] Template de alertas (syslog, email, webhook)
- [ ] Condições: failed runs, verify errors, SSH timeouts
- [ ] Escalation rules (admin se > N failures em T time)

**Logging Distribuído**

- [ ] Envio de auditlog para SIEM (Splunk/ELK)
- [ ] Correlação de eventos por session_id
- [ ] Dashboard de troubleshooting

**Tracing**

- [ ] Rastreamento de replay request → execution → completion
- [ ] Latency histograms por fase
- [ ] Flamegraph de replay (Go version)

---

### v0.3.0 — TUI de Debug (Q3-Q4 2026)

#### Objetivos
- Terminal interativo para troubleshoot em vivo
- Inspecionar estado da engine
- Pausar/step screen-by-screen

#### Features

**Conecta em ControlAPI (TCP)**

```
┌─ Control Server (lib/control.tcl) em localhost:random
│
└─ TUI client (Tcl + curses ou Go + bubbletea)
   ├─ Lista de telas capturadas (raw, norm, sig, state)
   ├─ Pausa/retoma execução
   ├─ Step screen-by-screen
   ├─ Inspect state machine state
   ├─ Ver último output do SSH (screen buffer)
   └─ Send custom commands via send
```

**Componentes**

- [ ] Finish `lib/control.tcl` (servidor TCP)
- [ ] Criar `bin/replay2-debug-tui` (cliente Tcl com curses)
  - [ ] Ou versão em Go (melhor UX, mais portável)
- [ ] Protocolo de comunicação versioned
- [ ] Test suite para TUI commands

**Casos de Uso**

```bash
# Terminal 1: Engine com debug control
expect bin/main.exp --legacy-cmd "..." --control-port 9999

# Terminal 2: TUI debug
tclsh bin/replay2-debug-tui --port 9999

# Dentro do TUI:
> status               # Ver estado atual
> pause                # Pausa automação
> dump                 # Dump tela atual
> step                 # Próxima captura
> resume               # Retoma
> send "usuario\r"     # Comando manual
> quit                 # Sai
```

---

### v1.0.0 — Production Ready (Q4 2026 - Q1 2027)

#### Objetivos
- Suporte enterprise
- Alta disponibilidade
- Performance garantida
- Compliance ready

#### Features

**Clustering & HA**

```
┌─ N gateways (load balanced)
│  ├─ Shared log directory (NFS ou S3)
│  ├─ Shared SQLite com WAL over NFS
│  └─ Heartbeat protocol
│
├─ Central coordinator
│  ├─ Detecção de falha
│  ├─ Failover automático
│  └─ Leader election (Raft? Paxos?)
│
└─ Backup bastion
   └─ Standby pronto para assumir
```

- [ ] Multi-leader replication (run fingerprint based)
- [ ] Automatic failover (< 30s)
- [ ] etcd ou Consul para coordination
- [ ] Health checks (liveness + readiness)

**API Enhancements**

- [ ] REST API v2 (hypermedia, HATEOAS)
- [ ] GraphQL endpoint (opcional)
- [ ] gRPC para performance (C++ client compatibility)
- [ ] Webhook delivery retry + circuit breaker
- [ ] API Key + OAuth2 integration

**Web UI Avançada**

- [ ] WebSocket real-time updates (sem polling)
- [ ] Charts D3.js: progresso de runs, taxa de sucesso, latency distribution
- [ ] Heat map de screen captures
- [ ] Regression detection (comparing runs)
- [ ] Dark mode + theme customization

**Segurança**

- [ ] 2FA (TOTP)
- [ ] OIDC / SAML integration
- [ ] Audit log detalhado (logins, ações, mudanças)
- [ ] Field-level encryption para sensitive params
- [ ] IP whitelist enforcement
- [ ] Rate limiting por role

**Performance & Scale**

- [ ] Suportar 1M+ events numa run
- [ ] Parallel checkpoint validation (multi-threaded)
- [ ] Lazy-load big screens (não carrega tudo em RAM)
- [ ] Compression JSONL (gzip ou zstd)
- [ ] Database indices otimizadas
- [ ] QueryPlanner para queries complexas

**Compliance & Audit**

- [ ] SOC2 readiness checklist
- [ ] Data retention policies (delete old runs após N days)
- [ ] Export em format padrão (CSV, Parquet para BI)
- [ ] Chain of custody log
- [ ] Digital signature do audit log

---

## 🔮 Filas de Features (Backlog Futuro)

### Curto Prazo (Dentro de 6 meses)

**Core Engine Evolution**

```
- [ ] Suporte a sessions múltiplas em paralelo (threading)
- [ ] Screen registry (biblioteca de telas conhecidas)
  └─ Share signatures entre time
  └─ Versionamento de handlers
- [ ] Machine learning para screen detection
  └─ Começar com SVM, depois neural net
  └─ Feedback loop: human validation → model update
- [ ] Scripting language customizado (DSL) para handlers
  └─ Mais seguro que Tcl direto
  └─ Type checking
```

**Gateway Enhancements**

```
- [ ] Multi-target replay (broadcast sessão a N destinos)
- [ ] Conditional replay (IF checkpoint X THEN send Y)
- [ ] Abort conditions (IF screen matches error THEN cancel)
- [ ] Variable substitution (${SESSION_ID}, ${ITERATION}, ...)
- [ ] Batch operations (CSV de inputs, loop automático)
```

**Dashboard Extensions**

```
- [ ] Event correlation (find similar issues)
- [ ] Saved queries / filters
- [ ] Performance regression alerting
- [ ] Cost estimation (compute hours, IO)
- [ ] Budget alerts
```

### Médio Prazo (6-12 meses)

**Integrations**

```
- [ ] Splunk app (custom dashboard)
- [ ] Datadog integration (logs + metrics)
- [ ] PagerDuty (incident creation)
- [ ] Slack bot (notifications, commands)
- [ ] GitHub Actions (CI/CD integration)
- [ ] Jenkins plugin
- [ ] GitLab Runner support
```

**Data Export & BI**

```
- [ ] Export to Data Warehouse
  └─ BigQuery
  └─ Snowflake
  └─ Redshift
- [ ] BI tool templates (Tableau, Looker)
- [ ] Metrics warehouse (InfluxDB, VictorOps)
```

**Advanced Features**

```
- [ ] Chaos engineering (inject failures durante replay)
- [ ] Comparison of runs (diff visualization)
- [ ] Record → annotate → publish (community library)
- [ ] Fuzzing (stress test screens com random inputs)
- [ ] Performance profiling (Flamegraph de Tcl)
```

### Longo Prazo (1+ anos)

**Strategic Vision**

```
- [ ] Multi-protocol support (Telnet, SSH, serial, HTTP?)
- [ ] Generic "screen OCR" (não dependendo de ANSI)
  └─ Screenshot capture + text extraction
  └─ Template matching visual
- [ ] VR/AR visualization (3D screen navigation)
- [ ] AI-powered test case generation
  └─ Entrada: audit log de usuário
  └─ Output: test scripts automáticos
  └─ Coverage analysis
- [ ] Código aberto? (Apache 2.0 license?)
```

---

## 📊 Iniciativas Cross-Cutting

### Performance

**Baseline (v0.1):**
- Replay a 1x speed (real-time)
- Checkpoints a cada 512 bytes

**v0.2:**
- [ ] Replay at 10x speed (jump quando não há mudança visível)
- [ ] Smart checkpointing (only after state changes)

**v1.0:**
- [ ] Replay at 100x+ speed (multi-session parallel)
- [ ] Hardware acceleration (GPU para normalization?)

### Compatibility

**Platforms:**
- ✅ Linux x86_64, ARM64
- ✅ AIX (PowerPC)
- [ ] macOS (Intel + Apple Silicon)
- [ ] Windows (WSL2 official support)
- [ ] Container (Docker, Podman)
  - [ ] Official images
  - [ ] Helm charts para K8s

**Protocols:**
- ✅ SSH
- [ ] Telnet (legacy)
- [ ] Serial (COM port)
- [ ] Raw socket (proprietary protocols)

**Encodings:**
- ✅ UTF-8, CP850 (EBCDIC), Latin-1
- [ ] Dynamic detection
- [ ] Codepage switching mid-session

### Testing

**v0.1:**
- ✅ Unit tests (tcltest)
- ✅ Integration tests (legacy_sim)

**v1.0:**
- [ ] End-to-end tests (full CI/CD pipeline)
- [ ] Load testing (k6 ou Locust)
- [ ] Chaos testing (fault injection)
- [ ] Compatibility matrix (AIX versions, Tcl versions)

### Documentation

**v0.1:**
- ✅ README
- ✅ Análises profundas

**v1.0:**
- [ ] Tutorial step-by-step
- [ ] Video walkthroughs
- [ ] Interactive playground (web-based)
- [ ] API reference (Swagger/OpenAPI)
- [ ] Architecture decision records (ADRs)
- [ ] Performance benchmarks (public)
- [ ] Blog posts / case studies

---

## 🎯 Success Metrics

### v0.1 → v0.2

| Métrica | Target |
|---------|--------|
| Uptime | 99% |
| Mean repair time | < 15 min |
| Screen accuracy | > 99.5% |
| Test coverage | > 70% |

### v0.2 → v1.0

| Métrica | Target |
|---------|--------|
| Uptime | 99.9% |
| P99 latency | < 100ms |
| Throughput | 1000 events/sec |
| Scale | 1M sessions/day |

### v1.0+

| Métrica | Target |
|---------|--------|
| Community users | 100+ |
| GitHub stars | 1000+ |
| Enterprise customers | 10+ |
| Open source contributions | 50+ |

---

## 🛣️ Known Constraints & Decisions

### Arquitetura

**Decisions Made:**

1. **Tcl/Expect para core** → Portabilidade AIX
   - Alternativa: C++ (mais rápido, mas menos portável)
   - Trade-off: Favorecemos compatibilidade

2. **SQLite para BD** → Simplicidade deploy
   - Alternativa: PostgreSQL (mais robusto, mas setup complexo)
   - Trade-off: v1.0 pode suportar ambos

3. **HTTP/1.1 com polling** → Simplicidade
   - Alternativa: WebSocket real-time
   - Trade-off: v1.0 adicionará WebSocket

4. **Append-only JSONL log** → Integrity first
   - Alternativa: Binary format (menor tamanho)
   - Trade-off: Comprometemos um pouco em size para auditoria

### Não será feito (por design)

- ❌ **GUI desktop** (Tcl/Tk, Electron, etc)
  - Razão: Web UI suficiente, resources limitados
  
- ❌ **Suporte a Windows native** (sem WSL)
  - Razão: Expect não é mainstream no Windows
  
- ❌ **Blockchain/Distributed Ledger**
  - Razão: Overkill para auditoria, hash-chain suficiente

---

## 📅 Cronograma Estimado

```
2026:
  Q1: v0.1 ✅ (current)
  Q2: v0.1.x bug fixes
  Q3: v0.2 (observability)
      + v0.3 initial (TUI)
  Q4: v1.0 beta
       - HA/Clustering
       - Advanced UI

2027 Q1: v1.0.0 GA
         Production support begins

2027 Q2+: v1.x maintenance
          + v2.0 planning
```

---

## 💡 Ideas & Suggestions

### Community Input Needed

**Prioritize these if you have use case:**

1. **Encoding support:** Quais mais usam seu sistema legado?
   - [ ] EBCDIC variants?
   - [ ] Multibyte encodings (Chinese, Arabic)?

2. **Compliance:** Você precisa de...
   - [ ] PCI-DSS audit trail?
   - [ ] HIPAA data retention?
   - [ ] GDPR data deletion?

3. **Integration:** Qual seu current stack?
   - [ ] Splunk? ELK? Datadog?
   - [ ] Slack? Teams? PagerDuty?
   - [ ] GitLab? GitHub? GitBucket?

4. **Performance:** Seus use cases exigem...
   - [ ] Replay de 100k events?
   - [ ] 10000 concurrent users?
   - [ ] Sub-second response time?

**Vote via GitHub Issues** (quando repo abrir)

---

## 🔗 Related Projects & Inspiration

**Learning from:**
- Terraform (declarative IaC)
- Kubernetes (reconciliation loop)
- LogStash (event processing)
- Vault (secrets management)
- HashiCorp (product philosophy)

**Compatible with:**
- ELK stack (log aggregation)
- Custom metrics consumers (via `/metrics` JSON endpoint)

---

## 📝 Changelog Template

### v0.1.0 (27 mar 2026)

**✨ Features:**
- Initial release with core engine
- Auditoria with HMAC integrity
- Gateway SSH proxy
- Control plane web UI
- Dashboard real-time viewer

**🐛 Fixes:**
- TBD

**📚 Documentation:**
- README + operational guide
- Threat model
- Deep analysis

**🚀 Performance:**
- Baseline metrics established

---

## Questions for Stakeholders

1. **Timing:** Qual é a urgência de v1.0?
2. **Priority:** Clustering ou better UI mais importante?
3. **Budget:** Quantos devs podem trabalhar?
4. **Community:** Será open-source?
5. **Support:** Nível de SLA esperado?

---

**Última atualização:** 27 de março de 2026  
**Mantido por:** Projeto Dakota Replay2  
**Status:** Living document (atualizar regularmente)

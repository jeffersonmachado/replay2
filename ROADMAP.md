# Roadmap Dakota Replay2 (v0.1.0 → Futuro)

## Status Atual: MVP (Minimum Viable Product)

**Versão:** 0.1.0 (27 de março de 2026)  
**Phase:** Alpha / Early Production

- ✅ Core automation engine (Expect/Tcl)
- ✅ Auditoria with integrity (JSONL + hash-chain + HMAC)
- ✅ Deterministic replay (seq_global ordering)
- ✅ Gateway SSH proxy com checkpoint validation
- ✅ Control plane básico (SQLite + Web UI)
- ✅ Distribuição portável (Linux + AIX, tarball)
- ✅ Tests (tcltest + Python)

**O que ainda NOT está pronto para produção:**
- [ ] TUI de debug (parcial em lib/control.tcl)
- [ ] Observabilidade plena (métricas, alertas)
- [ ] Alta disponibilidade (clustering, failover)
- [ ] Performance otimizada para scale
- [ ] Integração com sistemas externos

---

## 📍 Roadmap por Versão

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

- [ ] Prometheus exporter (`/metrics`)
- [ ] OpenTelemetry integration
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
- Prometheus (metrics exporter)
- OpenTelemetry (tracing)
- Grafana (visualization)
- AlertManager (alerting)
- ELK stack (log aggregation)

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

# Roadmap — Dakota Replay2

**Versão atual:** 0.7.9 (ver arquivo `VERSION`)
**Última atualização:** 2026-07-23

> Este documento substitui o roadmap original da v0.1.0 (2026-06-23), que
> planejava 8 sprints. **Todos os 8 sprints foram concluídos**; o conteúdo
> aspiracional antigo (HA/clustering, TUI em Go, WebSocket, visões de longo
> prazo) foi aposentado por não refletir o estado real do projeto.

---

## Estado Atual — Sprints Concluídos (v0.1.0 → v0.7.9)

| Sprint | Tema | Status |
|--------|------|--------|
| 1 | Estruturação | ✅ Concluído — build hardening, healthcheck, deploy MIG24 |
| 2 | Discovery Engine | ✅ Concluído — `source_analyzer/` (extractors SQL/ISAM/DBF/Recital, CRUD, menus, relacionamentos, catálogo) |
| 3 | Journey Generation | ✅ Concluído — inferência, geração CRUD, validação de jornadas |
| 4 | Synthetic Data Engine | ✅ Concluído — planejador de dataset, sintetizador, resolução de FK |
| 5 | Replay / Pipeline | ✅ Concluído — pipeline integrado Discovery→Journey→Synthetic→Replay (CLI + REST + testes e2e) |
| 6 | Observability | ✅ Concluído — `/health`, `/ready`, `/metrics` |
| 7 | Benchmark AIX × Linux | ✅ Concluído — `dakota_gateway/benchmark/` (CLI + REST) |
| 8 | AI Assessment | ✅ Concluído — `assessment.py` (6 engines) + CLI + REST |

Marcos posteriores relevantes:

- Terminal engine canônica em Python (`gateway/dakota_terminal/`) como fonte
  única de emulação de terminal (desde v0.3.19);
- Componente experimental em Go (`gateway/internal/audit/`) **removido** na
  v0.3.0 — o runtime Python é a única implementação de auditoria;
- Control plane reestruturado: `server.py` como shell HTTP leve, domínios em
  `routes/`, regras em `services/`, persistência em `dakota_gateway/db/`;
- Pipeline de aceitação (`scripts/final-acceptance.sh`, fases 01–08) com
  evidências em `artifacts/` exigidas pelo build.

---

## O Que Resta de Fato

### Curto prazo (dívida conhecida)

- **Homologação AIX operacional** — portabilidade contemplada no desenho
  (Expect/Tcl, scripts POSIX), mas a homologação dedicada no MIG24 AIX 7
  segue pendente (ver `docs/servidor-dakota-mig24.md`);
- **Refinamento da taxonomia de falhas** — `timeout`, `screen_divergence`,
  `navigation_error`, `concurrency_error` ainda são heurísticas por fluxo;
- **Catálogo formal de cenários de carga** — hoje não existe;
- **`replay_control.py` monolítico** — extração de `run_queue`/`run_executor`/
  `run_status` mapeada no `DEBT_MAP.md` (item G2);
- **Gestão de segredos** — suporte a `HMAC_KEY` via variável de ambiente e
  rotação de `COOKIE_SECRET`;
- **Documentação de API com exemplos** (`gateway/control/openapi.yaml`) e
  diagrama ER do schema SQLite.

### Backlog (sem compromisso de prazo)

- TUI de debug para sessões (`lib/control.tcl` já provê o servidor de
  controle local; falta o cliente interativo);
- Consolidação definitiva de `record.tcl` como gravador simplificado
  (a trilha auditável oficial é a do gateway SSH);
- Autenticação automática Telnet no replay (hoje prefere SSH).

### Direção de evolução (dentro das fronteiras de `FRONTEIRAS.md`)

- Discovery Engine (`source_analyzer/`) — cobertura e precisão dos extratores;
- Synthetic Engine (`synthetic/`) — jornadas de negócio e massa de dados;
- Replay determinístico — robustez de checkpoints e reprocessamento;
- Métricas internas via `/metrics` e endpoints REST da API existente.

---

## Fora de Escopo (decisões firmes)

Conforme `FRONTEIRAS.md`, **não** serão perseguidos: observabilidade externa
(Prometheus/Grafana/OpenTelemetry), banco diferente de SQLite, containers,
multi-tenancy e monitoramento de infraestrutura (projeto `r-observe/`).

---

## Referências

- `AGENTS.md` — arquitetura, convenções e lacunas conhecidas (§9)
- `DEBT_MAP.md` — dívida arquitetural por camada
- `FRONTEIRAS.md` — fronteiras arquiteturais
- Relatórios da v0.1.0 (GAPS, auditoria, análises) — `docs/historico/`

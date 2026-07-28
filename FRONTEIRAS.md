# Fronteiras Arquiteturais do Replay2

Este documento existe para evitar contaminação de contexto entre projetos.

## Domínio

Replay2 é uma ferramenta de **validação de migração** de sistemas legados (Recital 8 → Recital 24).

## Stack

| Camada | Tecnologia |
|--------|-----------|
| Core engine | Expect/Tcl |
| Gateway | Python |
| Control Plane | Python + SQLite |
| UI | HTML/CSS/JS vanilla (templates HTML estáticos servidos por `ui_templates.py`; sem engine de template como Jinja2) |
| Build | Shell script → tarball |
| Runtime | Processo direto no host (sem containers) |

## NÃO pertence ao Replay2

- ❌ Prometheus / Grafana / OpenTelemetry
- ❌ PostgreSQL (usa SQLite)
- ❌ Docker / Kubernetes / containers
- ❌ Multi-tenancy (tenant, tenant_id)
- ❌ Infra monitoring **externo** (host_status, service_check, hostgroup, probes
  de outros servidores) — continua no r-observe
- ❌ Porta 3000, 3001, 9090 (são do stack r-observe)

Nota (decisão do mantenedor, v0.7.x): o Replay2 **coleta métricas de recursos
do próprio host** (CPU/memória/load/disco do servidor onde o control plane
roda) para correlacionar com runs de estresse e comparar ambientes —
`dakota_gateway/host_metrics.py`, tabela `host_metrics`, painel
`/observability/resources`. Isso **não** é monitoramento de infra de terceiros:
é auto-observação local com fim de análise de estresse.

## O Replay2 JÁ TEM

- ✅ Camada de observabilidade interna: `/observability`
- ✅ Painel de recursos do host: `/observability/resources` (CPU/mem/load/disco
  locais, export/import para comparar ambientes)
- ✅ Relatórios: md, json, csv
- ✅ Tendências entre runs
- ✅ Comparação baseline (regressão)
- ✅ SLA tracking
- ✅ Catálogo operacional de cenários

## O que FAZ sentido evoluir

- `/health` e `/ready` — endpoints simples de liveness
- `/metrics` — endpoint com métricas internas (sem dependência externa)
- Discovery Engine — análise de código-fonte legado
- Journey Generation — inferência de jornadas
- Synthetic Data — geração de massa de teste
- Replay Engine — execução determinística
- AI Assessment — análise de resultados

## Projetos separados (NÃO misturar)

| Projeto | Propósito |
|---------|-----------|
| `replay2/` | Ferramenta de validação de migração |
| `remoto_dakota/` | Camada operacional (deploy, healthcheck, scripts) |
| `r-observe/` (externo) | Stack de observabilidade de infraestrutura da Results |

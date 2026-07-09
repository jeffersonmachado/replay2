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
| UI | HTML/CSS/JS vanilla (Jinja2 templates) |
| Build | Shell script → tarball |
| Runtime | Processo direto no host (sem containers) |

## NÃO pertence ao Replay2

- ❌ Prometheus / Grafana / OpenTelemetry
- ❌ PostgreSQL (usa SQLite)
- ❌ Docker / Kubernetes / containers
- ❌ Multi-tenancy (tenant, tenant_id)
- ❌ Infra monitoring (host_status, service_check, hostgroup)
- ❌ Porta 3000, 3001, 9090 (são do stack r-observe)

## O Replay2 JÁ TEM

- ✅ Camada de observabilidade interna: `/observability`
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

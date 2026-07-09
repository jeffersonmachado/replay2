# Benchmark AIX x Linux — Arquitetura

## Objetivo

Executar exatamente a mesma jornada, com a mesma massa e a mesma concorrência nos ambientes AIX e Linux, para comparar performance e identificar divergências.

## Arquitetura Proposta

```
┌─────────────────────────────────────────────────────────────────────┐
│                    BENCHMARK ORCHESTRATOR                            │
│                                                                      │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │                    BENCHMARK DEFINITION                       │   │
│  │                                                                │   │
│  │  journey_id: "cadastro_cliente"                                │   │
│  │  dataset: "clientes_1000"                                      │   │
│  │  concurrency: 10                                               │   │
│  │  duration: 300s                                                │   │
│  │  environments: [aix, linux]                                    │   │
│  │  iterations: 3                                                 │   │
│  └──────────────────────────────────────────────────────────────┘   │
│                           │                                          │
│              ┌────────────┴────────────┐                             │
│              ▼                         ▼                             │
│  ┌──────────────────┐      ┌──────────────────┐                     │
│  │   AIX TARGET     │      │  LINUX TARGET    │                     │
│  │   (Recital 24)   │      │  (Recital 24)    │                     │
│  │                  │      │                  │                     │
│  │  same journey    │      │  same journey    │                     │
│  │  same dataset    │      │  same dataset    │                     │
│  │  same concurrency│      │  same concurrency│                     │
│  │  same seed       │      │  same seed       │                     │
│  └────────┬─────────┘      └────────┬─────────┘                     │
│           │                         │                                │
│           └────────────┬────────────┘                                │
│                        ▼                                             │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │                  METRICS COLLECTOR                             │   │
│  │                                                                │   │
│  │  Por ambiente:                                                 │   │
│  │  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────────┐     │   │
│  │  │ TPS      │ │ Tempo    │ │ CPU      │ │ Memória      │     │   │
│  │  │ (trans/s)│ │ médio    │ │ (user/sys)│ │ (RSS/swap)   │     │   │
│  │  └──────────┘ └──────────┘ └──────────┘ └──────────────┘     │   │
│  │  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────────┐     │   │
│  │  │ I/O      │ │ Locks    │ │ Erros    │ │ Divergências │     │   │
│  │  │ (r/w)    │ │ (wait)   │ │ (count)  │ │ (screen)     │     │   │
│  │  └──────────┘ └──────────┘ └──────────┘ └──────────────┘     │   │
│  └──────────────────────────────────────────────────────────────┘   │
│                        │                                             │
│                        ▼                                             │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │                  COMPARISON ENGINE                             │   │
│  │                                                                │   │
│  │  AIX metrics ────┐                    ┌─── Pass/Fail           │   │
│  │                  ├── Delta Analysis ──┼─── Regression Flag     │   │
│  │  Linux metrics ──┘                    └─── Recommendation      │   │
│  └──────────────────────────────────────────────────────────────┘   │
│                        │                                             │
│                        ▼                                             │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │               EXECUTIVE REPORT                                 │   │
│  │                                                                │   │
│  │  Benchmark: "Migração Recital 8→24 — Lojas"                   │   │
│  │  Data: 2026-06-23                                              │   │
│  │                                                                │   │
│  │  ┌──────────────────────────────────────────────────────┐     │   │
│  │  │ MÉTRICA           │ AIX        │ LINUX      │ DELTA  │     │   │
│  │  ├────────────────────┼────────────┼────────────┼────────┤     │   │
│  │  │ TPS médio         │ 12.5       │ 14.2       │ +13.6% │     │   │
│  │  │ Tempo médio (ms)  │ 800        │ 704        │ -12.0% │     │   │
│  │  │ CPU user %        │ 45         │ 38         │ -15.6% │     │   │
│  │  │ CPU sys %         │ 12         │ 8          │ -33.3% │     │   │
│  │  │ Memória RSS (MB)  │ 256        │ 198        │ -22.7% │     │   │
│  │  │ I/O read (KB/s)   │ 340        │ 280        │ -17.6% │     │   │
│  │  │ I/O write (KB/s)  │ 120        │ 95         │ -20.8% │     │   │
│  │  │ Lock waits         │ 45         │ 23         │ -48.9% │     │   │
│  │  │ Erros              │ 2          │ 1          │ -50.0% │     │   │
│  │  │ Divergências       │ 0          │ 0          │ 0      │     │   │
│  │  └──────────────────────────────────────────────────────┘     │   │
│  │                                                                │   │
│  │  Status: ✅ APROVADO                                           │   │
│  │  Linux supera AIX em todas as métricas.                        │   │
│  │  Nenhuma divergência funcional detectada.                      │   │
│  └──────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────┘
```

## Métricas Coletadas

### Métricas de Throughput

| Métrica | Descrição | Coleta |
|---------|-----------|--------|
| TPS | Transações por segundo | Replay engine |
| Tempo médio por transação | ms por operação | Replay engine |
| Tempo total da run | ms do início ao fim | Replay engine |
| Latência P50/P95/P99 | Percentis de latência | Replay engine |

### Métricas de Sistema

| Métrica | Descrição | Coleta |
|---------|-----------|--------|
| CPU user % | Tempo de CPU em user space | /proc/stat ou vmstat |
| CPU sys % | Tempo de CPU em kernel space | /proc/stat ou vmstat |
| CPU iowait % | Tempo de CPU aguardando I/O | /proc/stat ou vmstat |
| Memória RSS | Resident Set Size do processo | /proc/[pid]/status |
| Memória swap | Swap usado pelo processo | /proc/[pid]/status |

### Métricas de I/O

| Métrica | Descrição | Coleta |
|---------|-----------|--------|
| Read KB/s | Throughput de leitura | iostat |
| Write KB/s | Throughput de gravação | iostat |
| Read IOPS | Operações de leitura/s | iostat |
| Write IOPS | Operações de gravação/s | iostat |

### Métricas de Aplicação

| Métrica | Descrição | Coleta |
|---------|-----------|--------|
| Lock waits | Esperas por lock de registro | Replay engine |
| Deadlocks | Deadlocks detectados | Error detector |
| Erros de validação | Dados rejeitados | Error detector |
| Timeouts | Operações que excederam timeout | Replay engine |
| Divergências de tela | Assinatura diferente do esperado | Replay engine |

## Implementação

### Fase 1: Baseline (Sprint 7)

- Script de coleta de métricas de sistema no target
- Integração com replay_control para métricas de aplicação
- Armazenamento estruturado em `benchmark_results`

### Fase 2: Comparação (Sprint 7)

- Comparison Engine: delta entre ambientes
- Thresholds configuráveis para PASS/WARN/FAIL
- Regressão flag: piora > X% em qualquer métrica

### Fase 3: Relatório (Sprint 8)

- Template de relatório executivo
- Gráficos de comparação
- Recomendações automáticas

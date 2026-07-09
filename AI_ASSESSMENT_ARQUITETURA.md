# AI Assessment — Arquitetura do Diferencial Estratégico

## Visão

Transformar o Replay2 de uma ferramenta de replay em uma plataforma inteligente de validação e benchmark. A IA não apenas executa, mas **analisa, diagnostica e recomenda**.

## Fluxo Desejado

```
┌─────────────────────────────────────────────────────────────────────┐
│                     AI-DRIVEN VALIDATION PIPELINE                     │
│                                                                      │
│   Target                                                             │
│   (Sistema Alvo)                                                     │
│      │                                                               │
│      ▼                                                               │
│   Discovery                                                          │
│   ┌──────────────────────────────────────────────────────────────┐  │
│   │ Análise automática de:                                        │  │
│   │  • Código fonte (.prg, .src, .sql)                           │  │
│   │  • DDL / Schema / ORM                                        │  │
│   │  • Metadados / Dicionário                                    │  │
│   │  • Estrutura de menus                                        │  │
│   │  • Regras de negócio                                         │  │
│   │                                                               │  │
│   │ Output: Entity Catalog + Screen Map + Business Rules         │  │
│   └──────────────────────────────────────────────────────────────┘  │
│      │                                                               │
│      ▼                                                               │
│   Journey Generation                                                 │
│   ┌──────────────────────────────────────────────────────────────┐  │
│   │ Construção automática de:                                     │  │
│   │  • CRUD journeys (por entidade)                               │  │
│   │  • Business journeys (por domínio)                            │  │
│   │  • Stress journeys (volume/concorrência)                      │  │
│   │  • Conditional journeys (IF/ELSE/CASE)                        │  │
│   │  • Macro journeys (end-to-end)                                │  │
│   │                                                               │  │
│   │ Output: Journey Catalog with Dataset Bindings                │  │
│   └──────────────────────────────────────────────────────────────┘  │
│      │                                                               │
│      ▼                                                               │
│   Synthetic Data                                                     │
│   ┌──────────────────────────────────────────────────────────────┐  │
│   │ Geração automática de:                                        │  │
│   │  • Dados válidos (respeitam constraints)                      │  │
│   │  • Dados inválidos (testam validações)                        │  │
│   │  • Dados de borda (testam limites)                            │  │
│   │  • Dados relacionais (respeitam FKs)                          │  │
│   │  • Volume data (stress testing)                               │  │
│   │                                                               │  │
│   │ Output: Datasets JSON/CSV + Template Bindings                │  │
│   └──────────────────────────────────────────────────────────────┘  │
│      │                                                               │
│      ▼                                                               │
│   Replay                                                             │
│   ┌──────────────────────────────────────────────────────────────┐  │
│   │ Execução determinística:                                      │  │
│   │  • Origem (Recital 8) → Destino (Recital 24)                 │  │
│   │  • Ordem global preservada                                    │  │
│   │  • Checkpoint validation                                      │  │
│   │  • Failure capture                                            │  │
│   │  • Multi-environment (AIX / Linux)                            │  │
│   │                                                               │  │
│   │ Output: Run Results + Failures + Screen Diffs                │  │
│   └──────────────────────────────────────────────────────────────┘  │
│      │                                                               │
│      ▼                                                               │
│   Observability                                                      │
│   ┌──────────────────────────────────────────────────────────────┐  │
│   │ Métricas e telemetria:                                        │  │
│   │  • TPS, latência, throughput                                  │  │
│   │  • CPU, memória, I/O                                          │  │
│   │  • Locks, deadlocks, timeouts                                 │  │
│   │  • Divergências de tela                                       │  │
│   │  • Tendências entre runs                                      │  │
│   │                                                               │  │
│   │ Output: Metrics + Trends + Dashboards                        │  │
│   └──────────────────────────────────────────────────────────────┘  │
│      │                                                               │
│      ▼                                                               │
│   AI Assessment  ←── DIFERENCIAL ESTRATÉGICO                         │
│   ┌──────────────────────────────────────────────────────────────┐  │
│   │                                                                │  │
│   │  ┌─────────────────────────────────────────────────────────┐  │  │
│   │  │ 1. GARBAGE COLLECTOR                                     │  │  │
│   │  │    • Identifica código não utilizado                     │  │  │
│   │  │    • Arquivos órfãos                                     │  │  │
│   │  │    • Programas sem chamadas                              │  │  │
│   │  │    • Tabelas sem referência                              │  │  │
│   │  └─────────────────────────────────────────────────────────┘  │  │
│   │  ┌─────────────────────────────────────────────────────────┐  │  │
│   │  │ 2. BOTTLENECK DETECTOR                                   │  │  │
│   │  │    • Identifica gargalos de performance                  │  │  │
│   │  │    • Compara AIX vs Linux                                │  │  │
│   │  │    • Aponta queries lentas                               │  │  │
│   │  │    • Detecta contenção de lock                           │  │  │
│   │  └─────────────────────────────────────────────────────────┘  │  │
│   │  ┌─────────────────────────────────────────────────────────┐  │  │
│   │  │ 3. RISK IDENTIFIER                                       │  │  │
│   │  │    • Código com maior taxa de falha                      │  │  │
│   │  │    • Entidades com mais divergências                     │  │  │
│   │  │    • Fluxos sensíveis a timing                           │  │  │
│   │  │    • Dependências externas frágeis                       │  │  │
│   │  └─────────────────────────────────────────────────────────┘  │  │
│   │  ┌─────────────────────────────────────────────────────────┐  │  │
│   │  │ 4. INCONSISTENCY FINDER                                  │  │  │
│   │  │    • Dados divergentes entre ambientes                   │  │  │
│   │  │    • Comportamento inconsistente                         │  │  │
│   │  │    • Ordem de execução alterada                          │  │  │
│   │  │    • Efeitos colaterais não esperados                    │  │  │
│   │  └─────────────────────────────────────────────────────────┘  │  │
│   │  ┌─────────────────────────────────────────────────────────┐  │  │
│   │  │ 5. REGRESSION DETECTOR                                   │  │  │
│   │  │    • Compara runs entre versões                          │  │  │
│   │  │    • Identifica novas falhas                             │  │  │
│   │  │    • Aponta falhas resolvidas                            │  │  │
│   │  │    • Detecta degradação de performance                   │  │  │
│   │  └─────────────────────────────────────────────────────────┘  │  │
│   │  ┌─────────────────────────────────────────────────────────┐  │  │
│   │  │ 6. ENVIRONMENT COMPARATOR                                 │  │  │
│   │  │    • AIX vs Linux side-by-side                           │  │  │
│   │  │    • Métricas de performance                              │  │  │
│   │  │    • Divergências funcionais                              │  │  │
│   │  │    • Comportamento específico de plataforma               │  │  │
│   │  └─────────────────────────────────────────────────────────┘  │  │
│   │                                                                │  │
│   └──────────────────────────────────────────────────────────────┘  │
│      │                                                               │
│      ▼                                                               │
│   Recommendation                                                     │
│   ┌──────────────────────────────────────────────────────────────┐  │
│   │ Recomendações acionáveis:                                     │  │
│   │                                                                │  │
│   │  PRIORIDADE ALTA:                                             │  │
│   │  • Substituir lock explícito por transacional no módulo X     │  │
│   │  • Corrigir PICTURE do campo Y que gera 45% dos erros         │  │
│   │  • Migrar tabela Z para engine InnoDB (atual: ISAM)           │  │
│   │                                                                │  │
│   │  PRIORIDADE MÉDIA:                                            │  │
│   │  • Ajustar timeout de 5s para 10s no programa W               │  │
│   │  • Revisar índices da tabela V (full scan detectado)          │  │
│   │                                                                │  │
│   │  OBSERVAÇÕES:                                                 │  │
│   │  • Linux 13.6% mais rápido que AIX nas mesmas condições       │  │
│   │  • Nenhuma divergência funcional crítica detectada            │  │
│   └──────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────┘
```

## Componentes do AI Assessment

### 1. Garbage Collector

**Objetivo:** Identificar código morto, tabelas órfãs, arquivos não referenciados.

**Fontes de dados:**
- Call graph do Source Analyzer
- Tabelas referenciadas vs tabelas existentes
- Arquivos no sistema vs programas chamados

**Output:**
```json
{
  "unused_programs": ["old_report.prg", "test_screen.prg"],
  "orphan_tables": ["temp_2020", "backup_clientes"],
  "dead_screens": ["SCREEN_045", "SCREEN_099"],
  "estimated_cleanup_kb": 2048
}
```

### 2. Bottleneck Detector

**Objetivo:** Identificar gargalos de performance comparando ambientes.

**Fontes de dados:**
- Métricas de replay (TPS, latência)
- Métricas de sistema (CPU, I/O, locks)
- Comparação AIX vs Linux

**Output:**
```json
{
  "bottlenecks": [
    {
      "type": "lock_contention",
      "entity": "clientes",
      "aix_wait_ms": 450,
      "linux_wait_ms": 120,
      "severity": "high",
      "recommendation": "Substituir lock explícito por transacional"
    }
  ]
}
```

### 3. Risk Identifier

**Objetivo:** Identificar áreas de maior risco na migração.

**Fontes de dados:**
- Taxa de falha por programa/módulo
- Divergências de tela por fluxo
- Sensibilidade a timing/concorrência
- Dependências externas

**Output:**
```json
{
  "high_risk_areas": [
    {
      "module": "financeiro",
      "failure_rate": 0.15,
      "risk_factors": ["lock_contention", "external_dependency"],
      "recommendation": "Testar com volume real antes do deploy"
    }
  ]
}
```

### 4. Inconsistency Finder

**Objetivo:** Encontrar inconsistências entre ambientes que não são detectadas por checkpoint simples.

**Fontes de dados:**
- Diffs de tela
- Ordem de eventos
- Efeitos colaterais (dados gravados)

### 5. Regression Detector

**Objetivo:** Comparar runs entre versões/ambientes e identificar regressões.

**Fontes de dados:**
- `parent_run_id` chain
- Baseline runs
- Falhas novas vs recorrentes vs resolvidas

### 6. Environment Comparator

**Objetivo:** Comparação lado a lado entre AIX e Linux.

**Fontes de dados:**
- Benchmark results
- Métricas de sistema
- Métricas de aplicação

## Implementação

### Fase 1: Foundation (Sprint 8)

- Extrair dados já existentes (Source Analyzer, Replay Failures, Observability)
- Estruturar em formato comum para análise
- Implementar Regression Detector (já parcialmente existente)

### Fase 2: Analysis Engines (Sprint 9)

- Garbage Collector
- Bottleneck Detector
- Risk Identifier

### Fase 3: Intelligence (Sprint 10)

- Inconsistency Finder
- Environment Comparator
- Recommendation Engine
- Executive Report Generator

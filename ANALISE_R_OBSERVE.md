# Análise de Contaminação r-observe no Replay2

**Data:** 2026-06-23
**Escopo:** Verificação de código, conceitos ou artefatos do projeto `r-observe` (stack de observabilidade da Results) indevidamente presentes no Replay2.

---

## Resultado da Investigação

### NÃO foram encontradas evidências fortes de código literalmente copiado do r-observe:

- ✅ **Zero referências** a `r-observe`, `observe-api`, `observe-grafana`, `observe-prometheus`, `observe-icingaweb2`, `observe-postgres`
- ✅ **Zero referências** a IPs da infra Results (`10.10.2.x`)
- ✅ **Zero referências** a PostgreSQL/psycopg2 (r-observe usa PostgreSQL; Replay2 usa exclusivamente SQLite)
- ✅ **Zero referências** a Docker, docker-compose, containers
- ✅ **Zero referências** a Icinga, Nagios, monitoramento de hosts/serviços
- ✅ CSS e JS usam prefixo `r2ctl-` (Replay2 Control), não prefixos do r-observe

### Foram encontrados indícios de influência/convergência de design:

---

## 1. Porta 3000 no `synthetic-openapi.yaml` ⚠️ SUSPEITO

**Arquivo:** `gateway/control/synthetic-openapi.yaml:10`
```yaml
servers:
  - url: http://localhost:3000
```

**Contexto:**
- O control plane do Replay2 roda na porta **8090** (padrão documentado em `DESENVOLVIMENTO.md`, `server.py:718`)
- A porta **3000** é a porta do `observe-api` (API de observabilidade da Results, conforme `CLAUDE.md`)
- O `openapi.yaml` principal (control plane) usa corretamente `127.0.0.1:8090`

**Ação recomendada:** Corrigir `synthetic-openapi.yaml` para usar a porta 8090 ou remover a URL do servidor (usar relative path).

---

## 2. Conceito de "Tenant" no `gateway_state_service.py` ⚠️ SUSPEITO

**Arquivo:** `gateway/control/services/gateway_state_service.py:77-79`
```python
"tenant": (
    os.environ.get("DAKOTA_TENANT")
    or os.environ.get("TENANT")
    or ""
),
```

**Contexto:**
- Multi-tenancy é um conceito central em plataformas de observabilidade (r-observe)
- Replay2 é uma ferramenta de validação de migração — não tem requisito de multi-tenancy
- O campo `tenant` é exposto no JSON de estado do gateway mas nunca é usado para isolamento de dados
- As variáveis `DAKOTA_INSTANCE_ID` e `DAKOTA_ENV_NAME` são úteis para identificação de instância, mas `DAKOTA_TENANT` parece desnecessário

**Ação recomendada:** Avaliar remoção do conceito de tenant ou documentar claramente seu propósito no contexto Replay2. Se for apenas para identificação de ambiente, renomear para `DAKOTA_DEPLOYMENT` ou similar.

---

## 3. Campos organizacionais no `operational_scenarios` ⚠️ QUESTIONÁVEL

**Arquivo:** `gateway/dakota_gateway/db/schema.py:176-182`
```sql
squad TEXT,
area TEXT,
owner_name TEXT,
owner_contact TEXT,
sla_max_failure_rate_pct REAL,
sla_max_criticality_score REAL,
```

**Contexto:**
- Campos como `squad`, `area`, `owner_name`, `owner_contact` são típicos de plataformas de monitoramento onde times diferentes gerenciam cenários diferentes
- No contexto Replay2, podem ser úteis para times diferentes gerenciarem cenários de replay
- SLA de falha é relevante para replay (taxa máxima de falha aceitável)
- Estes campos são migrados incrementalmente (`db/migrations.py` adiciona essas colunas via ALTER TABLE)

**Ação recomendada:** Manter — são funcionalidades legítimas para governança de cenários operacionais. Documentar no schema.

---

## 4. Sistema de "Analytics Scenarios" com visibilidade e favoritos ⚠️ QUESTIONÁVEL

**Tabela:** `analytics_scenarios` (schema.py)
- `visibility` (private/shared)
- `tags_csv`
- Tabela separada `analytics_scenario_favorites`

**Contexto:**
- Padrão comum em plataformas de observabilidade para salvar filtros/dashboards
- No Replay2, permite salvar consultas de observabilidade para reuso
- README menciona: "a /observability agora permite salvar esses recortes como cenarios analiticos nomeados"

**Ação recomendada:** Manter — funcionalidade legítima de usabilidade. Já documentada no README.

---

## 5. `_Port22CaptureSampler` ⚠️ NOMENCLATURA

**Arquivo:** `gateway/control/server.py:271`
```python
class _Port22CaptureSampler:
```

**Contexto:**
- "Sampler" é terminologia comum em monitoramento (Prometheus sampler, etc.)
- A classe implementa captura passiva na porta 22 — funcionalidade de replay, não de monitoramento
- Nome poderia ser mais descritivo: `_Port22CaptureMonitor` ou `_SshSessionDetector`

**Ação recomendada:** Renomear para evitar confusão com terminologia de observabilidade.

---

## 6. `gateway/state/captures/` com dados reais de sessão ⚠️ SEGURANÇA

**Diretório:** `gateway/state/captures/`

Contém arquivos `.jsonl` com:
- `session_id`, `capture_id`, `capture_session_uuid`
- `actor` (nome de usuário real)
- Dados de tela em base64
- Timestamps reais

**Contexto:**
- O diretório `gateway/state/` está no `.gitignore` ✅
- Os arquivos existem no disco (gerados durante desenvolvimento/teste)
- Contêm dados de sessões de teste, não de produção

**Ação recomendada:** Adicionar `gateway/state/captures/` ao `.gitignore` explicitamente (já coberto por `gateway/state/`). Considerar limpar os dados de teste do disco.

---

## Conclusão

**Não há contaminação direta de código do r-observe.** O que existe é:

1. **Convergência natural de design** — features como cenários salvos, favoritos, visibilidade e tags são padrões comuns em qualquer plataforma com UI operacional
2. **Um artefato suspeito** — `synthetic-openapi.yaml` com porta 3000 (porta do r-observe) que deve ser corrigido
3. **Conceitos importados** — tenant e sampler são conceitos mais típicos de plataformas de observabilidade, mas têm justificativa fraca no contexto Replay2

**Severidade geral:** BAIXA. Nenhum código do r-observe foi literalmente copiado. Os indícios são de influência de design, não de contaminação de código.

---

## Ações Recomendadas

| # | Ação | Prioridade |
|---|------|-----------|
| 1 | Corrigir porta em `synthetic-openapi.yaml` (3000 → 8090) | ALTA |
| 2 | Avaliar remoção/renomeação do conceito "tenant" | MÉDIA |
| 3 | Renomear `_Port22CaptureSampler` | BAIXA |
| 4 | Limpar `gateway/state/captures/` do disco | MÉDIA |
| 5 | Documentar campos squad/area/owner no schema | BAIXA |

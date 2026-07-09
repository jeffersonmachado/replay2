# Filtros de Auditoria - Dakota Replay2

## Novos Filtros Implementados

O sistema de auditoria agora suporta filtros avançados por **uid**, **logname**, **gid** e **data/hora**.

### Campos de Filtro

| Campo | Tipo | Descrição | Exemplo |
|-------|------|-----------|---------|
| **actor** | texto | Usuário que executou a ação (SUDO_USER/LOGNAME) | `alice` |
| **logname** | texto | Nome de login (LOGNAME/USER do ambiente) | `user123` |
| **uid** | número | ID numérico do usuário (UID do sistema) | `1000` |
| **gid** | número | ID numérico do grupo (GID do sistema) | `1000` |
| **session_id** | texto | UUID da sessão | `abc123-def456` |
| **tipo de evento** | texto | Tipo de evento auditado | `checkpoint`, `bytes`, `session_start` |
| **data/hora inicial** | número | Timestamp inicial (milissegundos) | `1704067200000` |
| **data/hora final** | número | Timestamp final (milissegundos) | `1704153600000` |
| **busca livre** | texto | Busca genérica em todos os campos | `erro`, `login` |

## Como Usar

### Via Interface Web

1. Acesse **Gateway > Sessões**
2. Preencha os filtros conforme necessário:
   - Use campos de número para **uid** e **gid**
   - Use campos de data/hora para filtrar por período
   - Os filtros são aplicados automaticamente ao digitar

### Via API REST

```bash
# Exemplo: Filtrar por uid=1000 e gid=1000
curl -X GET "http://localhost:5000/api/gateway/sessions?log_dir=/var/log/dakota&uid=1000&gid=1000"

# Exemplo: Filtrar por período (entre 2024-01-01 00:00 e 2024-01-02 00:00)
curl -X GET "http://localhost:5000/api/gateway/sessions?log_dir=/var/log/dakota&ts_from=1704067200000&ts_to=1704153600000"

# Exemplo: Combinação de filtros
curl -X GET "http://localhost:5000/api/gateway/sessions?log_dir=/var/log/dakota&logname=alice&uid=1000&ts_from=1704067200000"
```

## Informações Armazenadas

Cada evento de auditoria agora registra:

```json
{
  "v": "v1",
  "seq_global": 123,
  "ts_ms": 1704100000000,
  "type": "bytes",
  "actor": "alice",
  "logname": "alice",
  "uid": 1000,
  "gid": 1000,
  "session_id": "abc123-def456",
  "seq_session": 5,
  ...
}
```

## Exemplos de Uso

### 1. Encontrar todas as sessões de um usuário específico
```
ator: alice
```

### 2. Filtrar por período de tempo (últimos 7 dias)
```
data/hora inicial: 1703980800000  (7 dias atrás)
data/hora final: 1704585600000    (hoje)
```

### 3. Combinar múltiplos filtros
- **logname**: `user123`
- **uid**: `1000`
- **tipo de evento**: `checkpoint`

### 4. Buscar por grupo (gid=1000 = grupo 'staff')
```
gid: 1000
```

## Notas Técnicas

- **Timestamps**: Todos os timestamps são em milissegundos (epoch Unix)
- **UID/GID**: São valores numéricos do sistema (Unix/Linux)
- **Lógica AND**: Todos os filtros preenchidos funcionam com AND (precisam ser atendidos simultaneamente)
- **Busca Livre**: Busca em JSON serializado de todos os dados da sessão
- **Compatibilidade**: Retrocompatível com eventos antigos (campos novos são opcionais)

## Conversão de Data para Timestamp (ms)

Para converter uma data legível em timestamp (milissegundos):

```bash
# Linux/macOS - timestamp em milissegundos
date -d "2024-01-01 00:00:00" +%s000

# JavaScript no navegador
new Date("2024-01-01T00:00:00Z").getTime()
```

## Estrutura de Resposta da API

```json
{
  "log_dir": "/var/log/dakota",
  "files_scanned": 15,
  "sessions": [
    {
      "session_id": "abc123-def456",
      "actor": "alice",
      "logname": "alice",
      "uid": 1000,
      "gid": 1000,
      "started_at_ms": 1704100000000,
      "last_ts_ms": 1704100123456,
      "event_count": 250,
      ...
    }
  ],
  "summary": {
    "total_sessions": 10,
    "returned_sessions": 3,
    "filters": {
      "uid": "1000",
      "gid": "1000",
      "actor": "",
      "logname": "",
      "session_id": "",
      "event_type": "",
      "q": "",
      "ts_from": 0,
      "ts_to": 0
    }
  }
}
```

## Troubleshooting

**Nenhuma sessão encontrada?**
1. Verifique se `log_dir` está correto
2. Confirme se há arquivos `audit-*.jsonl` no diretório
3. Tente remover filtros restritivos para validar os dados

**Timestamps incorretos?**
1. Use milissegundos (não segundos)
2. Verifique timezone do servidor vs cliente
3. Use formato: `new Date().getTime()` em JavaScript

**uid/gid não aparecem?**
1. Verifique se os eventos foram capturados após esta atualização
2. Eventos antigos podem não ter esses campos (retrocompatível)
3. Os campos aparecem como `null` se não foram registrados

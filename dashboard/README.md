# Dashboard Web (opcional)

Este dashboard é **opcional** e fica separado do core para não adicionar dependências no AIX.

Ele consome **JSON-lines** emitido pela engine quando você roda com:

```bash
expect bin/main.exp ... --log-format json --log-stream stdout > /tmp/replay2.events.jsonl
```

Ou via CLI:

```bash
expect bin/replay2.exp run ... --log-format json --log-stream stdout > /tmp/replay2.events.jsonl
```

## Rodar

```bash
python3 dashboard/server.py --events-file /tmp/replay2.events.jsonl --listen 127.0.0.1:8080
```

Depois abra no navegador:

`http://127.0.0.1:8080/`


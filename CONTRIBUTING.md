# Contribuindo com o Dakota Replay2

## Stack

| Camada | Tecnologia | Localização |
|--------|-----------|-------------|
| Core engine | Expect/Tcl | `lib/`, `bin/` |
| Gateway | Python 3.10+ | `gateway/dakota_gateway/` |
| Control Plane | Python + SQLite | `gateway/control/` |
| Scripts | POSIX shell | `scripts/`, `remoto_dakota/scripts/` |

## Setup rápido

```bash
cd replay2
python3 -m venv .venv
source .venv/bin/activate
pip install -r gateway/requirements.txt
```

## Rodar testes

```bash
# Todos os testes
.venv/bin/python -m pytest tests/ -v

# Testes do gateway
.venv/bin/python -m pytest gateway/tests/ -v

# Um arquivo específico
.venv/bin/python -m pytest tests/test_targets_api.py -v
```

## Convenções de código

### Python
- PEP 8
- Docstrings em português
- Type hints (`from __future__ import annotations`)
- Imports: stdlib → third-party → dakota_gateway → control

### Shell
- POSIX compatível (não bash-isms)
- `set -euo pipefail` em todos os scripts
- Variáveis de ambiente como fallback: `${VAR:-default}`

## Arquitetura

Ver `FRONTEIRAS.md` para o que PERTENCE e NÃO PERTENCE ao Replay2.

### NÃO adicionar
- Prometheus, Grafana, OpenTelemetry
- PostgreSQL (projeto usa SQLite)
- Docker, Kubernetes
- Multi-tenancy

### SIM adicionar
- Melhorias no Discovery Engine (source_analyzer/)
- Melhorias no Synthetic Engine (synthetic/)
- Métricas internas via `/metrics`
- Endpoints REST na API existente

## Fluxo de PR

1. Criar branch: `feature/nome-da-feature`
2. Implementar + testar
3. Rodar `make test`
4. PR para `develop`
5. Squash merge

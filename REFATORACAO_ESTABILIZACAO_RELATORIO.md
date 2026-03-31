# Relatório de Refatoração e Estabilização

## 1. RESUMO EXECUTIVO

- O que foi simplificado:
  - O control plane deixou de concentrar regras, payloads HTTP e templates em um único arquivo.
  - A persistência SQLite foi separada em uma base nova sob `gateway/dakota_gateway/db/`, mantendo `state_db.py` como shim de compatibilidade.
  - A CLI deixou de concentrar parsing e execução de todos os comandos em um único módulo.
  - A UI do control plane saiu de strings inline em Python e foi movida para templates reais em arquivo.

- O que foi modularizado:
  - Control plane em `routes/`, `services/`, `ui_templates.py` e `templates/`.
  - CLI em `cli_commands/` por domínio.
  - Persistência em `db/connection.py`, `db/schema.py` e `db/migrations.py`.
  - Contratos de tela em `tests/contracts/screens/`.

- O que foi preservado:
  - Captura auditável.
  - Replay determinístico.
  - Ordem global dos eventos.
  - Compliance e enforcement do gateway.
  - SQLite.
  - Core Expect/Tcl.
  - Compatibilidade funcional dos principais endpoints e comandos existentes.

## 2. MUDANÇAS ESTRUTURAIS

- Árvore resumida antes:
  - `gateway/control/server.py` concentrava HTTP, UI inline, lógica de payload e partes de regra.
  - `gateway/dakota_gateway/cli.py` concentrava parsing e execução de múltiplos comandos.
  - `gateway/dakota_gateway/state_db.py` acumulava schema, helpers e bootstrap de persistência.
  - componentes auxiliares antigos e `gateway/internal/audit/` tinham fronteira arquitetural ambígua.

- Árvore resumida depois:
  - `gateway/control/server.py` virou shell HTTP leve.
  - `gateway/control/routes/` concentra rotas por domínio:
    - `admin_routes.py`
    - `catalog_routes.py`
    - `observability_routes.py`
    - `operational_routes.py`
    - `run_routes.py`
  - `gateway/control/services/` concentra regras/payloads:
    - `environment_service.py`
    - `gateway_observability_service.py`
    - `scenario_service.py`
    - `report_service.py`
    - `run_service.py`
  - `gateway/control/templates/` concentra HTML real:
    - `index.html`
    - `login.html`
    - `observability.html`
  - `gateway/control/ui_templates.py` virou loader fino.
  - `gateway/dakota_gateway/db/` concentra persistência nova.
  - `gateway/dakota_gateway/cli_commands/` concentra comandos modulares.

- Arquivos criados:
  - `gateway/control/services/__init__.py`
  - `gateway/control/services/environment_service.py`
  - `gateway/control/services/gateway_observability_service.py`
  - `gateway/control/services/scenario_service.py`
  - `gateway/control/services/report_service.py`
  - `gateway/control/services/run_service.py`
  - `gateway/control/routes/__init__.py`
  - `gateway/control/routes/admin_routes.py`
  - `gateway/control/routes/catalog_routes.py`
  - `gateway/control/routes/observability_routes.py`
  - `gateway/control/routes/operational_routes.py`
  - `gateway/control/routes/run_routes.py`
  - `gateway/control/templates/index.html`
  - `gateway/control/templates/login.html`
  - `gateway/control/templates/observability.html`
  - `gateway/dakota_gateway/db/__init__.py`
  - `gateway/dakota_gateway/db/connection.py`
  - `gateway/dakota_gateway/db/schema.py`
  - `gateway/dakota_gateway/db/migrations.py`
  - `gateway/dakota_gateway/cli_commands/__init__.py`
  - `gateway/dakota_gateway/cli_commands/catalog.py`
  - `gateway/dakota_gateway/cli_commands/runtime.py`
  - `gateway/internal/audit/README.md`
  - `tests/test_cli_catalog_unit.py`
  - `tests/test_control_plane_gateway_route_unit.py`
  - `tests/test_control_routes_unit.py`
  - `tests/test_db_layer_unit.py`
  - `tests/test_gateway_compliance_unit.py`
  - `tests/test_gateway_status_unit.py`
  - `tests/test_report_service_unit.py`
  - `tests/test_scenario_service_unit.py`
  - `tests/test_screen_contracts.py`
  - `tests/test_ui_templates_unit.py`
  - `tests/contracts/screens/case_001_raw.txt`
  - `tests/contracts/screens/case_001_normalized.txt`
  - `tests/contracts/screens/case_001_signature.txt`
  - `tests/contracts/screens/case_002_raw.txt`
  - `tests/contracts/screens/case_002_normalized.txt`
  - `tests/contracts/screens/case_002_signature.txt`

- Arquivos alterados:
  - `gateway/control/server.py`
  - `gateway/control/ui_templates.py`
  - `gateway/dakota_gateway/cli.py`
  - `gateway/dakota_gateway/state_db.py`
  - `README.md`
  - `gateway/README.md`
  - `openapi` e documentação operacional previamente ajustados na frente de compliance/gateway-only

- Arquivos marcados como experimental/futuro:
  - `gateway/internal/audit/`: experimental/futuro, sem integração obrigatória ao runtime atual.

- Arquivos removidos:
  - nenhum removido funcionalmente; houve substituição de conteúdo em `ui_templates.py` e `state_db.py` por versões mais leves e compatíveis.

## 3. VALIDAÇÃO

- Testes executados ao longo da rodada:
  - `python3 -m py_compile gateway/control/server.py`
  - `python3 -m py_compile gateway/control/ui_templates.py`
  - `python3 -m py_compile gateway/control/routes/*.py`
  - `python3 -m py_compile gateway/dakota_gateway/db/*.py`
  - `python3 -m py_compile gateway/dakota_gateway/cli_commands/*.py`
  - `python3 -m py_compile tests/test_control_routes_unit.py`
  - `python3 -m py_compile tests/test_ui_templates_unit.py`
  - `python3 -m unittest`
  - suítes focais adicionais por domínio durante cada onda

- Resultado dos testes:
  - baseline final: `61` testes OK, `11` skipped.

- Fluxos validados manualmente/semiautomaticamente:
  - composição do control plane modular.
  - rotas de `runs`, observabilidade, catálogo, admin e cenários operacionais.
  - carregamento de templates HTML a partir de `control/templates/`.
  - CLI modular de `targets`, `profiles`, `start`, `verify` e `replay`.
  - shim de compatibilidade de `state_db.py`.
  - contratos de normalização/assinatura em Python.

- Limitações de validação:
  - parte da cobertura HTTP/socket continua sensível ao sandbox e por isso alguns testes seguem `skipped`.
  - os contratos de tela ainda validam primariamente a implementação Python; a comparação automatizada Tcl x Python ainda é uma evolução futura.
  - permanecem aliases de compatibilidade em `server.py` porque a suíte trata esses nomes como API interna do módulo.

## 4. RISCOS REMANESCENTES

- O que ainda ficou concentrado:
  - `gateway/control/templates/index.html` e `gateway/control/templates/observability.html` ainda são arquivos grandes; a separação visual melhorou, mas eles ainda não foram quebrados em componentes menores.
  - `server.py` ficou pequeno, mas mantém alguns aliases públicos por compatibilidade de testes e acoplamento histórico.

- O que ainda depende de rodada futura:
  - validação cruzada automática entre Tcl e Python para normalização/assinatura.
  - eventual separação adicional da UI em fragmentos ou assets mais finos.
  - substituição gradual dos aliases de compatibilidade por testes menos acoplados ao namespace do módulo.

- Onde ainda existe dívida técnica:
  - parte da suíte depende de import por caminho e nomes internos do módulo `control_server`.
  - a UI ainda usa bastante JavaScript inline dentro dos próprios HTMLs.
  - o dashboard legado continua no repositório por compatibilidade operacional, ainda que já esteja bem classificado como não-oficial.

## 5. PRÓXIMAS ONDAS SUGERIDAS

- Próximos passos prioritários:
  - quebrar `index.html` e `observability.html` em fragmentos menores ou assets JS dedicados, sem alterar comportamento.
  - reduzir dependência de aliases internos em `server.py`, migrando testes para validar rotas e serviços diretamente.
  - adicionar validação cruzada Tcl/Python para os fixtures de tela.

- Melhorias que ficaram fora desta rodada:
  - componentização mais profunda da UI.
  - migrações SQL com versionamento explícito em arquivos numerados.
  - cobertura E2E mais forte para a superfície web.

- Recomendações para continuação segura:
  - manter refatoração por domínio, sempre com `py_compile` e `unittest` a cada onda.
  - preservar `state_db.py` e aliases públicos enquanto a suíte e consumidores internos dependerem deles.
  - continuar tratando `server.py` apenas como shell de composição, evitando retorno de lógica de negócio para esse arquivo.

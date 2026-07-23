.PHONY: help setup install dev dev-stop dev-logs test test-all test-p2 build tailwind knowledge-base check smoke-test demo clean lint

help:
	@echo "Dakota Replay2 - Desenvolvimento"
	@echo ""
	@echo "Comandos disponíveis:"
	@echo "  make dev            - Inicia ambiente de desenvolvimento completo"
	@echo "  make dev-stop       - Para servidor de desenvolvimento"
	@echo "  make dev-logs       - Mostra logs em tempo real"
	@echo "  make test           - Executa testes principais"
	@echo "  make test-all       - Executa TODOS os testes (Python + verificação Tcl)"
	@echo "  make test-p2        - Executa apenas testes do P2 (Knowledge Base)"
	@echo "  make build          - Gera tarball de distribuição"
	@echo "  make tailwind       - Rebuilda tailwind.css com todas as classes usadas"
	@echo "  make knowledge-base - Pipeline P2-A: analisa fonte e gera relatório"
	@echo "  make check          - Health check rápido (compileall + smoke test)"
	@echo "  make demo           - Executa demo P2-A com sistema de lojas fake"
	@echo "  make smoke-test     - Validação end-to-end completa do stack"
	@echo "  make install        - Instala dependências Python"
	@echo "  make clean          - Remove artefatos temporários"
	@echo "  make setup          - Configuração inicial (venv, deps)"
	@echo ""
	@echo "Variáveis de ambiente:"
	@echo "  DAKOTA_ENV          - lab (default) | homologation | production"
	@echo "  DAKOTA_ADMIN        - admin:senha para bootstrap"
	@echo "  SOURCE_DIR          - diretório de código-fonte para knowledge-base"
	@echo ""

setup:
	@if [ ! -d ".venv" ]; then \
		echo "Criando virtualenv..."; \
		python3 -m venv .venv; \
	fi
	@. .venv/bin/activate && pip install -q flask bottle werkzeug watchfiles pytest
	@echo "✓ Ambiente configurado. Ative com: source .venv/bin/activate"

install:
	@. .venv/bin/activate 2>/dev/null || true && pip install -q flask bottle werkzeug watchfiles

dev:
	./dev.sh

dev-stop:
	@if [ -f /tmp/replay2-control.pid ]; then \
		kill $$(cat /tmp/replay2-control.pid) 2>/dev/null || true; \
		rm -f /tmp/replay2-control.pid; \
		echo "✓ Servidor parado"; \
	else \
		echo "Servidor não está rodando"; \
	fi

dev-logs:
	@tail -f log/replay2-control.log

test:
	@python3 -m pytest tests/test_screen_entity_linker_unit.py tests/test_p2_knowledge_base.py tests/test_capture_knowledge_integrator.py tests/test_source_parser_inferencer_unit.py tests/test_integrated_pipeline_e2e.py tests/test_screen_registry_unit.py tests/test_screen_contracts.py gateway/tests/ -v --tb=short

test-all:
	@echo "=== Python compileall ==="
	@python3 -m compileall gateway/ tests/ 2>&1 | tail -1
	@echo "=== Python tests ==="
	@python3 -m pytest tests/ gateway/tests/ \
		--ignore=tests/test_web_ui_selenium.py \
		--ignore=tests/quick-test-api.py \
		-q
	@echo "=== Tcl syntax check ==="
	@if command -v tclsh >/dev/null 2>&1; then \
		sh scripts/check-tcl-syntax.sh bin/main.exp bin/replay2.exp; \
	else \
		echo "  tclsh não disponível — pulando"; \
	fi
	@echo "✓ test-all concluído"

test-p2:
	@python3 -m pytest -m p2 -v

build:
	@if [ ! -f artifacts/final-acceptance-results.json ]; then \
		echo "AVISO: artifacts/ de aceitação ausentes — o build vai falhar."; \
		echo "Rode primeiro: bash scripts/final-acceptance.sh"; \
	fi
	@./scripts/build-tarball.sh

tailwind:
	@cd gateway && npx tailwindcss --input control/tailwind.input.css --output control/static/tailwind.css --config ../tailwind.config.cjs
	@echo "✓ tailwind.css rebuildado ($(shell wc -c < gateway/control/static/tailwind.css) bytes)"

knowledge-base:
	@if [ -z "$${SOURCE_DIR:-}" ]; then \
		echo "Erro: defina SOURCE_DIR=/caminho/codigo/legado"; \
		exit 1; \
	fi
	@python3 gateway/dakota_gateway/synthetic/demo_p2_knowledge_base.py

check:
	@echo "=== compileall ==="
	@python3 -m compileall gateway/ 2>&1 | tail -1
	@echo "=== smoke tests ==="
	@python3 -m pytest tests/test_screen_entity_linker_unit.py tests/test_p2_knowledge_base.py -q
	@echo "✓ check concluído"
	@echo "  (build do tarball: 'make build' — requer artifacts/ gerados por scripts/final-acceptance.sh)"

smoke-test:
	@./scripts/smoke-test.sh

demo:
	@python3 gateway/dakota_gateway/synthetic/demo_p2_knowledge_base.py

clean:
	@find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	@find . -type f -name "*.pyc" -delete 2>/dev/null || true
	@rm -rf .pytest_cache 2>/dev/null || true
	@echo "✓ Limpeza concluída"

lint:
	@. .venv/bin/activate 2>/dev/null && python -m pylint gateway/ --disable=all --enable=E 2>/dev/null || echo "pylint disponível para linting avançado"

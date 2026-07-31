"""Hardening pós-auditoria P4: varredura recursiva anti-simulação e contenção
do benchmark legado simulado.

Complementa os testes imutáveis de tests/benchmark/ (que não podem ser
alterados — hashes travados em dev/p1-benchmark-test-hashes.sha256) cobrindo
os buracos apontados pela auditoria adversarial:

- a varredura AST oficial usa ``glob("*.py")`` não recursivo → aqui varremos
  recursivamente todo o pacote benchmark/;
- fabricação sem o módulo ``random`` (``secrets``/``os.urandom``) → proibida
  aqui no caminho oficial;
- o relatório do benchmark legado (simulação por seed) não pode emitir
  recomendação de migração em NENHUM consumidor (CLI imprime o dict cru).
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
BENCHMARK_PKG = PROJECT_ROOT / "gateway" / "dakota_gateway" / "benchmark"

FABRICATION_CALLS = {"uniform", "randint", "gauss", "random", "betavariate", "triangular"}
FABRICATION_MODULES = {"random", "secrets", "numpy.random"}
FABRICATION_ATTRS = {("os", "urandom")}


def _iter_benchmark_py():
    files = sorted(BENCHMARK_PKG.rglob("*.py"))
    assert files, f"pacote benchmark vazio ou não encontrado: {BENCHMARK_PKG}"
    return files


def test_varredura_recursiva_sem_fabricacao_de_metricas():
    """Nenhum módulo do pacote benchmark/ (recursivo) pode fabricar números."""
    violacoes: list[str] = []
    for path in _iter_benchmark_py():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in FABRICATION_MODULES or alias.name == "random":
                        violacoes.append(f"{path.name}:{node.lineno} import {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                if node.module and (node.module in FABRICATION_MODULES or node.module == "random"):
                    violacoes.append(f"{path.name}:{node.lineno} from {node.module} import ...")
            elif isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Attribute):
                    if func.attr in FABRICATION_CALLS:
                        violacoes.append(f"{path.name}:{node.lineno} .{func.attr}()")
                    if isinstance(func.value, ast.Name) and (func.value.id, func.attr) in FABRICATION_ATTRS:
                        violacoes.append(f"{path.name}:{node.lineno} {func.value.id}.{func.attr}()")
                elif isinstance(func, ast.Name) and func.id in FABRICATION_CALLS:
                    violacoes.append(f"{path.name}:{node.lineno} {func.id}()")
    assert not violacoes, "fabricação de métricas no caminho oficial:\n" + "\n".join(violacoes)


def _chaves_e_textos(obj, out):
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.append(str(k))
            _chaves_e_textos(v, out)
    elif isinstance(obj, list):
        for item in obj:
            _chaves_e_textos(item, out)
    elif isinstance(obj, str):
        out.append(obj)


def test_legado_nunca_emite_recomendacao_de_migracao():
    """run_and_report (consumido cru pelo CLI) não pode recomendar migração."""
    from dakota_gateway.benchmark_legacy import BenchmarkOrchestrator, BenchmarkConfig

    config = BenchmarkConfig(
        benchmark_id="audit-p4",
        name="auditoria",
        journey_id="j1",
        environments=[{"name": "a", "host": "h1"}, {"name": "b", "host": "h2"}],
        concurrency=1,
        iterations=1,
        seed=42,
    )
    report = BenchmarkOrchestrator().run_and_report(config)

    assert report["simulation"] is True
    assert "simulation_notice" in report

    tokens: list[str] = []
    _chaves_e_textos(report, tokens)
    for token in tokens:
        assert "recommendation" not in token.lower(), f"recomendação no relatório legado: {token!r}"
        assert "migracao aprovada" not in token.lower(), f"texto de aprovação no relatório legado: {token!r}"

    # o relatório deve ser serializável (CLI imprime com json.dumps)
    json.dumps(report, ensure_ascii=False)


def test_legado_veredito_simulado_nao_e_veredito_oficial():
    """Mesmo com verdict=PASS interno, o selo simulation=true desautoriza o número."""
    from dakota_gateway.benchmark_legacy import BenchmarkOrchestrator, BenchmarkConfig

    config = BenchmarkConfig(
        benchmark_id="audit-p4b",
        name="auditoria",
        journey_id="j1",
        environments=[{"name": "a", "host": "h1"}, {"name": "b", "host": "h2"}],
        concurrency=1,
        iterations=1,
        seed=7,
    )
    report = BenchmarkOrchestrator().run_and_report(config)
    for comp in report["comparisons"]:
        assert report["simulation"] is True
        assert "recommendations" not in comp


@pytest.mark.parametrize("arquivo", ["stress_runner.py", "remote_executor.py"])
def test_resultados_sinteticos_default_simulation_true(arquivo):
    """Dataclasses do stress sintético nascem com simulation=True."""
    source = (PROJECT_ROOT / "gateway" / "dakota_gateway" / "synthetic" / arquivo).read_text(encoding="utf-8")
    assert "simulation: bool = True" in source

"""§5.1 — Proibição de simulação no caminho oficial do benchmark.

Varredura ESTÁTICA (AST) de todos os arquivos
``gateway/dakota_gateway/benchmark/*.py``. O benchmark oficial é REPLAY REAL:
qualquer geração de números aleatórios ou caminho "simulado/placeholder" no
pacote é uma violação grave (foi exatamente o problema do skeleton
``__init__.py``: ``random.Random`` + ``rng.uniform`` em ``_run_single``).

Violações detectadas:
- ``import random`` / ``from random import ...`` em qualquer forma;
- atributos ``random.Random``/``random.uniform``/``random.randint`` etc.;
- variáveis criadas de ``random.Random(...)`` e depois usadas (ex. ``rng.uniform``);
- identificadores/funções contendo ``placeholder`` ou ``simula`` (cobre
  simulate/simulation/simulação/simulacao), incluindo ``_simulate_screens``;
- strings (exceto docstrings) contendo ``placeholder`` ou ``simula``;
- import de ``synthetic.stress_runner`` (o runner de estresse sintético NÃO
  pode ser reusado como motor do benchmark oficial).

Docstrings são excluídas da varredura de strings para permitir comentários
explicativos (ex.: "isto não é simulação"); o restante é intolerância zero.
"""
from __future__ import annotations

import ast
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "gateway"))

BENCH_DIR = ROOT / "gateway" / "dakota_gateway" / "benchmark"

_BANNED_RANDOM_ATTRS = {
    "Random", "uniform", "randint", "random", "choice", "choices",
    "gauss", "betavariate", "expovariate", "sample", "randrange",
}
_BANNED_WORD_RE = re.compile(r"placeholder|simula", re.IGNORECASE)
_STRESS_RUNNER_RE = re.compile(r"synthetic\.stress_runner")


def _docstring_nodes(tree: ast.AST) -> set[int]:
    """IDs dos nós de string que são docstrings (excluídos da varredura)."""
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                ids.add(id(body[0].value))
    return ids


def _violations_in(path: Path) -> list[str]:
    """Lista violações de simulação em um arquivo do pacote benchmark."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    docstrings = _docstring_nodes(tree)
    violations: list[str] = []

    # Nomes criados a partir de random.Random(...) — ex.: rng = random.Random(seed)
    rng_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            func = node.value.func
            if (isinstance(func, ast.Attribute) and func.attr == "Random"
                    and isinstance(func.value, ast.Name) and func.value.id == "random"):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        rng_names.add(target.id)

    for node in ast.walk(tree):
        lineno = getattr(node, "lineno", "?")

        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "random" or alias.name.startswith("random."):
                    violations.append(f"{path.name}:{lineno}: import de '{alias.name}'")
                if _STRESS_RUNNER_RE.search(alias.name):
                    violations.append(
                        f"{path.name}:{lineno}: import proibido '{alias.name}'")

        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module == "random" or module.startswith("random."):
                violations.append(f"{path.name}:{lineno}: import de '{module}'")
            if _STRESS_RUNNER_RE.search(module):
                violations.append(
                    f"{path.name}:{lineno}: import proibido '{module}'")

        elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            if node.value.id == "random" and node.attr in _BANNED_RANDOM_ATTRS:
                violations.append(
                    f"{path.name}:{lineno}: uso de random.{node.attr}")
            elif node.value.id in rng_names and node.attr in _BANNED_RANDOM_ATTRS:
                violations.append(
                    f"{path.name}:{lineno}: uso de {node.value.id}.{node.attr} "
                    f"(gerador criado de random.Random)")

        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if _BANNED_WORD_RE.search(node.name):
                violations.append(
                    f"{path.name}:{lineno}: função com nome proibido '{node.name}'")

        elif isinstance(node, ast.Name):
            if _BANNED_WORD_RE.search(node.id):
                violations.append(
                    f"{path.name}:{lineno}: identificador proibido '{node.id}'")

        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) not in docstrings and _BANNED_WORD_RE.search(node.value):
                trecho = node.value[:60].replace("\n", " ")
                violations.append(
                    f"{path.name}:{lineno}: string proibida '{trecho}'")

    return violations


class TestNoSimulationOfficial(unittest.TestCase):
    """O pacote benchmark oficial não pode conter NENHUMA simulação."""

    def test_pacote_benchmark_sem_simulacao(self) -> None:
        arquivos = sorted(BENCH_DIR.glob("*.py"))
        self.assertTrue(arquivos, f"pacote benchmark não encontrado em {BENCH_DIR}")
        todas: list[str] = []
        for arq in arquivos:
            todas.extend(_violations_in(arq))
        self.assertEqual(
            [], todas,
            "Simulação detectada no caminho oficial do benchmark:\n"
            + "\n".join(f"  - {v}" for v in todas),
        )


if __name__ == "__main__":
    unittest.main()

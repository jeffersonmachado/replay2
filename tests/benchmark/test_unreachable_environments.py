"""§5.2 — Ambientes inacessíveis: o experimento NUNCA pode dar PASS.

Contrato válido com 2 ambientes cujos adapters falham o preflight (um
retornando ``{"ok": False}`` e outro levantando exceção de conexão). O
executor deve abortar com:

- ``ExperimentResult.status == "FAILED"``
- ``ExperimentResult.verdict == "INCONCLUSIVE"``
- ``ExperimentResult.reason == "environment_unreachable"``

Os adapters fake são definidos aqui mesmo no teste; qualquer método além de
``preflight`` levanta ``AssertionError`` — se o executor tentar executar
jornada em ambiente reprovado no preflight, o teste quebra na hora.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "gateway"))

from dakota_gateway.benchmark.contract import (  # noqa: E402
    StopConditions,
    ThinkTimeProfile,
    create_contract,
)
from dakota_gateway.benchmark.executor import BenchmarkExecutor  # noqa: E402


def _contrato_dois_ambientes() -> "object":
    """Contrato mínimo válido com os ambientes 'aix-off' e 'linux-off'."""
    return create_contract(
        experiment_id="exp-p1-unreachable",
        journey_set_sha256="a" * 64,
        dataset_sha256="b" * 64,
        application_version_sha256="c" * 64,
        seed=42,
        terminal_geometry="80x24",
        concurrency_levels=(1,),
        warmup_seconds=1,
        measurement_seconds=1,
        cooldown_seconds=1,
        iterations=1,
        think_time_profile=ThinkTimeProfile(
            type="deterministic", sha256="d" * 64, params={"fixed_ms": 0}),
        stop_conditions=StopConditions(),
        environments=("aix-off", "linux-off"),
    )


class _AdapterRecusado:
    """Preflight retorna ok=False (host inacessível); o resto é proibido."""

    def preflight(self) -> dict:
        return {"ok": False, "checks": [
            {"name": "ssh_connectivity", "ok": False,
             "detail": "connection refused (simulado pelo teste)"},
        ]}

    def __getattr__(self, nome: str):
        raise AssertionError(
            f"método {nome} não deveria ser chamado após preflight reprovado")


class _AdapterExcecao:
    """Preflight levanta exceção de rede; o resto é proibido."""

    def preflight(self) -> dict:
        raise OSError("No route to host (simulado pelo teste)")

    def __getattr__(self, nome: str):
        raise AssertionError(
            f"método {nome} não deveria ser chamado após preflight com exceção")


class TestUnreachableEnvironments(unittest.TestCase):
    """Ambientes inacessíveis → FAILED/INCONCLUSIVE/environment_unreachable."""

    def _executa(self, adapters: dict) -> "object":
        with tempfile.TemporaryDirectory() as tmp:
            executor = BenchmarkExecutor(
                _contrato_dois_ambientes(), adapters, Path(tmp))
            return executor.run()

    def test_preflight_ok_false(self) -> None:
        resultado = self._executa({
            "aix-off": _AdapterRecusado(),
            "linux-off": _AdapterRecusado(),
        })
        self.assertEqual("FAILED", resultado.status)
        self.assertEqual("INCONCLUSIVE", resultado.verdict)
        self.assertEqual("environment_unreachable", resultado.reason)
        self.assertNotEqual("PASS", resultado.verdict)

    def test_preflight_com_excecao(self) -> None:
        resultado = self._executa({
            "aix-off": _AdapterExcecao(),
            "linux-off": _AdapterRecusado(),
        })
        self.assertEqual("FAILED", resultado.status)
        self.assertEqual("INCONCLUSIVE", resultado.verdict)
        self.assertEqual("environment_unreachable", resultado.reason)
        self.assertNotEqual("PASS", resultado.verdict)


if __name__ == "__main__":
    unittest.main()

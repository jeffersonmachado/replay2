"""§5.4 — Paridade de contrato entre ambientes (validate_environment_parity).

Antes de QUALQUER execução, os ambientes comparados precisam rodar exatamente
a mesma jornada, dataset, seed, níveis de concorrência e duração de medição.
Cada divergência deve levantar ``ContractViolation``; configurações idênticas
não levantam nada.

Interpretação documentada: ``validate_environment_parity`` recebe uma lista de
dicts (um por ambiente) com as chaves ``journey_set_sha256``,
``dataset_sha256``, ``seed``, ``concurrency_levels`` e ``measurement_seconds``
— os cinco eixos de paridade citados no contrato §5.4. A ordem dos níveis de
concorrência importa (é a escada de carga executada).
"""
from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "gateway"))

from dakota_gateway.benchmark.contract import (  # noqa: E402
    ContractViolation,
    validate_environment_parity,
)

_BASE = {
    "environment_id": "env",
    "journey_set_sha256": "a" * 64,
    "dataset_sha256": "b" * 64,
    "seed": 42,
    "concurrency_levels": [1, 5, 10, 20],
    "measurement_seconds": 60,
}


def _dois_ambientes(**sobrescreve_segundo: object) -> list[dict]:
    """Dois ambientes idênticos, salvo os campos sobrescritos no segundo."""
    primeiro = copy.deepcopy(_BASE)
    primeiro["environment_id"] = "aix"
    segundo = copy.deepcopy(_BASE)
    segundo["environment_id"] = "linux"
    segundo.update(sobrescreve_segundo)
    return [primeiro, segundo]


class TestContractParity(unittest.TestCase):
    """Cada eixo divergente levanta ContractViolation; igualdade não levanta."""

    def test_configuracoes_identicas_nao_levantam(self) -> None:
        validate_environment_parity(_dois_ambientes())  # não pode levantar

    def test_journey_set_divergente(self) -> None:
        with self.assertRaises(ContractViolation):
            validate_environment_parity(
                _dois_ambientes(journey_set_sha256="f" * 64))

    def test_dataset_divergente(self) -> None:
        with self.assertRaises(ContractViolation):
            validate_environment_parity(
                _dois_ambientes(dataset_sha256="e" * 64))

    def test_seed_divergente(self) -> None:
        with self.assertRaises(ContractViolation):
            validate_environment_parity(_dois_ambientes(seed=43))

    def test_concurrency_levels_divergentes(self) -> None:
        with self.assertRaises(ContractViolation):
            validate_environment_parity(
                _dois_ambientes(concurrency_levels=[1, 5, 10]))
        # ordem diferente também é divergência (a escada executada muda)
        with self.assertRaises(ContractViolation):
            validate_environment_parity(
                _dois_ambientes(concurrency_levels=[20, 10, 5, 1]))

    def test_measurement_seconds_divergente(self) -> None:
        with self.assertRaises(ContractViolation):
            validate_environment_parity(
                _dois_ambientes(measurement_seconds=30))


if __name__ == "__main__":
    unittest.main()

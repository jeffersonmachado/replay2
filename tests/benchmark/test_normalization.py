"""§5.10/§19 — Normalização de throughput por capacidade (normalize).

Modelos: AIX com LPAR (``entitled_capacity=0.5``, ``virtual_processors=2``,
``physical_processors=1``, 4096 MB) e Linux bare-metal (8 vCPUs = 8 cores
físicos, 8192 MB).

Fórmulas documentadas (interpretação do contrato §19):

- ``tps_per_vcpu``              = tps / virtual_processors
- ``tps_per_physical_core``     = tps / physical_processors
- ``tps_per_entitled_capacity`` = tps / entitled_capacity   (aplicável a AIX)
- ``tps_per_gb``                = tps / (memory_mb / 1024)

Interpretações fixadas por este teste:

- ``tps_per_entitled_capacity`` NÃO é aplicável a Linux: fica ``None`` e isso
  NÃO marca o resultado como inconclusivo;
- campo APLICÁVEL ausente ou zero (ex.: AIX com ``entitled_capacity=0`` ou
  ``memory_mb=0``) → valor ``None`` + ``status == NORMALIZATION_INCONCLUSIVE``
  no resultado global (nunca PASS/OK com dado faltando);
- o retorno segue ``{"per_environment": {...}, "formulas": {...}, "status": str}``
  e cada entrada de ambiente carrega ``status`` próprio;
- ``env_results`` traz o TPS medido por ambiente: ``{"<env_id>": {"tps": x}}``.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "gateway"))

from dakota_gateway.benchmark.environments import (  # noqa: E402
    CpuModel,
    EnvironmentModel,
)
from dakota_gateway.benchmark.normalize import (  # noqa: E402
    NORMALIZATION_INCONCLUSIVE,
    normalize,
)


def _modelo_aix(entitled: float = 0.5, memory_mb: int = 4096) -> EnvironmentModel:
    return EnvironmentModel(
        environment_id="aix",
        platform="AIX",
        architecture="POWER",
        host="192.0.2.1",
        cpu=CpuModel(
            model="POWER9",
            virtual_processors=2,
            physical_processors=1,
            entitled_capacity=entitled,
            smt_mode=8,
            shared_or_dedicated="shared",
            capped_or_uncapped="capped",
        ),
        memory_mb=memory_mb,
    )


def _modelo_linux() -> EnvironmentModel:
    return EnvironmentModel(
        environment_id="linux",
        platform="Linux",
        architecture="x86_64",
        host="192.0.2.2",
        cpu=CpuModel(
            model="Xeon",
            virtual_processors=8,
            physical_processors=8,
        ),
        memory_mb=8192,
    )


class TestNormalization(unittest.TestCase):
    """Normalização por vCPU, core físico, entitled capacity e GB de RAM."""

    def test_valores_pela_formula(self) -> None:
        resultado = normalize(
            {"aix": {"tps": 100.0}, "linux": {"tps": 160.0}},
            {"aix": _modelo_aix(), "linux": _modelo_linux()},
        )

        aix = resultado["per_environment"]["aix"]
        self.assertAlmostEqual(50.0, aix["tps_per_vcpu"])               # 100/2
        self.assertAlmostEqual(100.0, aix["tps_per_physical_core"])     # 100/1
        self.assertAlmostEqual(200.0, aix["tps_per_entitled_capacity"])  # 100/0.5
        self.assertAlmostEqual(25.0, aix["tps_per_gb"])                 # 100/4

        linux = resultado["per_environment"]["linux"]
        self.assertAlmostEqual(20.0, linux["tps_per_vcpu"])             # 160/8
        self.assertAlmostEqual(20.0, linux["tps_per_physical_core"])    # 160/8
        self.assertAlmostEqual(20.0, linux["tps_per_gb"])               # 160/8
        # entitled capacity não se aplica a Linux: None SEM inconclusão
        self.assertIsNone(linux["tps_per_entitled_capacity"])

        # com todos os campos aplicáveis presentes, nada fica inconclusivo
        self.assertNotEqual(NORMALIZATION_INCONCLUSIVE, resultado["status"])
        self.assertNotEqual(NORMALIZATION_INCONCLUSIVE, aix["status"])
        self.assertNotEqual(NORMALIZATION_INCONCLUSIVE, linux["status"])

        self.assertIn("formulas", resultado)
        self.assertIsInstance(resultado["formulas"], dict)

    def test_campo_aplicavel_zero_gera_none_e_inconclusive(self) -> None:
        # AIX sem entitled_capacity (0.0): métrica aplicável ausente.
        resultado = normalize(
            {"aix": {"tps": 100.0}, "linux": {"tps": 160.0}},
            {"aix": _modelo_aix(entitled=0.0), "linux": _modelo_linux()},
        )
        aix = resultado["per_environment"]["aix"]
        self.assertIsNone(aix["tps_per_entitled_capacity"])
        self.assertEqual(NORMALIZATION_INCONCLUSIVE, aix["status"])
        self.assertEqual(NORMALIZATION_INCONCLUSIVE, resultado["status"])
        self.assertNotEqual("PASS", resultado["status"])
        # as demais métricas do AIX continuam calculadas
        self.assertAlmostEqual(50.0, aix["tps_per_vcpu"])

    def test_memoria_zero_gera_none_e_inconclusive(self) -> None:
        resultado = normalize(
            {"aix": {"tps": 100.0}, "linux": {"tps": 160.0}},
            {"aix": _modelo_aix(memory_mb=0), "linux": _modelo_linux()},
        )
        aix = resultado["per_environment"]["aix"]
        self.assertIsNone(aix["tps_per_gb"])
        self.assertEqual(NORMALIZATION_INCONCLUSIVE, resultado["status"])
        self.assertNotEqual("PASS", resultado["status"])


if __name__ == "__main__":
    unittest.main()

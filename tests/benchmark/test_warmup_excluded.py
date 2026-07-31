"""§5.7/§12 — Warmup e cooldown NUNCA entram no agregado oficial.

Executa o ``BenchmarkExecutor`` com o adaptador controlado (operações reais
contra subprocesso local) e confirma que:

- ``EnvironmentRunResult.samples`` (o agregado oficial) contém APENAS
  amostras com ``phase == "MEASUREMENT"``;
- as amostras de WARMUP e COOLDOWN existem e ficam guardadas nos campos
  próprios ``warmup_samples`` / ``cooldown_samples`` (rastreáveis, mas fora
  do agregado);
- todas as amostras têm ``started_ns < finished_ns`` e ``latency_ms > 0``
  (medição real, não fabricada).

Interpretações documentadas (o contrato não fixa estes pontos):

- o executor executa as fases WARMUP → MEASUREMENT → COOLDOWN repetindo a(s)
  jornada(s) até decorrer o tempo da fase (``warmup_seconds`` etc.), sempre
  executando ao menos UMA jornada completa por fase;
- as jornadas são passadas ao executor pelo parâmetro opcional ``journeys``
  (lista de dicts ``{"journey_id", "steps": [...]}``) — o contrato guarda
  apenas o sha256 do conjunto de jornadas, então a origem concreta precisava
  ser definida; escolhemos a forma mais simples.
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
from tests.benchmark.support.controlled_adapter import ControlledAdapter  # noqa: E402

JORNADA = {
    "journey_id": "j-fases",
    "steps": [
        {"step_id": "consulta", "delay_ms": 2.0},
        {"step_id": "inclui", "delay_ms": 2.0},
        {"step_id": "confirma", "delay_ms": 0.0},
    ],
}


def _contrato_fases() -> "object":
    return create_contract(
        experiment_id="exp-p1-warmup",
        journey_set_sha256="a" * 64,
        dataset_sha256="b" * 64,
        application_version_sha256="c" * 64,
        seed=7,
        terminal_geometry="80x24",
        concurrency_levels=(1,),
        warmup_seconds=1,
        measurement_seconds=1,
        cooldown_seconds=1,
        iterations=1,
        think_time_profile=ThinkTimeProfile(
            type="none", sha256="d" * 64, params={}),
        stop_conditions=StopConditions(),
        environments=("env-controlled",),
    )


class TestWarmupExcluded(unittest.TestCase):
    """Amostras WARMUP/COOLDOWN fora do agregado oficial, mas preservadas."""

    def test_agregado_oficial_so_contem_measurement(self) -> None:
        adapter = ControlledAdapter(environment_id="env-controlled")
        with tempfile.TemporaryDirectory() as tmp:
            executor = BenchmarkExecutor(
                _contrato_fases(),
                {"env-controlled": adapter},
                Path(tmp),
                journeys=[JORNADA],
            )
            resultado = executor.run()

        self.assertEqual("COMPLETED", resultado.status)
        self.assertTrue(resultado.runs, "executor não produziu runs")
        run = resultado.runs[0]
        self.assertEqual("COMPLETED", run.status)

        # Agregado oficial: SOMENTE MEASUREMENT
        self.assertTrue(run.samples, "sem amostras de MEASUREMENT")
        self.assertTrue(
            all(s.phase == "MEASUREMENT" for s in run.samples),
            "amostra fora de MEASUREMENT contaminou o agregado oficial",
        )

        # Warmup e cooldown existem e ficam nos campos próprios
        self.assertTrue(run.warmup_samples, "sem amostras de WARMUP")
        self.assertTrue(
            all(s.phase == "WARMUP" for s in run.warmup_samples))
        self.assertTrue(run.cooldown_samples, "sem amostras de COOLDOWN")
        self.assertTrue(
            all(s.phase == "COOLDOWN" for s in run.cooldown_samples))

        # Nenhuma amostra de warmup/cooldown vaza para o agregado oficial
        ids_oficial = {id(s) for s in run.samples}
        self.assertFalse(any(id(s) in ids_oficial for s in run.warmup_samples))
        self.assertFalse(any(id(s) in ids_oficial for s in run.cooldown_samples))

        # Todas as amostras foram medidas de verdade
        for amostra in (*run.samples, *run.warmup_samples, *run.cooldown_samples):
            self.assertLess(amostra.started_ns, amostra.finished_ns)
            self.assertGreater(amostra.latency_ms, 0.0)


if __name__ == "__main__":
    unittest.main()

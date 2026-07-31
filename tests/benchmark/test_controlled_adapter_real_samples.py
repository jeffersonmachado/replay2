"""§5.5 — Adaptador controlado: medição de latência REAL, não inventada.

Usa ``tests/benchmark/support/controlled_adapter.py``: um
``EnvironmentExecutionAdapter`` que executa operações reais contra um
subprocesso Python local (pipes stdin/stdout), com atraso real injetado no
processo filho e cronometragem por ``time.monotonic_ns``.

Dois blocos de verificação:

1. ``test_amostras_reais_*`` — dependem APENAS do suporte (passam já na P1):
   mini-jornada de 5 operações com atrasos conhecidos (40ms em algumas
   respostas, 0 em outras) + 2 operações de warmup; confirma timestamps reais
   e distintos, latência ≈ atraso injetado e separação WARMUP × MEASUREMENT.

2. ``test_compute_stats_sobre_latencias_reais`` — alimenta
   ``dakota_gateway.benchmark.stats.compute_stats`` com as latências reais
   medidas e exige média coerente com os atrasos injetados (falha na P1 porque
   o módulo ``stats`` ainda não existe — é o teste que o P2 deve fazer passar).
"""
from __future__ import annotations

import statistics
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "gateway"))

from tests.benchmark.support.controlled_adapter import ControlledAdapter  # noqa: E402

# Mini-jornada de MEASUREMENT: 5 operações, atrasos reais conhecidos.
ATRASOS_MS = [40.0, 0.0, 40.0, 0.0, 40.0]
MEDIA_ATRASOS_MS = sum(ATRASOS_MS) / len(ATRASOS_MS)  # 24.0 ms

JORNADA_MEDICAO = {
    "journey_id": "j-medicao",
    "steps": [
        {"step_id": f"op{i}", "delay_ms": atraso, "payload": f"carga{i}"}
        for i, atraso in enumerate(ATRASOS_MS)
    ],
}
JORNADA_WARMUP = {
    "journey_id": "j-warmup",
    "steps": [
        {"step_id": "w0", "delay_ms": 0.0},
        {"step_id": "w1", "delay_ms": 0.0},
    ],
}

# Tolerâncias: o round-trip real por pipes locais custa poucos ms; a latência
# medida deve ser >= atraso injetado e nunca explodir além de uma folga ampla.
FOLGA_SUPERIOR_MS = 100.0


class TestControlledAdapterRealSamples(unittest.TestCase):
    """Medição real de latência com o adaptador controlado (suporte puro)."""

    def setUp(self) -> None:
        self.adapter = ControlledAdapter(environment_id="env-controlled")
        self.addCleanup(self.adapter.cleanup)
        self.preflight = self.adapter.preflight()
        self.sessao = self.adapter.start_session("vu-1")
        self.adapter.execute_journey(self.sessao, JORNADA_WARMUP, phase="WARMUP")
        self.adapter.execute_journey(self.sessao, JORNADA_MEDICAO, phase="MEASUREMENT")

    def tearDown(self) -> None:
        self.adapter.stop_session(self.sessao)

    def test_preflight_real_ok(self) -> None:
        self.assertTrue(self.preflight["ok"], self.preflight)
        self.assertTrue(all(c["ok"] for c in self.preflight["checks"]))

    def test_amostras_reais_timestamps_distintos_e_ordenados(self) -> None:
        amostras = self.adapter.samples
        self.assertEqual(7, len(amostras))  # 2 warmup + 5 medição
        todos_ns: list[int] = []
        for amostra in amostras:
            self.assertLess(amostra.started_ns, amostra.finished_ns)
            self.assertGreater(amostra.latency_ms, 0.0)
            todos_ns.extend((amostra.started_ns, amostra.finished_ns))
        # monotonic_ns garante timestamps reais e distintos entre si
        self.assertEqual(len(todos_ns), len(set(todos_ns)))

    def test_latencia_medida_compativel_com_atraso_injetado(self) -> None:
        for amostra, atraso in zip(self.adapter.measurement_samples, ATRASOS_MS):
            # latência real nunca é menor que o atraso injetado (folga p/ clock)
            self.assertGreaterEqual(amostra.latency_ms, atraso - 5.0,
                                    f"{amostra.step_id}: {amostra.latency_ms}ms")
            # nem absurdamente maior (sem latência inventada)
            self.assertLessEqual(amostra.latency_ms, atraso + FOLGA_SUPERIOR_MS,
                                 f"{amostra.step_id}: {amostra.latency_ms}ms")
            self.assertTrue(amostra.success)
            self.assertFalse(amostra.functional_divergence)

    def test_amostras_measurement_separadas_de_warmup(self) -> None:
        self.assertEqual(5, len(self.adapter.measurement_samples))
        self.assertEqual(2, len(self.adapter.warmup_samples))
        self.assertEqual(0, len(self.adapter.cooldown_samples))
        self.assertTrue(all(s.phase == "MEASUREMENT"
                            for s in self.adapter.measurement_samples))
        self.assertTrue(all(s.phase == "WARMUP"
                            for s in self.adapter.warmup_samples))
        # nenhuma amostra de warmup contamina o conjunto de medição
        ids_medicao = {id(s) for s in self.adapter.measurement_samples}
        self.assertFalse(any(id(s) in ids_medicao
                             for s in self.adapter.warmup_samples))

    def test_media_simples_coerente_com_atrasos(self) -> None:
        """Média (statistics puro) das latências reais ≈ média dos atrasos."""
        latencias = [s.latency_ms for s in self.adapter.measurement_samples]
        media = statistics.mean(latencias)
        self.assertGreaterEqual(media, MEDIA_ATRASOS_MS - 5.0)
        self.assertLessEqual(media, MEDIA_ATRASOS_MS + FOLGA_SUPERIOR_MS)


class TestComputeStatsSobreLatenciasReais(unittest.TestCase):
    """compute_stats do pacote benchmark sobre latências reais medidas."""

    def test_compute_stats_coerente_com_atrasos_injetados(self) -> None:
        from dakota_gateway.benchmark.stats import compute_stats

        adapter = ControlledAdapter(environment_id="env-controlled")
        self.addCleanup(adapter.cleanup)
        sessao = adapter.start_session("vu-1")
        adapter.execute_journey(sessao, JORNADA_MEDICAO, phase="MEASUREMENT")
        adapter.stop_session(sessao)

        latencias = [s.latency_ms for s in adapter.measurement_samples]
        self.assertEqual(5, len(latencias))
        stats = compute_stats(latencias)
        self.assertEqual(5, stats.n)
        # média coerente com os atrasos reais injetados (24ms + custo real)
        self.assertGreaterEqual(stats.mean, MEDIA_ATRASOS_MS - 5.0)
        self.assertLessEqual(stats.mean, MEDIA_ATRASOS_MS + FOLGA_SUPERIOR_MS)
        # máximo coerente com a operação mais lenta (40ms injetados)
        self.assertGreaterEqual(stats.max, 35.0)
        self.assertLessEqual(stats.max, 40.0 + FOLGA_SUPERIOR_MS)


if __name__ == "__main__":
    unittest.main()

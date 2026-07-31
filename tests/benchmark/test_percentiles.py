"""§5.6 — Percentis, estatísticas e agregação por pooling (valores à mão).

Valida ``percentile``, ``compute_stats`` e ``aggregate_samples`` do módulo
``dakota_gateway.benchmark.stats`` com valores calculados manualmente aqui no
teste — nada de tolerância frouxa: os números abaixo são exatos.

Interpretações documentadas (o contrato não fixa estas convenções):

- ``percentile``: interpolação linear (método "linear" do numpy):
  posição ``k = (n-1) * p/100`` sobre as amostras ORDENADAS; resultado
  ``s[floor(k)] + (k - floor(k)) * (s[ceil(k)] - s[floor(k)])``.
- ``compute_stats.stdev``: desvio-padrão POPULACIONAL (ddof=0), coerente com
  ``statistics.pstdev``. Para 1..100: variância = (100²-1)/12 = 833.25.
- ``compute_stats.cv``: ``stdev / mean``.
- ``compute_stats.ci95_*``: intervalo normal ``mean ± 1.96 * stdev / sqrt(n)``.
- ``aggregate_samples``: pooling das amostras brutas (concatenação), nunca
  média de médias — o teste com iterações de tamanhos diferentes pega o erro.
"""
from __future__ import annotations

import math
import statistics
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "gateway"))

from dakota_gateway.benchmark.stats import (  # noqa: E402
    aggregate_samples,
    compute_stats,
    percentile,
)


class TestPercentile(unittest.TestCase):
    """Interpolação linear sobre amostras conhecidas."""

    def test_amostras_1_a_100(self) -> None:
        amostras = sorted(range(1, 101))
        # k = 99 * p/100 → p50: k=49.5 → 50 + 0.5*(51-50) = 50.5
        self.assertAlmostEqual(50.5, percentile(amostras, 50))
        # p90: k=89.1 → 90 + 0.1*(91-90) = 90.1
        self.assertAlmostEqual(90.1, percentile(amostras, 90))
        # p95: k=94.05 → 95 + 0.05*(96-95) = 95.05
        self.assertAlmostEqual(95.05, percentile(amostras, 95))
        # p99: k=98.01 → 99 + 0.01*(100-99) = 99.01
        self.assertAlmostEqual(99.01, percentile(amostras, 99))
        # extremos
        self.assertAlmostEqual(1.0, percentile(amostras, 0))
        self.assertAlmostEqual(100.0, percentile(amostras, 100))

    def test_conjunto_pequeno_4_valores(self) -> None:
        amostras = [10.0, 20.0, 30.0, 40.0]
        # p50: k=1.5 → 20 + 0.5*(30-20) = 25
        self.assertAlmostEqual(25.0, percentile(amostras, 50))
        # p90: k=2.7 → 30 + 0.7*(40-30) = 37
        self.assertAlmostEqual(37.0, percentile(amostras, 90))
        # p95: k=2.85 → 30 + 0.85*10 = 38.5
        self.assertAlmostEqual(38.5, percentile(amostras, 95))
        # p99: k=2.97 → 30 + 0.97*10 = 39.7
        self.assertAlmostEqual(39.7, percentile(amostras, 99))

    def test_percentil_elemento_unico(self) -> None:
        self.assertAlmostEqual(7.5, percentile([7.5], 50))
        self.assertAlmostEqual(7.5, percentile([7.5], 99))


class TestComputeStats(unittest.TestCase):
    """Estatísticas exatas para 1..100 (valores calculados à mão)."""

    def test_stats_1_a_100(self) -> None:
        amostras = [float(i) for i in range(1, 101)]
        stats = compute_stats(amostras)

        self.assertEqual(100, stats.n)
        self.assertAlmostEqual(50.5, stats.mean)
        self.assertAlmostEqual(50.5, stats.p50)
        self.assertAlmostEqual(90.1, stats.p90)
        self.assertAlmostEqual(95.05, stats.p95)
        self.assertAlmostEqual(99.01, stats.p99)
        self.assertAlmostEqual(100.0, stats.max)

        # desvio POPULACIONAL (ddof=0): variância = (100²-1)/12 = 833.25
        stdev_esperado = math.sqrt(833.25)  # ≈ 28.8660702...
        self.assertAlmostEqual(statistics.pstdev(amostras), stdev_esperado)
        self.assertAlmostEqual(stdev_esperado, stats.stdev)

        # coeficiente de variação = stdev / mean
        self.assertAlmostEqual(stdev_esperado / 50.5, stats.cv)

        # CI95 normal: mean ± 1.96 * stdev / sqrt(n)
        margem = 1.96 * stdev_esperado / math.sqrt(100)  # ≈ 5.65775...
        self.assertAlmostEqual(50.5 - margem, stats.ci95_low)
        self.assertAlmostEqual(50.5 + margem, stats.ci95_high)

    def test_n_zero_levanta_value_error(self) -> None:
        with self.assertRaises(ValueError):
            compute_stats([])


class TestAggregateSamples(unittest.TestCase):
    """Pooling das amostras brutas — nunca média de médias."""

    def test_pooling_concatena_amostras_brutas(self) -> None:
        iteracoes = [[1.0, 2.0], [3.0, 4.0, 5.0]]
        agregado = aggregate_samples(iteracoes)
        self.assertEqual([1.0, 2.0, 3.0, 4.0, 5.0], sorted(agregado))
        self.assertEqual(5, len(agregado))

    def test_media_do_pooling_nao_e_media_de_medias(self) -> None:
        # iterações de tamanhos diferentes: média de médias daria (1.5+4.0)/2=2.75;
        # o pooling correto dá (1+2+3+4+5)/5 = 3.0
        iteracoes = [[1.0, 2.0], [3.0, 4.0, 5.0]]
        stats = compute_stats(aggregate_samples(iteracoes))
        self.assertEqual(5, stats.n)
        self.assertAlmostEqual(3.0, stats.mean)
        self.assertNotAlmostEqual(2.75, stats.mean)


if __name__ == "__main__":
    unittest.main()

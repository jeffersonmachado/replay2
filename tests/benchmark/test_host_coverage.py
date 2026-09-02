"""FASE 3 — Cobertura de coletores, host_series real e gargalo dominante.

Problemas corrigidos (verificados no código antes desta correção):

1. ``build_comparison`` passava ``host_series: list[dict] = []`` HARD-CODED
   para ``analyze_ladder`` — o gargalo dominante saía SEMPRE "unknown",
   mesmo com amostras reais de host gravadas em ``host-samples.jsonl``;
2. um JSON parseável não é prova de coleta válida: a cobertura separa
   coletor disponível / parcialmente disponível / amostra válida /
   cobertura temporal / métrica ausente, por grupo de métricas;
3. declarar gargalo dominante exige dados suficientes de CPU,
   memória/paginação, disco/IOPS/latência, rede, filas/run queue e
   banco/Recital/ISAM QUANDO APLICÁVEL — faltando métrica essencial, o
   gargalo é ``unknown`` e a decisão é INCONCLUSIVE (nunca gargalo
   inventado);
4. o relatório apresenta a cobertura por coletor e justifica a conclusão.

Estes testes DEVEM FALHAR antes da correção e PASSAR depois dela.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "gateway"))

from dakota_gateway.benchmark.comparison import (  # noqa: E402
    build_comparison,
    build_decision,
)
from dakota_gateway.benchmark.decision import Decision  # noqa: E402
from dakota_gateway.benchmark.models import (  # noqa: E402
    EnvironmentRunResult,
    ExperimentResult,
    OperationSample,
)

BASELINE = "aix-power"
TARGET = "linux-x86"


def _amostra(env: str, idx: int) -> OperationSample:
    ns0 = 1_000_000_000 + idx * 20_000_000
    return OperationSample(
        experiment_id="exp-cov", environment_id=env, iteration=1,
        concurrency=1, virtual_user_id="vu-1", journey_id="j-pedido",
        step_id=f"ev-{idx}", phase="MEASUREMENT", started_ns=ns0,
        finished_ns=ns0 + 10_000_000, latency_ms=10.0,
        success=True, timeout=False, functional_divergence=False,
        error_code=None, screen_sig_checked=True,
        expected_screen_sig="sha256:x", observed_screen_sig="sha256:x")


def _host_completo(ts_ms: int, **override) -> dict:
    """Amostra de host com TODOS os grupos essenciais cobertos."""
    amostra = {
        "ts_ms": ts_ms, "cpu_pct": 12.0, "mem_pct": 30.0, "swap_pct": 0.5,
        "disk_latency_ms": 2.0, "iops": 40.0, "disk_busy_pct": 5.0,
        "load1": 0.4, "net_rx_kbs": 120.0, "net_tx_kbs": 90.0,
    }
    amostra.update(override)
    return amostra


def _host_file(diretorio: Path, nome: str, linhas: list[dict]) -> Path:
    caminho = diretorio / nome
    caminho.write_text(
        "".join(json.dumps(l) + "\n" for l in linhas), encoding="utf-8")
    return caminho


def _run(env: str, host_path: Path, n: int = 20, *,
         offset_ms: int = 0, offset_medido: bool = True,
         net_window: dict | None = None) -> EnvironmentRunResult:
    run = EnvironmentRunResult(
        environment_id=env, iteration=1, concurrency=1, status="COMPLETED",
        samples=[_amostra(env, i) for i in range(n)],
        host_samples_path=str(host_path))
    run.host_clock_offset_ms = offset_ms
    run.host_clock_offset_measured = offset_medido
    run.net_window = net_window
    return run


def _resultado(tmp: Path, linhas_base: list[dict], linhas_alvo: list[dict],
               **kw) -> ExperimentResult:
    hb = _host_file(tmp, "host-base.jsonl", linhas_base)
    ha = _host_file(tmp, "host-alvo.jsonl", linhas_alvo)
    return ExperimentResult(
        contract_sha256="c" * 64, status="COMPLETED", runs=[
            _run(BASELINE, hb, **kw), _run(TARGET, ha, **kw)])


class TestHostSeriesRealNoGargalo(unittest.TestCase):
    """host_series REAL (lida do host-samples.jsonl da run) alimenta a
    análise de gargalo — nunca mais lista vazia hard-coded."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def test_cpu_sustentada_real_vira_gargalo_cpu(self) -> None:
        """CPU >= 90 sustentada nas amostras REAIS do arquivo → gargalo cpu
        (antes: sempre "unknown" porque host_series era [])."""
        resultado = _resultado(
            self.tmp,
            [_host_completo(1000 + i * 5000, cpu_pct=95.0) for i in range(6)],
            [_host_completo(1000 + i * 5000, cpu_pct=15.0) for i in range(6)])
        comparison = build_comparison(
            resultado, baseline_env=BASELINE, target_env=TARGET)
        gargalos = comparison["degradation_by_env"]
        self.assertEqual("cpu", gargalos[BASELINE]["dominant_bottleneck"])
        self.assertEqual("unknown", gargalos[TARGET]["dominant_bottleneck"])
        # evidência consumida: a série real fica registrada na comparação
        self.assertGreater(
            comparison["collector_coverage"][BASELINE]["host"]
            ["amostras_validas"], 0)

    def test_disco_saturado_real_vira_gargalo_disco(self) -> None:
        resultado = _resultado(
            self.tmp,
            [_host_completo(1000 + i * 5000, disk_latency_ms=80.0)
             for i in range(4)],
            [_host_completo(1000 + i * 5000) for i in range(4)])
        comparison = build_comparison(
            resultado, baseline_env=BASELINE, target_env=TARGET)
        self.assertEqual(
            "disk_io",
            comparison["degradation_by_env"][BASELINE]["dominant_bottleneck"])


class TestCoberturaPorMetrica(unittest.TestCase):
    """Cobertura mínima por métrica obrigatória: disponível/parcial/
    ausente — JSON parseável sozinho NÃO é prova de coleta válida."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def test_cobertura_completa_libera_gargalo_e_pass(self) -> None:
        resultado = _resultado(
            self.tmp,
            [_host_completo(1000 + i * 5000) for i in range(4)],
            [_host_completo(1000 + i * 5000) for i in range(4)])
        comparison = build_comparison(
            resultado, baseline_env=BASELINE, target_env=TARGET)
        for env in (BASELINE, TARGET):
            cob = comparison["collector_coverage"][env]["host"]
            self.assertEqual([], cob["grupos_ausentes"])
            self.assertTrue(comparison["bottleneck_evidence"][env]["ok"])
        decision = build_decision(resultado, comparison)
        self.assertEqual("PASS", decision.verdict)

    def test_metricas_ausentes_gargalo_unknown_e_inconclusive(self) -> None:
        """Só cpu_pct nas amostras (caso real v7: sem rede/run_queue):
        gargalo NÃO pode ser declarado → unknown + INCONCLUSIVE com a
        lista dos grupos ausentes."""
        so_cpu = [{"ts_ms": 1000 + i * 5000, "cpu_pct": 12.0}
                  for i in range(4)]
        resultado = _resultado(self.tmp, so_cpu, so_cpu)
        comparison = build_comparison(
            resultado, baseline_env=BASELINE, target_env=TARGET)
        for env in (BASELINE, TARGET):
            cob = comparison["collector_coverage"][env]["host"]
            self.assertIn("rede", cob["grupos_ausentes"])
            self.assertIn("memoria", cob["grupos_ausentes"])
            self.assertIn("disco", cob["grupos_ausentes"])
            self.assertNotIn("cpu", cob["grupos_ausentes"])
            self.assertEqual(
                "unknown",
                comparison["degradation_by_env"][env]["dominant_bottleneck"])
            self.assertFalse(comparison["bottleneck_evidence"][env]["ok"])
        decision = build_decision(resultado, comparison)
        self.assertEqual("INCONCLUSIVE", decision.verdict)
        self.assertIsNone(decision.recommendation)
        razoes = " ".join(decision.reasons)
        self.assertIn("rede", razoes)
        self.assertIn("gargalo", razoes)

    def test_marcador_indisponibilidade_nao_e_amostra_valida(self) -> None:
        """Linha {"available": false} é marcador, não amostra: cobertura
        temporal zero e coletor indisponível."""
        linhas = [{"available": False, "reason": "ssh_failed"}]
        resultado = _resultado(
            self.tmp, [_host_completo(1000)], linhas)
        comparison = build_comparison(
            resultado, baseline_env=BASELINE, target_env=TARGET)
        cob = comparison["collector_coverage"][TARGET]["host"]
        self.assertEqual(0, cob["amostras_validas"])
        self.assertEqual("indisponivel", cob["status"])

    def test_banco_nao_aplicavel_nao_bloqueia(self) -> None:
        """database_metrics available:false collector_not_supported (arquivos
        Recital/ISAM) → banco 'não aplicável', NÃO bloqueia o gargalo."""
        resultado = _resultado(
            self.tmp,
            [_host_completo(1000 + i * 5000) for i in range(4)],
            [_host_completo(1000 + i * 5000) for i in range(4)])
        for run in resultado.runs:
            run.database_metrics = {"available": False,
                                    "reason": "collector_not_supported"}
        comparison = build_comparison(
            resultado, baseline_env=BASELINE, target_env=TARGET)
        for env in (BASELINE, TARGET):
            banco = comparison["collector_coverage"][env]["database"]
            self.assertEqual("nao_aplicavel", banco["status"])
        decision = build_decision(resultado, comparison)
        self.assertEqual("PASS", decision.verdict)

    def test_rede_via_janela_de_contadores_conta_como_coberta(self) -> None:
        """Sem campos net_* nas amostras, mas com medição REAL de contadores
        de rede na janela da run (net_window) → rede coberta."""
        sem_rede = [_host_completo(1000 + i * 5000) for i in range(4)]
        for amostra in sem_rede:
            del amostra["net_rx_kbs"]
            del amostra["net_tx_kbs"]
        janela = {"net_rx_kbs": 55.0, "net_tx_kbs": 31.0,
                  "fonte": "contadores_remotos"}
        resultado = _resultado(self.tmp, sem_rede, sem_rede,
                               net_window=janela)
        comparison = build_comparison(
            resultado, baseline_env=BASELINE, target_env=TARGET)
        for env in (BASELINE, TARGET):
            cob = comparison["collector_coverage"][env]["host"]
            self.assertNotIn("rede", cob["grupos_ausentes"])
            self.assertEqual("janela", cob["rede_via"])
        decision = build_decision(resultado, comparison)
        self.assertEqual("PASS", decision.verdict)


class TestRelatorioCobertura(unittest.TestCase):
    """O relatório apresenta a cobertura por coletor e justifica o gargalo."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def test_report_md_expoe_cobertura_e_justificativa(self) -> None:
        from dakota_gateway.benchmark.report import write_experiment_artifacts

        so_cpu = [{"ts_ms": 1000 + i * 5000, "cpu_pct": 12.0}
                  for i in range(4)]
        resultado = _resultado(self.tmp, so_cpu, so_cpu)
        comparison = build_comparison(
            resultado, baseline_env=BASELINE, target_env=TARGET)
        decision = build_decision(resultado, comparison)
        self.assertEqual("INCONCLUSIVE", decision.verdict)
        with tempfile.TemporaryDirectory() as tmp2:
            write_experiment_artifacts(
                Path(tmp2), resultado, comparison, {}, decision)
            md = (Path(tmp2) / "report.md").read_text(encoding="utf-8")
            rep = json.loads(
                (Path(tmp2) / "report.json").read_text(encoding="utf-8"))
        self.assertIn("Cobertura por coletor", md)
        self.assertIn("rede", md)
        # gargalo unknown JUSTIFICADO (não um "unknown" mudo)
        self.assertIn("unknown", md)
        self.assertIn("collector_coverage", rep)
        self.assertIn("rede", json.dumps(rep["collector_coverage"]))


if __name__ == "__main__":
    unittest.main()

"""Metodologia de throughput do benchmark real (regressão — FASE 1).

O cálculo antigo de vazão (``_janela_segundos``/``_level_stats``) usava
``max(finished_ns) - min(started_ns)`` sobre TODAS as amostras do ambiente
(ou do nível), incluindo os intervalos ociosos ENTRE repetições executadas
sequencialmente. Com repetições espaçadas (ex.: 3h entre runs, caso real do
experimento cap13-aix-linux-oficial-v7), o throughput saía subestimado em
~10-12×.

Metodologia correta fixada por estes testes:

- ``operations_per_second`` de um nível = soma das operações válidas ÷ soma
  das durações REAIS de MEASUREMENT de cada run (duração da run =
  ``max(finished_ns) - min(started_ns)`` das amostras DAQUELA run) — nunca
  o intervalo entre repetições;
- níveis de concorrência NUNCA são agregados num único "TPS": o consumo
  interno (normalização, capacidade) usa métricas por nível; ``tps``/
  ``tps_by_env`` seguem expostos como LEGADO (depreciados) para não quebrar
  relatórios antigos;
- a normalização usa UM nível explicitamente identificado — o limite
  operacional seguro (``degradation.safe_operational_limit``) — nunca o
  agregado heterogêneo;
- runs ABORTED não entram na vazão saudável (só COMPLETED);
- run com duração inválida (``finished <= started``) é excluída da vazão e
  registrada (``invalid_duration_runs``) — nunca divide por ~0 nem infla;
- operação ≠ jornada: contagens separadas (``operations_count`` ×
  ``journeys_count``), e as métricas de jornada só existem quando a run
  carrega a contagem confiável (``completed_journeys`` registrado pelo
  executor); sem o dado, as chaves são OMITIDAS — nunca inferidas;
- recálculo do benchmark real v7 (artefatos persistidos) reproduz os
  números de referência com tolerância de 5%.

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
    build_capacity,
    build_comparison,
)
from dakota_gateway.benchmark.decision import Decision  # noqa: E402
from dakota_gateway.benchmark.environments import (  # noqa: E402
    CpuModel,
    EnvironmentModel,
)
from dakota_gateway.benchmark.models import (  # noqa: E402
    EnvironmentRunResult,
    ExperimentResult,
    OperationSample,
)

T0 = 1_000_000_000_000  # t0 arbitrário (ns)
HORA_NS = 3_600 * 1_000_000_000


def _amostra(env: str, conc: int, started_ns: int, dur_ns: int,
             *, iteration: int = 1, sucesso: bool = True) -> OperationSample:
    """Uma amostra de MEASUREMENT com janela real [started, started+dur]."""
    return OperationSample(
        experiment_id="exp-tp", environment_id=env, iteration=iteration,
        concurrency=conc, virtual_user_id="vu-1", journey_id="j1",
        step_id=f"ev-{started_ns}", phase="MEASUREMENT",
        started_ns=int(started_ns), finished_ns=int(started_ns + dur_ns),
        latency_ms=dur_ns / 1e6, success=sucesso, timeout=False,
        functional_divergence=False,
        error_code=None if sucesso else "erro_operacao")


def _run(env: str, conc: int, t0_ns: int, *, n_ops: int = 10,
         passo_ns: int = 1_000_000_000, dur_op_ns: int = 500_000_000,
         iteration: int = 1, status: str = "COMPLETED",
         completed_journeys=None, planned_s=None) -> EnvironmentRunResult:
    """Run com ``n_ops`` operações espaçadas de ``passo_ns``.

    Duração real da run = ``(n_ops-1)*passo_ns + dur_op_ns``
    (10 ops no default → 9.5 s).
    """
    amostras = [
        _amostra(env, conc, t0_ns + i * passo_ns, dur_op_ns, iteration=iteration)
        for i in range(n_ops)
    ]
    run = EnvironmentRunResult(
        environment_id=env, iteration=iteration, concurrency=conc,
        status=status, samples=amostras)
    # Atributos novos do modelo (o default None = "não medido", como nas
    # trilhas antigas): atribuídos fora do construtor para o teste falhar
    # por ASSERTION (não por TypeError) antes da correção.
    run.completed_journeys = completed_journeys
    run.planned_duration_s = planned_s
    return run


def _resultado(runs: list) -> ExperimentResult:
    return ExperimentResult(
        contract_sha256="c" * 64, status="COMPLETED", runs=list(runs))


def _nivel(comparison: dict, env: str, conc: int) -> dict:
    for nivel in comparison["ladder_by_env"][env]:
        if nivel["concurrency"] == conc:
            return nivel
    raise AssertionError(f"nível {conc} ausente na escada de {env}")


class TestJanelaPorRun(unittest.TestCase):
    """A vazão usa a soma das durações reais das runs, nunca o intervalo
    entre repetições."""

    def test_repeticoes_espacadas_3h_mesmo_ops_por_segundo(self) -> None:
        """Duas runs idênticas separadas por 3h produzem o MESMO
        operations_per_second de runs adjacentes (~10-12× de erro no
        cálculo antigo)."""
        adjacente = _resultado([
            _run("aix", 1, T0),
            _run("aix", 1, T0 + 20 * 1_000_000_000, iteration=2),
        ])
        espacado = _resultado([
            _run("aix", 1, T0),
            _run("aix", 1, T0 + 3 * HORA_NS, iteration=2),
        ])

        nivel_adj = _nivel(build_comparison(adjacente), "aix", 1)
        nivel_esp = _nivel(build_comparison(espacado), "aix", 1)

        # 20 operações / (9.5 s + 9.5 s) de duração real medida
        esperado = 20.0 / 19.0
        self.assertAlmostEqual(esperado, nivel_adj["operations_per_second"])
        self.assertAlmostEqual(esperado, nivel_esp["operations_per_second"])
        self.assertEqual(20, nivel_esp["operations_count"])
        self.assertAlmostEqual(19.0, nivel_esp["observed_duration_s"])
        self.assertEqual(0, nivel_esp["invalid_duration_runs"])

    def test_deslocamento_constante_dos_timestamps_nao_altera_throughput(
            self) -> None:
        """Somar uma constante a todos os timestamps não altera a vazão."""
        deslocamento = 7 * 24 * HORA_NS
        base = _resultado([_run("aix", 1, T0), _run("linux", 1, T0)])
        deslocado = _resultado([
            _run("aix", 1, T0 + deslocamento),
            _run("linux", 1, T0 + deslocamento),
        ])
        comp_base = build_comparison(base)
        comp_desl = build_comparison(deslocado)
        for env in ("aix", "linux"):
            self.assertAlmostEqual(
                _nivel(comp_base, env, 1)["operations_per_second"],
                _nivel(comp_desl, env, 1)["operations_per_second"])

    def test_compatibilidade_campo_tps_legado(self) -> None:
        """``tps`` segue presente na escada, marcado como depreciado, e
        carrega o MESMO valor corrigido de ``operations_per_second``."""
        comparison = build_comparison(
            _resultado([_run("aix", 1, T0), _run("aix", 1, T0 + 3 * HORA_NS,
                                                 iteration=2)]))
        nivel = _nivel(comparison, "aix", 1)
        self.assertIn("tps", nivel)
        self.assertTrue(nivel.get("tps_deprecated"))
        self.assertEqual(nivel["operations_per_second"], nivel["tps"])
        # tps_by_env também segue exposto, marcado como legado, com a base
        # de cálculo explícita (nível de referência, nunca agregado)
        self.assertTrue(comparison.get("tps_by_env_deprecated"))
        referencia = comparison["throughput_reference"]["aix"]
        self.assertEqual(1, referencia["concurrency"])
        self.assertAlmostEqual(
            nivel["operations_per_second"],
            comparison["tps_by_env"]["aix"])


class TestNiveisSeparados(unittest.TestCase):
    """Níveis de concorrência calculados separadamente, sem mistura."""

    def _resultado_escada(self) -> ExperimentResult:
        # conc1: 10 ops em 9.5s → 1.0526 ops/s
        # conc5: 10 ops em 5.0s → 2.0 ops/s
        # conc10: 10 ops em 2.3s → 4.3478 ops/s
        return _resultado([
            _run("aix", 1, T0),
            _run("aix", 5, T0, passo_ns=500_000_000),
            _run("aix", 10, T0, passo_ns=200_000_000),
        ])

    def test_niveis_1_5_10_distintos_e_sem_mistura(self) -> None:
        comparison = build_comparison(self._resultado_escada())
        n1 = _nivel(comparison, "aix", 1)
        n5 = _nivel(comparison, "aix", 5)
        n10 = _nivel(comparison, "aix", 10)
        self.assertAlmostEqual(10.0 / 9.5, n1["operations_per_second"])
        self.assertAlmostEqual(10.0 / 5.0, n5["operations_per_second"])
        self.assertAlmostEqual(10.0 / 2.3, n10["operations_per_second"])
        valores = {n1["operations_per_second"], n5["operations_per_second"],
                   n10["operations_per_second"]}
        self.assertEqual(3, len(valores))

    def test_tps_by_env_nao_e_agregado_heterogeneo(self) -> None:
        """O tps_by_env (legado) é a vazão do nível de referência — o limite
        operacional seguro — nunca total_ops ÷ janela_global."""
        comparison = build_comparison(self._resultado_escada())
        # escada toda saudável → limite seguro = maior nível (10)
        self.assertAlmostEqual(
            _nivel(comparison, "aix", 10)["operations_per_second"],
            comparison["tps_by_env"]["aix"])
        # prova negativa: o agregado heterogêneo antigo seria
        # 30 ops ÷ janela global (≠ vazão de qualquer nível)
        agregado_antigo = 30.0 / 9.5
        self.assertNotAlmostEqual(
            agregado_antigo, comparison["tps_by_env"]["aix"])


class TestDuracaoInvalidaEStatus(unittest.TestCase):
    """Runs inválidas/abortadas fora da vazão saudável."""

    def test_run_com_duracao_zero_excluida_e_registrada(self) -> None:
        """finished == started (duração 0) não pode gerar throughput
        artificial: a run sai da soma E do numerador, e fica registrada."""
        boa = _run("aix", 1, T0)
        invalida = _run("aix", 1, T0 + 60 * 1_000_000_000, iteration=2,
                        n_ops=3, passo_ns=0, dur_op_ns=0)  # duração 0
        comparison = build_comparison(_resultado([boa, invalida]))
        nivel = _nivel(comparison, "aix", 1)
        self.assertEqual(1, nivel["invalid_duration_runs"])
        self.assertEqual(10, nivel["operations_count"])
        self.assertAlmostEqual(9.5, nivel["observed_duration_s"])
        self.assertAlmostEqual(10.0 / 9.5, nivel["operations_per_second"])

    def test_run_com_finished_menor_que_started_excluida(self) -> None:
        boa = _run("aix", 1, T0)
        corrupta = EnvironmentRunResult(
            environment_id="aix", iteration=2, concurrency=1,
            status="COMPLETED",
            samples=[_amostra("aix", 1, T0 + 100, -50_000_000, iteration=2)])
        comparison = build_comparison(_resultado([boa, corrupta]))
        nivel = _nivel(comparison, "aix", 1)
        self.assertEqual(1, nivel["invalid_duration_runs"])
        self.assertAlmostEqual(10.0 / 9.5, nivel["operations_per_second"])

    def test_nivel_com_todas_as_runs_invalidas_nao_explode(self) -> None:
        """Sem nenhuma duração válida: vazão 0.0 registrada, nunca
        divisão por ~0 nem número inflado."""
        invalida = _run("aix", 1, T0, n_ops=3, passo_ns=0, dur_op_ns=0)
        comparison = build_comparison(_resultado([invalida]))
        nivel = _nivel(comparison, "aix", 1)
        self.assertEqual(1, nivel["invalid_duration_runs"])
        self.assertEqual(0, nivel["operations_count"])
        self.assertEqual(0.0, nivel["observed_duration_s"])
        self.assertEqual(0.0, nivel["operations_per_second"])

    def test_runs_aborted_fora_da_vazao(self) -> None:
        """Run ABORTED (nível parado por stop_condition) não entra na
        vazão; nível com TODAS as runs ABORTED nem aparece na escada."""
        resultado = _resultado([
            _run("aix", 1, T0),
            _run("aix", 1, T0 + 60 * 1_000_000_000, iteration=2,
                 status="ABORTED", n_ops=500),
            _run("aix", 20, T0, status="ABORTED", n_ops=999),
        ])
        comparison = build_comparison(resultado)
        nivel = _nivel(comparison, "aix", 1)
        self.assertEqual(10, nivel["operations_count"])
        self.assertAlmostEqual(10.0 / 9.5, nivel["operations_per_second"])
        niveis = [n["concurrency"] for n in comparison["ladder_by_env"]["aix"]]
        self.assertNotIn(20, niveis)


class TestOperacaoVersusJornada(unittest.TestCase):
    """Operação ≠ jornada: contagens separadas, e métricas de jornada só
    quando a contagem confiável existe (``completed_journeys`` da run)."""

    def test_contagens_separadas_operacao_e_jornada(self) -> None:
        """10 operações por passada de jornada: 2 runs × 2 jornadas
        completas = 4 jornadas e 20 operações — razões distintas."""
        resultado = _resultado([
            _run("aix", 1, T0, completed_journeys=2, planned_s=120.0),
            _run("aix", 1, T0 + 60 * 1_000_000_000, iteration=2,
                 completed_journeys=2, planned_s=120.0),
        ])
        nivel = _nivel(build_comparison(resultado), "aix", 1)
        self.assertEqual(20, nivel["operations_count"])
        self.assertEqual(4, nivel["journeys_count"])
        self.assertAlmostEqual(20.0 / 19.0, nivel["operations_per_second"])
        self.assertAlmostEqual(4.0 / 19.0,
                               nivel["completed_journeys_per_second"])
        self.assertNotAlmostEqual(nivel["operations_per_second"],
                                  nivel["completed_journeys_per_second"])
        self.assertAlmostEqual(240.0, nivel["planned_duration_s"])

    def test_sem_contagem_confiavel_jornada_e_omitida(self) -> None:
        """Run sem ``completed_journeys`` (trilhas antigas, ex.: v7) → as
        chaves de jornada são OMITIDAS, nunca inferidas das amostras."""
        resultado = _resultado([
            _run("aix", 1, T0),  # completed_journeys=None
            _run("aix", 1, T0 + 60 * 1_000_000_000, iteration=2,
                 completed_journeys=2),
        ])
        nivel = _nivel(build_comparison(resultado), "aix", 1)
        self.assertNotIn("journeys_count", nivel)
        self.assertNotIn("completed_journeys_per_second", nivel)


class TestNormalizacaoPorNivel(unittest.TestCase):
    """A normalização consome a vazão de UM nível explicitamente
    identificado (limite operacional seguro), nunca o agregado."""

    @staticmethod
    def _modelos() -> dict:
        return {
            "aix": EnvironmentModel(
                environment_id="aix", platform="AIX", architecture="POWER",
                host="192.0.2.1",
                cpu=CpuModel(virtual_processors=2, physical_processors=1,
                             entitled_capacity=0.5),
                memory_mb=4096),
        }

    def test_normalizacao_usa_limite_operacional_seguro(self) -> None:
        """Escada com degradação no nível 10 (erro 20% > 5%): a vazão
        normalizada é a do nível 5 (limite seguro), não a do 10 nem um
        agregado."""
        run10_degradada = _run("aix", 10, T0, passo_ns=200_000_000)
        # 2 erros em 10 operações → error_pct 20% > error_rate_max_pct (5%)
        run10_degradada.samples[3] = _amostra(
            "aix", 10, int(run10_degradada.samples[3].started_ns),
            500_000_000, sucesso=False)
        run10_degradada.samples[4] = _amostra(
            "aix", 10, int(run10_degradada.samples[4].started_ns),
            500_000_000, sucesso=False)
        resultado = _resultado([
            _run("aix", 1, T0),
            _run("aix", 5, T0, passo_ns=500_000_000),
            run10_degradada,
        ])
        comparison = build_comparison(
            resultado, self._modelos(), baseline_env="aix", target_env="aix")

        deg = comparison["degradation_by_env"]["aix"]
        self.assertEqual(10, deg["degradation_point"])
        self.assertEqual(5, deg["safe_operational_limit"])

        norm = comparison["normalization"]["per_environment"]["aix"]
        vazao_nivel5 = _nivel(comparison, "aix", 5)["operations_per_second"]
        self.assertEqual("operations_per_second", norm["throughput_metric"])
        self.assertEqual(5, norm["throughput_level"])
        self.assertAlmostEqual(vazao_nivel5, norm["tps"])
        # coerência da fórmula: tps_per_vcpu = tps / virtual_processors
        self.assertAlmostEqual(vazao_nivel5 / 2, norm["tps_per_vcpu"])


class TestCapacidadePorNivel(unittest.TestCase):
    """build_capacity passa a usar operations_per_second por nível."""

    def test_capacidade_usa_operations_per_second(self) -> None:
        resultado = _resultado([
            _run("aix", 1, T0),
            _run("aix", 5, T0, passo_ns=500_000_000),
            _run("aix", 5, T0 + 3 * HORA_NS, iteration=2,
                 passo_ns=500_000_000),
        ])
        capacidade = build_capacity(resultado)["aix"]
        # conc5: 20 ops / (5.0 + 5.0) s = 2.0 ops/s — o gap de 3h entre as
        # repetições NÃO derruba a capacidade observada
        self.assertAlmostEqual(2.0,
                               capacidade["max_operations_per_second_observed"])
        # alias legado preservado para consumidores antigos (UI/relatórios)
        self.assertAlmostEqual(2.0, capacidade["max_tps_observed"])
        self.assertEqual(5, capacidade["max_concurrency_tested"])
        nivel5 = [n for n in capacidade["levels"] if n["concurrency"] == 5][0]
        self.assertAlmostEqual(2.0, nivel5["operations_per_second"])


class TestReportPorNivel(unittest.TestCase):
    """O relatório expõe as métricas novas por nível e marca tps legado."""

    def test_report_json_e_md_com_metricas_por_nivel(self) -> None:
        from dakota_gateway.benchmark.report import write_experiment_artifacts

        resultado = _resultado([
            _run("aix", 1, T0),
            _run("aix", 5, T0, passo_ns=500_000_000),
            _run("linux", 1, T0),
        ])
        comparison = build_comparison(resultado)
        capacidade = build_capacity(resultado)
        decisao = Decision(verdict="INCONCLUSIVE", recommendation=None,
                           reasons=["teste de relatório"])
        with tempfile.TemporaryDirectory() as tmp:
            write_experiment_artifacts(
                Path(tmp), resultado, comparison, capacidade, decisao)
            report = json.loads(
                (Path(tmp) / "report.json").read_text(encoding="utf-8"))
            md = (Path(tmp) / "report.md").read_text(encoding="utf-8")

        perf = report["performance_comparison"]
        escada = perf["ladder_by_env"]["aix"]
        nivel5 = [n for n in escada if n["concurrency"] == 5][0]
        self.assertAlmostEqual(2.0, nivel5["operations_per_second"])
        # tps_by_env segue presente, marcado como legado/depreciado
        self.assertIn("tps_by_env", perf)
        self.assertTrue(perf.get("tps_by_env_deprecated"))
        # markdown: vazão por nível visível e tps marcado como legado
        self.assertIn("operations_per_second", md)
        self.assertIn("depreciado", md.lower())


class TestRecalculoBenchmarkV7Real(unittest.TestCase):
    """Recálculo do benchmark real cap13-aix-linux-oficial-v7 a partir dos
    artefatos persistidos (application-samples.jsonl por run).

    Números de referência (soma de ops ÷ soma das durações reais por run):
    AIX   conc1 ≈ 0.5679, conc5 ≈ 2.7920, conc10 ≈ 5.1311 ops/s
    Linux conc1 ≈ 0.6261, conc5 ≈ 3.0792, conc10 ≈ 6.1130 ops/s
    """

    ARTEFATOS = ROOT / "artifacts" / "benchmarks" / "cap13-aix-linux-oficial-v7"
    ESPERADO = {
        "aix-power": {1: 0.5679, 5: 2.7920, 10: 5.1311},
        "linux-x86": {1: 0.6261, 5: 3.0792, 10: 6.1130},
    }

    def _carregar(self) -> ExperimentResult:
        from control.services.benchmark_service import _rebuild_result
        from dakota_gateway.benchmark.contract import load_contract
        contrato = load_contract(self.ARTEFATOS / "experiment-manifest.json")
        return _rebuild_result(self.ARTEFATOS, contrato)

    @unittest.skipUnless(ARTEFATOS.is_dir(),
                         "artefatos do v7 ausentes neste checkout")
    def test_recalculo_escada_v7(self) -> None:
        resultado = self._carregar()
        self.assertTrue(resultado.runs, "v7 sem runs reconstruídas")
        comparison = build_comparison(resultado)
        for env, niveis in self.ESPERADO.items():
            for conc, esperado in niveis.items():
                nivel = _nivel(comparison, env, conc)
                obtido = nivel["operations_per_second"]
                self.assertAlmostEqual(
                    esperado, obtido, delta=abs(esperado) * 0.05,
                    msg=f"{env} conc{conc}: {obtido} vs referência {esperado}")
                # sanity: a vazão do nível vem da soma de durações reais
                self.assertGreater(nivel["observed_duration_s"], 0.0)
                self.assertEqual(0, nivel["invalid_duration_runs"])
        # nível 20 ABORTED (session_admission_limit) fora da escada
        for env in self.ESPERADO:
            niveis = [n["concurrency"]
                      for n in comparison["ladder_by_env"][env]]
            self.assertNotIn(20, niveis)
            # trilha v7 não registra completed_journeys → jornada omitida
            nivel1 = _nivel(comparison, env, 1)
            self.assertNotIn("completed_journeys_per_second", nivel1)
            # planned_duration_s vem do contrato (measurement_seconds=120,
            # 2 iterações por nível → 240 s planejados por nível)
            self.assertAlmostEqual(240.0, nivel1["planned_duration_s"])


if __name__ == "__main__":
    unittest.main()

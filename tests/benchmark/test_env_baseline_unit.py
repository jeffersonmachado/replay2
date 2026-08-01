"""Baseline próprio por ambiente + semântica de parada da escada (§17/§20).

Casos reais do run cap13-aix-linux-oficial-v4 que estes testes fecham:

1. **Dataset divergente com formato endian-nativo**: as telas do Linux
   divergiam da captura AIX em 40/45 checkpoints só por CONTEÚDO de dados
   (ex.: consumidor padrão "consumidor" × "indeterminado") — a cópia
   binária dos .est é impossível (header big×little-endian). A equivalência
   funcional passa a usar baseline PRÓPRIO do ambiente (passada real), e a
   decisão NUNCA deixa isso virar PASS (§20: "dados diferentes");
2. **Parada da escada tratada como falha genérica**: no nível conc20 o AIX
   saturou (CPU 100% sustentada) e o Linux estourou a licença do runtime
   ("User limit exceeded") — as runs falharam e o experimento inteiro foi
   para FAILED/FAIL. §17 prescreve que parar a escada é o comportamento
   correto: as falhas do NÍVEL PARADO são o achado de capacidade (ABORTED),
   o experimento fica COMPLETED e a decisão sinaliza WARN (nunca PASS).
"""
from __future__ import annotations

import json
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
from dakota_gateway.benchmark.decision import decide  # noqa: E402
from dakota_gateway.benchmark.degradation import DegradationReport  # noqa: E402
from dakota_gateway.benchmark.executor import BenchmarkExecutor  # noqa: E402
from dakota_gateway.benchmark.models import OperationSample  # noqa: E402
from dakota_gateway.benchmark.stats import Stats  # noqa: E402
from dakota_gateway.cli import _bench_apply_env_baselines  # noqa: E402


def _stats_ok() -> Stats:
    return Stats(n=100, mean=10.0, p50=9.0, p90=12.0, p95=13.0, p99=15.0,
                 max=20.0, stdev=1.0, cv=0.1, ci95_low=9.8, ci95_high=10.2)


def _contrato_base() -> "object":
    """Contrato mínimo para testes de adapter (1 env, 1 nível)."""
    return create_contract(
        experiment_id="exp-transporte",
        journey_set_sha256="a" * 64,
        dataset_sha256="b" * 64,
        application_version_sha256="c" * 64,
        seed=1, terminal_geometry="80x24",
        concurrency_levels=(1,),
        warmup_seconds=0, measurement_seconds=0, cooldown_seconds=0,
        iterations=1,
        think_time_profile=ThinkTimeProfile(
            type="none", sha256="d" * 64, params={}),
        stop_conditions=StopConditions(),
        environments=("linux-x86",),
    )


def _decide_ok(**kwargs) -> "object":
    """decide() com todas as portas verdes (PASS) + overrides do teste."""
    params = dict(
        functional_ok=True, functional_diffs=[],
        stats_by_env={"aix-power": _stats_ok(), "linux-x86": _stats_ok()},
        samples_complete=True, collectors_ok=True, ci_acceptable=True,
        degradation=DegradationReport(
            degradation_point=None, safe_operational_limit=None,
            maximum_observed_limit=None, dominant_bottleneck="unknown",
            recovery_seconds=None),
        normalization_status="OK")
    params.update(kwargs)
    return decide(**params)


class TestDecisaoBaselineProprio(unittest.TestCase):
    """Baseline próprio (per_env) e stop_reason limitam o veredito a WARN."""

    def test_caminho_feliz_sem_ressalvas_pass(self) -> None:
        self.assertEqual("PASS", _decide_ok().verdict)

    def test_baseline_proprio_nunca_pass(self) -> None:
        decision = _decide_ok(functional_basis="per_env")
        self.assertEqual("WARN", decision.verdict)
        razoes = " ".join(decision.reasons)
        self.assertIn("baseline próprio", razoes)
        self.assertIn("paridade de dados", razoes)

    def test_stop_reason_nunca_pass(self) -> None:
        decision = _decide_ok(stop_reason={
            "iteration": 1, "concurrency": 20, "condition": "host_cpu_pct",
            "value": 100.0, "limit": 98.0})
        self.assertEqual("WARN", decision.verdict)
        razoes = " ".join(decision.reasons)
        self.assertIn("stop_condition:host_cpu_pct", razoes)
        self.assertIn("20", razoes)

    def test_baseline_compartilhado_sem_stop_permite_pass(self) -> None:
        decision = _decide_ok(functional_basis="shared", stop_reason=None)
        self.assertEqual("PASS", decision.verdict)


class TestMergeEnvBaseline(unittest.TestCase):
    """Merge do baseline próprio nos passos da jornada (CLI)."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def _jornada(self) -> list[dict]:
        return [{
            "journey_id": "j",
            "steps": [
                {"step_id": "ev-1", "key_b64": "QQ==",
                 "expected_screen_text": "TELA-1-AIX",
                 "lag_window_texts": ["TELA-2-AIX"],
                 "lag_window_step_ids": ["ev-2"]},
                {"step_id": "ev-2", "key_b64": "Qg==",
                 "expected_screen_text": "TELA-2-AIX"},
                {"step_id": "ev-3", "key_b64": "Qw=="},  # sem checkpoint
            ],
        }]

    def _baseline(self, dados: dict) -> Path:
        arq = self.tmp / "baseline.json"
        arq.write_text(json.dumps(dados), encoding="utf-8")
        return arq

    def test_merge_preenche_texto_e_janela_por_env(self) -> None:
        arq = self._baseline({"ev-1": "TELA-1-LINUX", "ev-2": "TELA-2-LINUX"})
        jornadas = self._jornada()
        manifestos = _bench_apply_env_baselines(
            jornadas, [f"linux-x86:{arq}"])
        steps = jornadas[0]["steps"]
        self.assertEqual(
            "TELA-1-LINUX",
            steps[0]["expected_screen_text_by_env"]["linux-x86"])
        self.assertEqual(
            ["TELA-2-LINUX"],
            steps[0]["lag_window_texts_by_env"]["linux-x86"])
        self.assertEqual(
            "TELA-2-LINUX",
            steps[1]["expected_screen_text_by_env"]["linux-x86"])
        # texto compartilhado preservado como fallback dos demais ambientes
        self.assertEqual("TELA-1-AIX", steps[0]["expected_screen_text"])
        self.assertEqual(1, len(manifestos))
        self.assertEqual("linux-x86", manifestos[0]["environment_id"])
        self.assertEqual(2, manifestos[0]["screens"])
        self.assertEqual(64, len(manifestos[0]["sha256"]))

    def test_merge_exige_cobertura_total_dos_checkpoints(self) -> None:
        arq = self._baseline({"ev-1": "TELA-1-LINUX"})  # falta ev-2
        with self.assertRaises(ValueError) as ctx:
            _bench_apply_env_baselines(self._jornada(), [f"linux-x86:{arq}"])
        self.assertIn("ev-2", str(ctx.exception))

    def test_merge_rejeita_formato_e_arquivo_inexistente(self) -> None:
        with self.assertRaises(ValueError):
            _bench_apply_env_baselines(self._jornada(), ["sem-dois-pontos"])
        with self.assertRaises(ValueError):
            _bench_apply_env_baselines(
                self._jornada(), ["linux-x86:/nao/existe.json"])


class _AdapterQueFalhaNoNivel:
    """Adaptador fake: nível 1 completa; nível 2 falha no start_session.

    ``collect_host_metrics`` devolve CPU sustentada alta para a stop
    condition disparar no nível 2.
    """

    def __init__(self, env_id: str, *, falha_no_nivel: int = 2,
                 cpus: tuple = (100.0, 100.0, 100.0, 100.0)) -> None:
        self.environment_id = env_id
        self._falha_no_nivel = falha_no_nivel
        self._cpus = cpus
        self._concurrency = 0
        self.journey_incompletions: list = []

    def set_iteration_context(self, iteration: int, concurrency: int) -> None:
        self._concurrency = concurrency

    def _cpus_do_nivel(self) -> tuple:
        """CPU alta só no nível da falha (saturação); baixa nos demais."""
        if self._concurrency >= self._falha_no_nivel:
            return self._cpus
        return (10.0, 11.0, 12.0, 9.0)

    def preflight(self) -> dict:
        return {"ok": True, "checks": [{"name": "fake", "ok": True}]}

    def prepare_dataset(self, dataset_ref: dict) -> dict:
        return {"ok": True}

    def start_session(self, virtual_user_id: str) -> str:
        if self._concurrency >= self._falha_no_nivel:
            raise RuntimeError(
                "start_session_failed: anchor não apareceu (host saturado)")
        return f"h-{virtual_user_id}"

    def execute_journey(self, session_handle: str, journey: dict,
                        *, phase: str) -> list:
        ns0 = 1_000_000_000
        return [OperationSample(
            experiment_id="exp-stop-level", environment_id=self.environment_id,
            iteration=1, concurrency=self._concurrency,
            virtual_user_id="vu-1", journey_id="j", step_id="s1",
            phase=phase, started_ns=ns0, finished_ns=ns0 + 10_000_000,
            latency_ms=10.0, success=True, timeout=False,
            functional_divergence=False, error_code=None,
            screen_sig_checked=True)]

    def stop_session(self, session_handle: str) -> None:
        pass

    def collect_application_metrics(self) -> dict:
        return {"available": True}

    def collect_host_metrics(self, from_ms: int, to_ms: int) -> list[dict]:
        return [{"ts_ms": from_ms + i * 1000, "cpu_pct": cpu}
                for i, cpu in enumerate(self._cpus_do_nivel())]

    def collect_database_metrics(self) -> dict:
        return {"available": False, "reason": "collector_not_supported"}

    def cleanup(self) -> None:
        pass


class TestExecutorStopLevel(unittest.TestCase):
    """Falhas do nível parado por stop_condition → ABORTED, não FAILED."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def _contrato(self) -> "object":
        return create_contract(
            experiment_id="exp-stop-level",
            journey_set_sha256="a" * 64,
            dataset_sha256="b" * 64,
            application_version_sha256="c" * 64,
            seed=1, terminal_geometry="80x24",
            concurrency_levels=(1, 2),
            warmup_seconds=0, measurement_seconds=0, cooldown_seconds=0,
            iterations=1,
            think_time_profile=ThinkTimeProfile(
                type="none", sha256="d" * 64, params={}),
            stop_conditions=StopConditions(
                error_rate_pct=99.0, p99_limit_ms=999999.0,
                host_cpu_pct=95.0, host_cpu_sustained_samples=3),
            environments=("env-a", "env-b"),
        )

    def test_falhas_so_no_nivel_parado_aborted_e_completed(self) -> None:
        contrato = self._contrato()
        adapters = {
            "env-a": _AdapterQueFalhaNoNivel("env-a"),
            "env-b": _AdapterQueFalhaNoNivel("env-b"),
        }
        executor = BenchmarkExecutor(
            contrato, adapters, Path(self._tmp.name),
            journeys=[{"journey_id": "j", "steps": [{"step_id": "s1"}]}])
        resultado = executor.run()
        self.assertEqual("COMPLETED", resultado.status)
        self.assertEqual("stop_condition:host_cpu_pct", resultado.reason)
        self.assertIsNotNone(resultado.stop_reason)
        self.assertEqual(2, resultado.stop_reason["concurrency"])
        por_nivel = {}
        for run in resultado.runs:
            por_nivel.setdefault(run.concurrency, []).append(run.status)
        self.assertEqual(["COMPLETED", "COMPLETED"], sorted(por_nivel[1]))
        self.assertEqual(["ABORTED", "ABORTED"], sorted(por_nivel[2]))

    def test_falha_de_admissao_vira_teto_e_preserva_repeticoes(self) -> None:
        """Admission limit (§17): nível com ZERO sessões admitidas nos 2
        ambientes (sem transporte, sem saturação de CPU) vira teto: runs do
        nível ABORTED, níveis superiores pulados, mas as repetições dos
        níveis inferiores na iteração seguinte SÃO executadas."""
        contrato = create_contract(
            experiment_id="exp-admission",
            journey_set_sha256="a" * 64,
            dataset_sha256="b" * 64,
            application_version_sha256="c" * 64,
            seed=1, terminal_geometry="80x24",
            concurrency_levels=(1, 2, 5),
            warmup_seconds=0, measurement_seconds=0, cooldown_seconds=0,
            iterations=2,
            think_time_profile=ThinkTimeProfile(
                type="none", sha256="d" * 64, params={}),
            stop_conditions=StopConditions(
                error_rate_pct=99.0, p99_limit_ms=999999.0,
                host_cpu_pct=95.0, host_cpu_sustained_samples=3),
            environments=("env-a", "env-b"),
        )
        adapters = {
            # falha no start a partir do nível 2, CPU SEMPRE baixa (sem
            # saturação medida — a admission rule, não host_cpu, deve parar)
            "env-a": _AdapterQueFalhaNoNivel("env-a", falha_no_nivel=2,
                                             cpus=(10.0, 11.0, 12.0, 9.0)),
            "env-b": _AdapterQueFalhaNoNivel("env-b", falha_no_nivel=2,
                                             cpus=(10.0, 11.0, 12.0, 9.0)),
        }
        executor = BenchmarkExecutor(
            contrato, adapters, Path(self._tmp.name),
            journeys=[{"journey_id": "j", "steps": [{"step_id": "s1"}]}])
        resultado = executor.run()
        self.assertEqual("COMPLETED", resultado.status)
        self.assertEqual("stop_condition:session_admission_limit",
                         resultado.reason)
        self.assertIsNotNone(resultado.stop_reason)
        self.assertEqual(2, resultado.stop_reason["concurrency"])
        vistos = sorted(
            (r.iteration, r.concurrency, r.environment_id, r.status)
            for r in resultado.runs)
        esperado = sorted([
            (1, 1, "env-a", "COMPLETED"), (1, 1, "env-b", "COMPLETED"),
            (1, 2, "env-a", "ABORTED"), (1, 2, "env-b", "ABORTED"),
            # iteração 2: nível 1 repete (estatística); níveis >= 2 pulados
            (2, 1, "env-a", "COMPLETED"), (2, 1, "env-b", "COMPLETED"),
        ])
        self.assertEqual(esperado, vistos)
        # a reclassificação é PERSISTIDA: o rebuild (compare/report) lê o
        # status do disco — evidência não pode divergir da memória (bug v7)
        for env in ("env-a", "env-b"):
            resumo = json.loads(
                (Path(self._tmp.name) / "exp-admission" / "runs"
                 / f"{env}-iter1-conc2" / "execution-result.json")
                .read_text(encoding="utf-8"))
            self.assertEqual("ABORTED", resumo["status"])

    def test_falha_fora_do_nivel_parado_continua_failed(self) -> None:
        contrato = self._contrato()
        adapters = {
            # env-a falha JÁ no nível 1 — falha real, não saturação
            "env-a": _AdapterQueFalhaNoNivel("env-a", falha_no_nivel=1,
                                             cpus=(10.0, 11.0, 12.0, 9.0)),
            "env-b": _AdapterQueFalhaNoNivel("env-b",
                                             cpus=(10.0, 11.0, 12.0, 9.0)),
        }
        executor = BenchmarkExecutor(
            contrato, adapters, Path(self._tmp.name),
            journeys=[{"journey_id": "j", "steps": [{"step_id": "s1"}]}])
        resultado = executor.run()
        self.assertEqual("FAILED", resultado.status)
        # o nível 2 falhou admissão nos 2 ambientes → teto registrado e runs
        # do nível ABORTED; mas a falha do nível 1 (env-a) NÃO é reclassificada
        # e continua derrubando o experimento para FAILED.
        self.assertIsNotNone(resultado.stop_reason)
        self.assertEqual("session_admission_limit",
                         resultado.stop_reason["condition"])
        status_por_nivel = {}
        for r in resultado.runs:
            status_por_nivel.setdefault(r.concurrency, []).append(r.status)
        self.assertIn("FAILED", status_por_nivel[1])
        self.assertEqual(["ABORTED", "ABORTED"],
                         sorted(status_por_nivel[2]))


class _AdapterSemTransporte:
    """Adaptador fake: toda start_session falha por erro de transporte."""

    def __init__(self, env_id: str) -> None:
        self.environment_id = env_id
        self.last_start_error_transport = False
        self.journey_incompletions: list = []

    def set_iteration_context(self, iteration: int, concurrency: int) -> None:
        pass

    def preflight(self) -> dict:
        # preflight passa (probe "true" pontual); o colapso vem depois
        return {"ok": True, "checks": [{"name": "fake", "ok": True}]}

    def prepare_dataset(self, dataset_ref: dict) -> dict:
        return {"ok": True}

    def start_session(self, virtual_user_id: str) -> str:
        self.last_start_error_transport = True
        raise RuntimeError(
            "start_session_failed: passo 0: anchor '$' não apareceu em 15s")

    def execute_journey(self, session_handle: str, journey: dict,
                        *, phase: str) -> list:
        return []

    def stop_session(self, session_handle: str) -> None:
        pass

    def collect_application_metrics(self) -> dict:
        return {"available": True}

    def collect_host_metrics(self, from_ms: int, to_ms: int) -> list[dict]:
        return [{"ts_ms": from_ms, "cpu_pct": 5.0}]

    def collect_database_metrics(self) -> dict:
        return {"available": False, "reason": "collector_not_supported"}

    def cleanup(self) -> None:
        pass


class TestExecutorTransporteCaido(unittest.TestCase):
    """Colapso de transporte (VPN/rede local): aborto cedo, INCONCLUSIVE.

    Caso real cap13 v5: a VPN do orquestrador caiu no meio do run; sem o
    fail-fast o experimento moeria TODOS os níveis × iterações por horas
    com start_session_failed antes de concluir INCONCLUSIVE.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def test_dois_niveis_sem_transporte_abortam_cedo(self) -> None:
        contrato = create_contract(
            experiment_id="exp-transporte",
            journey_set_sha256="a" * 64,
            dataset_sha256="b" * 64,
            application_version_sha256="c" * 64,
            seed=1, terminal_geometry="80x24",
            concurrency_levels=(1, 2, 5, 10),
            warmup_seconds=0, measurement_seconds=0, cooldown_seconds=0,
            iterations=3,
            think_time_profile=ThinkTimeProfile(
                type="none", sha256="d" * 64, params={}),
            stop_conditions=StopConditions(
                error_rate_pct=99.0, p99_limit_ms=999999.0,
                host_cpu_pct=99.0),
            environments=("env-a", "env-b"),
        )
        adapters = {
            "env-a": _AdapterSemTransporte("env-a"),
            "env-b": _AdapterSemTransporte("env-b"),
        }
        executor = BenchmarkExecutor(
            contrato, adapters, Path(self._tmp.name),
            journeys=[{"journey_id": "j", "steps": [{"step_id": "s1"}]}])
        resultado = executor.run()
        self.assertEqual("FAILED", resultado.status)
        self.assertEqual("environment_unreachable_mid_run", resultado.reason)
        self.assertIsNone(resultado.stop_reason)
        # abortou após o 2º nível condenado — NÃO moeu os 12 níveis do plano
        self.assertEqual(4, len(resultado.runs))  # 2 níveis × 2 ambientes
        self.assertTrue(all(r.status == "FAILED" for r in resultado.runs))


class TestClassificacaoErroTransporte(unittest.TestCase):
    """Adapter: last_start_error_transport reflete a saída da sessão."""

    def _adapter_com_saida(self, saida: bytes):
        import os
        import threading

        from dakota_gateway.benchmark.adapters import SSHReplayAdapter
        from dakota_gateway.benchmark.environments import (
            CpuModel, EnvironmentModel)

        class _Proc:
            def __init__(self, argv, stdin=None, stdout=None, stderr=None):
                self._stdin_r, stdin_w = os.pipe()
                stdout_r, self._stdout_w = os.pipe()
                self.stdin = os.fdopen(stdin_w, "wb", buffering=0)
                self.stdout = os.fdopen(stdout_r, "rb", buffering=0)
                os.write(self._stdout_w, saida)
                os.close(self._stdout_w)  # EOF imediato (remoto caiu)

            def terminate(self) -> None:
                pass

            def kill(self) -> None:
                pass

            def wait(self, timeout=None) -> int:
                return 0

        def _popen(argv, **kwargs):
            return _Proc(argv, **kwargs)

        def _runner(argv, input_text, timeout):
            class _R:
                returncode = 0
                stdout = ""
                stderr = ""
            return _R()

        env = EnvironmentModel(
            environment_id="linux-x86", platform="Linux",
            architecture="x86_64", host="192.0.2.30", port=22,
            user_secret_ref="ssh-key:u@192.0.2.30",
            application_endpoint="ssh://u@192.0.2.30:22",
            cpu=CpuModel(model="x", virtual_processors=1,
                         physical_processors=1),
            memory_mb=1024,
            entry_preamble=({"wait_text": "$", "timeout_s": 1},))
        return SSHReplayAdapter(env, _contrato_base(),
                                ssh_runner=_runner, popen_factory=_popen)

    def test_erro_de_rede_marca_transporte(self) -> None:
        adapter = self._adapter_com_saida(
            b"ssh: connect to host 10.5.8.24 port 22: Network is unreachable\r\n")
        with self.assertRaises(Exception):
            adapter.start_session("vu-1")
        self.assertTrue(adapter.last_start_error_transport)

    def test_saida_vazia_marca_transporte(self) -> None:
        adapter = self._adapter_com_saida(b"")
        with self.assertRaises(Exception):
            adapter.start_session("vu-1")
        self.assertTrue(adapter.last_start_error_transport)

    def test_conteudo_de_tela_nao_marca_transporte(self) -> None:
        # licença/saturação chegam como CONTEÚDO — não é transporte
        adapter = self._adapter_com_saida(b"User limit exceeded\r\nConfirm")
        with self.assertRaises(Exception):
            adapter.start_session("vu-1")
        self.assertFalse(adapter.last_start_error_transport)


if __name__ == "__main__":
    unittest.main()

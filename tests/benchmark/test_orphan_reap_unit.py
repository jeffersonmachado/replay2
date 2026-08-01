"""Testes do janitor de processos órfãos do benchmark (reap_orphans).

Contexto real (cap13/AIX): matar o ssh local de uma sessão de replay NÃO
mata a árvore remota — o ksh de login sobrevive (reparented no init) e o
``db.exe`` (runtime Recital) continua rodando dentro do grupo. Dezenas de
sessões mortas no meio da jornada saturaram o host (load 23, draws do ERP
com pausas de ~1s — medido em 01/08/2026).

O janitor roda um comando remoto CONFIGURÁVEL por ambiente
(``orphan_reap_cmd`` no EnvironmentModel — conhecimento de plataforma fica
na configuração do ambiente, nunca no código do benchmark, §8). Sessões
VIVAS têm PPID=sshd; órfãos têm PPID=1 — o comando de limpeza só toca
grupos cujo shell está órfão, logo é seguro rodá-lo entre fases mesmo com
concorrência alta.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "gateway"))

from dakota_gateway.benchmark.adapters import SSHReplayAdapter  # noqa: E402
from dakota_gateway.benchmark.contract import (  # noqa: E402
    StopConditions,
    ThinkTimeProfile,
    create_contract,
)
from dakota_gateway.benchmark.environments import (  # noqa: E402
    CpuModel,
    EnvironmentModel,
)


def _contrato(environments=("aix-power",)):
    return create_contract(
        experiment_id="exp-reap-unit",
        journey_set_sha256="a" * 64,
        dataset_sha256="b" * 64,
        application_version_sha256="c" * 64,
        seed=1,
        terminal_geometry="80x24",
        concurrency_levels=(1,),
        warmup_seconds=1,
        measurement_seconds=1,
        cooldown_seconds=1,
        iterations=1,
        think_time_profile=ThinkTimeProfile(type="none", sha256="d" * 64, params={}),
        stop_conditions=StopConditions(),
        environments=environments,
    )


def _modelo(reap_cmd: str = "") -> EnvironmentModel:
    return EnvironmentModel(
        environment_id="aix-power",
        platform="AIX",
        architecture="POWER",
        host="192.0.2.20",
        port=22,
        user_secret_ref="ssh-key:ferblo@192.0.2.20",
        application_endpoint="ssh://ferblo@192.0.2.20:22",
        cpu=CpuModel(model="POWER9", virtual_processors=4, physical_processors=2),
        memory_mb=16384,
        orphan_reap_cmd=reap_cmd,
    )


class _RunnerEspiao:
    def __init__(self):
        self.chamadas: list[list[str]] = []

    def __call__(self, argv, input_text, timeout):
        self.chamadas.append(list(argv))

        class _R:
            returncode = 0
            stdout = ""
            stderr = ""
        return _R()


class TestReapOrphans(unittest.TestCase):
    """reap_orphans executa o comando configurado; vazio = no-op seguro."""

    def test_executa_comando_configurado_via_ssh(self) -> None:
        runner = _RunnerEspiao()
        adapter = SSHReplayAdapter(_modelo("pkill -KILL -u ferblo ksh"),
                                   _contrato(), ssh_runner=runner)
        resultado = adapter.reap_orphans()
        self.assertTrue(resultado["executed"])
        self.assertEqual(1, len(runner.chamadas))
        argv = runner.chamadas[0]
        # comando vai como último argumento do ssh, no host do ambiente
        self.assertEqual("pkill -KILL -u ferblo ksh", argv[-1])
        self.assertIn("ferblo@192.0.2.20", argv)

    def test_sem_comando_configurado_e_noop(self) -> None:
        runner = _RunnerEspiao()
        adapter = SSHReplayAdapter(_modelo(""), _contrato(), ssh_runner=runner)
        resultado = adapter.reap_orphans()
        self.assertFalse(resultado["executed"])
        self.assertEqual("not_configured", resultado["reason"])
        self.assertEqual([], runner.chamadas)

    def test_falha_de_transporte_nao_derruba_o_benchmark(self) -> None:
        def runner_falho(argv, input_text, timeout):
            raise OSError("ssh indisponível")
        adapter = SSHReplayAdapter(_modelo("pkill x"), _contrato(),
                                   ssh_runner=runner_falho)
        resultado = adapter.reap_orphans()
        self.assertFalse(resultado["executed"])
        self.assertEqual("error", resultado["reason"])


class TestExecutorChamaJanitor(unittest.TestCase):
    """O executor chama reap_orphans ao fim de CADA fase (duck typing).

    Órfãos acumulados DENTRO de um nível distorcem a medição das fases
    seguintes (caso real: load 23 no AIX inflou as latências do v3) — a
    limpeza não pode esperar o fim do experimento. Adaptadores sem o
    método (ex.: ControlledAdapter padrão) simplesmente não são chamados.
    """

    def test_reap_chamado_uma_vez_por_fase(self) -> None:
        import tempfile

        from dakota_gateway.benchmark.executor import BenchmarkExecutor
        from tests.benchmark.support.controlled_adapter import ControlledAdapter

        class AdapterComReap(ControlledAdapter):
            def __init__(self, **kwargs):
                super().__init__(**kwargs)
                self.reaps = 0

            def reap_orphans(self):
                self.reaps += 1
                return {"executed": True}

        adapter = AdapterComReap(environment_id="env-controlled")
        jornada = {"journey_id": "j1",
                   "steps": [{"step_id": "op0", "delay_ms": 0.0}]}
        with tempfile.TemporaryDirectory() as tmp:
            executor = BenchmarkExecutor(
                _contrato(environments=("env-controlled",)),
                {"env-controlled": adapter},
                __import__("pathlib").Path(tmp), journeys=[jornada])
            resultado = executor.run()
        self.assertEqual("COMPLETED", resultado.status)
        # 3 fases (WARMUP, MEASUREMENT, COOLDOWN) → 3 reaps
        self.assertEqual(3, adapter.reaps)


class TestEnvironmentModelReapCmd(unittest.TestCase):
    """orphan_reap_cmd no modelo: roundtrip e tolerância a ausência."""

    def test_roundtrip(self) -> None:
        modelo = _modelo("pkill -KILL -u ferblo ksh")
        restaurado = EnvironmentModel.from_dict(modelo.to_dict())
        self.assertEqual("pkill -KILL -u ferblo ksh",
                         restaurado.orphan_reap_cmd)

    def test_ausente_vira_vazio(self) -> None:
        restaurado = EnvironmentModel.from_dict({
            "environment_id": "e1", "platform": "AIX",
            "architecture": "POWER", "host": "192.0.2.20",
        })
        self.assertEqual("", restaurado.orphan_reap_cmd)


if __name__ == "__main__":
    unittest.main()

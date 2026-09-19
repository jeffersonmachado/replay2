"""Clock offset: compensação do RTT da medição SSH (caso real v8, 2026-09-17).

O offset de clock é medido embutindo o timestamp local no script remoto
(``__LOCAL_NOW_MS__``) e computando ``remote_now - local_now`` no host. Em
links com latência (VPN) o valor medido é ``skew_real + atraso_transporte``:
no experimento v8 os relógios estavam sincronizados (estação e Linux em NTP,
hosts a ~1 s entre si), mas o gate de 1000 ms reprovou os DOIS ambientes
(1209/2116 ms) — falso positivo puramente de transporte.

Regra fixada por estes testes:

- o coletor mede o RTT da chamada SSH e registra o offset COMPENSADO
  (``remote_now - ponto_médio_local``) em ``clock_offset_ms``, mantendo
  ``clock_offset_raw_ms`` e ``clock_rtt_ms`` como evidência;
- sentinela legada (sem ``remote_now_ms``) cai para o offset bruto —
  retrocompatibilidade com o formato antigo.

Estes testes DEVEM FALHAR antes da correção e PASSAR depois dela.
"""
from __future__ import annotations

import json
import re
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "gateway"))

from dakota_gateway.benchmark.adapters import SSHReplayAdapter  # noqa: E402
from dakota_gateway.benchmark.environments import (  # noqa: E402
    CpuModel,
    EnvironmentModel,
)

_SKEW_REAL_MS = 300       # relógio remoto 300 ms adiantado (dentro do gate)
_ATRASO_TRANSPORTE_S = 0.7  # cada sentido — RTT ~1,4 s (VPN)


def _modelo() -> EnvironmentModel:
    return EnvironmentModel(
        environment_id="env-a", platform="Linux", architecture="x86_64",
        host="192.0.2.10", port=22,
        user_secret_ref="ssh-key:ferblo@192.0.2.10",
        application_endpoint="ssh://ferblo@192.0.2.10:22",
        cpu=CpuModel(model="Xeon", virtual_processors=8,
                     physical_processors=8),
        memory_mb=8192,
    )


class _Res:
    def __init__(self, stdout: str) -> None:
        self.returncode = 0
        self.stdout = stdout
        self.stderr = ""


def _runner_com_latencia(argv, entrada: str, timeout):
    """Simula SSH sobre VPN: 0,7 s de ida, execução, 0,7 s de volta.

    O relógio "remoto" está ``_SKEW_REAL_MS`` adiantado. Responde a sentinela
    no formato novo (com ``remote_now_ms``), como o script remoto real faria.
    """
    m = re.search(r"offset = remote_now - (\d+)", entrada)
    local_embedded = int(m.group(1)) if m else 0
    time.sleep(_ATRASO_TRANSPORTE_S)  # ida
    remote_now = int(time.time() * 1000) + _SKEW_REAL_MS
    offset_bruto = remote_now - local_embedded
    time.sleep(_ATRASO_TRANSPORTE_S)  # volta
    sentinela = {
        "host_metrics_query": "done", "rows": 0,
        "clock_offset_ms": offset_bruto, "remote_now_ms": remote_now,
    }
    return _Res(json.dumps(sentinela) + "\n")


def _runner_legado(argv, entrada: str, timeout):
    """Sentinela antiga (sem remote_now_ms) — offset bruto de 800 ms."""
    return _Res('{"host_metrics_query": "done", "rows": 0, '
                '"clock_offset_ms": 800}\n')


class TestClockOffsetCompensado(unittest.TestCase):
    def test_offset_compensado_remove_latencia_do_transporte(self) -> None:
        """Skew real 300 ms + RTT ~1,4 s: o offset registrado deve ficar
        próximo de 300 ms (gate 1000 ms), NÃO ~1700 ms (skew+transporte)."""
        adapter = SSHReplayAdapter(
            _modelo(), contrato_stub(), ssh_runner=_runner_com_latencia)
        t0 = int(time.time() * 1000)
        adapter.collect_host_metrics(t0 - 60_000, t0)
        status = adapter.host_metrics_status
        self.assertTrue(status.get("available"), status)
        offset = status.get("clock_offset_ms")
        self.assertIsNotNone(offset)
        # compensado: |offset| deve estar perto do skew real (300 ms) e
        # claramente abaixo do gate de 1000 ms
        self.assertLessEqual(abs(offset - _SKEW_REAL_MS), 400, status)
        # evidência do transporte registrada
        self.assertGreaterEqual(status.get("clock_rtt_ms", 0), 1300)
        # bruto = skew + atraso de ida (~700+300 ms); claramente acima do skew
        self.assertGreaterEqual(
            abs(status.get("clock_offset_raw_ms", 0)), 900)

    def test_sentinela_legada_sem_remote_now_usa_offset_bruto(self) -> None:
        """Retrocompatibilidade: sentinela sem ``remote_now_ms`` registra o
        valor bruto como antes (800 ms)."""
        adapter = SSHReplayAdapter(
            _modelo(), contrato_stub(), ssh_runner=_runner_legado)
        t0 = int(time.time() * 1000)
        adapter.collect_host_metrics(t0 - 60_000, t0)
        status = adapter.host_metrics_status
        self.assertTrue(status.get("available"), status)
        self.assertEqual(800, status.get("clock_offset_ms"))


def contrato_stub():
    """Contrato mínimo para instanciar o adapter (a coleta não o usa)."""
    from dakota_gateway.benchmark.contract import (
        StopConditions, ThinkTimeProfile, create_contract,
    )
    return create_contract(
        experiment_id="exp-clock-rtt",
        journey_set_sha256="a" * 64, dataset_sha256="b" * 64,
        application_version_sha256="c" * 64,
        seed=1, terminal_geometry="80x24", concurrency_levels=(1,),
        warmup_seconds=0, measurement_seconds=0, cooldown_seconds=0,
        iterations=1,
        think_time_profile=ThinkTimeProfile(type="none", sha256="d" * 64,
                                            params={}),
        stop_conditions=StopConditions(
            error_rate_pct=99.0, p99_limit_ms=999999.0, host_cpu_pct=99.0),
        environments=("env-a",),
    )


if __name__ == "__main__":
    unittest.main()

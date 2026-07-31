"""Testes unitários do SSHReplayAdapter com ssh MOCKADO (subprocess fake).

Estes testes são ADICIONAIS aos testes imutáveis da P1: exercitam o adaptador
real (``dakota_gateway.benchmark.adapters.SSHReplayAdapter``) sem nenhum
servidor — o processo ``ssh -tt`` é substituído por um fake com pipes reais
(``os.pipe``) e uma thread servidora que lê o input, dorme ``delay_ms`` REAL
e responde, como faria a aplicação remota. A latência medida pelo adaptador
continua sendo cronometragem real (``time.monotonic_ns``) sobre round-trips
reais pelos pipes.
"""
from __future__ import annotations

import base64
import json
import os
import select
import sys
import threading
import time
import unittest
from unittest import mock
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


def _contrato() -> "object":
    return create_contract(
        experiment_id="exp-ssh-unit",
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
        environments=("linux-x86",),
    )


def _modelo() -> EnvironmentModel:
    return EnvironmentModel(
        environment_id="linux-x86",
        platform="Linux",
        architecture="x86_64",
        host="192.0.2.10",
        port=22,
        user_secret_ref="ssh-key:ferblo@192.0.2.10",
        application_endpoint="ssh://ferblo@192.0.2.10:22",
        cpu=CpuModel(model="Xeon", virtual_processors=8, physical_processors=8),
        memory_mb=8192,
    )


class _FakeCompleted:
    """Resultado fake de subprocess.run para o ssh one-shot."""

    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = ""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class _FakeSSHProcess:
    """Processo ssh PTY fake: pipes reais + thread "aplicação remota".

    Protocolo da aplicação fake: cada linha recebida no formato
    ``OP <delay_ms>`` dorme ``delay_ms`` (atraso REAL) e devolve
    ``RESP <delay_ms>``. No modo ``flood``, escreve sem parar (nunca fica
    estável) para exercitar o caminho de timeout.
    """

    def __init__(self, argv, stdin=None, stdout=None, stderr=None,
                 flood: bool = False):
        self.argv = argv
        self._flood = flood
        self._stdin_r, stdin_w = os.pipe()
        stdout_r, self._stdout_w = os.pipe()
        self.stdin = os.fdopen(stdin_w, "wb", buffering=0)
        self.stdout = os.fdopen(stdout_r, "rb", buffering=0)
        self._stop = threading.Event()
        self.returncode: int | None = None
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        if self._flood:
            while not self._stop.is_set():
                try:
                    os.write(self._stdout_w, b"fluxo-continuo\n")
                except OSError:
                    break
                time.sleep(0.005)
            return
        buf = b""
        while not self._stop.is_set():
            prontos, _, _ = select.select([self._stdin_r], [], [], 0.05)
            if not prontos:
                continue
            try:
                chunk = os.read(self._stdin_r, 65536)
            except OSError:
                break
            if not chunk:
                break  # stdin fechado pelo adaptador (stop_session)
            buf += chunk
            while b"\n" in buf:
                linha, buf = buf.split(b"\n", 1)
                # PTY real ecoa o input imediatamente; a resposta da
                # aplicação vem depois do atraso injetado
                try:
                    os.write(self._stdout_w, linha + b"\r\n")
                except OSError:
                    return
                partes = linha.decode("utf-8", "replace").split()
                delay_ms = float(partes[1]) if len(partes) > 1 else 0.0
                if delay_ms > 0:
                    time.sleep(delay_ms / 1000.0)  # atraso REAL
                try:
                    os.write(self._stdout_w, f"RESP {delay_ms}\n".encode())
                except OSError:
                    return

    def terminate(self) -> None:
        self._stop.set()

    def kill(self) -> None:
        self._stop.set()

    def wait(self, timeout: float | None = None) -> int:
        self._stop.set()
        self._thread.join(timeout=timeout or 1.0)
        try:
            os.close(self._stdout_w)
        except OSError:
            pass
        self.returncode = 0
        return 0


def _popen_ok(argv, **kwargs):
    return _FakeSSHProcess(argv, **kwargs)


def _popen_flood(argv, **kwargs):
    return _FakeSSHProcess(argv, flood=True, **kwargs)


def _runner_ok(argv, input_text, timeout):
    return _FakeCompleted(0, "", "")


def _runner_falho(argv, input_text, timeout):
    return _FakeCompleted(255, "", "ssh: connect to host: Connection refused")


def _jornada(atrasos_ms: list[float]) -> dict:
    return {
        "journey_id": "j-ssh",
        "steps": [
            {
                "step_id": f"op{i}",
                "key_b64": base64.b64encode(f"OP {a}\n".encode()).decode(),
            }
            for i, a in enumerate(atrasos_ms)
        ],
    }


class TestSSHReplayAdapterPreflight(unittest.TestCase):
    """Preflight real via ssh (mockado): ok e falha de conexão."""

    def test_preflight_ok(self) -> None:
        adapter = SSHReplayAdapter(_modelo(), _contrato(), ssh_runner=_runner_ok)
        res = adapter.preflight()
        self.assertTrue(res["ok"])
        self.assertTrue(all(c["ok"] for c in res["checks"]))

    def test_preflight_conexao_recusada(self) -> None:
        adapter = SSHReplayAdapter(_modelo(), _contrato(), ssh_runner=_runner_falho)
        res = adapter.preflight()
        self.assertFalse(res["ok"])
        self.assertIn("Connection refused", res["checks"][0]["detail"])

    def test_argv_ssh_usa_batchmode_e_destino(self) -> None:
        vistos: list[list[str]] = []

        def runner(argv, input_text, timeout):
            vistos.append(argv)
            return _FakeCompleted(0, '{"host_metrics_query": "done", "rows": 0}\n', "")

        adapter = SSHReplayAdapter(_modelo(), _contrato(), ssh_runner=runner)
        adapter.preflight()
        argv = vistos[0]
        self.assertIn("BatchMode=yes", argv)
        self.assertIn("ferblo@192.0.2.10", argv)
        self.assertNotIn("-tt", argv)  # preflight não pede PTY

    def test_regressao_comandos_sem_tty_usam_traco_t(self) -> None:
        """Bug real (AIX): com TTY local, `RequestTTY auto` aloca PTY remoto e
        ``python3 -`` vira REPL interativo em vez de executar o stdin. Todo
        comando one-shot (preflight, collect_host_metrics) deve levar ``-T``;
        só a sessão de replay leva ``-tt``."""
        vistos: list[list[str]] = []

        def runner(argv, input_text, timeout):
            vistos.append(argv)
            return _FakeCompleted(0, '{"host_metrics_query": "done", "rows": 0}\n', "")

        popen_argvs: list[list[str]] = []

        def popen(argv, **kwargs):
            popen_argvs.append(argv)
            return _FakeSSHProcess(argv, **kwargs)

        adapter = SSHReplayAdapter(
            _modelo(), _contrato(), ssh_runner=runner, popen_factory=popen)
        adapter.preflight()
        adapter.collect_host_metrics(100, 200)
        sessao = adapter.start_session("vu-1")
        adapter.stop_session(sessao)

        self.assertEqual(2, len(vistos))  # preflight + host_metrics
        for argv in vistos:
            self.assertIn("-T", argv, f"comando one-shot sem -T: {argv}")
            self.assertNotIn("-tt", argv)
        self.assertEqual(1, len(popen_argvs))
        sessao_argv = popen_argvs[0]
        self.assertIn("-tt", sessao_argv)
        self.assertNotIn("-T", sessao_argv)


class TestSSHReplayAdapterJornada(unittest.TestCase):
    """Execução de jornada com latência real medida via pipes fake."""

    def setUp(self) -> None:
        self.adapter = SSHReplayAdapter(
            _modelo(), _contrato(),
            ssh_runner=_runner_ok, popen_factory=_popen_ok,
            stable_ms=80, step_timeout_s=5.0)
        self.addCleanup(self.adapter.cleanup)
        self.adapter.set_iteration_context(2, 5)
        self.sessao = self.adapter.start_session("vu-1")

    def tearDown(self) -> None:
        self.adapter.stop_session(self.sessao)

    def test_latencia_real_compativel_com_atraso(self) -> None:
        atrasos = [40.0, 0.0, 40.0]
        amostras = self.adapter.execute_journey(
            self.sessao, _jornada(atrasos), phase="MEASUREMENT")
        self.assertEqual(3, len(amostras))
        for amostra, atraso in zip(amostras, atrasos):
            self.assertLess(amostra.started_ns, amostra.finished_ns)
            # latência >= atraso injetado (folga p/ scheduling) e sem explosão
            self.assertGreaterEqual(amostra.latency_ms, atraso - 5.0)
            self.assertLessEqual(amostra.latency_ms, atraso + 1000.0)
            self.assertTrue(amostra.success)
            self.assertFalse(amostra.timeout)
            self.assertEqual("MEASUREMENT", amostra.phase)
            # contexto estampado pelo set_iteration_context
            self.assertEqual(2, amostra.iteration)
            self.assertEqual(5, amostra.concurrency)
            self.assertEqual("linux-x86", amostra.environment_id)
            self.assertEqual("exp-ssh-unit", amostra.experiment_id)

    def test_envio_dos_bytes_exatos_da_captura(self) -> None:
        """key_b64 tem precedência: os bytes enviados são os da captura."""
        amostras = self.adapter.execute_journey(
            self.sessao,
            {"journey_id": "j-b64",
             "steps": [{"step_id": "s1",
                        "key_b64": base64.b64encode(b"OP 5\n").decode(),
                        "key_text": "TEXTO-QUE-NAO-DEVE-SER-ENVIADO"}]},
            phase="WARMUP")
        self.assertTrue(amostras[0].success)
        metricas = self.adapter.collect_application_metrics()
        self.assertEqual(1, len(metricas["warmup_samples"]))

    def test_fase_invalida_levanta_value_error(self) -> None:
        with self.assertRaises(ValueError):
            self.adapter.execute_journey(
                self.sessao, _jornada([0.0]), phase="INVALIDA")

    def test_divergencia_funcional_quando_assinatura_diverge(self) -> None:
        jornada = _jornada([0.0])
        jornada["steps"][0]["expected_screen_sig"] = "sig-inexistente-000"
        amostras = self.adapter.execute_journey(
            self.sessao, jornada, phase="MEASUREMENT")
        # a engine canônica existe no repo: a assinatura calculada difere da
        # esperada → divergência funcional real (não fabricada)
        self.assertTrue(amostras[0].functional_divergence)

    def test_sem_assinatura_esperada_nao_marca_divergencia(self) -> None:
        amostras = self.adapter.execute_journey(
            self.sessao, _jornada([0.0]), phase="MEASUREMENT")
        self.assertFalse(amostras[0].functional_divergence)

    def test_coletas_por_fase(self) -> None:
        self.adapter.execute_journey(self.sessao, _jornada([0.0]), phase="WARMUP")
        self.adapter.execute_journey(self.sessao, _jornada([0.0]), phase="MEASUREMENT")
        metricas = self.adapter.collect_application_metrics()
        self.assertTrue(metricas["ok"])
        self.assertEqual(1, len(metricas["warmup_samples"]))
        self.assertEqual(1, len(metricas["measurement_samples"]))
        self.assertEqual(0, len(metricas["cooldown_samples"]))

    def test_database_metrics_indisponivel_sem_fingir(self) -> None:
        res = self.adapter.collect_database_metrics()
        self.assertFalse(res["available"])
        self.assertEqual("collector_not_supported", res["reason"])


class TestSSHReplayAdapterTimeout(unittest.TestCase):
    """Saída que nunca estabiliza → timeout real (não sucesso)."""

    def test_fluxo_continuo_marca_timeout(self) -> None:
        adapter = SSHReplayAdapter(
            _modelo(), _contrato(),
            ssh_runner=_runner_ok, popen_factory=_popen_flood,
            stable_ms=20, step_timeout_s=0.3)
        self.addCleanup(adapter.cleanup)
        sessao = adapter.start_session("vu-1")
        amostras = adapter.execute_journey(sessao, _jornada([0.0]),
                                           phase="MEASUREMENT")
        adapter.stop_session(sessao)
        self.assertEqual(1, len(amostras))
        self.assertTrue(amostras[0].timeout)
        self.assertFalse(amostras[0].success)


class TestSSHReplayAdapterHostMetrics(unittest.TestCase):
    """Coleta de host_metrics da replay.db remota (ssh mockado)."""

    def setUp(self) -> None:
        # o backoff real é 1s/2s — nos testes, zero
        patcher = mock.patch(
            "dakota_gateway.benchmark.adapters._HOST_METRICS_BACKOFF_S",
            (0.0, 0.0))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_host_metrics_parseadas_e_anotadas(self) -> None:
        linhas = [
            {"id": 1, "ts_ms": 1000, "cpu_pct": 42.5, "load1": 1.2,
             "mem_total_mb": 8192.0, "mem_used_mb": 4096.0, "swap_pct": 0.0},
            {"id": 2, "ts_ms": 2000, "cpu_pct": None, "load1": 1.4,
             "mem_total_mb": 8192.0, "mem_used_mb": 4100.0, "swap_pct": None},
        ]

        def runner(argv, input_text, timeout):
            # o script python vai via stdin do ssh
            self.assertIn("SELECT * FROM host_metrics", input_text)
            self.assertIn("100", input_text)  # janela from_ms
            return _FakeCompleted(
                0, "\n".join(json.dumps(l) for l in linhas), "")

        adapter = SSHReplayAdapter(_modelo(), _contrato(), ssh_runner=runner)
        amostras = adapter.collect_host_metrics(100, 200)
        self.assertEqual(2, len(amostras))
        self.assertEqual(42.5, amostras[0]["cpu_pct"])
        # anotação de ambiente (§13)
        for amostra in amostras:
            self.assertEqual("192.0.2.10", amostra["host_id"])
            self.assertEqual("Linux", amostra["platform"])
            self.assertEqual("x86_64", amostra["architecture"])
        # campo indisponível permanece None — nunca zero fingido
        self.assertIsNone(amostras[1]["cpu_pct"])
        self.assertTrue(adapter.host_metrics_status["available"])

    def test_host_metrics_indisponiveis_reportam_reason(self) -> None:
        adapter = SSHReplayAdapter(_modelo(), _contrato(), ssh_runner=_runner_falho)
        amostras = adapter.collect_host_metrics(100, 200)
        self.assertEqual([], amostras)
        self.assertFalse(adapter.host_metrics_status["available"])
        self.assertIn("Connection refused", adapter.host_metrics_status["reason"])


class TestSSHReplayAdapterCanalMetricas(unittest.TestCase):
    """Canal dedicado da coleta de host_metrics (§13).

    Caso real (MIG24): o login do endpoint (ferblo) está sob ForceCommand de
    captura no sshd e não alcança a replay.db — a coleta usa
    ``metrics_ssh_user``/``metrics_remote_cmd`` do modelo de ambiente, sem
    afetar a sessão de replay (que continua no usuário do endpoint, com -tt).
    """

    def _modelo_canal(self) -> EnvironmentModel:
        return EnvironmentModel(
            environment_id="aix-power",
            platform="AIX",
            architecture="POWER",
            host="192.0.2.20",
            port=22,
            user_secret_ref="ssh-key:ferblo@192.0.2.20",
            application_endpoint="ssh://ferblo@192.0.2.20:22",
            cpu=CpuModel(virtual_processors=2, physical_processors=2),
            memory_mb=4096,
            replay2_db_path="/opt/dakota/replay2/gateway/state/replay.db",
            metrics_ssh_user="root",
            metrics_remote_cmd="su results -c 'python3 -'",
        )

    def test_coleta_usa_canal_dedicado_e_sessao_mantem_endpoint(self) -> None:
        vistos: list[list[str]] = []

        def runner(argv, input_text, timeout):
            vistos.append(argv)
            return _FakeCompleted(0, '{"host_metrics_query": "done", "rows": 0}\n', "")

        popen_argvs: list[list[str]] = []

        def popen(argv, **kwargs):
            popen_argvs.append(argv)
            return _FakeSSHProcess(argv, **kwargs)

        adapter = SSHReplayAdapter(
            self._modelo_canal(), _contrato(),
            ssh_runner=runner, popen_factory=popen)
        adapter.collect_host_metrics(100, 200)
        sessao = adapter.start_session("vu-1")
        adapter.stop_session(sessao)

        # coleta: usuário/comando dedicados, ainda sem PTY (-T)
        argv_metrics = vistos[0]
        self.assertIn("root@192.0.2.20", argv_metrics)
        self.assertNotIn("ferblo@192.0.2.20", argv_metrics)
        self.assertEqual("su results -c 'python3 -'", argv_metrics[-1])
        self.assertIn("-T", argv_metrics)
        self.assertNotIn("-tt", argv_metrics)

        # sessão de replay: usuário do endpoint, com PTY — inalterada
        self.assertEqual(1, len(popen_argvs))
        sessao_argv = popen_argvs[0]
        self.assertIn("ferblo@192.0.2.20", sessao_argv)
        self.assertIn("-tt", sessao_argv)

    def test_sem_canal_dedicado_cai_no_comportamento_padrao(self) -> None:
        """Sem metrics_ssh_user/metrics_remote_cmd: usuário do endpoint e
        ``remote_python_cmd`` (retrocompatível com os modelos antigos)."""
        vistos: list[list[str]] = []

        def runner(argv, input_text, timeout):
            vistos.append(argv)
            return _FakeCompleted(0, '{"host_metrics_query": "done", "rows": 0}\n', "")

        adapter = SSHReplayAdapter(_modelo(), _contrato(), ssh_runner=runner)
        adapter.collect_host_metrics(100, 200)
        argv_metrics = vistos[0]
        self.assertIn("ferblo@192.0.2.10", argv_metrics)
        self.assertEqual("python3 -", argv_metrics[-1])


class TestSSHReplayAdapterHostMetricsRetry(unittest.TestCase):
    """Retry da coleta contra falhas de TRANSPORTE (sshd AIX + VPN com perda).

    Caso real (smoke v3): uma única tentativa de coleta voltava com rc!=0 ou
    stdout vazio de forma intermitente e a run inteira ficava sem host
    metrics. A query VÁLIDA — inclusive janela com 0 amostras, confirmada
    pela linha sentinela — nunca é re-tentada.
    """

    def setUp(self) -> None:
        # o backoff real é 1s/2s — nos testes, zero
        patcher = mock.patch(
            "dakota_gateway.benchmark.adapters._HOST_METRICS_BACKOFF_S",
            (0.0, 0.0))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_falhas_de_transporte_depois_sucesso(self) -> None:
        chamadas: list[list[str]] = []

        def runner(argv, input_text, timeout):
            chamadas.append(argv)
            if len(chamadas) < 3:
                return _FakeCompleted(255, "", "ssh: Connection reset by peer")
            return _FakeCompleted(
                0, json.dumps({"id": 1, "ts_ms": 1000, "cpu_pct": 42.5}) + "\n"
                + '{"host_metrics_query": "done", "rows": 1}\n', "")

        adapter = SSHReplayAdapter(_modelo(), _contrato(), ssh_runner=runner)
        amostras = adapter.collect_host_metrics(100, 200)
        self.assertEqual(3, len(chamadas))
        self.assertEqual(1, len(amostras))
        self.assertEqual(42.5, amostras[0]["cpu_pct"])
        self.assertEqual("192.0.2.10", amostras[0]["host_id"])
        status = adapter.host_metrics_status
        self.assertTrue(status["available"])
        self.assertEqual(3, status["attempts"])

    def test_stdout_vazio_com_rc_zero_e_falha_de_transporte(self) -> None:
        """rc=0 sem nenhuma linha (sem sentinela) = saída perdida no
        transporte → tenta de novo; sentinela rows=0 no 2º attempt confirma
        janela vazia VÁLIDA e encerra sem nova tentativa."""
        chamadas: list[list[str]] = []

        def runner(argv, input_text, timeout):
            chamadas.append(argv)
            if len(chamadas) == 1:
                return _FakeCompleted(0, "", "")  # saída engolida pela VPN
            return _FakeCompleted(
                0, '{"host_metrics_query": "done", "rows": 0}\n', "")

        adapter = SSHReplayAdapter(_modelo(), _contrato(), ssh_runner=runner)
        amostras = adapter.collect_host_metrics(100, 200)
        self.assertEqual([], amostras)
        self.assertEqual(2, len(chamadas))
        status = adapter.host_metrics_status
        self.assertTrue(status["available"])
        self.assertEqual(2, status["attempts"])

    def test_falha_permanente_esgota_tentativas_com_reason_da_ultima(self) -> None:
        chamadas: list[list[str]] = []

        def runner(argv, input_text, timeout):
            chamadas.append(argv)
            return _FakeCompleted(255, "", f"erro-transporte-{len(chamadas)}")

        adapter = SSHReplayAdapter(_modelo(), _contrato(), ssh_runner=runner)
        amostras = adapter.collect_host_metrics(100, 200)
        self.assertEqual([], amostras)
        self.assertEqual(3, len(chamadas))  # exatamente 3 tentativas
        status = adapter.host_metrics_status
        self.assertFalse(status["available"])
        self.assertIn("erro-transporte-3", status["reason"])  # última tentativa
        self.assertEqual(3, status["attempts"])

    def test_janela_vazia_valida_nao_dispara_retry(self) -> None:
        """Query ok com 0 amostras na janela (sentinela rows=0) é resposta
        VÁLIDA: available:true, 0 amostras, exatamente 1 chamada."""
        chamadas: list[list[str]] = []

        def runner(argv, input_text, timeout):
            chamadas.append(argv)
            return _FakeCompleted(
                0, '{"host_metrics_query": "done", "rows": 0}\n', "")

        adapter = SSHReplayAdapter(_modelo(), _contrato(), ssh_runner=runner)
        amostras = adapter.collect_host_metrics(100, 200)
        self.assertEqual([], amostras)
        self.assertEqual(1, len(chamadas))
        status = adapter.host_metrics_status
        self.assertTrue(status["available"])
        self.assertEqual(1, status["attempts"])

    def test_saida_truncada_pela_sentinela_dispara_retry(self) -> None:
        """Sentinela diz rows=5 mas só chegaram 2 amostras: transporte
        truncou a saída → nova tentativa (nunca aceita dado parcial)."""
        chamadas: list[list[str]] = []

        def runner(argv, input_text, timeout):
            chamadas.append(argv)
            if len(chamadas) == 1:
                parcial = "".join(
                    json.dumps({"id": i, "ts_ms": i * 1000}) + "\n"
                    for i in range(2))
                return _FakeCompleted(
                    0, parcial + '{"host_metrics_query": "done", "rows": 5}\n', "")
            completo = "".join(
                json.dumps({"id": i, "ts_ms": i * 1000}) + "\n"
                for i in range(5))
            return _FakeCompleted(
                0, completo + '{"host_metrics_query": "done", "rows": 5}\n', "")

        adapter = SSHReplayAdapter(_modelo(), _contrato(), ssh_runner=runner)
        amostras = adapter.collect_host_metrics(100, 200)
        self.assertEqual(2, len(chamadas))
        self.assertEqual(5, len(amostras))
        self.assertEqual(2, adapter.host_metrics_status["attempts"])


class TestSSHReplayAdapterClockSkew(unittest.TestCase):
    """Compensação de clock skew na coleta de host_metrics (§13).

    Caso real (MIG24): o relógio do AIX estava ~171 s ATRASADO em relação ao
    orquestrador; a janela nominal da run capturava apenas amostras gravadas
    ANTES da run (o sampler remoto grava ``ts_ms`` com o relógio do host).
    O script remoto mede ``offset = remote_now - local_now`` e desloca a
    janela; o offset volta na sentinela e é registrado no status da coleta.
    """

    def test_sentinela_com_offset_e_registrada_no_status(self) -> None:
        def runner(argv, input_text, timeout):
            return _FakeCompleted(
                0, '{"host_metrics_query": "done", "rows": 0,'
                   ' "clock_offset_ms": -171234}\n', "")

        adapter = SSHReplayAdapter(_modelo(), _contrato(), ssh_runner=runner)
        amostras = adapter.collect_host_metrics(100, 200)
        self.assertEqual([], amostras)
        status = adapter.host_metrics_status
        self.assertTrue(status["available"])
        self.assertEqual(-171234, status["clock_offset_ms"])

    def test_script_desloca_janela_pelo_offset_medido(self) -> None:
        """Executa o script REAL gerado pelo adaptador (python3 local contra
        sqlite temporário) com ``local_now`` adulterado para simular um host
        com relógio 150 s ADIANTADO: a janela efetiva deve deslocar +150 s e
        selecionar as amostras que a janela nominal perderia."""
        import re
        import sqlite3
        import subprocess
        import tempfile
        from dataclasses import replace

        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "replay.db"
            con = sqlite3.connect(db_path)
            con.execute("CREATE TABLE host_metrics (ts_ms INTEGER, cpu_pct REAL)")
            con.executemany(
                "INSERT INTO host_metrics VALUES (?, ?)",
                [(1_000, 10.0), (2_000, 20.0), (165_000, 30.0),
                 (168_000, 40.0)])
            con.commit()
            con.close()

            capturado: dict[str, str] = {}
            modelo = replace(_modelo(), replay2_db_path=str(db_path))

            def runner(argv, input_text, timeout):
                capturado["script"] = input_text
                # simula host 150 s ADIANTADO: remote_now = local + 150 s
                local_now_falso = int(time.time() * 1000) - 150_000
                script = re.sub(
                    r"(offset = int\(time\.time\(\) \* 1000\) - )(\d+)",
                    lambda m: m.group(1) + str(local_now_falso),
                    input_text)
                res = subprocess.run(
                    ["python3", "-"], input=script, capture_output=True,
                    text=True, timeout=30)
                return _FakeCompleted(res.returncode, res.stdout, res.stderr)

            adapter = SSHReplayAdapter(modelo, _contrato(), ssh_runner=runner)
            # janela nominal [10000, 20000]; com offset +150 s vira [160000, 170000]
            amostras = adapter.collect_host_metrics(10_000, 20_000)

        self.assertIn("replay.db", capturado["script"])
        # sem a compensação a janela nominal estaria vazia; com ela, 165000/168000
        self.assertEqual([165_000, 168_000],
                         [a["ts_ms"] for a in amostras])
        status = adapter.host_metrics_status
        self.assertTrue(status["available"])
        # offset medido ≈ +150 s (tolerância de execução do teste)
        self.assertAlmostEqual(150_000, status["clock_offset_ms"], delta=5_000)

    def test_db_path_do_modelo_e_usado_no_script(self) -> None:
        from dataclasses import replace

        capturado: dict[str, str] = {}

        def runner(argv, input_text, timeout):
            capturado["script"] = input_text
            return _FakeCompleted(
                0, '{"host_metrics_query": "done", "rows": 0}\n', "")

        modelo = replace(_modelo(), replay2_db_path="/tmp/modelo-especifico.db")
        adapter = SSHReplayAdapter(modelo, _contrato(), ssh_runner=runner)
        adapter.collect_host_metrics(100, 200)
        self.assertIn("/tmp/modelo-especifico.db", capturado["script"])


if __name__ == "__main__":
    unittest.main()

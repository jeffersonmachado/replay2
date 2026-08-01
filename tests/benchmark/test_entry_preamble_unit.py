"""Testes do preamble de entrada (anchor-wait) e da curadoria de jornada.

Contexto real (cap13): a jornada derivada da captura começa DENTRO do ERP,
mas a sessão SSH nova abre num menu de login (AIX) ou num shell (Linux).
Sem um preamble determinístico o replay digita os passos do corpo no lugar
errado e NUNCA alcança a aplicação — foi o que produziu as divergências
funcionais do experimento cap13-aix-linux-oficial (o replay ficou preso no
"Menu de opcoes do usuario ferblo" e nenhum dado do ERP foi tocado).

O ``entry_preamble`` do EnvironmentModel descreve, POR AMBIENTE, os passos
de entrada: cada passo pode enviar bytes (``send``) e/ou aguardar um texto-
âncora (``wait_text``) com timeout. O preamble NÃO gera amostras — ele só
leva a sessão até o estado inicial da jornada; se um âncora não aparece, a
sessão falha com razão clara (bloqueio honesto, jamais PASS).

Estes testes são unitários: o ``ssh -tt`` é substituído por um fake com
pipes reais e uma thread que simula a máquina de estados menu→shell→ERP.
"""
from __future__ import annotations

import base64
import json
import os
import select
import sys
import tempfile
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
from dakota_gateway.cli import _bench_load_journeys  # noqa: E402


PREAMBLE_AIX = (
    {"wait_text": "Digite a sua opcao:", "timeout_s": 5},
    {"send": "0\r", "wait_text": "ferblo > ", "timeout_s": 5},
    {"send": "estl\r", "wait_text": "est > ", "timeout_s": 5},
    {"send": "k\r", "wait_text": "DAKOTA S/A", "timeout_s": 5},
)


def _contrato() -> "object":
    return create_contract(
        experiment_id="exp-preamble-unit",
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
        environments=("aix-power",),
    )


def _modelo(preamble=PREAMBLE_AIX) -> EnvironmentModel:
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
        entry_preamble=preamble,
    )


class _FakeERPMachine:
    """PTY fake: máquina de estados menu→shell→ERP do AIX (protocolo real).

    Roteiro: ao conectar emite o banner+menu; a cada token esperado recebido,
    responde com o próximo estado. Se ``travar_no_menu=True``, ignora o "0"
    (o âncora do shell nunca aparece) — exercita o timeout do preamble.
    """

    def __init__(self, argv, stdin=None, stdout=None, stderr=None,
                 travar_no_menu: bool = False):
        self.argv = argv
        self._travar = travar_no_menu
        self._stdin_r, stdin_w = os.pipe()
        stdout_r, self._stdout_w = os.pipe()
        self.stdin = os.fdopen(stdin_w, "wb", buffering=0)
        self.stdout = os.fdopen(stdout_r, "rb", buffering=0)
        self._stop = threading.Event()
        self.returncode: int | None = None
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _emit(self, dados: bytes) -> None:
        try:
            os.write(self._stdout_w, dados)
        except OSError:
            pass

    def _serve(self) -> None:
        roteiro = [
            (b"0\r", b"\r\n(ferblo)MIG24:/dakota1/u/ferblo > "),
            (b"estl\r", b"(ferblo)MIG24:/dakota11/est > "),
            (b"k\r", b"\x1b[2J DAKOTA S/A        ESTOQUE\r\n"
                    b" REDE DE LOJAS | 0 MENU PRINCIPAL\r\n"),
            (b"OP1\n", b"RESP1 estavel\n"),
            (b"OP2\n", b"RESP2 estavel\n"),
        ]
        self._emit(b"Acesso autorizado via VPN.\r\n"
                   b"Menu de opcoes do usuario ferblo\r\n"
                   b"  1 - (REDE LOJAS) Sistema das Lojas\r\n"
                   b"  0 - Fim\r\n"
                   b"Digite a sua opcao: ")
        buf = b""
        etapa = 0
        while not self._stop.is_set():
            prontos, _, _ = select.select([self._stdin_r], [], [], 0.05)
            if not prontos:
                continue
            try:
                chunk = os.read(self._stdin_r, 65536)
            except OSError:
                break
            if not chunk:
                break  # stdin fechado pelo adaptador
            if self._travar:
                continue  # menu "mudo": nenhum âncora adiante
            buf += chunk
            while etapa < len(roteiro) and roteiro[etapa][0] in buf:
                esperado, resposta = roteiro[etapa]
                buf = buf.split(esperado, 1)[1]
                self._emit(esperado)  # eco do PTY
                time.sleep(0.02)
                self._emit(resposta)
                etapa += 1

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


def _popen_erp(argv, **kwargs):
    return _FakeERPMachine(argv, **kwargs)


def _popen_travado(argv, **kwargs):
    return _FakeERPMachine(argv, travar_no_menu=True, **kwargs)


class _FakeERPMachineUltimaTelaEmDuasPartes(_FakeERPMachine):
    """Variante: a resposta ao "k" (tela inicial do ERP) chega EM DUAS PARTES.

    Reproduz o caso real do cap13: o âncora "DAKOTA S/A" aparece no meio do
    desenho do menu; o restante do desenho só chega ~50ms depois. Sem o dreno
    final do preamble, essa segunda parte vazaria para o primeiro passo do
    corpo da jornada.
    """

    def _serve(self) -> None:
        roteiro = [
            (b"0\r", (b"\r\n(ferblo)MIG24:/dakota1/u/ferblo > ",)),
            (b"estl\r", (b"(ferblo)MIG24:/dakota11/est > ",)),
            (b"k\r", (b"\x1b[2J DAKOTA S/A ESTOQUE\r\n",
                      b"DESENHO-ATRASADO-DO-MENU\r\n")),
            (b"OP1\n", (b"RESP1 estavel\n",)),
            (b"OP2\n", (b"RESP2 estavel\n",)),
        ]
        self._emit(b"Acesso autorizado via VPN.\r\n"
                   b"Menu de opcoes do usuario ferblo\r\n"
                   b"Digite a sua opcao: ")
        buf = b""
        etapa = 0
        while not self._stop.is_set():
            prontos, _, _ = select.select([self._stdin_r], [], [], 0.05)
            if not prontos:
                continue
            try:
                chunk = os.read(self._stdin_r, 65536)
            except OSError:
                break
            if not chunk:
                break
            buf += chunk
            while etapa < len(roteiro) and roteiro[etapa][0] in buf:
                esperado, partes = roteiro[etapa]
                buf = buf.split(esperado, 1)[1]
                self._emit(esperado)  # eco do PTY
                for parte in partes:
                    time.sleep(0.05)
                    self._emit(parte)
                etapa += 1


def _runner_ok(argv, input_text, timeout):
    class _R:
        returncode = 0
        stdout = ""
        stderr = ""
    return _R()


def _jornada_corpo() -> dict:
    return {
        "journey_id": "j-corpo",
        "steps": [
            {"step_id": "ev-89",
             "key_b64": base64.b64encode(b"OP1\n").decode()},
            {"step_id": "ev-96",
             "key_b64": base64.b64encode(b"OP2\n").decode()},
        ],
    }


class TestEntryPreamble(unittest.TestCase):
    """Preamble leva a sessão ao estado inicial da jornada (sem amostras)."""

    def _adapter(self, popen_factory=_popen_erp, preamble=PREAMBLE_AIX):
        return SSHReplayAdapter(
            _modelo(preamble), _contrato(),
            ssh_runner=_runner_ok, popen_factory=popen_factory)

    def test_preamble_alcanca_corpo_e_corpo_gera_amostras(self) -> None:
        adapter = self._adapter()
        handle = adapter.start_session("vu-1")
        amostras = adapter.execute_journey(handle, _jornada_corpo(), phase="MEASUREMENT")
        adapter.stop_session(handle)
        self.assertEqual(2, len(amostras))
        self.assertEqual(["ev-89", "ev-96"], [a.step_id for a in amostras])
        self.assertTrue(all(a.success for a in amostras))

    def test_preamble_nao_gera_amostras(self) -> None:
        adapter = self._adapter()
        handle = adapter.start_session("vu-1")
        adapter.stop_session(handle)
        self.assertEqual([], adapter._samples)

    def test_saida_do_preamble_vai_para_o_tail_forense(self) -> None:
        adapter = self._adapter()
        handle = adapter.start_session("vu-1")
        adapter.stop_session(handle)
        tail = adapter.session_tails().get("vu-1", b"")
        self.assertIn(b"Menu de opcoes", tail)
        self.assertIn(b"DAKOTA S/A", tail)

    def test_anchor_ausente_falha_sessao_com_razao_clara(self) -> None:
        adapter = self._adapter(popen_factory=_popen_travado)
        with self.assertRaises(Exception) as ctx:
            adapter.start_session("vu-1")
        self.assertIn("ferblo > ", str(ctx.exception))

    def test_escapes_do_send_decodificados(self) -> None:
        """"0\\r" deve chegar ao PTY como bytes 0x30 0x0D (não literais)."""
        recebidos: list[bytes] = []
        original_init = _FakeERPMachine.__init__

        def espiando(self, argv, **kwargs):
            original_init(self, argv, **kwargs)
            original_stdin_read = self._stdin_r
            # espiona o que chega no stdin do "remoto"
            import os as _os
            r, w = _os.pipe()
            self._stdin_r = r
            def ponte():
                while True:
                    try:
                        dados = _os.read(original_stdin_read, 65536)
                    except OSError:
                        return
                    if not dados:
                        return
                    recebidos.append(dados)
                    _os.write(w, dados)
            threading.Thread(target=ponte, daemon=True).start()

        with mock.patch.object(_FakeERPMachine, "__init__", espiando):
            adapter = self._adapter()
            handle = adapter.start_session("vu-1")
            adapter.stop_session(handle)
        bruto = b"".join(recebidos)
        self.assertIn(b"0\r", bruto)
        self.assertNotIn(b"0\\r", bruto)  # escape não pode ir literal

    def test_sem_preamble_comportamento_inalterado(self) -> None:
        """Ambiente sem entry_preamble: corpo executa direto (retrocompatível)."""
        adapter = self._adapter(preamble=())
        handle = adapter.start_session("vu-1")
        amostras = adapter.execute_journey(handle, _jornada_corpo(), phase="MEASUREMENT")
        adapter.stop_session(handle)
        self.assertEqual(2, len(amostras))

    def test_dreno_final_do_preamble_nao_polui_primeiro_passo(self) -> None:
        """Se o âncora aparece NO MEIO do desenho da tela inicial, o restante
        do desenho é drenado no preamble — não pode vazar para a observação
        do primeiro passo do corpo (causa real de divergência no cap13)."""
        maquina = _FakeERPMachineUltimaTelaEmDuasPartes
        adapter = SSHReplayAdapter(
            _modelo(PREAMBLE_AIX), _contrato(),
            ssh_runner=_runner_ok, popen_factory=maquina)
        drenados: list[bytes] = []
        original = adapter._drain_until_stable

        def espião(proc, tail=None, timeout_s=None):
            saida, to, eof = original(proc, tail, timeout_s)
            drenados.append(saida)
            return saida, to, eof
        # espião instalado ANTES do start_session para capturar o dreno
        # final do preamble (drenados[0])
        adapter._drain_until_stable = espião
        handle = adapter.start_session("vu-1")
        adapter.execute_journey(handle, _jornada_corpo(), phase="MEASUREMENT")
        adapter.stop_session(handle)
        corpo = b"".join(drenados[1:])  # drenados[0] = dreno final do preamble
        self.assertNotIn(b"DESENHO-ATRASADO-DO-MENU", corpo)
        self.assertIn(b"DESENHO-ATRASADO-DO-MENU", drenados[0])

    def test_stable_ms_do_ambiente_e_respeitado(self) -> None:
        """stable_ms no EnvironmentModel sobrepõe o default (150ms) — resposta
        do ERP com pausa interna de ~190ms não pode ser cortada no meio."""
        modelo = _modelo(())
        object.__setattr__(modelo, "stable_ms", 500)
        adapter = SSHReplayAdapter(
            modelo, _contrato(),
            ssh_runner=_runner_ok, popen_factory=_popen_erp)
        self.assertEqual(500, adapter.stable_ms)
        # sem o campo: default 150
        adapter2 = SSHReplayAdapter(
            _modelo(()), _contrato(),
            ssh_runner=_runner_ok, popen_factory=_popen_erp)
        self.assertEqual(150, adapter2.stable_ms)


class TestEnvironmentModelPreamble(unittest.TestCase):
    """entry_preamble no modelo: roundtrip e tolerância a ausência."""

    def test_roundtrip_to_dict_from_dict(self) -> None:
        modelo = _modelo()
        d = modelo.to_dict()
        self.assertIn("entry_preamble", d)
        restaurado = EnvironmentModel.from_dict(d)
        self.assertEqual(list(PREAMBLE_AIX), list(restaurado.entry_preamble))

    def test_ausente_vira_vazio(self) -> None:
        restaurado = EnvironmentModel.from_dict({
            "environment_id": "e1", "platform": "Linux",
            "architecture": "x86_64", "host": "192.0.2.30",
        })
        self.assertEqual((), tuple(restaurado.entry_preamble))


class TestCuradoriaJornada(unittest.TestCase):
    """_bench_load_journeys com faixa de seq (cabeça/ruído fora do corpo)."""

    def _jsonl(self, tmp: str) -> str:
        eventos = []
        for seq in (3, 27, 89, 96, 103):
            eventos.append({
                "type": "deterministic_input", "seq_global": seq,
                "key_b64": base64.b64encode(f"K{seq}".encode()).decode(),
                "text_sig": f"sha256:{'%064x' % seq}",
            })
        # evento de outro tipo no meio — deve ser ignorado
        linhas = [json.dumps(eventos[0]),
                  json.dumps({"type": "bytes", "seq_global": 4}),
                  *[json.dumps(e) for e in eventos[1:]]]
        caminho = os.path.join(tmp, "jornada.jsonl")
        with open(caminho, "w", encoding="utf-8") as fh:
            fh.write("\n".join(linhas) + "\n")
        return caminho

    def test_sem_faixa_comportamento_inalterado(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            jornadas = _bench_load_journeys(self._jsonl(tmp))
        steps = jornadas[0]["steps"]
        self.assertEqual(["ev-3", "ev-27", "ev-89", "ev-96", "ev-103"],
                         [s["step_id"] for s in steps])

    def test_from_seq_corta_cabeca_ruidosa(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            jornadas = _bench_load_journeys(self._jsonl(tmp), from_seq=89)
        steps = jornadas[0]["steps"]
        self.assertEqual(["ev-89", "ev-96", "ev-103"], [s["step_id"] for s in steps])

    def test_to_seq_corta_cauda(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            jornadas = _bench_load_journeys(self._jsonl(tmp), from_seq=89, to_seq=96)
        steps = jornadas[0]["steps"]
        self.assertEqual(["ev-89", "ev-96"], [s["step_id"] for s in steps])

    def test_sig_esperada_do_proximo_evento_dentro_da_faixa(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            jornadas = _bench_load_journeys(self._jsonl(tmp), from_seq=89)
        steps = jornadas[0]["steps"]
        # sig esperada do passo i = sig do evento i+1; último fica sem
        self.assertEqual("sha256:" + "%064x" % 96, steps[0]["expected_screen_sig"])
        self.assertEqual("sha256:" + "%064x" % 103, steps[1]["expected_screen_sig"])
        self.assertNotIn("expected_screen_sig", steps[2])

    def test_capture_terminal_extraido_do_jsonl(self) -> None:
        """A sig da captura inclui rows/cols/term — a jornada deve carregá-los
        para o adaptador inicializar a engine com a MESMA geometria/term."""
        with tempfile.TemporaryDirectory() as tmp:
            caminho = os.path.join(tmp, "jornada.jsonl")
            with open(caminho, "w", encoding="utf-8") as fh:
                fh.write(json.dumps({
                    "type": "session_start", "seq_global": 1,
                    "rows": 58, "cols": 80, "term": "dk100"}) + "\n")
                fh.write(json.dumps({
                    "type": "deterministic_input", "seq_global": 89,
                    "key_b64": base64.b64encode(b"3").decode(),
                    "text_sig": "sha256:" + "0" * 64}) + "\n")
            jornadas = _bench_load_journeys(caminho)
        term = jornadas[0].get("capture_terminal")
        self.assertIsNotNone(term)
        self.assertEqual(58, term["rows"])
        self.assertEqual(80, term["cols"])
        self.assertEqual("dk100", term["term"])

    def test_engine_usa_geometria_da_captura(self) -> None:
        """Sig observada pela engine com rows/term da captura casa com a sig
        pré-computada na MESMA geometria/term (regressão: antes a engine
        usava sempre a geometria do contrato e a sig nunca batia)."""
        from dakota_terminal.engine import TerminalEngine
        from dakota_terminal.signatures import text_sig
        saida = (b"\x1b[2J\x1b[1;1H DAKOTA S/A        ESTOQUE\r\n"
                 b" REDE DE LOJAS | 0 MENU PRINCIPAL\r\n")
        engine_cap = TerminalEngine(rows=58, cols=80, term="dk100")
        engine_cap.feed_bytes(saida, direction="out")
        esperada = text_sig(engine_cap.snapshot())

        adapter = SSHReplayAdapter(
            _modelo(()), _contrato(),
            ssh_runner=_runner_ok, popen_factory=_popen_erp)
        handle = adapter.start_session("vu-geo")
        session = adapter._sessions[handle]
        session["capture_terminal"] = {"rows": 58, "cols": 80, "term": "dk100"}
        observada = adapter._compute_text_sig(session, saida)
        adapter.stop_session(handle)
        self.assertEqual(esperada, observada)

    def test_sig_esperada_computada_dos_bytes_nao_do_campo(self) -> None:
        """A sig esperada deve ser COMPUTADA do byte stream (mesmo pipeline do
        replay), NÃO copiada do campo text_sig do evento — que vem de outro
        pipeline e pode estar defasado/incompatível (caso real cap13)."""
        from dakota_terminal.engine import TerminalEngine
        from dakota_terminal.signatures import text_sig

        menu = b"\x1b[2J\x1b[1;1HMENU PRINCIPAL\r\n 1 - Abre tela A\r\n"
        resp = b"\x1b[3;1HTELA A ABERTA\r\n"
        # engine de referência = EXATAMENTE o que o replay vê: engine fresca
        # no primeiro passo do corpo, alimentada só com eco+resposta dali em
        # diante (o desenho do menu aconteceu no preamble — fora da engine).
        engine_ref = TerminalEngine(rows=24, cols=80, term="xterm")
        engine_ref.feed_bytes(b"1" + resp, direction="out")
        sig_esperada_passo1 = text_sig(engine_ref.snapshot())

        with tempfile.TemporaryDirectory() as tmp:
            caminho = os.path.join(tmp, "jornada.jsonl")
            eventos = [
                {"type": "session_start", "seq_global": 1,
                 "rows": 24, "cols": 80, "term": "xterm"},
                {"type": "bytes", "seq_global": 2, "dir": "out",
                 "data_b64": base64.b64encode(menu).decode()},
                {"type": "deterministic_input", "seq_global": 3,
                 "key_b64": base64.b64encode(b"1").decode(),
                 "text_sig": "sha256:" + "f" * 64},  # campo defasado/lixo
                {"type": "bytes", "seq_global": 4, "dir": "out",
                 "data_b64": base64.b64encode(b"1").decode()},  # eco
                {"type": "bytes", "seq_global": 5, "dir": "out",
                 "data_b64": base64.b64encode(resp).decode()},
                {"type": "deterministic_input", "seq_global": 6,
                 "key_b64": base64.b64encode(b"q").decode(),
                 "text_sig": "sha256:" + "f" * 64},
            ]
            with open(caminho, "w", encoding="utf-8") as fh:
                for e in eventos:
                    fh.write(json.dumps(e) + "\n")
            jornadas = _bench_load_journeys(caminho)
        steps = jornadas[0]["steps"]
        # a sig do passo 1 NÃO pode ser o campo lixo do evento
        self.assertNotEqual("sha256:" + "f" * 64,
                            steps[0].get("expected_screen_sig", ""))
        # deve ser a sig computada do byte stream (estado pós-resposta)
        self.assertEqual(sig_esperada_passo1, steps[0]["expected_screen_sig"])


class TestSkewConvergencia(unittest.TestCase):
    """Regra de convergência para skew de segmentação por type-ahead (§16).

    Caso real cap13: a captura gravou o input seguinte ANTES da resposta
    anterior terminar de desenhar (operador digitou às cegas — '6' chegou
    65ms após o último burst da resposta ao '3'; o submenu só desenhou 7ms
    DEPOIS do input). O segmento da captura fica com a resposta cortada; o
    replay ao vivo (que espera a tela estabilizar) vê a resposta inteira no
    passo certo. Resultado: o passo cortado diverge, mas o passo seguinte
    CONVERGE (as duas engines acumularam todos os bytes).

    Regra: divergência no passo i só é funcional de verdade se PERSISTIR —
    se o passo i+1 foi verificado e casou, o passo i é rebaixado para
    ``segmentation_skew=True`` (evidência preservada, não vira PASS cego:
    o último passo e divergências persistentes NUNCA são rebaixados).
    """

    def _sigs_captura(self, r1a: bytes, r1b: bytes, r2: bytes):
        """Sigs como a captura type-ahead as teria segmentado: segmento 1 sem
        a cauda da resposta 1; segmento 2 com a cauda + resposta 2 completa
        (estado convergido)."""
        from dakota_terminal.engine import TerminalEngine
        from dakota_terminal.signatures import text_sig
        eng = TerminalEngine(rows=24, cols=80, term="xterm")
        eng.feed_bytes(b"OP1\n" + r1a, direction="out")
        sig1 = text_sig(eng.snapshot())
        eng.feed_bytes(r1b + b"OP2\n" + r2, direction="out")
        sig2 = text_sig(eng.snapshot())
        return sig1, sig2

    def _jornada(self, sig1: str, sig2: str) -> dict:
        return {"journey_id": "j-skew", "steps": [
            {"step_id": "s1", "key_b64": base64.b64encode(b"OP1\n").decode(),
             "expected_screen_sig": sig1},
            {"step_id": "s2", "key_b64": base64.b64encode(b"OP2\n").decode(),
             "expected_screen_sig": sig2},
        ]}

    def _adapter(self):
        return SSHReplayAdapter(
            _modelo(()), _contrato(),
            ssh_runner=_runner_ok, popen_factory=_popen_type_ahead)

    def test_divergencia_que_converge_vira_segmentation_skew(self) -> None:
        sig1, sig2 = self._sigs_captura(_R1A, _R1B, _R2)
        adapter = self._adapter()
        handle = adapter.start_session("vu-1")
        amostras = adapter.execute_journey(
            handle, self._jornada(sig1, sig2), phase="MEASUREMENT")
        adapter.stop_session(handle)
        s1, s2 = amostras
        # passo 1: skew — rebaixado, com evidência
        self.assertFalse(s1.functional_divergence)
        self.assertTrue(s1.segmentation_skew)
        self.assertTrue(s1.screen_sig_checked)
        # passo 2: convergiu — intacto
        self.assertFalse(s2.functional_divergence)
        self.assertFalse(s2.segmentation_skew)

    def test_divergencia_persistente_nunca_e_rebaixada(self) -> None:
        sig1, _ = self._sigs_captura(_R1A, _R1B, _R2)
        # sig do passo 2 calculada de conteúdo DIFERENTE: divergência real
        _, sig2_errada = self._sigs_captura(b"OUTRA-TELA\r\n", _R1B, _R2)
        adapter = self._adapter()
        handle = adapter.start_session("vu-1")
        amostras = adapter.execute_journey(
            handle, self._jornada(sig1, sig2_errada), phase="MEASUREMENT")
        adapter.stop_session(handle)
        s1, s2 = amostras
        self.assertTrue(s1.functional_divergence)
        self.assertTrue(s2.functional_divergence)
        self.assertFalse(s1.segmentation_skew)
        self.assertFalse(s2.segmentation_skew)


class TestQuietPointsCurador(unittest.TestCase):
    """Curador: checkpoints de tela por quiet point com screen_raw (ground truth).

    A sig reconstruída do byte stream quebra em capturas com type-ahead e
    desenho incremental (engine fresca não tem o contexto das telas
    anteriores). O ``screen_raw_b64`` do evento é a tela REAL no momento do
    input — verdade de terreno gravada pelo gateway. Só vira checkpoint
    quando o input é um QUIET POINT (gap >= quiet_ms desde o último byte de
    saída: a tela estava completa e estável quando o usuário digitou).
    Quando a jornada tem screen_raw, o modelo de sig NÃO é emitido (evita
    falsos positivos da reconstrução)."""

    def _jsonl(self, tmp: str) -> str:
        menu = "MENU PRINCIPAL\n 1 - Abre A\n"
        eventos = [
            {"type": "session_start", "seq_global": 1,
             "rows": 24, "cols": 80, "term": "xterm"},
            {"type": "bytes", "seq_global": 2, "dir": "out",
             "data_b64": base64.b64encode(b"menu-bytes").decode(),
             "ts_ms": 1000},
            {"type": "deterministic_input", "seq_global": 3,
             "key_b64": base64.b64encode(b"1").decode(), "ts_ms": 1400,
             "screen_raw_b64": base64.b64encode(menu.encode()).decode()},
            {"type": "bytes", "seq_global": 4, "dir": "out",
             "data_b64": base64.b64encode(b"resp").decode(), "ts_ms": 1500},
            {"type": "deterministic_input", "seq_global": 6,
             "key_b64": base64.b64encode(b"2").decode(), "ts_ms": 1560,
             "screen_raw_b64": base64.b64encode(b"PARCIAL").decode()},
        ]
        caminho = os.path.join(tmp, "jornada.jsonl")
        with open(caminho, "w", encoding="utf-8") as fh:
            for e in eventos:
                fh.write(json.dumps(e) + "\n")
        return caminho

    def test_quiet_point_ganha_texto_esperado(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            jornadas = _bench_load_journeys(self._jsonl(tmp))
        steps = jornadas[0]["steps"]
        # gap 400ms >= 300 → quiet: checkpoint de texto do próprio evento
        self.assertEqual("MENU PRINCIPAL\n 1 - Abre A\n",
                         steps[0].get("expected_screen_text"))
        # gap 60ms → type-ahead: sem checkpoint (não verificável na captura)
        self.assertNotIn("expected_screen_text", steps[1])

    def test_checkpoint_ganha_janela_de_lag_limitada(self) -> None:
        """Cada checkpoint carrega uma janela de telas vizinhas (screen_raw
        de eventos próximos, quiet ou não — a janela exclui o próprio evento
        e é LIMITADA: telas idênticas distantes, como menus repetidos, não
        podem mascarar uma divergência real)."""
        with tempfile.TemporaryDirectory() as tmp:
            jornadas = _bench_load_journeys(self._jsonl(tmp))
        steps = jornadas[0]["steps"]
        janela = steps[0].get("lag_window_texts")
        self.assertIsNotNone(janela)
        # o vizinho imediato (ev-6, não-quiet) está na janela
        self.assertIn("PARCIAL", janela)
        # a própria tela esperada NÃO está na janela
        self.assertNotIn("MENU PRINCIPAL\n 1 - Abre A\n", janela)
        # passo sem checkpoint não tem janela
        self.assertNotIn("lag_window_texts", steps[1])

    def test_modelo_texto_suprime_sig_reconstruida(self) -> None:
        """Com screen_raw disponível, NENHUM passo leva expected_screen_sig:
        a reconstrução por bytes é inválida para esta captura (falsos
        positivos em massa no cap13-v3)."""
        with tempfile.TemporaryDirectory() as tmp:
            jornadas = _bench_load_journeys(self._jsonl(tmp))
        for step in jornadas[0]["steps"]:
            self.assertNotIn("expected_screen_sig", step)

    def test_sem_screen_raw_mantem_modelo_sig(self) -> None:
        """Fixtures/sintéticas sem screen_raw: comportamento sig inalterado."""
        with tempfile.TemporaryDirectory() as tmp:
            caminho = os.path.join(tmp, "jornada.jsonl")
            with open(caminho, "w", encoding="utf-8") as fh:
                fh.write(json.dumps({
                    "type": "deterministic_input", "seq_global": 3,
                    "key_b64": base64.b64encode(b"1").decode(),
                    "text_sig": "sha256:" + "0" * 64}) + "\n")
                fh.write(json.dumps({
                    "type": "deterministic_input", "seq_global": 6,
                    "key_b64": base64.b64encode(b"2").decode(),
                    "text_sig": "sha256:" + "1" * 64}) + "\n")
            jornadas = _bench_load_journeys(caminho)
        steps = jornadas[0]["steps"]
        self.assertEqual("sha256:" + "1" * 64, steps[0]["expected_screen_sig"])
        self.assertNotIn("expected_screen_text", steps[0])


class _FakeMaquinaTexto(_FakeERPMachine):
    """Emite telas completas por comando (para checkpoints de texto)."""

    TELAS = {
        b"OP1\n": b"\x1b[2J\x1b[1;1HTELA A\r\nCONTEUDO A\r\n",
        b"OP2\n": b"\x1b[2J\x1b[1;1HTELA B\r\nCONTEUDO B\r\n",
        b"OP3\n": b"\x1b[2J\x1b[1;1HTELA C\r\nCONTEUDO C\r\n",
    }

    def _serve(self) -> None:
        self._emit(b"\x1b[2J\x1b[1;1HMENU PRINCIPAL\r\n 1 - Abre A\r\n")
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
                break
            buf += chunk
            for cmd, tela in list(self.TELAS.items()):
                if cmd in buf:
                    buf = buf.split(cmd, 1)[1]
                    self._emit(cmd)  # eco
                    time.sleep(0.02)
                    self._emit(tela)


def _popen_texto(argv, **kwargs):
    return _FakeMaquinaTexto(argv, **kwargs)


class TestCheckpointsTexto(unittest.TestCase):
    """Adapter: comparação de texto no estado estável ANTES do envio.

    O checkpoint do passo i é a tela em que o input i foi pressionado na
    captura (screen_raw) — ou seja, o estado APÓS a resposta i-1. O adapter
    compara ANTES de enviar o input i, com a engine alimentada por TODO o
    stream da sessão (preamble incluso — retro-feed do tail), nunca por um
    recorte fresco. Máscaras voláteis (data, Kb livres, número de pedido)
    são aplicadas dos dois lados; diferença estrutural NÃO é mascarada.
    Divergência de texto NUNCA é rebaixada pela regra de skew.
    """

    PREAMBLE_MENU = ({"wait_text": "MENU PRINCIPAL", "timeout_s": 5},)

    def _adapter(self):
        return SSHReplayAdapter(
            _modelo(self.PREAMBLE_MENU), _contrato(),
            ssh_runner=_runner_ok, popen_factory=_popen_texto)

    def _jornada(self, textos: list[str | None]) -> dict:
        passos = []
        for idx, texto in enumerate(textos, start=1):
            passo = {"step_id": f"s{idx}",
                     "key_b64": base64.b64encode(f"OP{idx}\n".encode()).decode()}
            if texto is not None:
                passo["expected_screen_text"] = texto
            passos.append(passo)
        return {"journey_id": "j-texto", "steps": passos}

    def test_checkpoint_pre_envio_casa_tela_do_preamble(self) -> None:
        """Passo 1: tela esperada = a do FIM do preamble (menu). Sem o
        retro-feed do tail a engine estaria vazia e divergiria."""
        adapter = self._adapter()
        handle = adapter.start_session("vu-1")
        amostras = adapter.execute_journey(handle, self._jornada([
            "MENU PRINCIPAL\n 1 - Abre A",
            "TELA A\nCONTEUDO A",
            "TELA B\nCONTEUDO B",
        ]), phase="MEASUREMENT")
        adapter.stop_session(handle)
        self.assertEqual(3, len(amostras))
        for a in amostras:
            self.assertTrue(a.screen_sig_checked, a.step_id)
            self.assertFalse(a.functional_divergence, a.step_id)
            self.assertEqual("text", a.screen_check_kind)

    def test_divergencia_de_texto_reconvergente_vira_checkpoint_lag(self) -> None:
        """Divergência de texto com reconvergência no checkpoint seguinte:
        rebaixada para checkpoint_lag (evidência preservada, não vira PASS
        cego — a regra exige o PRÓXIMO CHECKPOINT convergindo)."""
        adapter = self._adapter()
        handle = adapter.start_session("vu-1")
        amostras = adapter.execute_journey(handle, self._jornada([
            "MENU PRINCIPAL\n 1 - Abre A",
            "TELA A\nCONTEUDO A",
            "TELA ERRADA\nFORA DO CAMINHO",  # real: TELA B — diverge
            "TELA C\nCONTEUDO C",            # reconverge no checkpoint seguinte
        ]), phase="MEASUREMENT")
        adapter.stop_session(handle)
        s3, s4 = amostras[2], amostras[3]
        self.assertFalse(s3.functional_divergence)
        self.assertTrue(s3.checkpoint_lag)
        self.assertFalse(s4.functional_divergence)
        self.assertFalse(s4.checkpoint_lag)

    def test_checkpoint_com_baseline_proprio_do_ambiente(self) -> None:
        """``expected_screen_text_by_env`` tem precedência sobre o texto
        compartilhado da captura (datasets divergentes, ex.: .est
        endian-nativo): o passo verifica contra o baseline PRÓPRIO, marca
        ``screen_check_basis="env"`` e não diverge quando a tela real casa
        com o baseline do ambiente (mesmo divergindo do compartilhado)."""
        adapter = self._adapter()
        handle = adapter.start_session("vu-1")
        jornada = self._jornada([None, None])
        # passo 1: tela do fim do preamble (menu) — texto compartilhado
        # ERRADO de propósito; o baseline do ambiente (aix-power) é o certo
        jornada["steps"][0]["expected_screen_text"] = "TELA DE OUTRO DATASET"
        jornada["steps"][0]["expected_screen_text_by_env"] = {
            "aix-power": "MENU PRINCIPAL\n 1 - Abre A"}
        # passo 2: sem baseline por env → fallback no compartilhado
        jornada["steps"][1]["expected_screen_text"] = "TELA A\nCONTEUDO A"
        amostras = adapter.execute_journey(handle, jornada, phase="MEASUREMENT")
        adapter.stop_session(handle)
        s1, s2 = amostras
        self.assertFalse(s1.functional_divergence)
        self.assertEqual("env", s1.screen_check_basis)
        self.assertFalse(s2.functional_divergence)
        self.assertEqual("shared", s2.screen_check_basis)

    def test_mascaras_volateis_dos_dois_lados(self) -> None:
        from dakota_gateway.benchmark.adapters import _mask_volatil
        esperado = _mask_volatil(
            "Pedido: D00011073 Emissao: 27/07/26 792,000 Kb livres 10:15:30")
        observado = _mask_volatil(
            "Pedido: D00011200 Emissao: 01/08/26 813,440 Kb livres 22:41:05")
        self.assertEqual(esperado, observado)
        estrutura = _mask_volatil("Pedido: D00011073 CANCELADO Emissao:")
        self.assertNotEqual(esperado, estrutura)

    def test_mascara_identidade_plataforma_rodape(self) -> None:
        """O rodapé do ERP exibe a identidade da plataforma ("IBM AIX
        (COMMON)" × "Linux X86") — diferença ESPERADA entre os ambientes do
        comparativo (caso real cap13: 45/45 checkpoints do Linux divergiam
        só por essa linha). As duas formas normalizam igual; qualquer outra
        diferença estrutural na mesma linha continua divergindo."""
        from dakota_gateway.benchmark.adapters import _normalizar_texto_tela
        # O campo do rodapé tem largura fixa: o padding varia com o nome da
        # plataforma ("IBM AIX (COMMON)" = 16 chars, "Linux X86" = 9) — a
        # máscara consome também os espaços de preenchimento.
        aix = _normalizar_texto_tela(
            "  IBM AIX (COMMON)    |    over    |    792,000 Kb livres")
        linux = _normalizar_texto_tela(
            "  Linux X86           |    over    |    813,440 Kb livres")
        self.assertEqual(aix, linux)
        outro = _normalizar_texto_tela(
            "  IBM AIX (COMMON)    |    over    |    ERRO na base")
        self.assertNotEqual(aix, outro)

    def test_lag_de_checkpoint_rebaixado_so_com_reconvergencia(self) -> None:
        """Lag transitório (pausa 'aguarde' > stable_ms): o checkpoint i fica
        uma tela atrás/adiante, mas o PRÓXIMO CHECKPOINT (não o próximo
        passo — passos sem checkpoint não provam nada) reconverge. Regra:
        rebaixa para checkpoint_lag SOMENTE com essa reconvergência; se o
        próximo checkpoint também diverge, ou se é o último, permanece
        divergência funcional (porta 1, §16)."""
        adapter = self._adapter()
        handle = adapter.start_session("vu-1")
        # s1 diverge (texto errado), s2 sem checkpoint (não prova nada),
        # s3 converge → s1 vira checkpoint_lag
        amostras = adapter.execute_journey(handle, self._jornada([
            "TELA QUE NAO EXISTE",
            None,
            "TELA B\nCONTEUDO B",
        ]), phase="MEASUREMENT")
        adapter.stop_session(handle)
        s1, _s2, s3 = amostras
        self.assertFalse(s1.functional_divergence)
        self.assertTrue(s1.checkpoint_lag)
        self.assertFalse(s3.functional_divergence)
        self.assertFalse(s3.checkpoint_lag)

    def test_lag_sem_reconvergencia_permanece_divergencia(self) -> None:
        adapter = self._adapter()
        handle = adapter.start_session("vu-1")
        # s1 diverge, s3 (próximo checkpoint) TAMBÉM diverge → s1 permanece
        amostras = adapter.execute_journey(handle, self._jornada([
            "TELA QUE NAO EXISTE",
            None,
            "OUTRA TELA ERRADA",
        ]), phase="MEASUREMENT")
        adapter.stop_session(handle)
        s1, _s2, s3 = amostras
        self.assertTrue(s1.functional_divergence)
        self.assertFalse(s1.checkpoint_lag)
        self.assertTrue(s3.functional_divergence)

    def test_ultimo_checkpoint_divergente_nunca_rebaixado(self) -> None:
        adapter = self._adapter()
        handle = adapter.start_session("vu-1")
        amostras = adapter.execute_journey(handle, self._jornada([
            "TELA A\nCONTEUDO A",
            "TELA ERRADA FINAL",
        ]), phase="MEASUREMENT")
        adapter.stop_session(handle)
        ultimo = amostras[-1]
        self.assertTrue(ultimo.functional_divergence)
        self.assertFalse(ultimo.checkpoint_lag)

    def test_janela_de_lag_casa_tela_vizinha_na_hora_do_check(self) -> None:
        """Se o checkpoint diverge mas a tela observada casa EXATAMENTE com
        uma tela vizinha da captura (janela de lag), é atraso/adianto de
        apresentação comprovado — rebaixa na hora, sem esperar o próximo
        checkpoint. Caso real cap13 ev-445: replay 3 passos à frente no
        cascata de ESC; a tela observada era idêntica à screen_raw do
        ev-459 da captura."""
        adapter = self._adapter()
        handle = adapter.start_session("vu-1")
        passo = {"step_id": "s1",
                 "key_b64": base64.b64encode(b"OP1\n").decode(),
                 "expected_screen_text": "TELA QUE NAO EXISTE",
                 "lag_window_texts": ["MENU PRINCIPAL\n 1 - Abre A"]}
        amostras = adapter.execute_journey(
            handle, {"journey_id": "j-janela", "steps": [passo]},
            phase="MEASUREMENT")
        adapter.stop_session(handle)
        s1 = amostras[0]
        self.assertFalse(s1.functional_divergence)
        self.assertTrue(s1.checkpoint_lag)
        self.assertEqual("text", s1.screen_check_kind)


_R1A = b"TELA-A-PARCIAL\r\n"
_R1B = b"TELA-A-COMPLEMENTO\r\n"
_R2 = b"TELA-B-COMPLETA\r\n"


class _FakeERPMachineTypeAhead(_FakeERPMachine):
    """Resposta ao OP1 sai EM DUAS PARTES no mesmo dreno (type-ahead: na
    captura original a cauda cairia no segmento do input seguinte)."""

    def _serve(self) -> None:
        roteiro = [
            (b"OP1\n", (_R1A, _R1B)),
            (b"OP2\n", (_R2,)),
        ]
        buf = b""
        etapa = 0
        while not self._stop.is_set():
            prontos, _, _ = select.select([self._stdin_r], [], [], 0.05)
            if not prontos:
                continue
            try:
                chunk = os.read(self._stdin_r, 65536)
            except OSError:
                break
            if not chunk:
                break
            buf += chunk
            while etapa < len(roteiro) and roteiro[etapa][0] in buf:
                esperado, partes = roteiro[etapa]
                buf = buf.split(esperado, 1)[1]
                self._emit(esperado)  # eco do PTY
                for parte in partes:
                    time.sleep(0.05)
                    self._emit(parte)
                etapa += 1


def _popen_type_ahead(argv, **kwargs):
    return _FakeERPMachineTypeAhead(argv, **kwargs)


if __name__ == "__main__":
    unittest.main()

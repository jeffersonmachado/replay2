"""Adaptadores de execução de ambiente (contrato §8/§9).

``EnvironmentExecutionAdapter`` é o Protocol que todo adaptador de ambiente
implementa (o ``ControlledAdapter`` dos testes, este ``SSHReplayAdapter`` real
e qualquer futuro adaptador).

``SSHReplayAdapter`` executa jornadas REAIS contra os servidores via SSH a
partir do orquestrador:

- abre uma sessão SSH com PTY (``ssh -tt``) por usuário virtual no host alvo;
- envia os bytes exatos das teclas de cada passo (``key_b64`` da captura
  auditável — nunca texto reconstruído quando o b64 existe);
- mede a latência REAL de cada operação com ``time.monotonic_ns``:
  ``started_ns`` antes do envio, ``finished_ns`` quando a saída fica estável
  (sem bytes novos por ``stable_ms``) — ou marca ``timeout``;
- valida a assinatura de tela quando o passo traz ``expected_screen_sig``,
  alimentando a saída na engine canônica ``dakota_terminal`` (text_sig);
- coleta métricas de host consultando a tabela ``host_metrics`` da replay.db
  REMOTA via ssh, filtrando a janela temporal da run; métricas indisponíveis
  são reportadas como ``{"available": false, "reason": ...}`` — NUNCA zero
  fingindo medição.

Segredos nunca em texto claro: a credencial vem de ``env.user_secret_ref``
(``env:VAR`` / ``file:<path>`` / ``ssh-key:user@host``) e o SSH roda com
``BatchMode=yes`` (sem prompt interativo).
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import select
import subprocess
import time
from typing import Protocol, runtime_checkable

from .contract import ExperimentContract
from .environments import EnvironmentModel
from .models import OperationSample

_PHASES_VALIDAS = ("WARMUP", "MEASUREMENT", "COOLDOWN")

#: Tamanho máximo do tail de saída guardado por sessão (log forense).
_TAIL_MAX_BYTES = 65536

#: Retry da coleta de host_metrics contra falhas de TRANSPORTE (sshd do AIX
#: atrás do ForceCommand + VPN com perda devolve intermitentemente rc!=0 ou
#: stdout vazio/corrompido). Nunca se aplica a resposta válida da query —
#: inclusive janela com 0 linhas, confirmada pela linha sentinela do script.
_HOST_METRICS_MAX_ATTEMPTS = 3
_HOST_METRICS_BACKOFF_S = (1.0, 2.0)


class PreambleError(RuntimeError):
    """Âncora do preamble de entrada não apareceu dentro do timeout.

    A sessão não alcançou o estado inicial da jornada (ex.: menu de login
    não respondeu, launcher do ERP falhou). É bloqueio honesto: a fase
    aborta via ``start_session_failed`` e o ambiente NUNCA produz amostras
    a partir de um estado errado.
    """


_ESCAPES_PREAMBLE = {
    "r": b"\r", "n": b"\n", "t": b"\t", "e": b"\x1b", "\\": b"\\",
}

#: Padrões de erro de TRANSPORTE na saída da sessão (colapso de rede local,
#: ex.: VPN do orquestrador caiu — caso real cap13 v5: "Connection timed
#: out" seguido de "Network is unreachable" nos DOIS hosts por horas).
#: Distinto de saturação/limite de licença, que chegam como CONTEÚDO de tela.
_PADROES_ERRO_TRANSPORTE = (
    b"ssh: connect",
    b"Network is unreachable",
    b"Connection timed out",
    b"Connection refused",
    b"Connection reset",
    b"Could not resolve hostname",
)

#: Máscaras de campos voláteis aplicadas DOS DOIS LADOS da comparação de
#: texto de tela (checkpoint quiet point): data de emissão, contador de
#: memória livre do rodapé do ERP, números sequenciais (pedido etc.) e
#: horários mudam a cada execução sem significar divergência funcional.
#: A identidade da plataforma no rodapé do ERP ("IBM AIX (COMMON)" ×
#: "Linux X86") é a diferença ESPERADA entre os ambientes — é exatamente o
#: que o benchmark existe para comparar; rótulos, dados e estado da tela
#: continuam integralmente comparados. Diferença estrutural NUNCA é
#: mascarada.
_MASCARAS_VOLATEIS = (
    (re.compile(r"\d{2}/\d{2}/\d{2,4}"), "##DATA##"),
    (re.compile(r"[\d.,]+\s*Kb\b"), "##KB##"),
    (re.compile(r"\b[A-Z]\d{5,}\b"), "##ID##"),
    (re.compile(r"\b\d{2}:\d{2}(?::\d{2})?\b"), "##HORA##"),
    (re.compile(r"(?:ibm aix \(common\)|linux x86) *", re.IGNORECASE),
     "##PLATAFORMA##"),
)


def _mask_volatil(texto: str) -> str:
    """Substitui campos voláteis por tokens fixos (comparação de tela)."""
    for padrao, token in _MASCARAS_VOLATEIS:
        texto = padrao.sub(token, texto)
    return texto


def _normalizar_texto_tela(texto: str) -> str:
    """Normaliza texto de tela para comparação: rstrip por linha, remove
    linhas vazias do fim, aplica as máscaras voláteis e iguala caixa
    (indicadores de status do ERP variam "Ok"/"ok" entre captura e replay
    sem significar divergência funcional)."""
    linhas = [l.rstrip() for l in
              texto.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    while linhas and not linhas[-1]:
        linhas.pop()
    return _mask_volatil("\n".join(linhas)).lower()


def _render_terminal_text(engine) -> str:
    """Renderiza as células da engine canônica como texto (linhas × cols)."""
    return "\n".join("".join(cell.ch for cell in row) for row in engine.cells)


def _decode_preamble_send(texto: str) -> bytes:
    """Decodifica escapes simples do ``send`` do preamble (``\\r`` → CR etc.).

    Suportados: ``\\r \\n \\t \\e \\\\``. Qualquer outro escape é enviado
    literalmente (sem surpresas silenciosas).
    """
    out = bytearray()
    i = 0
    while i < len(texto):
        ch = texto[i]
        if ch == "\\" and i + 1 < len(texto):
            nxt = texto[i + 1]
            if nxt in _ESCAPES_PREAMBLE:
                out += _ESCAPES_PREAMBLE[nxt]
                i += 2
                continue
        out += ch.encode("utf-8")
        i += 1
    return bytes(out)


@runtime_checkable
class EnvironmentExecutionAdapter(Protocol):
    """Protocolo do adaptador de execução de ambiente (§8)."""

    def preflight(self) -> dict:
        """Valida acessibilidade: ``{"ok": bool, "checks": [...]}``."""
        ...

    def prepare_dataset(self, dataset_ref: dict) -> dict:
        """Garante o dataset do contrato no ambiente."""
        ...

    def start_session(self, virtual_user_id: str) -> str:
        """Abre sessão para um usuário virtual; devolve o session_handle."""
        ...

    def execute_journey(self, session_handle: str, journey: dict,
                        *, phase: str) -> list:
        """Executa a jornada medindo latência real por operação."""
        ...

    def stop_session(self, session_handle: str) -> None:
        """Encerra a sessão do usuário virtual."""
        ...

    def collect_application_metrics(self) -> dict:
        """Amostras de aplicação acumuladas, separadas por fase."""
        ...

    def collect_host_metrics(self, from_ms: int, to_ms: int) -> list[dict]:
        """Métricas de host na janela temporal da run (§13)."""
        ...

    def collect_database_metrics(self) -> dict:
        """§14 — ``{"available": false, "reason": ...}`` quando não aplicável."""
        ...

    def cleanup(self) -> None:
        """Encerra recursos abertos (sessões restantes)."""
        ...


def _default_ssh_runner(argv: list[str], input_text: str | None,
                        timeout: float) -> subprocess.CompletedProcess:
    """Runner padrão: ``subprocess.run`` capturando saída em texto."""
    return subprocess.run(
        argv,
        input=input_text,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


class SSHReplayAdapter:
    """Adaptador real §8/§9: replay de jornadas via SSH (AIX e Linux).

    Parâmetros injetáveis para teste: ``ssh_runner`` (comando ssh one-shot)
    e ``popen_factory`` (processo persistente da sessão PTY).
    """

    def __init__(self, env: EnvironmentModel, contract: ExperimentContract,
                 *, ssh_runner=None, popen_factory=None,
                 ssh_user: str = "", stable_ms: int | None = None,
                 step_timeout_s: float = 30.0,
                 remote_python_cmd: str = "python3 -") -> None:
        self.env = env
        self.contract = contract
        self._ssh_runner = ssh_runner or _default_ssh_runner
        self._popen_factory = popen_factory or subprocess.Popen
        self._ssh_user = ssh_user
        # stable_ms explícito (testes) vence; senão o do ambiente; senão 150.
        self.stable_ms = (int(stable_ms) if stable_ms is not None
                          else int(getattr(env, "stable_ms", 0) or 150))
        self.step_timeout_s = step_timeout_s
        self.remote_python_cmd = remote_python_cmd
        self._sessions: dict[str, dict] = {}
        self._session_seq = 0
        self._samples: list[OperationSample] = []
        self._iteration = 0
        self._concurrency = 0
        self.host_metrics_status: dict = {"available": True}
        # Forense: jornadas abortadas por morte/fechamento da sessão (steps
        # restantes NÃO executados — registrados aqui, não como amostras).
        self.journey_incompletions: list[dict] = []
        # Cobertura da verificação funcional (FASE 4): um registro por passo
        # COM expectativa de tela (checkpoint executado) — {"phase",
        # "journey_id", "step_id", "checked", "reason"}; a razão da
        # não-checação é auditável ("terminal_engine_unavailable",
        # "sem_resposta", "timeout", código de erro da sessão).
        self.checkpoint_log: list[dict] = []
        # Forense: tail da saída de cada usuário virtual (últimos 64KB) para
        # o run logs/ — evidência do que a sessão recebeu perto da morte.
        self._tails_by_vu: dict[str, bytearray] = {}
        # Classe do último erro de start_session: True quando a saída da
        # sessão é vazia ou só traz erro de transporte (rede local caída) —
        # o executor usa para abortar cedo como environment_unreachable
        # em vez de moer níveis condenados por horas (caso real v5).
        self.last_start_error_transport = False

    # -- contexto de iteração/concorrência (chamado pelo executor, duck typing)

    def set_iteration_context(self, iteration: int, concurrency: int) -> None:
        """Estampa iteração/concorrência correntes nas próximas amostras.

        Também REINICIA o acúmulo forense de tails: o executor chama este
        método no início de CADA run (nível × iteração × ambiente) e, sem a
        limpeza, ``session_tails()`` da run N incluía VUs encerrados em runs
        anteriores — uma run de concorrência 1 depois de uma de 10 gravava
        ``session-vu-2.log``…``session-vu-10.log`` de sessões alheias
        (evidência forense contaminada). As sessões AINDA ABERTAS mantêm
        seus tails (``_sessions`` não é tocado).
        """
        self._iteration = int(iteration)
        self._concurrency = int(concurrency)
        self._tails_by_vu.clear()

    # -- resolução de acesso (sem segredo em texto claro) --------------------

    def _resolve_key_path(self) -> str | None:
        """Resolve a referência de credencial para um caminho de chave SSH."""
        ref = self.env.user_secret_ref or ""
        if ref.startswith("env:"):
            return os.environ.get(ref[4:]) or None
        if ref.startswith("file:"):
            return os.path.expanduser(ref[5:])
        return None  # ssh-key:user@host → chaves default do agente/usuário

    def _login_user(self) -> str:
        """Usuário de login SSH (endpoint, secret_ref ssh-key: ou parâmetro)."""
        endpoint = self.env.application_endpoint or ""
        if endpoint.startswith("ssh://") and "@" in endpoint:
            return endpoint[len("ssh://"):].split("@", 1)[0]
        ref = self.env.user_secret_ref or ""
        if ref.startswith("ssh-key:") and "@" in ref:
            return ref[len("ssh-key:"):].split("@", 1)[0]
        return self._ssh_user

    def _ssh_base_argv(self, *, tty: bool = False,
                       user_override: str = "") -> list[str]:
        """Monta o argv base do ssh (BatchMode, sem prompt interativo).

        ``tty=True`` (``-tt``) é usado APENAS nas sessões de replay da
        jornada. Comandos one-shot (preflight, coleta de métricas) usam
        ``-T``: sem PTY remoto — com PTY, ``python3 -`` entraria em modo
        interativo (banner/prompt >>>) em vez de executar o script do stdin,
        e a saída de comandos não-tty se perde no teardown do PTY (bug
        observado no AIX real).

        ``user_override`` troca APENAS o usuário de login do destino (canal
        dedicado da coleta de métricas — ex.: o login do endpoint está sob
        ForceCommand de captura e não lê a replay.db).
        """
        argv = [
            "ssh",
            "-o", "BatchMode=yes",
            "-o", "StrictHostKeyChecking=accept-new",
            "-o", "ConnectTimeout=10",
            "-p", str(self.env.port),
        ]
        if tty:
            argv.append("-tt")
        else:
            argv.append("-T")
        key_path = self._resolve_key_path()
        if key_path:
            argv += ["-i", key_path]
        user = user_override or self._login_user()
        destino = f"{user}@{self.env.host}" if user else self.env.host
        argv.append(destino)
        return argv

    # -- ciclo de vida -----------------------------------------------------

    def preflight(self) -> dict:
        """Preflight REAL: conecta via ssh e executa comando trivial remoto."""
        checks: list[dict] = []
        try:
            res = self._ssh_runner(
                self._ssh_base_argv() + ["true"], None, 20.0)
            ok = getattr(res, "returncode", 1) == 0
            detail = "" if ok else str(getattr(res, "stderr", "") or "")[:300]
        except Exception as exc:
            ok = False
            detail = str(exc)[:300]
        checks.append({"name": "ssh_connectivity", "ok": ok, "detail": detail})
        return {"ok": ok, "checks": checks}

    def prepare_dataset(self, dataset_ref: dict) -> dict:
        """Dataset é provisionamento operacional; registra e confirma a referência."""
        return {"ok": True, "dataset_ref": dict(dataset_ref)}

    def start_session(self, virtual_user_id: str) -> str:
        """Abre uma sessão SSH com PTY dedicada ao usuário virtual.

        Se o ambiente define ``entry_preamble``, executa os passos de entrada
        (menu de login → shell → launcher do ERP) ANTES de devolver o handle:
        a jornada sempre começa do estado inicial correto. O preamble não
        gera amostras; âncora ausente aborta a sessão com ``PreambleError``.
        """
        self._session_seq += 1
        handle = f"{virtual_user_id}#{self._session_seq}"
        argv = self._ssh_base_argv(tty=True)
        proc = self._popen_factory(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        session = {
            "proc": proc,
            "virtual_user_id": virtual_user_id,
            "terminal": None,
            "tail": bytearray(),
        }
        self._sessions[handle] = session
        self.last_start_error_transport = False
        try:
            self._run_preamble(session)
        except Exception:
            # sessão não alcançou o estado inicial: encerra e propaga —
            # o executor registra start_session_failed (bloqueio honesto)
            tail = bytes(session.get("tail") or b"")
            self.last_start_error_transport = (
                not tail.strip()
                or any(p in tail for p in _PADROES_ERRO_TRANSPORTE))
            try:
                self.stop_session(handle)
            except Exception:
                pass
            raise
        return handle

    # -- preamble de entrada (§9) -------------------------------------------

    def _run_preamble(self, session: dict) -> None:
        """Executa os passos de ``env.entry_preamble`` sobre a sessão nova.

        Cada passo pode ter ``send`` (bytes com escapes simples) e/ou
        ``wait_text`` (âncora aguardada na saída, com ``timeout_s``).
        A saída acumula no tail forense da sessão, mas NUNCA vira amostra.
        """
        proc = session["proc"]
        for idx, passo in enumerate(self.env.entry_preamble or ()):
            send = passo.get("send")
            if send:
                proc.stdin.write(_decode_preamble_send(str(send)))
                proc.stdin.flush()
            anchor = passo.get("wait_text")
            if anchor:
                timeout_s = float(passo.get("timeout_s", 15.0))
                if not self._wait_for_anchor(
                        proc, session["tail"],
                        str(anchor).encode("utf-8"), timeout_s):
                    raise PreambleError(
                        f"passo {idx}: anchor {anchor!r} não apareceu "
                        f"em {timeout_s:.0f}s (ambiente "
                        f"{self.env.environment_id} não alcançou o estado "
                        f"inicial da jornada)")
        # Dreno final: o âncora pode aparecer NO MEIO do desenho da tela
        # inicial (caso real cap13: "DAKOTA S/A" sai antes do restante do
        # menu). Sem este dreno o restante do desenho vaza para a observação
        # do primeiro passo do corpo e diverge a sig da tela.
        if self.env.entry_preamble:
            self._drain_until_stable(proc, session["tail"])

    def _wait_for_anchor(self, proc, tail: bytearray, needle: bytes,
                         timeout_s: float) -> bool:
        """Aguarda ``needle`` aparecer na saída da sessão (echo incluso).

        Lê com ``select`` até o deadline; acumula no tail forense. Devolve
        True assim que o âncora aparece; False em timeout ou EOF remoto.
        """
        fd = proc.stdout.fileno()
        buf = bytearray()
        deadline = time.monotonic() + timeout_s
        while True:
            restante = deadline - time.monotonic()
            if restante <= 0:
                return False
            prontos, _, _ = select.select([fd], [], [], min(0.2, restante))
            if not prontos:
                continue
            try:
                chunk = os.read(fd, 65536)
            except OSError:
                return False
            if not chunk:
                return False  # EOF: remoto encerrou antes do âncora
            buf += chunk
            tail.extend(chunk)
            if len(tail) > _TAIL_MAX_BYTES:
                del tail[:len(tail) - _TAIL_MAX_BYTES]
            if needle in buf:
                return True

    def stop_session(self, session_handle: str) -> None:
        """Encerra a sessão (fecha stdin, termina e aguarda o processo)."""
        session = self._sessions.pop(session_handle, None)
        if session is None:
            return
        proc = session["proc"]
        try:
            if proc.stdin is not None:
                proc.stdin.close()
        except OSError:
            pass
        # drena o restante da saída para o tail forense antes de matar
        try:
            self._drain_until_stable(proc, session["tail"], timeout_s=1.0)
        except Exception:
            pass
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
                proc.wait(timeout=5)
            except Exception:
                pass
        self._tails_by_vu[session["virtual_user_id"]] = session["tail"]

    def session_tails(self) -> dict[str, bytes]:
        """Tails de saída por usuário virtual (vivas e encerradas) — forense."""
        tails: dict[str, bytes] = {}
        for handle, session in self._sessions.items():
            tails[session["virtual_user_id"]] = bytes(session["tail"])
        for vu, tail in self._tails_by_vu.items():
            tails[vu] = bytes(tail)
        return tails

    def reap_orphans(self) -> dict:
        """Janitor de processos órfãos do ambiente (chamado entre fases).

        Matar o ssh local de uma sessão NÃO mata a árvore remota: o shell
        de login sobrevive (PPID=1) e o runtime do ERP continua consumindo
        CPU, distorcendo as fases seguintes. Executa ``env.orphan_reap_cmd``
        via ssh one-shot; o comando só pode tocar shells ÓRFÃOS (PPID=1) —
        sessões vivas têm PPID=sshd e ficam intocadas. Falha de transporte
        nunca derruba o benchmark: é reportada no resultado.
        """
        cmd = (self.env.orphan_reap_cmd or "").strip()
        if not cmd:
            return {"executed": False, "reason": "not_configured"}
        try:
            res = self._ssh_runner(self._ssh_base_argv() + [cmd], None, 60.0)
            ok = getattr(res, "returncode", 1) == 0
            saida = str(getattr(res, "stderr", "") or "")[:300]
            return {"executed": ok,
                    "reason": "" if ok else f"rc={getattr(res, 'returncode', '?')}",
                    "detail": saida}
        except Exception as exc:
            return {"executed": False, "reason": "error", "detail": str(exc)[:300]}

    def cleanup(self) -> None:
        """Encerra todas as sessões ainda abertas."""
        for handle in list(self._sessions):
            self.stop_session(handle)

    # -- execução de jornada -------------------------------------------------

    def execute_journey(self, session_handle: str, journey: dict,
                        *, phase: str) -> list[OperationSample]:
        """Executa os passos da jornada medindo latência real por operação.

        Formato dos passos (jornada derivada de captura auditável):
        ``{"step_id", "key_b64"?, "key_text"?, "payload"?, "think_time_ms"?,
        "expected_screen_sig"?}`` — ``key_b64`` tem precedência (bytes exatos).
        """
        if phase not in _PHASES_VALIDAS:
            raise ValueError(f"fase inválida: {phase!r}")
        session = self._sessions[session_handle]
        # Geometria/term da captura: a engine de assinatura deve ser a MESMA
        # da captura (a text_sig incorpora rows/cols/term no payload hashado).
        # Troca de jornada = estado de tela novo → engine reconstruída.
        cap_term = journey.get("capture_terminal")
        if cap_term != session.get("capture_terminal"):
            session["capture_terminal"] = cap_term
            session["terminal"] = None
        proc = session["proc"]
        # Garante a engine da sessão desde o início da jornada: na criação
        # ela é retro-alimentada com TODO o stream já recebido (preamble,
        # banners) e passa a espelhar a tela REAL — os checkpoints de texto
        # comparam estado de tela completo, nunca um recorte fresco.
        self._ensure_terminal(session)
        journey_id = str(journey.get("journey_id", "journey"))
        steps = list(journey.get("steps", []))
        produzidas: list[OperationSample] = []
        for idx, step in enumerate(steps):
            step_id = str(step.get("step_id") or f"step-{idx}")
            dados = self._step_bytes(step)
            think_ms = step.get("think_time_ms")
            if think_ms:
                # pacing determinístico ANTES do envio (fora da latência)
                time.sleep(float(think_ms) / 1000.0)

            divergence = False
            observado_str = ""
            esperado_str = ""
            checked = False
            check_kind = ""
            checkpoint_lag_imediato = False
            engine_indisponivel = False

            # Checkpoint de TEXTO (quiet point, ground truth da captura): a
            # tela esperada é o estado em que o input i foi pressionado na
            # captura — ou seja, o estado APÓS a resposta i-1. Compara ANTES
            # de enviar o input, no estado estável pós-dreno anterior.
            # ``expected_screen_text_by_env`` (baseline PRÓPRIO do ambiente,
            # gerado de uma passada real quando os datasets divergem entre
            # ambientes — ex.: formato .est endian-nativo inviabiliza cópia
            # binária AIX→Linux) tem precedência sobre o texto compartilhado
            # da captura; a base usada fica marcada na amostra
            # (``screen_check_basis``) para a decisão NUNCA tratar
            # equivalência por baseline próprio como prova de paridade de
            # dados (veredito máximo: WARN).
            texto_esperado = step.get("expected_screen_text")
            check_basis = "shared"
            por_env = step.get("expected_screen_text_by_env")
            if isinstance(por_env, dict):
                texto_env = por_env.get(self.env.environment_id)
                if texto_env:
                    texto_esperado = texto_env
                    check_basis = "env"
            if texto_esperado:
                engine = self._ensure_terminal(session)
                if engine is None:
                    # engine indisponível: checkpoint executado mas NÃO
                    # checado — fica registrado no checkpoint_log (FASE 4)
                    engine_indisponivel = True
                else:
                    checked = True
                    check_kind = "text"
                    obs_norm = _normalizar_texto_tela(
                        _render_terminal_text(engine))
                    exp_norm = _normalizar_texto_tela(str(texto_esperado))
                    observado_str = "sha256:" + hashlib.sha256(
                        obs_norm.encode("utf-8")).hexdigest()
                    esperado_str = "sha256:" + hashlib.sha256(
                        exp_norm.encode("utf-8")).hexdigest()
                    if obs_norm != exp_norm:
                        # janela de lag: a tela observada casa com uma tela
                        # VIZINHA da captura (mesma tela, outro momento)?
                        # Casa real cap13: replay uns passos à frente/atrás
                        # na cascata de ESC — atraso de apresentação
                        # comprovado, não divergência funcional. Com baseline
                        # próprio, a janela do ambiente (mesmos step ids)
                        # tem precedência pelo mesmo motivo.
                        janela = step.get("lag_window_texts") or ()
                        if check_basis == "env":
                            janela_env = step.get("lag_window_texts_by_env")
                            if isinstance(janela_env, dict):
                                janela = (janela_env.get(
                                    self.env.environment_id) or janela)
                        for vizinho in janela:
                            if _normalizar_texto_tela(
                                    str(vizinho)) == obs_norm:
                                checkpoint_lag_imediato = True
                                break
                        else:
                            divergence = True

            timeout = False
            eof = False
            error_code: str | None = None
            saida = b""
            started_ns = time.monotonic_ns()
            try:
                if dados:
                    proc.stdin.write(dados)
                    proc.stdin.flush()
                saida, timeout, eof = self._drain_until_stable(
                    proc, session["tail"])
            except (OSError, BrokenPipeError):
                error_code = "session_io_error"
            finished_ns = time.monotonic_ns()

            # EOF: o lado remoto encerrou a sessão (ex.: a jornada sai do ERP
            # e dá logout no shell). É fim NATURAL da jornada, não erro de I/O.
            if eof and error_code is None:
                error_code = "session_closed"

            # Checkpoint de SIG (legado: jornadas sem screen_raw) — apenas
            # quando NÃO houve checkpoint de texto neste passo. O
            # _compute_text_sig já alimenta a engine com a saída.
            esperado_sig = ""
            if check_kind != "text":
                esperado_sig = str(
                    step.get("expected_screen_sig") or step.get("screen_sig")
                    or "")
            if esperado_sig and saida and not timeout and error_code is None:
                observado = self._compute_text_sig(session, saida)
                if observado is not None:
                    # evidência auditável de que a comparação ACONTECEU
                    checked = True
                    check_kind = "sig"
                    observado_str = observado
                    esperado_str = esperado_sig
                    if observado != esperado_sig:
                        divergence = True
            elif saida:
                # sem checkpoint de sig: ainda assim alimenta a engine para
                # manter o espelho da tela real (checkpoints seguintes)
                self._feed_terminal(session, saida)

            success = not timeout and error_code is None
            amostra = OperationSample(
                experiment_id=self.contract.experiment_id,
                environment_id=self.env.environment_id,
                iteration=self._iteration,
                concurrency=self._concurrency,
                virtual_user_id=session["virtual_user_id"],
                journey_id=journey_id,
                step_id=step_id,
                phase=phase,
                started_ns=started_ns,
                finished_ns=finished_ns,
                latency_ms=(finished_ns - started_ns) / 1_000_000.0,
                success=success,
                timeout=timeout,
                functional_divergence=divergence,
                error_code=error_code,
                screen_sig_checked=checked,
                expected_screen_sig=esperado_str,
                observed_screen_sig=observado_str,
                screen_check_kind=check_kind,
                screen_check_basis=check_basis if checked else "",
                checkpoint_lag=checkpoint_lag_imediato,
            )
            self._samples.append(amostra)
            produzidas.append(amostra)

            # FASE 4 — cobertura da verificação funcional: todo passo com
            # expectativa de tela (texto ou sig) é um checkpoint EXECUTADO;
            # quando não checado, a razão fica registrada para auditoria
            # (a decisão exige cobertura 100% ou exceções com razão).
            if texto_esperado or esperado_sig:
                if checked:
                    motivo = ""
                elif engine_indisponivel:
                    motivo = "terminal_engine_unavailable"
                elif error_code:
                    motivo = error_code
                elif timeout:
                    motivo = "timeout"
                elif not saida:
                    motivo = "sem_resposta"
                else:
                    motivo = "nao_checado"
                self.checkpoint_log.append({
                    "phase": phase,
                    "journey_id": journey_id,
                    "step_id": step_id,
                    "checked": checked,
                    "reason": motivo,
                    "ts_ms": int(time.time() * 1000),
                })

            # CIRCUIT BREAKER: sessão morta/fechada → ABORTA a jornada agora.
            # Os steps restantes NÃO são executados (e não viram amostras) —
            # ficam registrados em journey_incompletions para auditoria.
            if error_code in ("session_io_error", "session_closed"):
                restantes = len(steps) - idx - 1
                if restantes > 0:
                    self.journey_incompletions.append({
                        "journey_id": journey_id,
                        "phase": phase,
                        "virtual_user_id": session["virtual_user_id"],
                        "executed_steps": idx + 1,
                        "skipped_steps": restantes,
                        "total_steps": len(steps),
                        "reason": error_code,
                        "ts_ms": int(time.time() * 1000),
                    })
                break
        self._rebaixar_skew_segmentacao(produzidas)
        self._rebaixar_lag_checkpoint(produzidas)
        return produzidas

    @staticmethod
    def _rebaixar_lag_checkpoint(amostras: list[OperationSample]) -> None:
        """Rebaixa lags transitórios de checkpoint de texto (§16).

        O ERP faz atualizações longas com silêncio maior que o stable_ms do
        drain (ex.: "aguarde. atualizando dados..." buscando dados ISAM):
        o checkpoint i é comparado no meio da atualização (uma tela atrás
        ou, na captura, à frente por type-ahead) e diverge — mas o PRÓXIMO
        CHECKPOINT reconverge, provando que o caminho é o mesmo.

        Regra: divergência de TEXTO no passo i só é rebaixada para
        ``checkpoint_lag`` quando o próximo passo VERIFICADO (checkpoint,
        não qualquer passo — passo sem checkpoint não prova nada) casou.
        Divergência no último checkpoint, ou seguida de outra divergência,
        permanece divergência funcional (porta 1).
        """
        for idx, atual in enumerate(amostras):
            if not atual.functional_divergence:
                continue
            if atual.screen_check_kind != "text":
                continue
            for proximo in amostras[idx + 1:]:
                if not proximo.screen_sig_checked:
                    continue  # passo sem checkpoint não prova reconvergência
                if not proximo.functional_divergence:
                    atual.functional_divergence = False
                    atual.checkpoint_lag = True
                break

    @staticmethod
    def _rebaixar_skew_segmentacao(amostras: list[OperationSample]) -> None:
        """Rebaixa divergências causadas por skew de segmentação (type-ahead).

        Capturas com digitação à frente (operador digita antes da tela
        terminar de desenhar) cortam a resposta entre dois segmentos: a sig
        esperada do passo i fica sem a cauda da resposta, enquanto o replay
        (que aguarda a tela estabilizar) a vê inteira no passo i. O passo i
        diverge, mas o passo i+1 CONVERGE — as duas engines acumularam o
        byte stream completo até ali (checagem mais forte que o prefixo).

        Regra: a divergência do passo i só é rebaixada para
        ``segmentation_skew`` quando é de SIG (reconstrução suscetível ao
        corte) E o passo i+1 foi VERIFICADO e casou. Divergência de TEXTO
        (checkpoint quiet point, ground truth) é REAL e NUNCA é rebaixada;
        divergência persistente e o último passo da jornada também não.
        """
        for idx in range(len(amostras) - 1):
            atual, proximo = amostras[idx], amostras[idx + 1]
            if (atual.functional_divergence
                    and atual.screen_check_kind == "sig"
                    and proximo.screen_sig_checked
                    and not proximo.functional_divergence):
                atual.functional_divergence = False
                atual.segmentation_skew = True

    @staticmethod
    def _step_bytes(step: dict) -> bytes:
        """Bytes exatos do input do passo (key_b64 > key_text > payload)."""
        if step.get("key_b64"):
            return base64.b64decode(step["key_b64"])
        if step.get("key_text") is not None:
            return str(step["key_text"]).encode("utf-8")
        if step.get("payload") is not None:
            return str(step["payload"]).encode("utf-8")
        return b""

    def _drain_until_stable(self, proc, tail: bytearray | None = None,
                            timeout_s: float | None = None) -> tuple[bytes, bool, bool]:
        """Lê a saída até ficar estável (sem bytes por ``stable_ms``).

        Devolve ``(saida, timed_out, eof)``: ``timed_out=True`` quando a saída
        não estabiliza dentro do timeout; ``eof=True`` quando o lado remoto
        ENCERROU a sessão (ex.: logout do shell ao fim da jornada). Quando
        ``tail`` é informado, acumula os últimos ``_TAIL_MAX_BYTES`` da saída
        para o log forense do run.
        """
        fd = proc.stdout.fileno()
        chunks: list[bytes] = []
        deadline = time.monotonic() + (timeout_s or self.step_timeout_s)
        timed_out = False
        eof = False
        while True:
            restante = deadline - time.monotonic()
            if restante <= 0:
                timed_out = True
                break
            janela = min(self.stable_ms / 1000.0, restante)
            prontos, _, _ = select.select([fd], [], [], janela)
            if not prontos:
                # a janela de estabilidade pode ter consumido o restante até
                # o deadline: nesse caso é timeout, não tela estável
                if time.monotonic() >= deadline:
                    timed_out = True
                break  # tela estável: nenhum byte novo dentro da janela
            try:
                chunk = os.read(fd, 65536)
            except OSError:
                eof = True
                break
            if not chunk:
                eof = True
                break  # EOF: sessão encerrada pelo lado remoto
            chunks.append(chunk)
            if tail is not None:
                tail.extend(chunk)
                if len(tail) > _TAIL_MAX_BYTES:
                    del tail[:len(tail) - _TAIL_MAX_BYTES]
        return b"".join(chunks), timed_out, eof

    def _ensure_terminal(self, session: dict):
        """Engine canônica da sessão — cria se ausente e retro-alimenta.

        Na criação, a engine recebe TODO o stream já acumulado no tail da
        sessão (banner, preamble, drenos anteriores): ela passa a espelhar
        a tela REAL no ponto em que a jornada começa. Sem isso, checkpoints
        de texto comparariam uma tela vazia/parcial (falso divergente).
        Devolve ``None`` quando a engine canônica não está disponível.
        """
        engine = session.get("terminal")
        if engine is not None:
            return engine
        try:
            from dakota_terminal.engine import TerminalEngine
        except Exception:
            return None
        cap = session.get("capture_terminal") or {}
        if cap.get("rows") and cap.get("cols"):
            rows, cols = int(cap["rows"]), int(cap["cols"])
            term = str(cap.get("term") or "xterm")
        else:
            rows, cols = self._terminal_geometry()
            term = "xterm"
        try:
            engine = TerminalEngine(rows=rows, cols=cols, term=term)
        except Exception:
            return None
        session["terminal"] = engine
        tail = bytes(session.get("tail") or b"")
        if tail:
            try:
                engine.feed_bytes(tail, direction="out")
            except Exception:
                pass
        return engine

    def _feed_terminal(self, session: dict, saida: bytes) -> None:
        """Alimenta a engine da sessão com saída nova (espelho da tela)."""
        if not saida:
            return
        engine = self._ensure_terminal(session)
        if engine is not None:
            try:
                engine.feed_bytes(saida, direction="out")
            except Exception:
                pass

    def _compute_text_sig(self, session: dict, saida: bytes) -> str | None:
        """Assinatura de texto da tela via engine canônica (dakota_terminal).

        Alimenta a engine com ``saida`` e devolve a ``text_sig`` do estado
        resultante. Retorna ``None`` quando a engine não está disponível —
        nesse caso a validação de tela simplesmente não é aplicada (jamais
        fingida).
        """
        try:
            from dakota_terminal.signatures import text_sig
        except Exception:
            return None
        engine = self._ensure_terminal(session)
        if engine is None:
            return None
        try:
            engine.feed_bytes(saida, direction="out")
            return text_sig(engine.snapshot())
        except Exception:
            return None

    def _terminal_geometry(self) -> tuple[int, int]:
        """(rows, cols) a partir de ``terminal_geometry`` do contrato ("80x24")."""
        try:
            cols_s, rows_s = str(self.contract.terminal_geometry).lower().split("x")
            return int(rows_s), int(cols_s)
        except (ValueError, AttributeError):
            return 24, 80

    # -- coletores ---------------------------------------------------------

    def collect_application_metrics(self) -> dict:
        """Amostras reais acumuladas, separadas por fase."""
        return {
            "ok": True,
            "samples": list(self._samples),
            "measurement_samples": [s for s in self._samples if s.phase == "MEASUREMENT"],
            "warmup_samples": [s for s in self._samples if s.phase == "WARMUP"],
            "cooldown_samples": [s for s in self._samples if s.phase == "COOLDOWN"],
        }

    def collect_host_metrics(self, from_ms: int, to_ms: int) -> list[dict]:
        """Consulta ``host_metrics`` da replay.db REMOTA via ssh (§13).

        Canal dedicado opcional: quando o ambiente define
        ``metrics_ssh_user``/``metrics_remote_cmd`` (ex.: o login do endpoint
        está sob ForceCommand de captura e não alcança a replay.db), a coleta
        usa esse usuário/comando — o replay da jornada NÃO é afetado.

        RETRY contra falha de TRANSPORTE (até ``_HOST_METRICS_MAX_ATTEMPTS``
        tentativas, backoff ``_HOST_METRICS_BACKOFF_S``): exceção no ssh,
        rc!=0, stdout vazio ou JSON inválido/truncado disparam nova tentativa
        — o sshd do AIX (ForceCommand + VPN com perda) falha assim de forma
        intermitente. Resposta VÁLIDA da query nunca é re-tentada, inclusive
        janela com 0 amostras (confirmada pela linha sentinela do script
        remoto — sem ela, stdout vazio é indistinguível de perda de saída).

        Esgotadas as tentativas, a indisponibilidade é reportada em
        ``self.host_metrics_status`` como ``{"available": False, "reason":
        <última falha>, "attempts": N}`` e a lista devolvida é vazia —
        NUNCA zero fingindo medição. Sucesso registra ``{"available": True,
        "attempts": N, "clock_offset_ms": <offset medido>}``.

        CLOCK SKEW (caso real MIG24: AIX ~171 s atrasado): a janela
        ``from_ms``/``to_ms`` usa o relógio do orquestrador, mas o sampler
        remoto grava ``ts_ms`` com o relógio do host. O script remoto mede o
        offset no momento da query e desloca a janela — ver o comentário de
        ``_REMOTE_HOST_METRICS_SCRIPT``.
        """
        db_path = self.env.replay2_db_path or "/opt/dakota/replay2/gateway/state/replay.db"
        script = (_REMOTE_HOST_METRICS_SCRIPT
                  .replace("__DB_PATH__", db_path)
                  .replace("__FROM_MS__", str(int(from_ms)))
                  .replace("__TO_MS__", str(int(to_ms)))
                  .replace("__LOCAL_NOW_MS__", str(int(time.time() * 1000))))
        metrics_user = getattr(self.env, "metrics_ssh_user", "") or ""
        metrics_cmd = (getattr(self.env, "metrics_remote_cmd", "")
                       or self.remote_python_cmd)
        argv = self._ssh_base_argv(user_override=metrics_user) + [metrics_cmd]
        ultima_falha = "ssh_failed"
        attempts = 0
        for tentativa in range(_HOST_METRICS_MAX_ATTEMPTS):
            attempts = tentativa + 1
            if tentativa > 0:
                time.sleep(_HOST_METRICS_BACKOFF_S[
                    min(tentativa - 1, len(_HOST_METRICS_BACKOFF_S) - 1)])
            try:
                res = self._ssh_runner(argv, script, 30.0)
            except Exception as exc:
                ultima_falha = str(exc)[:300]
                continue
            if getattr(res, "returncode", 1) != 0:
                ultima_falha = str(
                    getattr(res, "stderr", "") or "ssh_failed")[:300]
                continue
            stdout = str(getattr(res, "stdout", "") or "")
            if not stdout.strip():
                # rc=0 sem NENHUMA linha: a query não confirmou execução
                # (a sentinela sempre é impressa) → saída perdida no transporte
                ultima_falha = "transporte_vazio: rc=0 mas stdout vazio"
                continue
            amostras: list[dict] = []
            esperadas: object = None
            offset_ms: object = None
            try:
                for linha in stdout.splitlines():
                    linha = linha.strip()
                    if not linha:
                        continue
                    dado = json.loads(linha)
                    if (isinstance(dado, dict)
                            and dado.get("host_metrics_query") == "done"):
                        esperadas = dado.get("rows")
                        offset_ms = dado.get("clock_offset_ms")
                        continue
                    dado["host_id"] = self.env.host
                    dado["platform"] = self.env.platform
                    dado["architecture"] = self.env.architecture
                    amostras.append(dado)
            except (ValueError, TypeError) as exc:
                ultima_falha = f"host_metrics_parse_error: {exc}"[:300]
                continue
            if esperadas is not None:
                # sentinela presente: confere truncamento do transporte
                try:
                    if int(esperadas) != len(amostras):
                        ultima_falha = (
                            f"transporte_truncado: {len(amostras)} de "
                            f"{esperadas} amostras recebidas")[:300]
                        continue
                except (TypeError, ValueError):
                    pass
            self.host_metrics_status = {"available": True, "attempts": attempts}
            if offset_ms is not None:
                try:
                    self.host_metrics_status["clock_offset_ms"] = int(offset_ms)
                except (TypeError, ValueError):
                    pass
            return amostras
        self.host_metrics_status = {
            "available": False, "reason": ultima_falha, "attempts": attempts,
        }
        return []

    def collect_database_metrics(self) -> dict:
        """§14 — sem coletor de banco para arquivos Recital/ISAM."""
        return {"available": False, "reason": "collector_not_supported"}

    def collect_net_counters(self) -> dict | None:
        """Contadores ABSOLUTOS de rede do host remoto (best-effort, FASE 3).

        O sampler de host (``host_metrics``) não instrumenta rede; a
        cobertura do grupo "rede" vem da leitura dos contadores antes/depois
        das fases da run (taxas = delta/tempo — ver
        ``BenchmarkExecutor._net_window``). Linux lê ``/proc/net/dev``
        (bytes+pacotes das interfaces não-lo); AIX cai para ``netstat -i``
        (pacotes — Ipkts/Opkts). Falha de transporte ou parse devolve None:
        sem janela de rede a cobertura marca o grupo como AUSENTE — nunca
        zero fingindo medição.
        """
        metrics_user = getattr(self.env, "metrics_ssh_user", "") or ""
        argv = self._ssh_base_argv(user_override=metrics_user) + ["sh"]
        try:
            res = self._ssh_runner(argv, _REMOTE_NET_COUNTERS_SCRIPT, 20.0)
        except Exception:
            return None
        if getattr(res, "returncode", 1) != 0:
            return None
        stdout = str(getattr(res, "stdout", "") or "")
        for linha in stdout.splitlines():
            linha = linha.strip()
            if not linha:
                continue
            try:
                dado = json.loads(linha)
            except ValueError:
                continue
            if isinstance(dado, dict) and any(
                    isinstance(dado.get(k), (int, float))
                    for k in ("rx_bytes", "tx_bytes",
                              "rx_packets", "tx_packets")):
                return dado
        return None


#: Script Python executado no host remoto (via stdin do ssh) para extrair as
#: amostras de host_metrics da janela temporal da run. Campos indisponíveis
#: na plataforma já vêm NULL da tabela — nunca são preenchidos com zero.
#: A linha final ``host_metrics_query: done`` é a SENTINELA que confirma ao
#: coletor que a query executou (sem ela, stdout vazio por perda de
#: transporte seria indistinguível de uma janela válida com 0 amostras) e
#: permite detectar saída truncada (rows != linhas de amostra recebidas).
#:
#: COMPENSAÇÃO DE CLOCK SKEW: a janela [from_ms, to_ms] é medida no relógio
#: do ORQUESTRADOR, mas ``host_metrics.ts_ms`` é gravado pelo sampler com o
#: relógio do host REMOTO. Caso real (MIG24): o AIX estava ~171 s ATRASADO
#: e a janela nominal capturava só as amostras anteriores à run. O script
#: mede ``offset = remote_now - local_now`` no momento da query e desloca a
#: janela por esse offset; o offset medido volta na sentinela
#: (``clock_offset_ms``) e é registrado em ``host_metrics_status`` e no
#: execution-result.json da run, para auditoria. As amostras mantêm o
#: ``ts_ms`` original do host (evidência bruta, sem reescrita).
_REMOTE_HOST_METRICS_SCRIPT = (
    "import json, sqlite3, time\n"
    "offset = int(time.time() * 1000) - __LOCAL_NOW_MS__\n"
    "con = sqlite3.connect('__DB_PATH__')\n"
    "con.row_factory = sqlite3.Row\n"
    "try:\n"
    "    rows = con.execute(\n"
    "        'SELECT * FROM host_metrics WHERE ts_ms BETWEEN ? AND ? ORDER BY ts_ms',\n"
    "        (__FROM_MS__ + offset, __TO_MS__ + offset)).fetchall()\n"
    "except Exception as exc:\n"
    "    print(json.dumps({'error': str(exc)}))\n"
    "    rows = []\n"
    "for row in rows:\n"
    "    print(json.dumps(dict(row)))\n"
    "print(json.dumps({'host_metrics_query': 'done', 'rows': len(rows),\n"
    "                  'clock_offset_ms': offset}))\n"
)


#: Script shell (POSIX, roda via ``sh`` no stdin do ssh) que emite UMA linha
#: JSON com os contadores absolutos de rede do host. Linux: ``/proc/net/dev``
#: (rx/tx bytes+pacotes das interfaces não-lo — colunas 2/3 e 10/11 após o
#: nome da interface). AIX (sem /proc): ``netstat -i`` (Ipkts=$5, Opkts=$7,
#: sem bytes). A fonte fica registrada no próprio JSON para auditoria.
_REMOTE_NET_COUNTERS_SCRIPT = (
    "if [ -r /proc/net/dev ]; then\n"
    "awk 'NR>2 { nome=$1; sub(/:$/, \"\", nome); if (nome != \"lo\") "
    "{ rx+=$2; rxp+=$3; tx+=$10; txp+=$11 } } END { printf "
    "\"{\\\"rx_bytes\\\": %d, \\\"tx_bytes\\\": %d, "
    "\\\"rx_packets\\\": %d, \\\"tx_packets\\\": %d, "
    "\\\"fonte\\\": \\\"/proc/net/dev\\\"}\\n\", rx, tx, rxp, txp }' "
    "/proc/net/dev\n"
    "else\n"
    "netstat -i 2>/dev/null | awk 'NR>1 && $1 !~ /^lo/ { rxp+=$5; "
    "txp+=$7 } END { printf \"{\\\"rx_packets\\\": %d, "
    "\\\"tx_packets\\\": %d, \\\"fonte\\\": \\\"netstat -i\\\"}\\n\", "
    "rxp, txp }'\n"
    "fi\n"
)


__all__ = [
    "EnvironmentExecutionAdapter",
    "SSHReplayAdapter",
]

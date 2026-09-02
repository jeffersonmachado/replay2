"""Cliente do daemon de captura (sink remoto de auditoria).

Usado pelo ``capture-session``, que roda como o usuário SSH final e não tem
acesso à chave HMAC nem ao banco de estado. Espelha a interface do
:class:`~dakota_gateway.audit_writer.AuditWriter` (``append``/``close``) para
ser injetado como sink plugável no ``TerminalGateway``.

A conexão AF_UNIX é persistente (uma por sessão de captura) e protegida por
lock; em caso de queda (daemon reiniciado), tenta uma reconexão antes de
desistir — o daemon reconstrói o estado da hash-chain a partir do log
(self-healing do AuditWriter), então a continuidade é preservada.
"""

from __future__ import annotations

import json
import socket
import threading
import time
from dataclasses import asdict
from pathlib import Path

from .schema import AuditEvent

_EVENT_FIELDS = frozenset(AuditEvent.__dataclass_fields__)


class AuditClientError(RuntimeError):
    """Falha de comunicação com o capture-daemon."""


class AuditClient:
    """Sink de auditoria que delega hash-chain/HMAC/escrita ao daemon."""

    def __init__(self, socket_path: str, log_dir: str, timeout: float = 10.0):
        self.socket_path = str(socket_path)
        self.log_dir = str(log_dir)
        self.timeout = float(timeout)
        self._lock = threading.Lock()
        self._sock: socket.socket | None = None
        self._wfile = None
        self._rfile = None
        self._connect()

    # -- conexão ---------------------------------------------------------

    def _connect(self) -> None:
        self._close_quiet()
        # Sob concorrência o backlog do daemon pode encher e o connect AF_UNIX
        # falha com EAGAIN (BlockingIOError) — transitório: retry com backoff
        # dentro do orçamento do timeout em vez de derrubar o append.
        deadline = time.monotonic() + self.timeout
        tentativa = 0
        while True:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                sock.settimeout(self.timeout)
                sock.connect(self.socket_path)
                break
            except BlockingIOError:
                sock.close()
                if time.monotonic() >= deadline:
                    raise
                time.sleep(min(0.05 * (2 ** tentativa), 0.4))
                tentativa += 1
            except OSError:
                sock.close()
                raise
        self._sock = sock
        self._wfile = sock.makefile("wb")
        self._rfile = sock.makefile("rb")

    def _close_quiet(self) -> None:
        for fh in (self._rfile, self._wfile):
            try:
                if fh is not None:
                    fh.close()
            except OSError:
                pass
        try:
            if self._sock is not None:
                self._sock.close()
        except OSError:
            pass
        self._sock = None
        self._wfile = None
        self._rfile = None

    def close(self) -> None:
        with self._lock:
            self._close_quiet()

    # -- protocolo --------------------------------------------------------

    def _request(self, req: dict):
        """Envia uma requisição e aguarda a resposta (com 1 reconexão)."""
        raw = json.dumps(req, ensure_ascii=False).encode("utf-8") + b"\n"
        last_exc: OSError | None = None
        for attempt in (0, 1):
            try:
                self._wfile.write(raw)
                self._wfile.flush()
                line = self._rfile.readline()
                if not line:
                    raise OSError("daemon fechou a conexão")
                return json.loads(line.decode("utf-8"))
            except OSError as exc:
                last_exc = exc
                if attempt == 0:
                    self._connect()
        raise AuditClientError(f"capture-daemon indisponível: {last_exc}")

    def request(self, req: dict) -> dict:
        """Requisição pública thread-safe (usada também para resolve/ping)."""
        with self._lock:
            return self._request(req)

    # -- interface de sink (compatível com AuditWriter) -------------------

    def append(self, ev: AuditEvent) -> AuditEvent:
        """Envia o evento ao daemon e aplica os campos assinados de volta.

        ``seq_global``/``prev_hash``/``hash``/``hmac`` são calculados pelo
        daemon; o evento local é atualizado por referência, como faz o
        ``AuditWriter.append``.
        """
        return self.append_many([ev])[0]

    def append_many(self, events: list[AuditEvent]) -> list[AuditEvent]:
        """Envia um lote ao daemon (tudo-ou-nada) e aplica as assinaturas.

        Confirmação individual: cada evento local é atualizado por
        referência com os campos assinados, na mesma ordem do envio.
        """
        events = list(events)
        if not events:
            raise ValueError("append_many exige ao menos um evento")
        payload = [asdict(ev) for ev in events]
        resp = self.request({"op": "append_many", "log_dir": self.log_dir, "events": payload})
        if not resp.get("ok"):
            raise AuditClientError(f"append_many recusado pelo daemon: {resp.get('error')}")
        signed_list = resp.get("events") or []
        if len(signed_list) != len(events):
            raise AuditClientError(
                f"append_many: daemon confirmou {len(signed_list)} de {len(events)} eventos"
            )
        for ev, signed in zip(events, signed_list):
            for key, value in signed.items():
                if key in _EVENT_FIELDS:
                    setattr(ev, key, value)
        return events


def daemon_request(socket_path: str, req: dict, timeout: float = 5.0) -> dict | None:
    """Requisição pontual ao daemon (conexão aberta e fechada).

    Retorna o dict de resposta ou ``None`` se o daemon estiver
    indisponível (socket ausente, recusado, timeout, resposta inválida).
    """
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.settimeout(timeout)
            sock.connect(str(socket_path))
            raw = json.dumps(req, ensure_ascii=False).encode("utf-8") + b"\n"
            sock.sendall(raw)
            chunks = []
            while True:
                chunk = sock.recv(65536)
                if not chunk:
                    break
                chunks.append(chunk)
                if b"\n" in chunk:
                    break
        finally:
            sock.close()
        line = b"".join(chunks).split(b"\n", 1)[0]
        if not line:
            return None
        resp = json.loads(line.decode("utf-8"))
        return resp if isinstance(resp, dict) else None
    except (OSError, ValueError, UnicodeDecodeError):
        return None


def daemon_resolve(socket_path: str, capture_id: int = 0, timeout: float = 5.0) -> dict | None:
    """Resolve a captura ativa via daemon; ``None`` se inativa/indisponível."""
    socket_path = str(socket_path or "").strip()
    if not socket_path or not Path(socket_path).exists():
        return None
    resp = daemon_request(socket_path, {"op": "resolve", "capture_id": int(capture_id or 0)}, timeout)
    if resp and resp.get("ok"):
        return resp
    return None

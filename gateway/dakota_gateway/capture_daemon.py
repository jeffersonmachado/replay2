"""Daemon privilegiado de captura (AF_UNIX).

Roda como o usuário de serviço (ex.: ``results``) e centraliza tudo o que
exige privilégio na captura de sessões SSH:

- leitura da chave HMAC e assinatura dos eventos (hash-chain + HMAC);
- escrita dos arquivos de auditoria (``audit-*.jsonl``, ``audit.state``,
  manifests) via :class:`~dakota_gateway.audit_writer.AuditWriter`;
- resolução da captura ativa no SQLite (``capture_sessions``).

Os processos ``capture-session`` (que rodam como o usuário SSH final) ficam
responsáveis apenas pelo PTY/proxy loop e enviam os eventos prontos por um
socket de domínio Unix. Assim a chave HMAC pode voltar a ``0600`` e o banco
de estado a ``0660``, sem depender do grupo do usuário.

Protocolo: JSON-lines, uma requisição por linha, uma resposta por linha.

- ``{"op": "ping"}`` → ``{"ok": true, "pong": true}``
- ``{"op": "resolve", "capture_id": 0}`` → dados da captura ativa
  (``ok: false, error: "no_active_capture"`` quando não há);
- ``{"op": "append", "log_dir": "...", "event": {...}}`` → evento assinado
  em ``{"ok": true, "event": {...}}`` (``seq_global``/``prev_hash``/``hash``/
  ``hmac`` sempre (re)calculados pelo daemon — valores do cliente ignorados);
- ``{"op": "append_many", "log_dir": "...", "events": [{...}, ...]}`` → lote
  assinado em ``{"ok": true, "events": [...]}`` (tudo-ou-nada por requisição;
  máx. ``MAX_BATCH_EVENTS`` eventos).

Escrita em fila (FASE 7): cada captura (log_dir) tem UMA fila com UMA thread
de escrita (single-writer por captura). As threads do servidor apenas
enfileiram e bloqueiam à espera da confirmação individual; a thread de
escrita drena o que estiver pendente e grava num único ``append_many`` —
a ordem global é a ordem FIFO de chegada na fila (determinística) e o
checkpoint do ``audit.state`` acontece uma vez por drenagem. O agrupamento
não muda a semântica dos eventos: cada evento continua assinado e
confirmado individualmente; eventos do mesmo input (ex.: tela estável +
input) já chegam combinados num único ``deterministic_input`` do cliente,
então nenhum agrupamento semântico extra é necessário — só o de transporte.

Portável para AIX 7: apenas stdlib (``socketserver`` AF_UNIX).
"""

from __future__ import annotations

import json
import os
import queue
import signal
import socketserver
import threading
from pathlib import Path

from .audit_writer import AuditWriter
from .db.connection import connect as db_connect
from .db.connection import default_db_path
from .schema import AuditEvent

# Eventos carregam payloads grandes (screen_raw_b64 de telas 25x80+, diffs);
# 4 MiB por linha é folga suficiente sem abrir margem para abuso.
MAX_LINE_BYTES = 4 * 1024 * 1024

# Teto de eventos por requisição append_many (proteção contra abuso; a fila
# por captura aceita qualquer volume em requisições separadas).
MAX_BATCH_EVENTS = 512

# Teto de eventos fundidos numa única drenagem da fila de escrita.
MAX_DRAIN_EVENTS = 1024

_EVENT_FIELDS = frozenset(AuditEvent.__dataclass_fields__)


def default_socket_path(db_path: str = "") -> str:
    """Socket padrão: ``<state dir>/daemon/capture.sock`` ao lado do replay.db."""
    path = db_path or default_db_path()
    return str(Path(path).resolve().parent / "daemon" / "capture.sock")


def event_from_dict(data: dict) -> AuditEvent:
    """Reconstrói um AuditEvent a partir do dict recebido do cliente.

    Campos desconhecidos são descartados; os campos de integridade
    (``seq_global``, ``prev_hash``, ``hash``, ``hmac``) são zerados porque
    quem os calcula é o AuditWriter do daemon.
    """
    clean = {k: v for k, v in data.items() if k in _EVENT_FIELDS}
    clean["seq_global"] = 0
    clean["prev_hash"] = ""
    clean["hash"] = ""
    clean["hmac"] = ""
    return AuditEvent(**clean)


def resolve_capture(db_path: str, capture_id: int = 0) -> dict | None:
    """Resolve a captura ativa (mesma regra de ``runtime._resolve_capture_session``).

    Retorna ``{"id", "session_uuid", "log_dir"}`` ou ``None`` quando não há
    captura ativa. Garante que o log_dir existe (criado como o usuário do
    daemon, dono dos arquivos de auditoria).
    """
    con = db_connect(db_path or default_db_path())
    try:
        if int(capture_id or 0) > 0:
            row = con.execute(
                "SELECT id, session_uuid, log_dir FROM capture_sessions WHERE id=?",
                (int(capture_id),),
            ).fetchone()
        else:
            row = con.execute(
                "SELECT id, session_uuid, log_dir FROM capture_sessions WHERE status='active' ORDER BY id DESC LIMIT 1"
            ).fetchone()
        if not row:
            return None
        log_dir = str(row["log_dir"] or "").strip()
        if not log_dir:
            return None
        os.makedirs(log_dir, exist_ok=True)
        return {
            "id": int(row["id"] or 0),
            "session_uuid": str(row["session_uuid"] or "").strip(),
            "log_dir": log_dir,
        }
    finally:
        con.close()


class _QueueItem:
    """Uma requisição de escrita enfileirada (1+ eventos) + confirmação."""

    __slots__ = ("events", "done", "signed", "error")

    def __init__(self, events: list[AuditEvent]):
        self.events = events
        self.done = threading.Event()
        self.signed: list[AuditEvent] | None = None
        self.error: BaseException | None = None

    def set_result(self, signed: list[AuditEvent]) -> None:
        self.signed = signed
        self.done.set()

    def set_error(self, exc: BaseException) -> None:
        self.error = exc
        self.done.set()

    def result(self) -> list[AuditEvent]:
        self.done.wait()
        if self.error is not None:
            raise self.error
        return self.signed or []


class _CaptureWriter:
    """Fila single-writer de UMA captura: uma thread drena e grava em lotes.

    A hash-chain é por captura, então basta UMA thread de escrita por
    log_dir: ela é a única que toca no AuditWriter, o que preserva a ordem
    global (FIFO de chegada na fila) e permite fundir eventos pendentes num
    único ``append_many`` (1 checkpoint de state por drenagem).
    """

    def __init__(self, log_dir: str, hmac_key: bytes):
        self.log_dir = log_dir
        self.writer = AuditWriter(log_dir, hmac_key)
        self._queue: queue.Queue = queue.Queue()
        self._closed = False
        self._thread = threading.Thread(
            target=self._run, name=f"audit-writer-{Path(log_dir).name}", daemon=True
        )
        self._thread.start()

    def submit(self, events: list[AuditEvent]) -> list[AuditEvent]:
        """Enfileira e bloqueia até a confirmação individual dos eventos."""
        if self._closed:
            raise RuntimeError("writer da captura encerrado")
        item = _QueueItem(events)
        self._queue.put(item)
        return item.result()

    def _run(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:  # sentinel de encerramento
                return
            batch = [item]
            total = len(item.events)
            while total < MAX_DRAIN_EVENTS:
                try:
                    nxt = self._queue.get_nowait()
                except queue.Empty:
                    break
                if nxt is None:  # sentinel: reprocessa após o lote
                    self._queue.put(None)
                    break
                batch.append(nxt)
                total += len(nxt.events)
            self._write_batch(batch)

    def _write_batch(self, batch: list[_QueueItem]) -> None:
        events = [ev for item in batch for ev in item.events]
        try:
            signed = self.writer.append_many(events)
        except Exception:
            # fallback por requisição: um lote ruim não derruba os vizinhos
            # (o append_many fundido é tudo-ou-nada; isolando, cada
            # requisição mantém sua própria semântica tudo-ou-nada).
            for item in batch:
                try:
                    item.set_result(self.writer.append_many(item.events))
                except Exception as exc:
                    item.set_error(exc)
            return
        pos = 0
        for item in batch:
            item.set_result(signed[pos : pos + len(item.events)])
            pos += len(item.events)

    def close(self) -> None:
        """Sinaliza encerramento, drena o pendente e fecha o writer."""
        if self._closed:
            return
        self._closed = True
        self._queue.put(None)
        self._thread.join(timeout=30)
        self.writer.close()


class _WriterCache:
    """Um _CaptureWriter (fila + thread) por log_dir.

    A hash-chain é por captura, então a serialização exigida é por log_dir —
    capturas distintas escrevem em paralelo. Cada captura tem sua fila com
    thread dedicada; o AuditWriter também se protege via lock interno +
    flock (``audit.lock``) contra escritores de outros processos.
    """

    def __init__(self, hmac_key: bytes):
        self._hmac_key = hmac_key
        self._writers: dict[str, _CaptureWriter] = {}
        self._guard = threading.Lock()
        self._closed = False

    def _get(self, log_dir: str) -> _CaptureWriter:
        with self._guard:
            if self._closed:
                # sem isso, uma conexão ainda viva após o shutdown recriava o
                # writer e gravava numa captura já encerrada
                raise RuntimeError("daemon encerrado: writers fechados")
            writer = self._writers.get(log_dir)
            if writer is None:
                writer = _CaptureWriter(log_dir, self._hmac_key)
                self._writers[log_dir] = writer
            return writer

    def append(self, log_dir: str, event: dict) -> AuditEvent:
        return self.append_many(log_dir, [event])[0]

    def append_many(self, log_dir: str, events: list[dict]) -> list[AuditEvent]:
        parsed = [event_from_dict(e) for e in events]
        return self._get(log_dir).submit(parsed)

    def close_all(self) -> None:
        with self._guard:
            self._closed = True
            entries = list(self._writers.values())
            self._writers.clear()
        for writer in entries:
            writer.close()


class _Handler(socketserver.StreamRequestHandler):
    """Atende uma conexão do cliente: loop de requisições JSON-lines."""

    def handle(self) -> None:
        while True:
            line = self.rfile.readline(MAX_LINE_BYTES + 1)
            if not line:
                return
            if len(line) > MAX_LINE_BYTES:
                self._respond({"ok": False, "error": "request_too_large"})
                return
            try:
                req = json.loads(line.decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                self._respond({"ok": False, "error": "invalid_json"})
                continue
            self._respond(self._dispatch(req))

    def _respond(self, resp: dict) -> None:
        self.wfile.write(json.dumps(resp, ensure_ascii=False).encode("utf-8") + b"\n")
        self.wfile.flush()

    def _dispatch(self, req: dict) -> dict:
        if not isinstance(req, dict):
            return {"ok": False, "error": "invalid_request"}
        op = str(req.get("op") or "")
        server: "CaptureDaemonServer" = self.server  # type: ignore[assignment]
        if op == "ping":
            return {"ok": True, "pong": True}
        if op == "resolve":
            try:
                capture = resolve_capture(server.db_path, int(req.get("capture_id") or 0))
            except Exception as exc:  # DB indisponível/corrompido: reporta, não derruba
                return {"ok": False, "error": f"resolve_failed: {exc}"}
            if capture is None:
                return {"ok": False, "error": "no_active_capture"}
            return {"ok": True, **capture}
        if op == "append":
            log_dir = str(req.get("log_dir") or "").strip()
            event = req.get("event")
            if not log_dir or not isinstance(event, dict):
                return {"ok": False, "error": "invalid_append"}
            try:
                ev = server.writers.append(log_dir, event)
            except Exception as exc:
                return {"ok": False, "error": f"append_failed: {exc}"}
            return {"ok": True, "event": _event_to_dict(ev)}
        if op == "append_many":
            log_dir = str(req.get("log_dir") or "").strip()
            events = req.get("events")
            if (
                not log_dir
                or not isinstance(events, list)
                or not events
                or not all(isinstance(e, dict) for e in events)
            ):
                return {"ok": False, "error": "invalid_append"}
            if len(events) > MAX_BATCH_EVENTS:
                return {"ok": False, "error": "too_many_events"}
            try:
                signed = server.writers.append_many(log_dir, events)
            except Exception as exc:
                return {"ok": False, "error": f"append_failed: {exc}"}
            return {"ok": True, "events": [_event_to_dict(ev) for ev in signed]}
        return {"ok": False, "error": "unknown_op"}


def _event_to_dict(ev: AuditEvent) -> dict:
    from dataclasses import asdict

    return asdict(ev)


class CaptureDaemonServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    """Servidor AF_UNIX multi-thread do daemon de captura."""

    daemon_threads = True

    def __init__(self, socket_path: str, db_path: str, hmac_key: bytes):
        self.socket_path = str(socket_path)
        self.db_path = db_path or default_db_path()
        self.writers = _WriterCache(hmac_key)
        self._prepare_socket()
        super().__init__(self.socket_path, _Handler)
        # Qualquer usuário local precisa conseguir conectar (a proteção real
        # está nos arquivos que só o daemon lê/escreve, não no socket).
        os.chmod(self.socket_path, 0o666)

    def _prepare_socket(self) -> None:
        parent = Path(self.socket_path).parent
        parent.mkdir(parents=True, exist_ok=True)
        # O diretório só precisa permitir travessia (o socket é criado pelo
        # próprio daemon); 0755 evita que outros usuários removam o socket.
        try:
            os.chmod(parent, 0o755)
        except OSError:
            pass
        try:
            os.unlink(self.socket_path)
        except FileNotFoundError:
            pass

    def cleanup(self) -> None:
        """Fecha writers e remove o socket (chamado no encerramento)."""
        self.writers.close_all()
        try:
            os.unlink(self.socket_path)
        except OSError:
            pass


def run_daemon(socket_path: str, db_path: str, hmac_key: bytes) -> int:
    """Sobe o daemon em foreground até receber SIGTERM/SIGINT."""
    server = CaptureDaemonServer(socket_path, db_path, hmac_key)

    def _request_shutdown(_signum, _frame) -> None:
        # shutdown() não pode rodar no handler do sinal (deadlock com
        # serve_forever na thread principal); dispara em thread auxiliar.
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, _request_shutdown)
    signal.signal(signal.SIGINT, _request_shutdown)
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        server.server_close()
        server.cleanup()
    return 0

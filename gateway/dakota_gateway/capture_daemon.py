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
  ``hmac`` sempre (re)calculados pelo daemon — valores do cliente ignorados).

Portável para AIX 7: apenas stdlib (``socketserver`` AF_UNIX).
"""

from __future__ import annotations

import json
import os
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


class _WriterCache:
    """Um AuditWriter por log_dir, com lock dedicado por captura.

    A hash-chain é por captura, então a serialização exigida é por log_dir —
    capturas distintas fazem append em paralelo. O AuditWriter já se protege
    contra escritores locais concorrentes via flock (``audit.lock``).
    """

    def __init__(self, hmac_key: bytes):
        self._hmac_key = hmac_key
        self._writers: dict[str, tuple[AuditWriter, threading.Lock]] = {}
        self._guard = threading.Lock()

    def append(self, log_dir: str, event: dict) -> AuditEvent:
        with self._guard:
            entry = self._writers.get(log_dir)
            if entry is None:
                entry = (AuditWriter(log_dir, self._hmac_key), threading.Lock())
                self._writers[log_dir] = entry
        writer, lock = entry
        ev = event_from_dict(event)
        with lock:
            return writer.append(ev)

    def close_all(self) -> None:
        with self._guard:
            entries = list(self._writers.values())
            self._writers.clear()
        for writer, _lock in entries:
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

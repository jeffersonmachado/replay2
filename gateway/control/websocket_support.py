"""
WebSocket minimalista para status em tempo real do gateway.
Implementa RFC 6455 parcial: handshake, text frames, ping/pong, close.
"""

from __future__ import annotations

import hashlib
import json
import select
import socket
import struct
import threading
import time
from http.server import BaseHTTPRequestHandler
from typing import Callable

WS_MAGIC = b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

# Limite de payload aceito por frame (64 KB) — protege contra frames 64-bit
# gigantes declarados pelo cliente.
WS_MAX_PAYLOAD_BYTES = 65536

# MSG_DONTWAIT (Linux/AIX): envio não-bloqueante sem alterar o timeout do
# socket — alterar settimeout afetaria a thread que lê frames na mesma conexão.
_WS_DONTWAIT = getattr(socket, "MSG_DONTWAIT", None)


def _accept_key(key: str) -> str:
    accept = hashlib.sha1(key.encode() + WS_MAGIC).digest()
    import base64
    return base64.b64encode(accept).decode()


def ws_handshake(handler: BaseHTTPRequestHandler) -> bool:
    """Tenta upgrade para WebSocket. Retorna True se fez upgrade."""
    upgrade = handler.headers.get("Upgrade", "").lower()
    if upgrade != "websocket":
        return False
    ws_key = handler.headers.get("Sec-WebSocket-Key", "")
    if not ws_key:
        handler.send_response(400)
        handler.end_headers()
        return False
    handler.send_response(101)
    handler.send_header("Upgrade", "websocket")
    handler.send_header("Connection", "Upgrade")
    handler.send_header("Sec-WebSocket-Accept", _accept_key(ws_key))
    handler.end_headers()
    return True


def ws_send_text(handler: BaseHTTPRequestHandler, payload: str, *, timeout: float | None = None) -> bool:
    """Envia frame de texto (opcode 0x1). Retorna True em sucesso, False se o
    cliente está morto/lento — o caller (broadcaster) remove o cliente.

    timeout=None: envio bloqueante via wfile (uso pontual fora do broadcast).
    timeout=N: envio com deadline via send não-bloqueante + select — um cliente
    que não lê o socket não pode travar o broadcaster além de N segundos.
    """
    data = payload.encode("utf-8")
    frame = _build_frame(0x1, data)
    return _send_frame(handler, frame, timeout=timeout)


def _send_frame(handler: BaseHTTPRequestHandler, frame: bytes, *, timeout: float | None = None) -> bool:
    connection = getattr(handler, "connection", None)
    if timeout is None or connection is None:
        try:
            handler.wfile.write(frame)
            handler.wfile.flush()
            return True
        except (BrokenPipeError, ConnectionResetError, OSError):
            return False
    return _send_with_deadline(connection, frame, timeout)


def _send_with_deadline(connection, frame: bytes, timeout: float) -> bool:
    """Envia frame respeitando um deadline absoluto.

    Estratégia: tentativa de send não-bloqueante (MSG_DONTWAIT, quando a
    plataforma tem); se o buffer do SO está cheio, select() por escrita até o
    deadline. Sem MSG_DONTWAIT, faz select() antes de cada send bloqueante.
    Retorna False em erro de socket ou estouro do deadline.
    """
    deadline = time.monotonic() + max(0.0, timeout)
    view = memoryview(frame)
    while len(view):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        try:
            if _WS_DONTWAIT is not None:
                try:
                    sent = connection.send(view, _WS_DONTWAIT)
                    view = view[sent:]
                    continue
                except (BlockingIOError, InterruptedError):
                    pass  # buffer cheio: aguarda escrita com select abaixo
            try:
                writable = select.select([], [connection], [], remaining)[1]
            except (OSError, ValueError):
                return False
            if not writable:
                return False
            sent = connection.send(view)
            view = view[sent:]
        except (BrokenPipeError, ConnectionResetError, OSError):
            return False
    return True


def ws_send_ping(handler: BaseHTTPRequestHandler) -> None:
    """Envia ping frame (opcode 0x9)."""
    try:
        handler.wfile.write(_build_frame(0x9, b"ping"))
        handler.wfile.flush()
    except (BrokenPipeError, ConnectionResetError, OSError):
        pass


def ws_send_pong(handler: BaseHTTPRequestHandler, payload: bytes = b"") -> None:
    """Envia pong frame (opcode 0xA) em resposta a um ping (RFC 6455)."""
    try:
        handler.wfile.write(_build_frame(0xA, payload))
        handler.wfile.flush()
    except (BrokenPipeError, ConnectionResetError, OSError):
        pass


def ws_recv_frame(handler: BaseHTTPRequestHandler) -> dict | None:
    """
    Lê um frame do cliente. Retorna:
      {"opcode": int, "payload": bytes, "close_code": int | None}
    ou None se a conexão fechou/erro ou o frame viola o protocolo
    (sem máscara — RFC 6455 exige máscara em frames do cliente — ou
    payload acima de WS_MAX_PAYLOAD_BYTES).
    """
    try:
        header = handler.rfile.read(2)
        if not header or len(header) < 2:
            return None
        b0, b1 = header[0], header[1]
        opcode = b0 & 0x0F
        masked = (b1 & 0x80) != 0
        length = b1 & 0x7F

        if length == 126:
            extra = handler.rfile.read(2)
            if len(extra) < 2:
                return None
            length = struct.unpack("!H", extra)[0]
        elif length == 127:
            extra = handler.rfile.read(8)
            if len(extra) < 8:
                return None
            length = struct.unpack("!Q", extra)[0]

        # RFC 6455: frames do cliente DEVEM ser mascarados.
        if not masked:
            return None
        # Cap de payload: recusa frames gigantes antes de alocar memória.
        if length > WS_MAX_PAYLOAD_BYTES:
            return None

        mask_key = handler.rfile.read(4)
        if len(mask_key) < 4:
            return None
        payload = handler.rfile.read(length) if length > 0 else b""

        if masked and mask_key:
            payload = bytes(b ^ mask_key[i % 4] for i, b in enumerate(payload))

        close_code = None
        if opcode == 0x8 and len(payload) >= 2:
            close_code = struct.unpack("!H", payload[:2])[0]

        return {"opcode": opcode, "payload": payload, "close_code": close_code}
    except (BrokenPipeError, ConnectionResetError, OSError):
        return None


def _build_frame(opcode: int, payload: bytes) -> bytes:
    frame = bytearray([0x80 | opcode])
    length = len(payload)
    if length < 126:
        frame.append(length)
    elif length < 65536:
        frame.append(126)
        frame.extend(struct.pack("!H", length))
    else:
        frame.append(127)
        frame.extend(struct.pack("!Q", length))
    frame.extend(payload)
    return bytes(frame)


class WebSocketBroadcaster:
    """
    Gerencia clientes conectados e faz broadcast periódico do status.
    Thread-safe.

    Invariantes:
    - o envio NUNCA acontece dentro do lock de clientes (snapshot sob lock,
      envio fora) — um cliente lento não bloqueia add/remove nem os demais;
    - cada envio tem deadline (send_timeout): cliente que não lê o socket é
      descartado em vez de travar o ciclo;
    - cliente cuja escrita falha é removido imediatamente;
    - stop() encerra a thread de broadcast (server_close chama via
      shutdown_broadcaster) — sem thread vazada.
    """

    def __init__(self, status_fn: Callable[[], dict], interval: float = 3.0,
                 send_timeout: float = 2.0):
        self._status_fn = status_fn
        self._interval = interval
        self._send_timeout = send_timeout
        self._clients: list[BaseHTTPRequestHandler] = []
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = threading.Thread(
            target=self._broadcast_loop, daemon=True, name="ws-broadcaster",
        )
        self._thread.start()

    def add_client(self, handler: BaseHTTPRequestHandler) -> None:
        with self._lock:
            self._clients.append(handler)

    def remove_client(self, handler: BaseHTTPRequestHandler) -> None:
        with self._lock:
            try:
                self._clients.remove(handler)
            except ValueError:
                pass

    def broadcast_once(self) -> None:
        """Um ciclo de broadcast: snapshot da lista sob lock, envio fora."""
        status = self._status_fn()
        msg = json.dumps(status, ensure_ascii=False, default=str)
        with self._lock:
            clients = list(self._clients)
        dead: list[BaseHTTPRequestHandler] = []
        for client in clients:
            if not ws_send_text(client, msg, timeout=self._send_timeout):
                dead.append(client)
        if dead:
            with self._lock:
                for d in dead:
                    try:
                        self._clients.remove(d)
                    except ValueError:
                        pass

    def _broadcast_loop(self) -> None:
        while not self._stop_event.wait(self._interval):
            try:
                self.broadcast_once()
            except Exception:
                # status_fn quebrada não pode matar a thread de broadcast.
                pass

    def stop(self, timeout: float = 5.0) -> None:
        """Encerra a thread de broadcast. Idempotente."""
        self._stop_event.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout)
        self._thread = None


_broadcaster: WebSocketBroadcaster | None = None
_broadcaster_lock = threading.Lock()


def get_broadcaster(status_fn: Callable[[], dict]) -> WebSocketBroadcaster:
    """Retorna o broadcaster singleton, criando-o na primeira chamada.

    Novas conexões apenas registram mais clientes no broadcaster existente;
    a função de status é atualizada a cada chamada (o closure carrega o
    handler mais recente, mas todos compartilham o pool de conexões do
    servidor, então qualquer um serve).
    """
    global _broadcaster
    with _broadcaster_lock:
        if _broadcaster is None:
            _broadcaster = WebSocketBroadcaster(status_fn)
        else:
            _broadcaster._status_fn = status_fn
        return _broadcaster


def shutdown_broadcaster() -> None:
    """Para e limpa o broadcaster singleton (chamado em server_close()).

    Sem isto a thread de broadcast vive além do servidor que a criou — em
    suítes que sobem dezenas de ControlServer, threads vazadas seguem chamando
    a status_fn sobre pools de conexão já fechados.
    """
    global _broadcaster
    with _broadcaster_lock:
        broadcaster = _broadcaster
        _broadcaster = None
    if broadcaster is not None:
        broadcaster.stop()

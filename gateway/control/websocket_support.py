"""
WebSocket minimalista para status em tempo real do gateway.
Implementa RFC 6455 parcial: handshake, text frames, ping/pong, close.
"""

from __future__ import annotations

import hashlib
import json
import struct
import threading
import time
from http.server import BaseHTTPRequestHandler
from typing import Callable

WS_MAGIC = b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

# Limite de payload aceito por frame (64 KB) — protege contra frames 64-bit
# gigantes declarados pelo cliente.
WS_MAX_PAYLOAD_BYTES = 65536


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


def ws_send_text(handler: BaseHTTPRequestHandler, payload: str) -> None:
    """Envia frame de texto (opcode 0x1)."""
    data = payload.encode("utf-8")
    frame = _build_frame(0x1, data)
    try:
        handler.wfile.write(frame)
        handler.wfile.flush()
    except (BrokenPipeError, ConnectionResetError, OSError):
        pass


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
    """

    def __init__(self, status_fn: Callable[[], dict], interval: float = 3.0):
        self._status_fn = status_fn
        self._interval = interval
        self._clients: list[BaseHTTPRequestHandler] = []
        self._lock = threading.Lock()
        self._running = True
        self._thread = threading.Thread(target=self._broadcast_loop, daemon=True)
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

    def _broadcast_loop(self) -> None:
        while self._running:
            time.sleep(self._interval)
            status = self._status_fn()
            msg = json.dumps(status, ensure_ascii=False, default=str)
            with self._lock:
                dead: list[BaseHTTPRequestHandler] = []
                for client in self._clients:
                    try:
                        ws_send_text(client, msg)
                    except Exception:
                        dead.append(client)
                for d in dead:
                    try:
                        self._clients.remove(d)
                    except ValueError:
                        pass


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

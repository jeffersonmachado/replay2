"""Regressão: broadcaster WebSocket robusto.

Antes da correção:
- ws_send_text engolia erros e não retornava nada (caller não sabia se o
  cliente estava morto);
- o broadcast enviava DENTRO do lock global de clientes — um cliente lento
  travava o envio para todos os demais;
- clientes mortos só eram removidos se o envio levantasse exceção (e o
  write engolido nunca levantava);
- não havia stop(): a thread do broadcaster vazava no server_close().

O teste server_close prova a ausência de thread vazada contando threads
antes/depois.
"""

from __future__ import annotations

import io
import socket
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
GATEWAY_DIR = ROOT / "gateway"
if str(GATEWAY_DIR) not in sys.path:
    sys.path.insert(0, str(GATEWAY_DIR))

import control.websocket_support as ws

from dakota_gateway.state_db import connect, init_db

import importlib.util

CONTROL_SERVER_PATH = GATEWAY_DIR / "control" / "server.py"
SPEC = importlib.util.spec_from_file_location("control_server_ws", CONTROL_SERVER_PATH)
CONTROL = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(CONTROL)


def _fill_send_buffer(sock: socket.socket) -> None:
    """Enche o buffer de envio do socket (par não lê) até o SO recusar."""
    sock.setblocking(False)
    try:
        while True:
            sock.send(b"x" * 65536)
    except (BlockingIOError, InterruptedError):
        pass
    finally:
        sock.setblocking(True)


class WsSendTextTests(unittest.TestCase):
    def test_returns_true_on_success(self):
        handler = SimpleNamespace(wfile=io.BytesIO())
        self.assertTrue(ws.ws_send_text(handler, "ok"))

    def test_returns_false_on_dead_client(self):
        class DeadWfile:
            def write(self, _data):
                raise BrokenPipeError("pipe quebrado")

            def flush(self):
                raise BrokenPipeError("pipe quebrado")

        handler = SimpleNamespace(wfile=DeadWfile())
        self.assertFalse(ws.ws_send_text(handler, "ok"))

    def test_slow_client_send_times_out_instead_of_blocking(self):
        s1, s2 = socket.socketpair()
        try:
            _fill_send_buffer(s1)
            handler = SimpleNamespace(connection=s1)
            start = time.monotonic()
            ok = ws.ws_send_text(handler, "z" * 1000, timeout=0.3)
            elapsed = time.monotonic() - start
            self.assertFalse(ok, "envio para cliente que não lê deveria falhar")
            self.assertLess(elapsed, 2.0, f"envio bloqueou {elapsed:.2f}s (timeout=0.3s)")
        finally:
            s1.close()
            s2.close()


class BroadcasterTests(unittest.TestCase):
    def test_dead_client_removed_and_healthy_client_not_blocked(self):
        healthy_a, healthy_b = socket.socketpair()
        received: list[bytes] = []
        stop_reading = threading.Event()

        def reader():
            healthy_b.settimeout(0.2)
            while not stop_reading.is_set():
                try:
                    data = healthy_b.recv(65536)
                    if not data:
                        return
                    received.append(data)
                except socket.timeout:
                    continue
                except OSError:
                    return

        dead = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        dead.close()  # qualquer send falha na hora

        reader_thread = threading.Thread(target=reader, daemon=True)
        reader_thread.start()

        broadcaster = ws.WebSocketBroadcaster(status_fn=lambda: {"ok": True}, interval=0.05, send_timeout=0.2)
        try:
            healthy_handler = SimpleNamespace(connection=healthy_a)
            dead_handler = SimpleNamespace(connection=dead)
            broadcaster.add_client(healthy_handler)
            broadcaster.add_client(dead_handler)
            broadcaster.broadcast_once()

            deadline = time.monotonic() + 2.0
            while not received and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertTrue(received, "cliente saudável não recebeu broadcast (bloqueado pelo morto?)")
            with broadcaster._lock:
                clients = list(broadcaster._clients)
            self.assertIn(healthy_handler, clients)
            self.assertNotIn(dead_handler, clients, "cliente morto não foi removido imediatamente")
        finally:
            broadcaster.stop()
            stop_reading.set()
            healthy_a.close()
            healthy_b.close()
            reader_thread.join(timeout=2)

    def test_stop_terminates_broadcast_thread(self):
        broadcaster = ws.WebSocketBroadcaster(status_fn=lambda: {}, interval=0.02)
        self.assertTrue(broadcaster._thread is not None and broadcaster._thread.is_alive())
        broadcaster.stop()
        self.assertFalse(
            broadcaster._thread is not None and broadcaster._thread.is_alive(),
            "thread do broadcaster segue viva após stop()",
        )
        # stop é idempotente
        broadcaster.stop()


class ServerCloseBroadcasterTests(unittest.TestCase):
    def test_server_close_stops_broadcaster_without_leaking_threads(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "test.db")
            con = connect(db_path)
            init_db(con)
            con.close()

            server = CONTROL.ControlServer(
                ("127.0.0.1", 0),
                CONTROL.Handler,
                db_path=db_path,
                cookie_secret=b"test_cookie_secret_32_bytes___",
                hmac_key=b"test_hmac_key_32_bytes__________",
                capture_log_dir=str(Path(tmp) / "captures"),
            )
            broadcaster = ws.get_broadcaster(status_fn=lambda: {})
            self.assertTrue(broadcaster._thread is not None and broadcaster._thread.is_alive())

            threads_before = threading.enumerate()
            server.server_close()
            time.sleep(0.2)
            threads_after = threading.enumerate()

            self.assertIsNone(ws._broadcaster, "singleton do broadcaster não foi limpo no server_close")
            self.assertFalse(
                broadcaster._thread is not None and broadcaster._thread.is_alive(),
                "thread do broadcaster segue viva após server_close",
            )
            leaked = {t.name for t in threads_after} - {t.name for t in threads_before}
            self.assertEqual(leaked, set(), f"threads vazadas no server_close: {leaked}")


if __name__ == "__main__":
    unittest.main()

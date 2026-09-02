"""Regressão: o control plane não pode criar threads ilimitadas.

ThreadingHTTPServer cria uma thread por conexão, sem teto — N conexões
concorrentes (mesmo lentas/travadas) derrubam o processo por exaustão de
threads. A correção limita as conexões curtas com um semáforo
(DAKOTA_HTTP_MAX_CONNECTIONS, default 128): sem slot livre, a conexão
recebe 503 e é fechada (fail-fast, sem travar o loop de accept).

Conexões WebSocket (/ws/*) são longas por natureza: ao concluir o upgrade,
o handler devolve o slot à cota de conexões curtas — senão poucos clientes
de tempo real esgotariam o servidor. A cota separada é exatamente isso:
threads de WS não contam no limite de requisições curtas.
"""

from __future__ import annotations

import importlib.util
import http.client
import json
import os
import socket
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from urllib.request import Request, build_opener

ROOT = Path(__file__).resolve().parents[2]
GATEWAY_DIR = ROOT / "gateway"
if str(GATEWAY_DIR) not in sys.path:
    sys.path.insert(0, str(GATEWAY_DIR))

import dakota_gateway.auth as auth
from dakota_gateway.state_db import connect, init_db, now_ms

CONTROL_SERVER_PATH = GATEWAY_DIR / "control" / "server.py"
SPEC = importlib.util.spec_from_file_location("control_server_limit", CONTROL_SERVER_PATH)
CONTROL = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(CONTROL)


def _read_status_line(sock: socket.socket) -> int:
    sock.settimeout(5)
    data = b""
    while b"\r\n" not in data:
        chunk = sock.recv(1)
        if not chunk:
            break
        data += chunk
    line = data.split(b"\r\n", 1)[0].decode("latin-1")
    return int(line.split(" ", 2)[1])


class HttpConnectionLimitTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tmpdir.name) / "test.db")
        con = connect(self.db_path)
        init_db(con)
        con.execute(
            "INSERT INTO users(username,password_hash,role,created_at_ms) VALUES(?,?,'admin',?)",
            ("admin", auth.pbkdf2_hash_password("admin123"), now_ms()),
        )
        con.close()
        self._old_env = os.environ.get("DAKOTA_HTTP_MAX_CONNECTIONS")
        os.environ["DAKOTA_HTTP_MAX_CONNECTIONS"] = "2"
        self.server = CONTROL.ControlServer(
            ("127.0.0.1", 0),
            CONTROL.Handler,
            db_path=self.db_path,
            cookie_secret=b"test_cookie_secret_32_bytes___",
            hmac_key=b"test_hmac_key_32_bytes__________",
        )
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        time.sleep(0.2)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        if self._old_env is None:
            os.environ.pop("DAKOTA_HTTP_MAX_CONNECTIONS", None)
        else:
            os.environ["DAKOTA_HTTP_MAX_CONNECTIONS"] = self._old_env
        self.tmpdir.cleanup()

    def _open_stalled_connection(self) -> socket.socket:
        """Conexão que ocupa um slot: request parcial, handler bloqueia lendo."""
        s = socket.create_connection(("127.0.0.1", self.port), timeout=5)
        s.sendall(b"GET /health HTTP/1.1\r\nHost: 127.0.0.1\r\n")  # sem CRLF final
        return s

    def test_excess_connection_gets_503_instead_of_unbounded_thread(self):
        stalled = [self._open_stalled_connection() for _ in range(2)]
        try:
            time.sleep(0.4)  # garante que as 2 threads ocuparam os slots
            s = socket.create_connection(("127.0.0.1", self.port), timeout=5)
            try:
                s.sendall(b"GET /health HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n")
                status = _read_status_line(s)
            finally:
                s.close()
            self.assertEqual(
                status, 503,
                "sem slot livre o servidor deveria responder 503 (fail-fast), não criar thread",
            )
        finally:
            for s in stalled:
                s.close()

    def test_slots_are_released_after_short_requests(self):
        # Estoura a cota uma vez...
        stalled = [self._open_stalled_connection() for _ in range(2)]
        time.sleep(0.4)
        for s in stalled:
            s.close()
        time.sleep(0.4)
        # ...e o servidor volta a responder normalmente (slots devolvidos).
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            conn.request("GET", "/health")
            resp = conn.getresponse()
            resp.read()
            self.assertEqual(resp.status, 200)
        finally:
            conn.close()

    def test_websocket_connection_frees_short_request_slot(self):
        """Com cota 1 ocupada por um WebSocket, requests curtas continuam
        respondendo: o upgrade devolve o slot à cota."""
        # Servidor dedicado com cota 1 (o limite é lido na criação).
        os.environ["DAKOTA_HTTP_MAX_CONNECTIONS"] = "1"
        server = CONTROL.ControlServer(
            ("127.0.0.1", 0),
            CONTROL.Handler,
            db_path=self.db_path,
            cookie_secret=b"test_cookie_secret_32_bytes___",
            hmac_key=b"test_hmac_key_32_bytes__________",
        )
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        time.sleep(0.2)
        try:
            # Login para obter cookie de sessão (/ws/* exige auth).
            opener = build_opener()
            req = Request(
                f"http://127.0.0.1:{port}/api/login",
                data=json.dumps({"username": "admin", "password": "admin123"}).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with opener.open(req, timeout=5) as resp:
                set_cookie = resp.headers.get("Set-Cookie", "")
            cookie = set_cookie.split(";", 1)[0]
            self.assertIn("dakota_session=", cookie)

            ws_sock = socket.create_connection(("127.0.0.1", port), timeout=5)
            try:
                handshake = (
                    "GET /ws/gateway-status HTTP/1.1\r\n"
                    "Host: 127.0.0.1\r\n"
                    "Upgrade: websocket\r\n"
                    "Connection: Upgrade\r\n"
                    "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
                    "Sec-WebSocket-Version: 13\r\n"
                    f"Cookie: {cookie}\r\n\r\n"
                )
                ws_sock.sendall(handshake.encode("latin-1"))
                status = _read_status_line(ws_sock)
                self.assertEqual(status, 101, "handshake WebSocket deveria retornar 101")
                time.sleep(0.4)  # handler WS devolve o slot após o upgrade

                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                try:
                    conn.request("GET", "/health")
                    resp = conn.getresponse()
                    resp.read()
                    self.assertEqual(
                        resp.status, 200,
                        "com o WS ocupando a única cota, /health deveria responder "
                        "(slot devolvido no upgrade), não 503",
                    )
                finally:
                    conn.close()
            finally:
                ws_sock.close()
        finally:
            server.shutdown()
            server.server_close()
            os.environ["DAKOTA_HTTP_MAX_CONNECTIONS"] = "2"


if __name__ == "__main__":
    unittest.main()

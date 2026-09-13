"""Testes do smoke de replay (scripts/smoke-test-replay.py) contra o contrato
X6 do endpoint /api/captures/{id}/replay.

Contrato X6 (session_replay_service.py): `timeline` e `playback` trafegam
como dicts de referencias (event_refs/checkpoint_refs + meta); os eventos
completos da janela vivem em `timeline_items`. O smoke foi escrito para o
contrato antigo (timeline como lista de eventos) e crashava com
AttributeError ao iterar as chaves do dict — reproduzido na homologacao do
AIX (capture 2). Estes testes sobem um stub HTTP com o contrato real e
rodam o script por subprocess.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SMOKE_REPLAY = os.path.join(ROOT, "scripts", "smoke-test-replay.py")
SMOKE_CAPTURE = os.path.join(ROOT, "scripts", "smoke-test-capture.py")


def _replay_payload() -> dict:
    """Payload minimo no contrato X6 (refs + timeline_items completos)."""
    items = [
        {
            "event_id": "ev-2",
            "seq_global": 2,
            "ts_ms": 1784915803605,
            "timestamp_ms": 1784915803605,
            "type": "bytes",
            "direction": "out",
            "n_bytes": 2,
            "data_b64": "DQo=",
            "text_sig": "sha256:aa",
            "visual_sig": "sha256:bb",
        },
        {
            "event_id": "ev-3",
            "seq_global": 3,
            "ts_ms": 1784915803700,
            "timestamp_ms": 1784915803700,
            "type": "bytes",
            "direction": "out",
            "n_bytes": 5,
            "data_b64": "aGVsbG8=",
            "is_checkpoint": True,
            "snapshot_compact": {"v": 1},
            "text_sig": "sha256:cc",
            "visual_sig": "sha256:dd",
        },
    ]
    return {
        "error": None,
        "session_id": "sess-1",
        "session_start": {"rows": 25, "cols": 80, "term": "xterm", "encoding": "utf-8"},
        "session_end": {},
        "geometry": {"rows": 25, "cols": 80, "geometry_source": "session_metadata", "encoding": "utf-8"},
        "timeline": {"event_refs": ["ev-2", "ev-3"], "checkpoint_refs": ["0"]},
        "timeline_items": items,
        "playback": {
            "event_refs": ["ev-2", "ev-3"],
            "checkpoint_refs": ["0"],
            "event_count": 2,
            "total_bytes_in": 0,
            "total_bytes_out": 7,
        },
        "window": {"offset": 0, "limit": 20, "total_events": 2, "truncated": False},
    }


def _make_handler(payload: dict):
    class Stub(BaseHTTPRequestHandler):
        def _json(self, obj, status=200):
            body = json.dumps(obj).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):  # noqa: N802
            path = self.path.split("?")[0]
            if path == "/api/captures":
                self._json({"captures": [{"id": 7, "event_count": 10}], "total": 1})
            elif path == "/api/captures/7/sessions":
                self._json({"sessions": [{"session_id": "sess-1", "bytes_out": 7, "event_count": 2}]})
            elif path == "/api/captures/7/replay":
                self._json(payload)
            elif path == "/health":
                self._json({"status": "ok"})
            elif path == "/ready":
                self._json({"status": "ready"})
            else:
                self._json({"error": "not found"}, 404)

        def do_POST(self):  # noqa: N802
            if self.path.split("?")[0] == "/api/login":
                self._json({"ok": True})
            else:
                self._json({"error": "not found"}, 404)

        def log_message(self, *a):  # silencioso
            pass

    return Stub


def _run_smoke(script: str, port: int, extra_env: dict | None = None) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.update(extra_env or {})
    proc = subprocess.run(
        [sys.executable, script, "--host", "127.0.0.1", "--port", str(port),
         "--user", "admin", "--pass", "x"],
        capture_output=True, env=env, timeout=60,
    )
    # A saida pode estar em latin-1 (AIX): decodifica com replace para o
    # proprio teste nao quebrar no decode do pai.
    proc.stdout = proc.stdout.decode("utf-8", errors="replace")
    proc.stderr = proc.stderr.decode("utf-8", errors="replace")
    return proc


class _StubServer:
    def __init__(self, payload: dict):
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(payload))
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *a):
        self.httpd.shutdown()
        self.httpd.server_close()


class TestSmokeReplayX6Contract(unittest.TestCase):
    def test_smoke_replay_aceita_contrato_x6(self):
        """Contrato X6 (timeline como refs + timeline_items) deve passar."""
        with _StubServer(_replay_payload()) as srv:
            proc = _run_smoke(SMOKE_REPLAY, srv.port)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("Fail: 0", proc.stdout)
        self.assertNotIn("Traceback", proc.stderr)

    def test_smoke_replay_reprova_timeline_item_sem_timestamp(self):
        """Item de timeline sem timestamp_ms deve ser reprovado (exit 1)."""
        payload = _replay_payload()
        del payload["timeline_items"][0]["timestamp_ms"]
        with _StubServer(payload) as srv:
            proc = _run_smoke(SMOKE_REPLAY, srv.port)
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn("[FAIL]", proc.stdout)

    def test_smoke_replay_stdout_latin1(self):
        """Stdout latin-1 (AIX) nao pode derrubar o script com UnicodeEncodeError."""
        with _StubServer(_replay_payload()) as srv:
            proc = _run_smoke(SMOKE_REPLAY, srv.port, {"PYTHONIOENCODING": "latin-1"})
        self.assertNotIn("UnicodeEncodeError", proc.stderr)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_smoke_capture_stdout_latin1(self):
        """O smoke de captura tambem imprime setas unicode — mesma protecao."""
        with _StubServer(_replay_payload()) as srv:
            proc = _run_smoke(SMOKE_CAPTURE, srv.port, {"PYTHONIOENCODING": "latin-1"})
        self.assertNotIn("UnicodeEncodeError", proc.stderr)


if __name__ == "__main__":
    unittest.main()

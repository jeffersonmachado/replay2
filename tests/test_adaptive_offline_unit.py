#!/usr/bin/env python3
"""§17/§16.18: o motor adaptativo é 100% offline.

- análise estática: os módulos do motor adaptativo não importam nada de
  rede/IA/SaaS (http, urllib, socket, requests, openai, anthropic, ...);
- análise dinâmica: scheduler, classificador, guard, telemetria, perfil de
  latência e evidência de type-ahead executam com ``socket`` e DNS
  bloqueados — qualquer tentativa de rede quebra o teste.
"""
from __future__ import annotations

import re
import socket
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "gateway"))

ADAPTIVE_MODULES = [
    "gateway/dakota_gateway/replay_control/action_classifier.py",
    "gateway/dakota_gateway/replay_control/safety_guard.py",
    "gateway/dakota_gateway/replay_control/adaptive_scheduler.py",
    "gateway/dakota_gateway/replay_control/execution_telemetry.py",
    "gateway/dakota_gateway/replay_control/synchronization.py",
    "gateway/dakota_gateway/replay_control/latency_profile.py",
    "gateway/dakota_gateway/synthetic/action_model.py",
    "gateway/dakota_gateway/synthetic/replay_adapter.py",
]

# padrões proibidos: rede, DNS, SDKs de IA, telemetria externa
FORBIDDEN = re.compile(
    r"\b(import|from)\s+("
    r"socket|ssl|http|urllib|requests|httpx|aiohttp|ftplib|smtplib|telnetlib"
    r"|openai|anthropic|google\.generativeai|google|gemini|deepseek|kimi"
    r"|boto3|azure|websocket|websockets|grpc"
    r")"
)


class StaticOfflineTests(unittest.TestCase):
    def test_modulos_adaptativos_nao_importam_rede_nem_ia(self):
        for rel in ADAPTIVE_MODULES:
            with self.subTest(modulo=rel):
                text = (ROOT / rel).read_text(encoding="utf-8")
                # ignora comentários/docstrings: checa só linhas de import
                for line in text.splitlines():
                    stripped = line.strip()
                    if stripped.startswith(("import ", "from ")):
                        self.assertIsNone(
                            FORBIDDEN.search(stripped),
                            f"{rel}: import proibido: {stripped}",
                        )


class RuntimeOfflineTests(unittest.TestCase):
    """Executa o motor com socket/DNS sabotados."""

    def setUp(self):
        self._orig_socket = socket.socket
        self._orig_getaddrinfo = socket.getaddrinfo
        self._orig_create = socket.create_connection

        def _blocked(*args, **kwargs):
            raise AssertionError("acesso à rede proibido no motor adaptativo")

        socket.socket = _blocked  # type: ignore[assignment]
        socket.getaddrinfo = _blocked  # type: ignore[assignment]
        socket.create_connection = _blocked  # type: ignore[assignment]

    def tearDown(self):
        socket.socket = self._orig_socket  # type: ignore[assignment]
        socket.getaddrinfo = self._orig_getaddrinfo  # type: ignore[assignment]
        socket.create_connection = self._orig_create  # type: ignore[assignment]

    def test_pipeline_completo_sem_rede(self):
        import base64
        import tempfile

        from dakota_gateway.replay_control import (
            AdaptiveScheduler,
            LatencyProfile,
            RunTelemetry,
            SafetyGuard,
            TelemetryBucket,
            extract_typeahead_evidence,
            quiet_points,
        )
        from dakota_gateway.replay_control.action_classifier import classify_bytes
        from dakota_gateway.synthetic.action_model import parse_replay_script

        # 1. parse + classificação
        actions = parse_replay_script("ABC\n{KEY:ENTER}\n{WAIT:100}")
        self.assertEqual(len(actions), 3)
        self.assertEqual(classify_bytes(b"ABC").action_class.value, "printable_input")

        # 2. scheduler + guard
        guard = SafetyGuard()
        sch = AdaptiveScheduler(guard=guard, synthetic_trail=True)
        evs = [
            {"seq_global": i + 1, "ts_ms": 1000 + i * 50, "type": "bytes",
             "dir": "in", "session_id": "s1",
             "data_b64": base64.b64encode(ch.encode()).decode(),
             "key_kind": "printable"}
            for i, ch in enumerate("ABC")
        ]
        decisions = sch.plan_events(evs)
        self.assertTrue(decisions)

        # 3. evidência de type-ahead + quiet points
        cap = [
            {"seq_global": 1, "ts_ms": 1000, "type": "bytes", "dir": "in", "session_id": "s"},
            {"seq_global": 2, "ts_ms": 1040, "type": "bytes", "dir": "out", "session_id": "s"},
            {"seq_global": 3, "ts_ms": 1060, "type": "bytes", "dir": "in", "session_id": "s"},
            {"seq_global": 4, "ts_ms": 1200, "type": "bytes", "dir": "out", "session_id": "s"},
            {"seq_global": 5, "ts_ms": 5000, "type": "session_end", "session_id": "s"},
        ]
        self.assertTrue(quiet_points(cap, stable_ms=150))
        self.assertTrue(extract_typeahead_evidence(cap, stable_ms=150))

        # 4. telemetria + perfil de latência (persistência em arquivo local)
        rt = RunTelemetry()
        sess = rt.session("s1")
        sess.begin_session(0.0)
        sess.record(TelemetryBucket.PACING, 10.0)
        sess.end_session(100.0)
        self.assertIn("replay_overhead_ratio", rt.snapshot())

        with tempfile.TemporaryDirectory() as tmp:
            profile = LatencyProfile(path=str(Path(tmp) / "lat.json"))
            profile.record("aix-mig24", "checkpoint", "est361", 420.0)
            profile.save()
            self.assertTrue((Path(tmp) / "lat.json").exists())


if __name__ == "__main__":
    unittest.main()

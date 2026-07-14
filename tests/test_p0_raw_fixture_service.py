#!/usr/bin/env python3
"""Teste P0-18: fixture bruta passando pelo servico real prepare_session_replay_data."""
from __future__ import annotations

import base64
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GATEWAY_DIR = ROOT / "gateway"
sys.path.insert(0, str(GATEWAY_DIR))

from control.services.session_replay_service import prepare_session_replay_data


class RawFixtureServiceTests(unittest.TestCase):
    """Fixture bruta que passa pelo prepare_session_replay_data real."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.log_dir = os.path.join(self.tmp.name, "capture-uuid")
        os.makedirs(self.log_dir, exist_ok=True)

    def tearDown(self):
        self.tmp.cleanup()

    def _b64(self, text: str) -> str:
        return base64.b64encode(text.encode("utf-8")).decode()

    def _write_audit(self, events: list[dict], filename: str = "audit-20260714.part001.jsonl"):
        path = os.path.join(self.log_dir, filename)
        with open(path, "w", encoding="utf-8") as f:
            for ev in events:
                f.write(json.dumps(ev) + "\n")

    def test_minimal_session_with_metadata(self):
        """Sessao minima com session_start + bytes out + session_end."""
        session_id = "test-sess-001"
        events = [
            {
                "type": "session_start", "v": "v1", "seq_global": 1, "ts_ms": 1000,
                "actor": "test", "session_id": session_id, "seq_session": 1,
                "entry_mode": "gateway_ssh", "via_gateway": True,
                "source_host": "test", "source_user": "test",
                "rows": 30, "cols": 100, "term": "xterm-256color", "encoding": "cp850",
            },
            {
                "type": "bytes", "v": "v1", "seq_global": 2, "ts_ms": 1100,
                "actor": "test", "session_id": session_id, "seq_session": 2,
                "dir": "out", "n": 12,
                "data_b64": self._b64("Hello World!\n"),
            },
            {
                "type": "session_end", "v": "v1", "seq_global": 3, "ts_ms": 2000,
                "actor": "test", "session_id": session_id, "seq_session": 3,
            },
        ]
        self._write_audit(events)

        result = prepare_session_replay_data(self.log_dir, session_id)

        # Sem erro
        self.assertIsNone(result.get("error"))

        # Geometria com metadados
        geom = result.get("geometry", {})
        self.assertEqual(geom.get("rows"), 30)
        self.assertEqual(geom.get("cols"), 100)
        self.assertEqual(geom.get("geometry_source"), "session_metadata")

        # Timeline com eventos
        tl = result.get("timeline", [])
        self.assertGreater(len(tl), 0, "timeline deve ter eventos")

        # Playback com data_b64
        pb = result.get("playback", {})
        pb_events = pb.get("events", [])
        self.assertGreater(len(pb_events), 0, "playback deve ter eventos")

        out_events = [e for e in pb_events if e.get("direction") == "out"]
        if out_events:
            self.assertIn("data_b64", out_events[0], "data_b64 presente no playback")
            self.assertNotEqual(out_events[0]["data_b64"], "")

    def test_geometry_from_metadata_has_priority(self):
        """Metadados do session_start tem prioridade sobre resize."""
        session_id = "test-sess-002"
        # CSI 8;50;120t (resize) como evento OUT + metadados 30x100
        resize_b64 = base64.b64encode(b"\x1b[8;50;120t").decode()
        events = [
            {
                "type": "session_start", "v": "v1", "seq_global": 1, "ts_ms": 1000,
                "actor": "test", "session_id": session_id, "seq_session": 1,
                "rows": 30, "cols": 100, "term": "xterm-256color", "encoding": "utf-8",
            },
            {
                "type": "bytes", "v": "v1", "seq_global": 2, "ts_ms": 1100,
                "actor": "test", "session_id": session_id, "seq_session": 2,
                "dir": "out", "n": len(b"\x1b[8;50;120t"),
                "data_b64": resize_b64,
            },
            {
                "type": "session_end", "v": "v1", "seq_global": 3, "ts_ms": 2000,
                "actor": "test", "session_id": session_id, "seq_session": 3,
            },
        ]
        self._write_audit(events)

        result = prepare_session_replay_data(self.log_dir, session_id)
        geom = result.get("geometry", {})

        # Metadados do session_start tem prioridade sobre resize
        self.assertEqual(geom.get("rows"), 30, "metadata rows deve ter prioridade")
        self.assertEqual(geom.get("cols"), 100, "metadata cols deve ter prioridade")
        self.assertEqual(geom.get("geometry_source"), "session_metadata")

    def test_resize_in_ignored(self):
        """Evento com dir=in contendo resize nao altera geometria."""
        session_id = "test-sess-003"
        resize_b64 = base64.b64encode(b"\x1b[8;50;120t").decode()
        events = [
            {
                "type": "session_start", "v": "v1", "seq_global": 1, "ts_ms": 1000,
                "actor": "test", "session_id": session_id, "seq_session": 1,
            },
            {
                "type": "bytes", "v": "v1", "seq_global": 2, "ts_ms": 1100,
                "actor": "test", "session_id": session_id, "seq_session": 2,
                "dir": "in", "n": len(b"\x1b[8;50;120t"),
                "data_b64": resize_b64,
            },
            {
                "type": "session_end", "v": "v1", "seq_global": 3, "ts_ms": 2000,
                "actor": "test", "session_id": session_id, "seq_session": 3,
            },
        ]
        self._write_audit(events)

        result = prepare_session_replay_data(self.log_dir, session_id)
        geom = result.get("geometry", {})

        # Resize IN ignorado — fallback 25x80
        self.assertEqual(geom.get("rows"), 25)
        self.assertEqual(geom.get("cols"), 80)
        self.assertEqual(geom.get("geometry_source"), "legacy_fallback")

    def test_session_nonexistent_returns_error(self):
        """Session ID que nao existe retorna lista vazia (sem erro, mas sem eventos)."""
        result = prepare_session_replay_data(self.log_dir, "nonexistent")
        timeline = result.get("timeline", [])
        self.assertEqual(len(timeline), 0, "timeline vazia para sessao inexistente")

    def test_encoding_from_metadata(self):
        """Encoding dos metadados aparece na geometria."""
        session_id = "test-sess-004"
        events = [
            {
                "type": "session_start", "v": "v1", "seq_global": 1, "ts_ms": 1000,
                "actor": "test", "session_id": session_id, "seq_session": 1,
                "encoding": "latin1",
            },
            {
                "type": "session_end", "v": "v1", "seq_global": 2, "ts_ms": 2000,
                "actor": "test", "session_id": session_id, "seq_session": 2,
            },
        ]
        self._write_audit(events)

        result = prepare_session_replay_data(self.log_dir, session_id)
        geom = result.get("geometry", {})
        self.assertEqual(geom.get("encoding"), "latin1",
                         "encoding latin1 dos metadados deve ser preservado")


if __name__ == "__main__":
    unittest.main()

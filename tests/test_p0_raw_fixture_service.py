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
                "dir": "out", "n": len("Hello World!\n".encode("utf-8")),
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

    def test_geometry_source_from_session_start_is_preserved_when_valid(self):
        """Origem resolvida na captura deve atravessar o servico de replay."""
        session_id = "test-sess-002b"
        events = [
            {
                "type": "session_start", "v": "v1", "seq_global": 1, "ts_ms": 1000,
                "actor": "test", "session_id": session_id, "seq_session": 1,
                "rows": 40, "cols": 120, "term": "xterm-256color", "encoding": "utf-8",
                "geometry_source": "explicit",
            },
            {
                "type": "session_end", "v": "v1", "seq_global": 2, "ts_ms": 2000,
                "actor": "test", "session_id": session_id, "seq_session": 2,
            },
        ]
        self._write_audit(events)

        result = prepare_session_replay_data(self.log_dir, session_id)
        geom = result.get("geometry", {})
        self.assertEqual(geom.get("rows"), 40)
        self.assertEqual(geom.get("cols"), 120)
        self.assertEqual(geom.get("geometry_source"), "explicit")

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

    def test_unknown_encoding_from_metadata_falls_back_to_utf8(self):
        session_id = "test-sess-004b"
        self._write_audit([
            {
                "type": "session_start", "v": "v1", "seq_global": 1, "ts_ms": 1000,
                "actor": "test", "session_id": session_id, "seq_session": 1,
                "encoding": "x-unknown-codepage",
            },
            {
                "type": "bytes", "v": "v1", "seq_global": 2, "ts_ms": 1100,
                "actor": "test", "session_id": session_id, "seq_session": 2,
                "dir": "out", "n": 2,
                "data_b64": base64.b64encode("á".encode("utf-8")).decode(),
            },
        ])

        result = prepare_session_replay_data(self.log_dir, session_id)

        self.assertEqual(result["geometry"]["encoding"], "utf-8")
        self.assertEqual(result["geometry"]["encoding_source"], "fallback")
        self.assertEqual(result["geometry"]["encoding_warning"]["requested_encoding"], "x-unknown-codepage")
        self.assertEqual(result["timeline"][0]["data_decoded"], "á")

    def test_utf8_split_across_events_decodes_incrementally_without_replacement(self):
        session_id = "test-sess-004c"
        self._write_audit([
            {"type": "session_start", "v": "v1", "seq_global": 1, "ts_ms": 1000, "actor": "test", "session_id": session_id, "seq_session": 1, "encoding": "utf-8"},
            {
                "type": "bytes", "v": "v1", "seq_global": 2, "ts_ms": 1100,
                "actor": "test", "session_id": session_id, "seq_session": 2,
                "dir": "out", "n": 1, "data_b64": base64.b64encode(b"\xc3").decode(),
            },
            {
                "type": "bytes", "v": "v1", "seq_global": 3, "ts_ms": 1200,
                "actor": "test", "session_id": session_id, "seq_session": 3,
                "dir": "out", "n": 1, "data_b64": base64.b64encode(b"\xa1").decode(),
            },
        ])

        result = prepare_session_replay_data(self.log_dir, session_id)

        self.assertEqual([item["data_decoded"] for item in result["timeline"]], ["", "á"])
        self.assertNotIn("�", "".join(item["summary"] for item in result["timeline"]))

    def test_metadata_is_selected_by_exact_session_id(self):
        first_session = "wrong-session"
        wanted_session = "wanted-session"
        self._write_audit([
            {
                "type": "session_start", "v": "v1", "seq_global": 1, "ts_ms": 1000,
                "actor": "test", "session_id": first_session, "seq_session": 1,
                "rows": 10, "cols": 40, "term": "ansi", "encoding": "cp850",
            },
            {
                "type": "session_start", "v": "v1", "seq_global": 2, "ts_ms": 1010,
                "actor": "test", "session_id": wanted_session, "seq_session": 1,
                "rows": 33, "cols": 132, "term": "xterm-256color", "encoding": "utf-8",
            },
            {
                "type": "bytes", "v": "v1", "seq_global": 3, "ts_ms": 1020,
                "actor": "test", "session_id": wanted_session, "seq_session": 2,
                "dir": "out", "n": 2, "data_b64": base64.b64encode("á".encode("utf-8")).decode(),
            },
        ])

        result = prepare_session_replay_data(self.log_dir, wanted_session)
        geom = result["geometry"]

        self.assertEqual((geom["rows"], geom["cols"]), (33, 132))
        self.assertEqual(geom["term"], "xterm-256color")
        self.assertEqual(result["timeline"][0]["data_decoded"], "á")

    def test_byte_count_uses_decoded_base64_length_and_reports_mismatch(self):
        session_id = "test-sess-005"
        events = [
            {"type": "session_start", "v": "v1", "seq_global": 1, "ts_ms": 1000, "actor": "test", "session_id": session_id, "seq_session": 1},
            {
                "type": "bytes", "v": "v1", "seq_global": 2, "ts_ms": 1100,
                "actor": "test", "session_id": session_id, "seq_session": 2,
                "dir": "out", "n": 1, "data_b64": base64.b64encode(b"HELLO").decode(),
            },
        ]
        self._write_audit(events)

        result = prepare_session_replay_data(self.log_dir, session_id)
        item = result["timeline"][0]
        self.assertEqual(item["n_bytes"], 5)
        self.assertEqual(item["declared_bytes"], 1)
        self.assertEqual(item["actual_bytes"], 5)
        self.assertEqual(item["integrity_warning"]["integrity_error"], "byte_count_mismatch")
        self.assertEqual(result["playback"]["total_bytes_out"], 5)

    def test_invalid_and_missing_byte_counts_are_structured(self):
        session_id = "test-sess-006"
        events = [
            {"type": "session_start", "v": "v1", "seq_global": 1, "ts_ms": 1000, "actor": "test", "session_id": session_id, "seq_session": 1},
            {
                "type": "bytes", "v": "v1", "seq_global": 2, "ts_ms": 1100,
                "actor": "test", "session_id": session_id, "seq_session": 2,
                "dir": "out", "data_b64": base64.b64encode("á".encode("utf-8")).decode(),
            },
            {
                "type": "bytes", "v": "v1", "seq_global": 3, "ts_ms": 1200,
                "actor": "test", "session_id": session_id, "seq_session": 3,
                "dir": "out", "n": 99, "data_b64": "not base64!!!",
            },
        ]
        self._write_audit(events)

        result = prepare_session_replay_data(self.log_dir, session_id)
        first, second = result["timeline"]
        self.assertEqual(first["n_bytes"], 2)
        self.assertIsNone(first["declared_bytes"])
        self.assertNotIn("integrity_warning", first)
        self.assertEqual(second["n_bytes"], 0)
        self.assertEqual(second["integrity_warning"]["integrity_error"], "invalid_base64")


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""Testes de regressao P0 — AuditEvent + metadados de sessao."""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GATEWAY_DIR = ROOT / "gateway"
sys.path.insert(0, str(GATEWAY_DIR))

from dakota_gateway.schema import AuditEvent
from dakota_gateway.canonical import canonical_string


class AuditEventMetadataTests(unittest.TestCase):
    """P0-2: AuditEvent deve aceitar rows, cols, term, encoding."""

    def test_audit_event_accepts_session_metadata(self):
        """AuditEvent com rows/cols/term/encoding nao pode dar TypeError."""
        ev = AuditEvent(
            v="v1",
            seq_global=1,
            ts_ms=1000,
            type="session_start",
            actor="test",
            session_id="sess-001",
            seq_session=1,
            rows=25,
            cols=80,
            term="xterm",
            encoding="utf-8",
            geometry_source="gateway_config",
        )
        self.assertEqual(ev.rows, 25)
        self.assertEqual(ev.cols, 80)
        self.assertEqual(ev.term, "xterm")
        self.assertEqual(ev.encoding, "utf-8")
        self.assertEqual(ev.geometry_source, "gateway_config")

    def test_audit_event_serialization_includes_metadata(self):
        """Serializacao JSON do session_start inclui metadados."""
        ev = AuditEvent(
            v="v1",
            seq_global=1,
            ts_ms=1000,
            type="session_start",
            actor="test",
            session_id="sess-001",
            seq_session=1,
            rows=30,
            cols=100,
            term="xterm-256color",
            encoding="cp850",
        )
        # canonical_string usa asdict
        canonical = canonical_string(ev)
        self.assertIn("rows=30", canonical)
        self.assertIn("cols=100", canonical)
        self.assertIn("term=xterm-256color", canonical)
        self.assertIn("encoding=cp850", canonical)

    def test_audit_event_backward_compatible(self):
        """Eventos antigos sem metadados mantem defaults None."""
        ev = AuditEvent(
            v="v1",
            seq_global=1,
            ts_ms=1000,
            type="bytes",
            actor="test",
            session_id="sess-001",
            seq_session=1,
            dir="out",
            data_b64="dGVzdA==",
            n=4,
        )
        self.assertIsNone(ev.rows)
        self.assertIsNone(ev.cols)
        self.assertIsNone(ev.term)
        self.assertIsNone(ev.encoding)

    def test_canonical_string_omits_none_metadata(self):
        """Campos None nao aparecem como 'None' no canonical_string."""
        ev = AuditEvent(
            v="v1",
            seq_global=1,
            ts_ms=1000,
            type="bytes",
            actor="test",
            session_id="sess-001",
            seq_session=1,
            dir="out",
            data_b64="dGVzdA==",
            n=4,
        )
        canonical = canonical_string(ev)
        # Nao deve conter 'rows=None' literal
        self.assertNotIn("rows=None", canonical)
        self.assertNotIn("cols=None", canonical)


if __name__ == "__main__":
    unittest.main()

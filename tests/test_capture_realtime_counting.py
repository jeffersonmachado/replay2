#!/usr/bin/env python3
"""Testes de contagem em tempo real de sessoes/eventos em capturas ativas."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GATEWAY_DIR = ROOT / "gateway"
sys.path.insert(0, str(GATEWAY_DIR))

from dakota_gateway.state_db import connect, init_db, now_ms


class RealTimeCaptureCountingTests(unittest.TestCase):
    """Testa a logica de contagem em tempo real do disco."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "test.db")
        self.captures_dir = os.path.join(self.tmp.name, "captures")
        os.makedirs(self.captures_dir, exist_ok=True)

    def tearDown(self):
        self.tmp.cleanup()

    def _create_db_with_capture(self, log_dir: str, status: str = "active") -> int:
        con = connect(self.db_path)
        init_db(con)
        # Cria admin
        con.execute(
            "INSERT INTO users(username,password_hash,role,created_at_ms) VALUES(?,?, 'admin', ?)",
            ("admin", "hash", now_ms()),
        )
        # Cria capture
        con.execute(
            """INSERT INTO capture_sessions
            (session_uuid, status, created_by, created_by_username, started_at_ms, log_dir, notes, session_count, event_count)
            VALUES (?, ?, 1, 'admin', ?, ?, 'test', 0, 0)""",
            ("uuid-1", status, now_ms(), log_dir),
        )
        con.commit()
        capture_id = con.execute("SELECT last_insert_rowid()").fetchone()[0]
        con.close()
        return capture_id

    def _create_audit_file(self, log_dir: str, lines: int = 3) -> str:
        """Cria um arquivo audit-*.jsonl com N linhas de eventos."""
        os.makedirs(log_dir, exist_ok=True)
        fpath = os.path.join(log_dir, "audit-20260713-test.part001.jsonl")
        with open(fpath, "w") as f:
            for i in range(lines):
                f.write(json.dumps({"type": "test", "seq": i}) + "\n")
        return fpath

    def test_count_from_disk_active_capture(self):
        """Captura ativa: conta audit-*.jsonl direto no log_dir."""
        log_dir = os.path.join(self.captures_dir, "capture-uuid")
        os.makedirs(log_dir, exist_ok=True)
        self._create_audit_file(log_dir, lines=3)

        # Simula a logica de contagem do capture_routes.py
        import glob
        session_count = 0
        event_count = 0
        for fpath in glob.glob(os.path.join(log_dir, "audit-*.jsonl")):
            session_count += 1
            with open(fpath) as fh:
                event_count += sum(1 for _ in fh)

        self.assertEqual(session_count, 1)
        self.assertEqual(event_count, 3)

    def test_count_from_disk_subdirs_backward_compat(self):
        """Captura antiga: audit-*.jsonl em subdiretorios."""
        log_dir = os.path.join(self.captures_dir, "old-capture")
        session_dir = os.path.join(log_dir, "session-uuid")
        self._create_audit_file(session_dir, lines=5)

        import glob
        # Direto (novo padrão)
        session_count = len(glob.glob(os.path.join(log_dir, "audit-*.jsonl")))
        # Subdiretórios (compatibilidade)
        if session_count == 0:
            for fpath in glob.glob(os.path.join(log_dir, "*", "audit-*.jsonl")):
                session_count += 1
                with open(fpath) as fh:
                    event_count = sum(1 for _ in fh)
        else:
            event_count = 0
            for fpath in glob.glob(os.path.join(log_dir, "audit-*.jsonl")):
                with open(fpath) as fh:
                    event_count += sum(1 for _ in fh)

        self.assertEqual(session_count, 1)
        self.assertEqual(event_count, 5)

    def test_count_zero_when_no_audit_files(self):
        """Sem arquivos de auditoria, conta 0."""
        log_dir = os.path.join(self.captures_dir, "empty-capture")
        os.makedirs(log_dir, exist_ok=True)

        import glob
        session_count = len(glob.glob(os.path.join(log_dir, "audit-*.jsonl")))

        self.assertEqual(session_count, 0)

    def test_count_multiple_sessions(self):
        """Multiplos arquivos audit-*.jsonl = multiplas sessoes."""
        log_dir = os.path.join(self.captures_dir, "multi-session")
        self._create_audit_file(log_dir, lines=2)
        # Cria segundo arquivo (simula outra sessão)
        fpath2 = os.path.join(log_dir, "audit-20260713-other.part001.jsonl")
        with open(fpath2, "w") as f:
            f.write(json.dumps({"type": "test", "seq": 99}) + "\n")

        import glob
        session_count = 0
        event_count = 0
        for fpath in sorted(glob.glob(os.path.join(log_dir, "audit-*.jsonl"))):
            session_count += 1
            with open(fpath) as fh:
                event_count += sum(1 for _ in fh)

        self.assertEqual(session_count, 2)
        self.assertEqual(event_count, 3)  # 2 + 1


if __name__ == "__main__":
    unittest.main(verbosity=2)

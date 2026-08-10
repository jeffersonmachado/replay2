#!/usr/bin/env python3
"""Testes unitários da assinatura de grupo de falhas (Rec 2, análise da run 12).

A assinatura do grupo deixou de incluir o ``observed_value``: o observado
muda a cada sessão (nº de pedido, data/hora, dado sintético) e impedia que
a "mesma falha" (mesmo checkpoint esperado) fosse reconhecida como
recorrente entre runs. A chave agora é ``tipo|severidade|expected``; o
``observed_value`` continua no payload do grupo como evidência.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

GATEWAY_DIR = Path(__file__).resolve().parents[1] / "gateway"
sys.path.insert(0, str(GATEWAY_DIR))

import dakota_gateway.auth as auth
from dakota_gateway.replay_control import add_run_failure, build_failure_record, create_run
from dakota_gateway.state_db import connect, init_db, now_ms
from control.services.report_run_service import build_run_comparison, build_run_report


def _failure(seq_global: int, expected: str, observed: str) -> dict:
    return build_failure_record(
        session_id="sess-1",
        seq_global=seq_global,
        seq_session=seq_global,
        event_type="deterministic_input",
        failure_type="screen_divergence",
        severity="medium",
        expected_value=expected,
        observed_value=observed,
        message="divergiu",
        evidence={},
    )


class FailureGroupSignatureTests(unittest.TestCase):
    def _db_with_user(self, tmpdir: str):
        con = connect(str(Path(tmpdir) / "replay.db"))
        init_db(con)
        ph = auth.pbkdf2_hash_password("admin123")
        con.execute(
            "INSERT INTO users(username,password_hash,role,created_at_ms) VALUES(?,?,'admin',?)",
            ("admin", ph, now_ms()),
        )
        user = con.execute("SELECT id FROM users WHERE username='admin'").fetchone()
        return con, int(user["id"])

    def test_mesmo_checkpoint_com_observados_diferentes_agrupa(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            con, uid = self._db_with_user(tmpdir)
            run_id = create_run(con, uid, tmpdir, "legacy.example", "recital", "", "strict-global")
            add_run_failure(con, run_id, _failure(10, "SIG:TELA_A", "sha256:obs1"))
            add_run_failure(con, run_id, _failure(12, "SIG:TELA_A", "sha256:obs2"))
            report = build_run_report(con, run_id)
            con.close()

        grupos = report["grouped_failures"]
        self.assertEqual(len(grupos), 1)
        self.assertEqual(grupos[0]["count"], 2)
        self.assertEqual(grupos[0]["seq_globals"], [10, 12])
        # observed_value continua disponível como evidência no grupo
        self.assertTrue(grupos[0]["observed_value"])

    def test_comparacao_reconhece_recorrencia_com_observado_diferente(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            con, uid = self._db_with_user(tmpdir)
            base = create_run(con, uid, tmpdir, "legacy.example", "recital", "", "strict-global")
            add_run_failure(con, base, _failure(10, "SIG:TELA_A", "sha256:obs_antigo"))
            con.execute("UPDATE replay_runs SET status='success' WHERE id=?", (base,))
            atual = create_run(con, uid, tmpdir, "legacy.example", "recital", "", "strict-global")
            add_run_failure(con, atual, _failure(10, "SIG:TELA_A", "sha256:obs_novo"))
            con.execute("UPDATE replay_runs SET status='success' WHERE id=?", (atual,))
            comparison = build_run_comparison(con, atual)
            con.close()

        summary = comparison["summary"]
        self.assertEqual(summary["baseline_mode"] if "baseline_mode" in summary else comparison["baseline_mode"], "previous_match")
        self.assertEqual(summary["new_failure_groups"], 0)
        self.assertEqual(summary["recurring_failure_groups"], 1)
        self.assertEqual(summary["resolved_failure_groups"], 0)
        self.assertFalse(summary["regression"])


if __name__ == "__main__":
    unittest.main()

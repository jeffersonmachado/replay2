"""Regressão: synthesize deve reusar a knowledge base persistida (sem re-parse).

Exercício real no AIX (v0.8.12, captura 13): o analyze-source persistiu
1.293 telas / 6.277 entidades no banco, mas o
``POST /api/captures/{id}/synthesize`` re-parseava os 1.965 fontes inteiros
(~25 min no POWER) a cada execução, porque ``JourneySynthesizer.from_capture``
instancia ``SourceParser`` sempre. E a tabela ``screen_entity_bindings``
(existe no schema desde o P2-A) nunca era gravada — bindings só viviam em
memória, recalculados a cada uso.

Esperado:
1. analyze-source persiste os bindings (truncate+rebuild, como entidades);
2. synthesize_capture carrega entidades+bindings do banco quando presentes
   e NÃO instancia SourceParser; fallback para parse só com base vazia.

Os testes DEVEM FALHAR antes da correção e PASSAR depois dela.
"""

from __future__ import annotations

import base64
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
GATEWAY_DIR = ROOT / "gateway"
if str(GATEWAY_DIR) not in sys.path:
    sys.path.insert(0, str(GATEWAY_DIR))

from dakota_gateway.state_db import connect, init_db

from control.services import synthetic_plan_service as plan_svc
from control.services import capture_synthesis_service as synth_svc


FONTE_CADCLI = """
TITLE "Cadastro de Clientes"
@ 1,1 SAY "Nome:"
@ 1,20 GET nome
@ 2,1 SAY "CPF:"
@ 2,20 GET cpf
@ 3,1 SAY "Email:"
@ 3,20 GET email
USE CLIENTES
APPEND BLANK
REPLACE nome WITH m.nome, cpf WITH m.cpf, email WITH m.email
"""


def _b64(texto: str) -> str:
    return base64.b64encode(texto.encode("utf-8")).decode("ascii")


class SynthesizeReusesKnowledgeBaseTests(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        tmp = Path(self.tmpdir.name)
        self.db_path = str(tmp / "test.db")
        self.source_dir = tmp / "fontes"
        self.source_dir.mkdir()
        (self.source_dir / "cadcli.prg").write_text(FONTE_CADCLI, encoding="utf-8")
        self.con = connect(self.db_path)
        init_db(self.con)
        self.con.execute(
            "INSERT INTO users(username,password_hash,role,created_at_ms) "
            "VALUES('admin','x','admin',1)")

    def tearDown(self):
        self.con.close()
        self.tmpdir.cleanup()

    def _make_capture(self, capture_id: int = 1) -> Path:
        """Captura com trilha auditável nativa (deterministic_input)."""
        log_dir = Path(self.tmpdir.name) / f"cap{capture_id}"
        log_dir.mkdir()
        eventos = [
            {"type": "session_start", "session_id": "s1", "seq_global": 1},
            {"type": "deterministic_input", "session_id": "s1", "seq_global": 2,
             "screen_sig": "L=3;W=40;LBL=Cadastro de Clientes",
             "screen_sample": "Cadastro de Clientes", "norm_len": 120,
             "key_b64": _b64("JOSE DA SILVA\r")},
            {"type": "deterministic_input", "session_id": "s1", "seq_global": 3,
             "screen_sig": "L=3;W=40;LBL=Cadastro de Clientes",
             "screen_sample": "Cadastro de Clientes", "norm_len": 120,
             "key_b64": _b64("123.456.789-09\r")},
            {"type": "session_end", "session_id": "s1", "seq_global": 4},
        ]
        (log_dir / "audit-20260807-000000.part001.jsonl").write_text(
            "\n".join(json.dumps(e) for e in eventos), encoding="utf-8")
        self.con.execute(
            """INSERT INTO capture_sessions
               (session_uuid, status, created_by, created_by_username,
                started_at_ms, log_dir)
               VALUES (?,?,?,?,?,?)""",
            (f"uuid-cap{capture_id}", "finished", 1, "admin", 1, str(log_dir)))
        self.con.commit()
        return log_dir

    def test_analyze_source_persists_bindings(self):
        resumo = plan_svc.analyze_source_payload(self.con, str(self.source_dir))
        self.assertGreater(resumo["entities"], 0)

        n = self.con.execute(
            "SELECT COUNT(*) FROM screen_entity_bindings").fetchone()[0]
        self.assertGreater(
            n, 0, "analyze-source deve persistir screen_entity_bindings")

    def test_synthesize_reuses_db_without_reparsing_source(self):
        plan_svc.analyze_source_payload(self.con, str(self.source_dir))
        self._make_capture(1)

        # Se o synthesize tentar re-parsear o fonte, explode — a base do
        # banco deve bastar.
        with mock.patch(
                "dakota_gateway.synthetic.journey_synthesizer.SourceParser",
                side_effect=RuntimeError("re-parse proibido")):
            resultado = synth_svc.synthesize_capture(
                self.con, 1, source_dir=str(self.source_dir),
                samples=2, seed=42, name="cap-teste")

        self.assertTrue(resultado["ok"])
        self.assertGreater(
            resultado["mapped_inputs"], 0,
            "com a base do banco, inputs devem mapear para entidade.campo")
        self.assertEqual(resultado["unmapped_inputs"], 0)


if __name__ == "__main__":
    unittest.main()

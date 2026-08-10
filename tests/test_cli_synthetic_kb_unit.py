"""Regressão: CLI synthetic — analyze-source persiste bindings e o
journey synthesize reusa a knowledge base do banco (paridade com a API).

Antes da correção:
1. ``analyze-source`` via CLI gravava entidades mas NUNCA bindings —
   só o endpoint HTTP (synthetic_plan_service) persistia
   ``screen_entity_bindings``; quem analisava o fonte pelo CLI ficava
   com a base incompleta;
2. ``journey template``/``journey synthesize`` ignoravam a base do banco
   e re-parseavam o fonte inteiro a cada execução (~25 min no AIX).

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

from dakota_gateway import cli
from dakota_gateway.state_db import connect

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


class CliSyntheticKnowledgeBaseTests(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        tmp = Path(self.tmpdir.name)
        self.db_path = str(tmp / "test.db")
        self.source_dir = tmp / "fontes"
        self.source_dir.mkdir()
        (self.source_dir / "cadcli.prg").write_text(FONTE_CADCLI, encoding="utf-8")

        self.capture = tmp / "audit-test.jsonl"
        eventos = [
            {"type": "session_start", "session_id": "s1", "seq_global": 1},
            {"type": "deterministic_input", "session_id": "s1", "seq_global": 2,
             "screen_sig": "L=3;W=40;LBL=Cadastro de Clientes",
             "screen_sample": "Cadastro de Clientes\nNome: ____\nCPF: ____\n",
             "norm_len": 120, "key_b64": _b64("JOSE DA SILVA\r")},
            {"type": "deterministic_input", "session_id": "s1", "seq_global": 3,
             "screen_sig": "L=3;W=40;LBL=Cadastro de Clientes",
             "screen_sample": "Cadastro de Clientes\nNome: ____\nCPF: ____\n",
             "norm_len": 120, "key_b64": _b64("123.456.789-09\r")},
            {"type": "session_end", "session_id": "s1", "seq_global": 4},
        ]
        self.capture.write_text(
            "\n".join(json.dumps(e) for e in eventos), encoding="utf-8")

    def tearDown(self):
        self.tmpdir.cleanup()

    def _run_cli(self, *args: str) -> int:
        return cli.main(list(args))

    def test_analyze_source_persiste_bindings(self):
        rc = self._run_cli(
            "synthetic", "--db", self.db_path,
            "analyze-source", "--source-dir", str(self.source_dir))
        self.assertEqual(rc, 0)

        con = connect(self.db_path)
        try:
            n = con.execute(
                "SELECT COUNT(*) FROM screen_entity_bindings").fetchone()[0]
        finally:
            con.close()
        self.assertGreater(
            n, 0, "analyze-source via CLI deve persistir screen_entity_bindings")

    def test_journey_synthesize_reusa_base_sem_reparse(self):
        rc = self._run_cli(
            "synthetic", "--db", self.db_path,
            "analyze-source", "--source-dir", str(self.source_dir))
        self.assertEqual(rc, 0)

        out_dir = Path(self.tmpdir.name) / "out"
        # Se o synthesize tentar re-parsear o fonte, explode — a base do
        # banco deve bastar (mesma garantia do endpoint HTTP).
        with mock.patch(
                "dakota_gateway.synthetic.journey_synthesizer.SourceParser",
                side_effect=RuntimeError("re-parse proibido")):
            rc = self._run_cli(
                "synthetic", "--db", self.db_path,
                "journey", "synthesize",
                "--capture", str(self.capture),
                "--source-dir", str(self.source_dir),
                "--samples", "2", "--seed", "42",
                "--out", str(out_dir))
        self.assertEqual(rc, 0)

        report = json.loads((out_dir / "report.json").read_text(encoding="utf-8"))
        self.assertIn("knowledge_source=db", report["evidence"])
        self.assertGreater(report["mapped_inputs"], 0)
        self.assertEqual(report["unmapped_inputs"], 0)


if __name__ == "__main__":
    unittest.main()

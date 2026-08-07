"""Regressão: CaptureParametrizer deve consumir eventos deterministic_input.

A trilha auditável real do gateway grava a tela estável + input como eventos
``deterministic_input`` (com ``screen_sig``/``screen_sample`` e o input em
``key_text`` ou ``key_b64``) — NÃO como ``checkpoint`` + ``bytes.key_text``.
Sem suporte a esse formato, a síntese a partir de captura real produzia
template vazio (0 telas, 0 inputs) e exigia um conversor manual fora do fluxo
oficial (dev/convert_capture_for_synthetic.py). Gap evidenciado na captura 13
do AIX (121 deterministic_input, 0 checkpoint).

O teste DEVE FALHAR antes da correção e PASSAR depois dela.
"""

from __future__ import annotations

import base64
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GATEWAY_DIR = ROOT / "gateway"
sys.path.insert(0, str(GATEWAY_DIR))

from dakota_gateway.synthetic.capture_parametrizer import CaptureParametrizer


def _b64(texto: str) -> str:
    return base64.b64encode(texto.encode("utf-8")).decode("ascii")


class CaptureParametrizerDeterministicInputTests(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.d = Path(self.tmpdir.name)
        self.param = CaptureParametrizer()

    def tearDown(self):
        self.tmpdir.cleanup()

    def _write_jsonl(self, events: list[dict]) -> str:
        p = self.d / "capture.jsonl"
        p.write_text("\n".join(json.dumps(e) for e in events))
        return str(p)

    def test_deterministic_input_produces_screens_and_inputs(self):
        events = [
            {"type": "session_start", "session_id": "s1", "seq_global": 1},
            {"type": "deterministic_input", "session_id": "s1", "seq_global": 3,
             "screen_sig": "L=7;W=36;LBL=Digite a sua opcao",
             "screen_sample": "Digite a sua opcao", "norm_len": 103,
             "key_b64": _b64("1\r")},
            {"type": "deterministic_input", "session_id": "s1", "seq_global": 9,
             "screen_sig": "L=3;W=40;LBL=Nome do cliente",
             "screen_sample": "Nome do cliente", "norm_len": 210,
             "key_b64": _b64("JOSE DA SILVA\r")},
            {"type": "session_end", "session_id": "s1", "seq_global": 10},
        ]
        tmpl = self.param.analyze_capture(self._write_jsonl(events))

        self.assertEqual(tmpl.screen_sequence,
                         ["L=7;W=36;LBL=Digite a sua opcao",
                          "L=3;W=40;LBL=Nome do cliente"])
        self.assertEqual(len(tmpl.screen_contexts), 2)
        self.assertEqual(tmpl.screen_contexts[0]["inputs"], ["1", "{KEY:ENTER}"])
        self.assertEqual(tmpl.screen_contexts[1]["inputs"],
                         ["JOSE DA SILVA", "{KEY:ENTER}"])
        self.assertEqual(tmpl.metadata["total_inputs"], 4)

    def test_same_screen_sig_groups_consecutive_inputs(self):
        """Inputs seguidos na mesma tela estável ficam no mesmo contexto."""
        events = [
            {"type": "deterministic_input", "session_id": "s1", "seq_global": 3,
             "screen_sig": "SIG-A", "screen_sample": "Tela A", "norm_len": 100,
             "key_b64": _b64("CAMPO1")},
            {"type": "deterministic_input", "session_id": "s1", "seq_global": 4,
             "screen_sig": "SIG-A", "screen_sample": "Tela A", "norm_len": 100,
             "key_b64": _b64("CAMPO2\r")},
        ]
        tmpl = self.param.analyze_capture(self._write_jsonl(events))

        self.assertEqual(len(tmpl.screen_contexts), 1)
        self.assertEqual(tmpl.screen_contexts[0]["inputs"],
                         ["CAMPO1", "CAMPO2", "{KEY:ENTER}"])

    def test_deterministic_input_key_text_tambem_aceito(self):
        """Quando o evento já traz key_text (sem key_b64), usar direto."""
        events = [
            {"type": "deterministic_input", "session_id": "s1", "seq_global": 3,
             "screen_sig": "SIG-B", "screen_sample": "Tela B", "norm_len": 80,
             "key_text": "VALOR\t"},
        ]
        tmpl = self.param.analyze_capture(self._write_jsonl(events))

        self.assertEqual(tmpl.screen_contexts[0]["inputs"],
                         ["VALOR", "{KEY:TAB}"])

    def test_checkpoint_bytes_flow_unchanged(self):
        """O formato antigo (checkpoint + bytes.key_text) continua igual."""
        events = [
            {"type": "checkpoint", "session_id": "s1", "screen_sig": "S1",
             "screen_sample": "Tela 1", "seq_global": 1, "norm_len": 400},
            {"type": "bytes", "key_text": "123"},
            {"type": "bytes", "key_text": "\r"},
        ]
        tmpl = self.param.analyze_capture(self._write_jsonl(events))

        self.assertEqual(tmpl.screen_sequence, ["S1"])
        self.assertEqual(tmpl.screen_contexts[0]["inputs"],
                         ["123", "{KEY:ENTER}"])


if __name__ == "__main__":
    unittest.main()

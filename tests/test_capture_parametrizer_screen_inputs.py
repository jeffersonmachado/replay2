#!/usr/bin/env python3
"""Testa que screen_contexts contem inputs por tela no CaptureParametrizer."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GATEWAY_DIR = ROOT / "gateway"
sys.path.insert(0, str(GATEWAY_DIR))

from dakota_gateway.synthetic.capture_parametrizer import CaptureParametrizer


class CaptureParametrizerScreenInputsTests(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.d = Path(self.tmpdir.name)

    def tearDown(self):
        self.tmpdir.cleanup()

    def _write_jsonl(self, events: list[dict]) -> str:
        p = self.d / "test.jsonl"
        p.write_text("\n".join(json.dumps(e) for e in events))
        return str(p)

    def test_screen_contexts_have_inputs(self):
        """Cada screen_context deve conter seus inputs."""
        events = [
            {"type": "checkpoint", "session_id": "s1", "screen_sig": "S1",
             "screen_sample": "Cadastro Cliente", "seq_global": 1, "norm_len": 400},
            {"type": "bytes", "key_text": "123.456.789-09"},
            {"type": "checkpoint", "session_id": "s1", "screen_sig": "S2",
             "screen_sample": "Cadastro Produto", "seq_global": 2, "norm_len": 410},
            {"type": "bytes", "key_text": "PRODUTO X"},
        ]
        path = self._write_jsonl(events)

        cp = CaptureParametrizer()
        template = cp.analyze_capture(path)

        self.assertEqual(len(template.screen_contexts), 2)
        ctx0 = template.screen_contexts[0]
        ctx1 = template.screen_contexts[1]

        self.assertIn("inputs", ctx0)
        self.assertIn("inputs", ctx1)
        self.assertIn("input_start", ctx0)
        self.assertIn("input_end", ctx0)

        self.assertEqual(ctx0["inputs"], ["123.456.789-09"])
        self.assertEqual(ctx1["inputs"], ["PRODUTO X"])

    def test_input_start_end_correct(self):
        """input_start e input_end devem refletir indices no array global."""
        events = [
            {"type": "checkpoint", "session_id": "s1", "screen_sig": "S1",
             "screen_sample": "Tela 1", "seq_global": 1, "norm_len": 400},
            {"type": "bytes", "key_text": "A"},
            {"type": "bytes", "key_text": "B"},
            {"type": "checkpoint", "session_id": "s1", "screen_sig": "S2",
             "screen_sample": "Tela 2", "seq_global": 2, "norm_len": 410},
            {"type": "bytes", "key_text": "C"},
        ]
        path = self._write_jsonl(events)

        cp = CaptureParametrizer()
        template = cp.analyze_capture(path)

        self.assertEqual(len(template.screen_contexts), 2)
        ctx0 = template.screen_contexts[0]
        ctx1 = template.screen_contexts[1]

        self.assertEqual(ctx0["input_start"], 0)
        self.assertEqual(ctx0["input_end"], 2)
        self.assertEqual(ctx0["inputs"], ["A", "B"])
        self.assertEqual(ctx1["input_start"], 2)
        self.assertEqual(ctx1["input_end"], 3)
        self.assertEqual(ctx1["inputs"], ["C"])

    def test_enter_tab_esc_f10_preserved(self):
        """ENTER, TAB, ESC, F10 devem ser preservados como {KEY:...}."""
        events = [
            {"type": "checkpoint", "session_id": "s1", "screen_sig": "S1",
             "screen_sample": "Tela", "seq_global": 1, "norm_len": 400},
            {"type": "bytes", "key_text": "\r"},
            {"type": "bytes", "key_text": "\t"},
            {"type": "bytes", "key_text": "\x1b"},
            {"type": "bytes", "key_text": "F10"},
        ]
        path = self._write_jsonl(events)

        cp = CaptureParametrizer()
        template = cp.analyze_capture(path)

        inputs = template.metadata.get("original_inputs", [])
        self.assertIn("{KEY:ENTER}", inputs)
        self.assertIn("{KEY:TAB}", inputs)
        self.assertIn("{KEY:ESC}", inputs)
        # F10 nao vira {KEY:...} — eh preservado como string
        self.assertIn("F10", inputs)

    def test_menu_option_preserved(self):
        """Opcao de menu deve ser preservada."""
        events = [
            {"type": "checkpoint", "session_id": "s1", "screen_sig": "S1",
             "screen_sample": "Menu", "seq_global": 1, "norm_len": 400},
            {"type": "bytes", "key_text": "1"},
            {"type": "bytes", "key_text": "2"},
        ]
        path = self._write_jsonl(events)

        cp = CaptureParametrizer()
        template = cp.analyze_capture(path)

        inputs = template.metadata.get("original_inputs", [])
        self.assertIn("1", inputs)
        self.assertIn("2", inputs)


if __name__ == "__main__":
    unittest.main()

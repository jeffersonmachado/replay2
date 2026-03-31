#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GATEWAY_DIR = ROOT / "gateway"
sys.path.insert(0, str(GATEWAY_DIR))

from dakota_gateway.screen import (
    build_screen_snapshot,
    classify_input_bytes,
    input_text_sample,
    normalize_screen,
    signature_from_screen,
)


class ScreenContractTests(unittest.TestCase):
    def _load_case(self, case_id: str) -> tuple[str, str, str]:
        base = ROOT / "tests" / "contracts" / "screens"
        raw = (base / f"{case_id}_raw.txt").read_text(encoding="utf-8")
        normalized = (base / f"{case_id}_normalized.txt").read_text(encoding="utf-8")
        signature = (base / f"{case_id}_signature.txt").read_text(encoding="utf-8").strip()
        return raw, normalized, signature

    def test_contract_cases_keep_python_normalization_and_signature_stable(self):
        for case_id in ("case_001", "case_002"):
            with self.subTest(case_id=case_id):
                raw, expected_normalized, expected_signature = self._load_case(case_id)
                normalized = normalize_screen(raw)
                signature = signature_from_screen(normalized)

                self.assertEqual(normalized, expected_normalized)
                self.assertEqual(signature, expected_signature)

    def test_build_screen_snapshot_exposes_signature_hash_and_sample(self):
        raw = "\x1b[2J\x1b[H+ MENU +\nOpcao: 1\n"
        snapshot = build_screen_snapshot(raw)

        self.assertTrue(snapshot.screen_sig.startswith("L="))
        self.assertEqual(snapshot.norm_len, len(snapshot.norm_text))
        self.assertIn("MENU", snapshot.screen_sample)
        self.assertEqual(len(snapshot.norm_sha256), 64)

    def test_input_helpers_classify_control_and_text_inputs(self):
        self.assertEqual(classify_input_bytes(b"\r"), "enter")
        self.assertEqual(classify_input_bytes(b"A"), "printable")
        self.assertEqual(classify_input_bytes(b"abc"), "multi_char")
        self.assertEqual(classify_input_bytes(b"\x1b[A"), "ansi_sequence")
        self.assertEqual(input_text_sample(b"\r\tA"), "\\r\\tA")


if __name__ == "__main__":
    unittest.main()

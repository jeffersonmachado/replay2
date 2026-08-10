#!/usr/bin/env python3
"""Testes unitários da classificação de falhas de checkpoint em replay sintético.

Rec 1 (run 12, captura 13): em run sintética com ``send-anyway``, o
"checkpoint não estabilizou" com tela observada presente é divergência de
conteúdo esperada (dados substituídos) — deve sair como
``screen_divergence``/``medium``, não ``timeout``/``high``. Sem tela
observada continua ``timeout``/``critical`` (falha real). Runs não
sintéticas preservam a classificação atual.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

GATEWAY_DIR = Path(__file__).resolve().parents[1] / "gateway"
sys.path.insert(0, str(GATEWAY_DIR))

from dakota_gateway.replay_failures import classify_checkpoint_failure

SYNTHETIC_PARAMS = {
    "synthetic": True,
    "on_deterministic_mismatch": "send-anyway",
    "input_mode": "deterministic",
}


class CheckpointFailureClassificationTests(unittest.TestCase):
    def test_synthetic_send_anyway_com_tela_observada_e_screen_divergence(self):
        failure_type, severity, reason = classify_checkpoint_failure(
            expected_sig="sha256:esperado",
            observed_sig="sha256:observado",
            params=SYNTHETIC_PARAMS,
            timeout_reached=True,
            concurrent_mode=False,
        )
        self.assertEqual(failure_type, "screen_divergence")
        self.assertEqual(severity, "medium")
        self.assertIn("sintétic", reason)

    def test_synthetic_send_anyway_sem_tela_observada_segue_timeout_critical(self):
        failure_type, severity, _ = classify_checkpoint_failure(
            expected_sig="sha256:esperado",
            observed_sig="",
            params=SYNTHETIC_PARAMS,
            timeout_reached=True,
            concurrent_mode=False,
        )
        self.assertEqual(failure_type, "timeout")
        self.assertEqual(severity, "critical")

    def test_run_comum_preserva_timeout_high(self):
        for params in ({"on_deterministic_mismatch": "send-anyway"}, {}, None):
            failure_type, severity, _ = classify_checkpoint_failure(
                expected_sig="sha256:esperado",
                observed_sig="sha256:observado",
                params=params,
                timeout_reached=True,
                concurrent_mode=False,
            )
            self.assertEqual(failure_type, "timeout")
            self.assertEqual(severity, "high")

    def test_sintetico_sem_send_anyway_preserva_timeout_high(self):
        failure_type, severity, _ = classify_checkpoint_failure(
            expected_sig="sha256:esperado",
            observed_sig="sha256:observado",
            params={"synthetic": True, "on_deterministic_mismatch": "fail"},
            timeout_reached=True,
            concurrent_mode=False,
        )
        self.assertEqual(failure_type, "timeout")
        self.assertEqual(severity, "high")


if __name__ == "__main__":
    unittest.main()

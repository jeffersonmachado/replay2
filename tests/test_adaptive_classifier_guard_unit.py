#!/usr/bin/env python3
"""Testes do classificador de ações e do safety guard (§5 e §9).

Classificação determinística de bytes de terminal + regras de bloqueio
conservador. Nenhuma IA envolvida.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "gateway"))

from dakota_gateway.replay_control.action_classifier import (
    ActionClass,
    classify_bytes,
)
from dakota_gateway.replay_control.safety_guard import (
    GuardBlockReason,
    SafetyGuard,
)


class ActionClassifierTests(unittest.TestCase):
    """classify_bytes é determinístico e conservador no desconhecido."""

    def test_printable_ascii(self):
        self.assertEqual(classify_bytes(b"ABC123").action_class, ActionClass.PRINTABLE_INPUT)

    def test_printable_utf8_multibyte(self):
        self.assertEqual(
            classify_bytes("ação çã".encode("utf-8")).action_class,
            ActionClass.PRINTABLE_INPUT,
        )

    def test_enter(self):
        self.assertEqual(classify_bytes(b"\r").action_class, ActionClass.ENTER)
        self.assertEqual(classify_bytes(b"\n").action_class, ActionClass.ENTER)

    def test_tab(self):
        self.assertEqual(classify_bytes(b"\t").action_class, ActionClass.TAB)

    def test_esc_isolado(self):
        self.assertEqual(classify_bytes(b"\x1b").action_class, ActionClass.ESC)

    def test_function_keys(self):
        for data in (b"\x1bOP", b"\x1bOQ", b"\x1b[15~", b"\x1b[21~", b"\x1b[24~"):
            with self.subTest(data=data):
                self.assertEqual(classify_bytes(data).action_class, ActionClass.FUNCTION_KEY)

    def test_setas_sao_navegacao(self):
        for data in (b"\x1b[A", b"\x1b[B", b"\x1b[C", b"\x1b[D"):
            with self.subTest(data=data):
                self.assertEqual(classify_bytes(data).action_class, ActionClass.NAVIGATION)

    def test_backspace_e_field_edit(self):
        self.assertEqual(classify_bytes(b"\x7f").action_class, ActionClass.FIELD_EDIT)
        self.assertEqual(classify_bytes(b"\x08").action_class, ActionClass.FIELD_EDIT)

    def test_desconhecido_e_unknown(self):
        self.assertEqual(classify_bytes(b"\x01").action_class, ActionClass.UNKNOWN)  # Ctrl-A
        self.assertEqual(classify_bytes(b"\x1b[999~").action_class, ActionClass.UNKNOWN)
        self.assertEqual(classify_bytes(b"").action_class, ActionClass.UNKNOWN)
        self.assertEqual(classify_bytes(b"\xff\xfe").action_class, ActionClass.UNKNOWN)

    def test_hint_key_kind_da_trilha_vence(self):
        self.assertEqual(
            classify_bytes(b"\r", key_kind="enter").action_class, ActionClass.ENTER,
        )
        self.assertEqual(
            classify_bytes(b"", key_kind="wait").action_class, ActionClass.EXPLICIT_WAIT,
        )
        self.assertEqual(
            classify_bytes(b"9", key_kind="printable").action_class, ActionClass.PRINTABLE_INPUT,
        )

    def test_hint_inconsistente_cai_para_bytes(self):
        # key_kind inválido/desconhecido não é confiável: classifica pelos bytes
        self.assertEqual(
            classify_bytes(b"\r", key_kind="printable").action_class,
            ActionClass.ENTER,
        )

    def test_batchable_apenas_printable(self):
        self.assertTrue(classify_bytes(b"ABC").batchable)
        self.assertFalse(classify_bytes(b"\r").batchable)
        self.assertFalse(classify_bytes(b"\t").batchable)
        self.assertFalse(classify_bytes(b"\x1b[21~").batchable)
        self.assertFalse(classify_bytes(b"\x01").batchable)

    def test_determinismo(self):
        for data in (b"ABC", b"\r", b"\t", b"\x1b", b"\x1b[15~", b"\x01"):
            self.assertEqual(classify_bytes(data), classify_bytes(data))


class SafetyGuardTests(unittest.TestCase):
    """O guard bloqueia otimização em todas as condições inseguras (§9)."""

    def test_permitido_em_condicoes_limpas(self):
        guard = SafetyGuard()
        decision = guard.evaluate_batch(
            action_classes=[ActionClass.PRINTABLE_INPUT, ActionClass.PRINTABLE_INPUT],
            checkpoint_pending=False,
            delta_positive=False,
            has_evidence=False,
        )
        self.assertTrue(decision.allowed)

    def test_bloqueia_acao_desconhecida(self):
        guard = SafetyGuard()
        decision = guard.evaluate_batch(
            action_classes=[ActionClass.PRINTABLE_INPUT, ActionClass.UNKNOWN],
            checkpoint_pending=False,
            delta_positive=False,
            has_evidence=False,
        )
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason_code, GuardBlockReason.UNKNOWN_ACTION)

    def test_bloqueia_checkpoint_pendente(self):
        guard = SafetyGuard()
        decision = guard.evaluate_batch(
            action_classes=[ActionClass.PRINTABLE_INPUT, ActionClass.PRINTABLE_INPUT],
            checkpoint_pending=True,
            delta_positive=False,
            has_evidence=False,
        )
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason_code, GuardBlockReason.CHECKPOINT_PENDING)

    def test_bloqueia_collapse_de_delta_positivo_sem_evidencia(self):
        guard = SafetyGuard()
        decision = guard.evaluate_batch(
            action_classes=[ActionClass.PRINTABLE_INPUT, ActionClass.PRINTABLE_INPUT],
            checkpoint_pending=False,
            delta_positive=True,
            has_evidence=False,
        )
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason_code, GuardBlockReason.NO_EVIDENCE)

    def test_permite_collapse_de_delta_com_evidencia(self):
        guard = SafetyGuard()
        decision = guard.evaluate_batch(
            action_classes=[ActionClass.PRINTABLE_INPUT, ActionClass.PRINTABLE_INPUT],
            checkpoint_pending=False,
            delta_positive=True,
            has_evidence=True,
        )
        self.assertTrue(decision.allowed)

    def test_divergencia_forca_conservador(self):
        guard = SafetyGuard()
        guard.record_divergence("checkpoint mismatch")
        decision = guard.evaluate_batch(
            action_classes=[ActionClass.PRINTABLE_INPUT, ActionClass.PRINTABLE_INPUT],
            checkpoint_pending=False,
            delta_positive=False,
            has_evidence=True,
        )
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason_code, GuardBlockReason.DIVERGED_STATE)

    def test_ressincronizacao_libera(self):
        guard = SafetyGuard()
        guard.record_divergence("x")
        guard.record_resync()
        decision = guard.evaluate_batch(
            action_classes=[ActionClass.PRINTABLE_INPUT, ActionClass.PRINTABLE_INPUT],
            checkpoint_pending=False,
            delta_positive=False,
            has_evidence=False,
        )
        self.assertTrue(decision.allowed)

    def test_teclas_criticas_nunca_entram_em_batch(self):
        guard = SafetyGuard()
        for cls in (ActionClass.ENTER, ActionClass.ESC, ActionClass.FUNCTION_KEY,
                    ActionClass.TAB, ActionClass.NAVIGATION, ActionClass.SUBMIT,
                    ActionClass.QUERY, ActionClass.SCREEN_TRANSITION,
                    ActionClass.EXPLICIT_WAIT, ActionClass.CHECKPOINT):
            with self.subTest(cls=cls):
                decision = guard.evaluate_batch(
                    action_classes=[ActionClass.PRINTABLE_INPUT, cls],
                    checkpoint_pending=False,
                    delta_positive=False,
                    has_evidence=True,
                )
                self.assertFalse(decision.allowed)
                self.assertEqual(decision.reason_code, GuardBlockReason.CRITICAL_TRANSITION)


if __name__ == "__main__":
    unittest.main()

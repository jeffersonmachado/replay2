#!/usr/bin/env python3
"""Testes para detecção de erros em telas de sistemas legados."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GATEWAY_DIR = ROOT / "gateway"
sys.path.insert(0, str(GATEWAY_DIR))

from dakota_gateway.synthetic.error_detector import ErrorDetector, DetectedError
from dakota_gateway.synthetic.journey_verifier import (
    JourneyVerifier,
    JourneyVerificationResult,
)
from dakota_gateway.synthetic.journey import JourneyDefinition, JourneyStep


class ErrorDetectorTests(unittest.TestCase):

    def setUp(self):
        self.detector = ErrorDetector()

    def test_detect_fatal_error(self):
        screen = "Ocorreu um erro fatal no sistema\nContate o administrador"
        errors = self.detector.detect(screen)
        self.assertGreaterEqual(len(errors), 1)
        self.assertTrue(any(e.error_type == "fatal" for e in errors))

    def test_detect_validation_error(self):
        screen = "Codigo do banco invalido para duplicata: 999"
        errors = self.detector.detect(screen)
        self.assertGreaterEqual(len(errors), 1)
        self.assertTrue(any(e.error_type == "validation" for e in errors))

    def test_detect_validation_error_2(self):
        screen = "@10,10 GET v_campo VALID v_campo > 0 ERROR [Valor deve ser positivo]"
        errors = self.detector.detect(screen)
        self.assertTrue(any(e.error_type == "validation" for e in errors))

    def test_detect_not_found(self):
        screen = "Registro nao encontrado\nVerifique o codigo informado"
        errors = self.detector.detect(screen)
        self.assertGreaterEqual(len(errors), 1)
        self.assertTrue(any(e.error_type == "not_found" for e in errors))

    def test_detect_not_found_2(self):
        screen = "Condicao ou Banco nao encontrado para espelho: 001"
        errors = self.detector.detect(screen)
        self.assertTrue(any(e.error_type == "not_found" for e in errors))

    def test_detect_permission_error(self):
        screen = "Acesso negado. Usuario sem permissao para esta operacao."
        errors = self.detector.detect(screen)
        self.assertGreaterEqual(len(errors), 1)
        self.assertTrue(any(e.error_type == "permission" for e in errors))

    def test_detect_lock_error(self):
        screen = "Registro bloqueado por outro usuario. Tente novamente."
        errors = self.detector.detect(screen)
        self.assertGreaterEqual(len(errors), 1)
        self.assertTrue(any(e.error_type == "lock" for e in errors))

    def test_detect_duplicate_key(self):
        screen = "CHAVE DUPLICADA: Registro ja existe na tabela CLIENTES"
        errors = self.detector.detect(screen)
        self.assertGreaterEqual(len(errors), 1)
        self.assertTrue(any(e.error_type == "data_error" for e in errors))

    def test_detect_timeout(self):
        screen = "TIMEOUT: Tempo de espera esgotado para o servidor"
        errors = self.detector.detect(screen)
        self.assertGreaterEqual(len(errors), 1)
        self.assertTrue(any(e.error_type == "timeout" for e in errors))

    def test_detect_file_not_found(self):
        screen = "Arquivo nao encontrado: /dados/clientes.dbf"
        errors = self.detector.detect(screen)
        self.assertGreaterEqual(len(errors), 1)
        self.assertTrue(any(e.error_type == "data_error" for e in errors))

    def test_detect_recital_specific_errors(self):
        test_cases = [
            ("SUBSCRIPT OUT OF RANGE at line 42", "fatal"),
            ("DATATYPE MISMATCH in field NOME", "data_error"),
            ("END OF FILE ENCOUNTERED on CLIENTES", "data_error"),
            ("RECORD OUT OF RANGE in area 3", "data_error"),
            ("WORKAREA NOT IN USE", "fatal"),
            ("UNRECOGNIZED COMMAND: XYZ", "fatal"),
            ("INSUFFICIENT MEMORY", "fatal"),
        ]
        for screen, expected_type in test_cases:
            with self.subTest(screen=screen):
                errors = self.detector.detect(screen)
                self.assertTrue(
                    any(e.error_type == expected_type for e in errors),
                    f"Esperado '{expected_type}' em: {screen}. Detectado: {[e.error_type for e in errors]}",
                )

    def test_clean_screen_no_errors(self):
        screen = """
        +--------------------------------------------------+
        | CADASTRO DE CLIENTES                             |
        |                                                  |
        | Codigo....: ______                               |
        | Nome......: ______                               |
        | CPF.......: ______                               |
        +--------------------------------------------------+
        """
        errors = self.detector.detect(screen)
        self.assertEqual(len(errors), 0)

    def test_classify_screen_error(self):
        screen = "Erro fatal: nao foi possivel abrir arquivo CLIENTES.DBF"
        result = self.detector.classify_screen(screen, "cadastro_clientes", 0)
        self.assertEqual(result["status"], "error")
        self.assertGreater(len(result.get("all_errors", [])), 0)

    def test_classify_screen_ok(self):
        screen = "+--- MENU PRINCIPAL ---+\n| 1. Cadastros        |\n| 2. Financeiro       |"
        result = self.detector.classify_screen(screen, "menu_principal", 0)
        self.assertEqual(result["status"], "ok")

    def test_classify_possible_error(self):
        screen = "Não foi possível completar a operação solicitada"
        result = self.detector.classify_screen(screen, "cadastro", 1)
        self.assertIn(result["status"], ["warning", "error"])

    def test_error_with_context(self):
        screen = "ERRO [Valor deve ser maior que zero]"
        errors = self.detector.detect(screen, {
            "screen_signature": "cadastro_produto",
            "step_order": 3,
            "journey_id": "cad_prod",
            "session_index": 0,
        })
        self.assertGreaterEqual(len(errors), 1)
        # Verifica que o contexto foi propagado
        for e in errors:
            self.assertIsNotNone(e.error_type)
            self.assertIsNotNone(e.severity)

    def test_detect_multiple_errors(self):
        screen = """
        Erro fatal no processamento
        Registro nao encontrado: CLIENTE 999
        Acesso negado para operacao
        """
        errors = self.detector.detect(screen)
        error_types = {e.error_type for e in errors}
        self.assertIn("fatal", error_types)
        self.assertIn("not_found", error_types)
        self.assertIn("permission", error_types)

    def test_detect_all_screens(self):
        screens = [
            {"text": "Registro nao encontrado", "screen_sig": "cad_cli", "step_order": 0},
            {"text": "CHAVE DUPLICADA: CPF", "screen_sig": "cad_cli", "step_order": 1},
            {"text": "+-- MENU --+", "screen_sig": "menu", "step_order": 2},
        ]
        errors = self.detector.detect_all(screens)
        self.assertGreaterEqual(len(errors), 2)  # "não encontrado" + "duplicada" e/ou "chave duplicada"


class JourneyVerifierTests(unittest.TestCase):

    def setUp(self):
        self.verifier = JourneyVerifier()

    def test_verify_session_all_ok(self):
        journey = JourneyDefinition(
            journey_id="test",
            name="Test",
            steps=[
                JourneyStep(step_order=0, screen_id="menu", action="navigate"),
                JourneyStep(step_order=1, screen_id="cadastro", action="input"),
            ],
        )
        screens = [
            {"screen_text": "+-- MENU --+\n| 1. Cadastro |", "screen_sig": "menu"},
            {"screen_text": "+-- CADASTRO --+\n| Nome: ____ |", "screen_sig": "cadastro"},
        ]

        result = self.verifier.verify_session(journey, 0, screens)
        self.assertEqual(result.steps_passed, 2)
        self.assertEqual(result.steps_failed, 0)

    def test_verify_session_with_error(self):
        journey = JourneyDefinition(
            journey_id="test_err",
            name="Test Error",
            steps=[
                JourneyStep(step_order=0, screen_id="consulta", action="input"),
            ],
        )
        screens = [
            {"screen_text": "Registro nao encontrado. Verifique.", "screen_sig": "consulta"},
        ]

        result = self.verifier.verify_session(journey, 0, screens)
        self.assertGreaterEqual(result.steps_failed, 1)
        self.assertGreater(len(result.errors), 0)

    def test_verify_session_fatal_error(self):
        journey = JourneyDefinition(
            journey_id="test_fatal",
            name="Fatal Test",
            steps=[JourneyStep(step_order=0, screen_id="abertura", action="navigate")],
        )
        screens = [
            {"screen_text": "Ocorreu um erro fatal no sistema. WORKAREA NOT IN USE", "screen_sig": ""},
        ]

        result = self.verifier.verify_session(journey, 0, screens)
        self.assertGreaterEqual(result.steps_failed, 1)
        fatal_errors = [e for e in result.errors if e.error_type == "fatal"]
        self.assertGreater(len(fatal_errors), 0)

    def test_analyze_multiple_sessions(self):
        r1 = JourneyVerificationResult(
            journey_id="j1", session_index=0,
            total_steps=10, steps_passed=8, steps_failed=2,
            errors=[
                DetectedError(error_type="not_found", severity="low", pattern_matched="", line_text="não encontrado"),
                DetectedError(error_type="validation", severity="medium", pattern_matched="", line_text="inválido"),
            ],
        )
        r2 = JourneyVerificationResult(
            journey_id="j1", session_index=1,
            total_steps=10, steps_passed=10, steps_failed=0,
        )
        r3 = JourneyVerificationResult(
            journey_id="j1", session_index=2,
            total_steps=10, steps_passed=7, steps_failed=3,
            errors=[
                DetectedError(error_type="fatal", severity="critical", pattern_matched="", line_text="fatal"),
                DetectedError(error_type="timeout", severity="high", pattern_matched="", line_text="timeout"),
                DetectedError(error_type="not_found", severity="low", pattern_matched="", line_text="não encontrado"),
            ],
        )

        analysis = self.verifier.analyze_errors([r1, r2, r3])
        self.assertEqual(analysis["total_sessions"], 3)
        self.assertEqual(analysis["total_sessions_with_errors"], 2)
        self.assertEqual(analysis["total_errors"], 5)

    def test_journey_verification_result_properties(self):
        result = JourneyVerificationResult(
            total_steps=10, steps_passed=9, steps_failed=1,
        )
        self.assertFalse(result.passed)
        self.assertEqual(result.failure_rate_pct, 10.0)

        result2 = JourneyVerificationResult(
            total_steps=10, steps_passed=10, steps_failed=0,
        )
        self.assertTrue(result2.passed)
        self.assertEqual(result2.failure_rate_pct, 0.0)


if __name__ == "__main__":
    unittest.main()

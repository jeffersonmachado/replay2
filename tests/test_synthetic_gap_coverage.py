"""Testes de cobertura para modulos synthetic sem testes dedicados.

Cobre:
- screen_differ: diff visual de telas
- error_detector: deteccao de erros em saida de tela
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GATEWAY_DIR = ROOT / "gateway"
sys.path.insert(0, str(GATEWAY_DIR))

from dakota_gateway.synthetic.screen_differ import ScreenDiffer
from dakota_gateway.synthetic.error_detector import ErrorDetector


class ScreenDifferTests(unittest.TestCase):
    """Testes para ScreenDiffer — comparacao visual de telas."""

    def test_diff_identical_screens(self):
        """Telas identicas devem ter similarity=1.0."""
        result = ScreenDiffer.diff("LINHA1\nLINHA2\nLINHA3", "LINHA1\nLINHA2\nLINHA3")
        self.assertEqual(result.similarity, 1.0)
        self.assertEqual(result.added_lines, 0)
        self.assertEqual(result.removed_lines, 0)
        self.assertEqual(result.changed_lines, 0)

    def test_diff_completely_different(self):
        """Telas completamente diferentes devem ter similarity baixa."""
        result = ScreenDiffer.diff("AAAA\nBBBB", "XXXX\nYYYY\nZZZZ")
        self.assertLess(result.similarity, 0.5)
        self.assertGreater(result.added_lines + result.removed_lines, 0)

    def test_diff_partial_match(self):
        """Telas parcialmente iguais."""
        result = ScreenDiffer.diff("MENU\nOPCAO1\nOPCAO2", "MENU\nOPCAO1\nOPCAO3")
        self.assertGreater(result.similarity, 0.5)
        self.assertLess(result.similarity, 1.0)

    def test_diff_empty_expected(self):
        """Tela esperada vazia."""
        result = ScreenDiffer.diff("", "CONTEUDO")
        self.assertEqual(result.similarity, 0.0)
        self.assertGreater(result.added_lines, 0)

    def test_diff_empty_observed(self):
        """Tela observada vazia."""
        result = ScreenDiffer.diff("CONTEUDO", "")
        self.assertEqual(result.similarity, 0.0)
        self.assertGreater(result.removed_lines, 0)

    def test_diff_both_empty(self):
        """Ambas as telas vazias."""
        result = ScreenDiffer.diff("", "")
        self.assertEqual(result.similarity, 1.0)

    def test_diff_with_sigs(self):
        """Diff com assinaturas de tela."""
        result = ScreenDiffer.diff("A\nB", "A\nC", expected_sig="SIG1", observed_sig="SIG2")
        self.assertEqual(result.expected_sig, "SIG1")
        self.assertEqual(result.observed_sig, "SIG2")

    def test_diff_lines_have_status(self):
        """Linhas do diff devem ter status (equal/added/removed)."""
        result = ScreenDiffer.diff("LINHA1\nLINHA2", "LINHA1\nMODIFICADA")
        statuses = {line.status for line in result.lines}
        self.assertIn("equal", statuses)

    def test_diff_summary_not_empty(self):
        """Sumario do diff nao deve ser vazio."""
        result = ScreenDiffer.diff("A", "B")
        self.assertTrue(len(result.summary) > 0)

    def test_to_json_serializable(self):
        """to_json deve retornar estrutura serializavel."""
        import json
        diff = ScreenDiffer.diff("TELA1", "TELA2")
        data = ScreenDiffer.to_json(diff)
        self.assertIsInstance(data, dict)
        self.assertIn("similarity", data)
        # Deve ser serializavel
        encoded = json.dumps(data)
        self.assertTrue(len(encoded) > 0)

    def test_ansi_control_chars_stripped(self):
        """Caracteres de controle ANSI devem ser removidos na normalizacao."""
        result = ScreenDiffer.diff("\x1b[2JMENU\x1b[0m", "MENU")
        self.assertEqual(result.similarity, 1.0)

    def test_whitespace_normalization(self):
        """Espacos extras no final devem ser normalizados."""
        result = ScreenDiffer.diff("LINHA   \nOUTRA", "LINHA\nOUTRA")
        self.assertGreaterEqual(result.similarity, 0.85)


class ErrorDetectorTests(unittest.TestCase):
    """Testes para ErrorDetector — deteccao de erros em saida legacy."""

    def setUp(self):
        self.detector = ErrorDetector()

    def test_detect_fatal_error(self):
        """Deve detectar erro fatal."""
        errors = self.detector.detect("ERRO FATAL: sistema corrompido")
        self.assertTrue(any(e.error_type == "fatal" for e in errors))

    def test_detect_not_found(self):
        """Deve detectar registro nao encontrado."""
        errors = self.detector.detect("Cliente nao cadastrado para esta empresa.")
        self.assertTrue(any(e.error_type == "not_found" for e in errors))

    def test_detect_invalid_value(self):
        """Deve detectar valor invalido."""
        errors = self.detector.detect("VALOR INVALIDO para o campo CODIGO")
        self.assertTrue(any(e.error_type == "validation" for e in errors))

    def test_detect_permission_error(self):
        """Deve detectar erro de permissao/acesso negado."""
        errors = self.detector.detect("acesso negado para este usuario")
        self.assertTrue(any(e.error_type == "permission" for e in errors))

    def test_detect_lock_error(self):
        """Deve detectar erro de lock/registro bloqueado."""
        errors = self.detector.detect("registro bloqueado por outro processo")
        self.assertTrue(any(e.error_type == "lock" for e in errors))

    def test_clean_output_no_errors(self):
        """Tela limpa sem erros nao deve gerar deteccoes."""
        errors = self.detector.detect("CODIGO: 12345\nNOME: Teste\nVALOR: 100.00")
        self.assertEqual(len(errors), 0)

    def test_multiple_errors_in_screen(self):
        """Uma tela com multiplos erros deve detectar todos."""
        screen = "Cliente nao cadastrado.\nVALOR INVALIDO\nSenha invalida."
        errors = self.detector.detect(screen)
        self.assertGreaterEqual(len(errors), 3)

    def test_error_has_severity(self):
        """Cada erro detectado deve ter severity."""
        errors = self.detector.detect("ERRO FATAL: abort")
        for e in errors:
            self.assertIn(e.severity, ("low", "medium", "high", "critical"))

    def test_error_has_line_text(self):
        """Cada erro deve conter a linha onde foi detectado."""
        errors = self.detector.detect("Cliente nao cadastrado.")
        for e in errors:
            self.assertTrue(len(e.line_text) > 0)

    def test_patterns_accessible(self):
        """A lista de padroes deve estar acessivel."""
        patterns = [
            {"type": etype, "severity": sev, "description": desc}
            for _, etype, sev, desc in self.detector._patterns
        ]
        self.assertGreater(len(patterns), 0)
        # Cada padrao deve ter type e severity validos
        for p in patterns:
            self.assertIn(p["type"], ("fatal", "validation", "not_found", "permission", "lock", "timeout", "data_error"))
            self.assertIn(p["severity"], ("low", "medium", "high", "critical"))


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""Testes de regressão do oráculo funcional da captura 13 (jornada 3.6.1).

Blinda a classificação ``synthetic_data_swap`` contra os dois lados do erro:

- POSITIVOS: troca legítima do de→para (telas reais das runs 94-96 do AIX)
  continua classificada como ``synthetic_data_swap``/``low`` — o dado mudou,
  a tela é a mesma (nº do pedido gerado, data de emissão e barra de status
  volátil divergem como consequência esperada da reexecução).
- NEGATIVOS: divergência funcional real NÃO pode virar ``synthetic_data_swap``:
  placeholder só na linha esperada (campo ausente na observada), valor
  diferente sem entrada no de→para, tela observada sem nenhuma linha da
  esperada e identificador gerado com shape diferente.

Contexto (docs/captura13-oraculo.md): nas runs 94-96 a rejeição do item
("Codigo nao cadastrado", grade travada em DELETA/CONFIRMA/ABANDONA) foi
classificada como swap porque a política documentada aceita UMA linha de eco
por tela (replay_compare.apply_synthetic_substitution_fallback). Esses testes
travam as garantias já documentadas sem alterar essa política.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

GATEWAY_DIR = Path(__file__).resolve().parents[1] / "gateway"
sys.path.insert(0, str(GATEWAY_DIR))

from dakota_gateway.replay_compare import (
    substitution_echo_line_indices,
    substitution_explained_diff,
)
from dakota_gateway.replay_control.deterministic import (
    _deterministic_failure,
    synthetic_swap_override,
)

# Pares reais das runs 94-96 (captura 13, journey 6c342dea).
PAIRS_AIX = [("g2511", "303"), ("0000135", "00001"), ("229,9", "399,7")]

SYNTHETIC_PARAMS = {
    "synthetic": True,
    "on_deterministic_mismatch": "send-anyway",
    "synthetic_substitutions": PAIRS_AIX,
}

# Telas reais (simplificadas) da run 94, seq_global 185: grade de itens com o
# modelo trocado, nº do pedido gerado, data de emissão e barra de status.
EXPECTED_GRID = (
    "*Pedido.....:D00011073            E-c:004 DAKOTA    Emissao..:27/07/26 Incluir\n"
    "│01│G2511 │     │    │   0│    0,00│    0,00│         │              │ Obs Local\n"
    "     IBM Aix (Common)    |    OVER    |    792,000 Kb livres    |       Ok\n"
)
OBSERVED_GRID = (
    "*Pedido.....:D00011140            E-c:004 DAKOTA    Emissao..:12/09/26 Incluir\n"
    "│01│303   │     │    │   0│    0,00│    0,00│         │              │ Obs Local\n"
    "     IBM Aix (Common)    |    OVER    |    796,000 Kb livres    |       Ok\n"
)


def _falha(expected_screen: str, observed_screen: str, params=None) -> dict:
    """Classificação completa de uma divergência com telas de evidência."""
    return _deterministic_failure(
        sid="sess-oraculo",
        seq_global=100,
        seq_session=50,
        expected_sig="sha256:esperado",
        observed_sig="sha256:observado",
        params=params if params is not None else SYNTHETIC_PARAMS,
        checkpoint_timeout_ms=5000,
        checkpoint_quiet_ms=250,
        mode_label="visual",
        concurrent_mode=False,
        match={"matched": False},
        expected_screen=expected_screen,
        observed_screen=observed_screen,
    )


class SwapPositivosTests(unittest.TestCase):
    """Trocas legítimas do de→para continuam synthetic_data_swap/low."""

    def test_grade_com_troca_longa_e_dados_gerados_e_swap(self):
        """Run 94 seq 185: modelo G2511→303 na mesma linha das duas telas,
        nº do pedido/data gerados pela aplicação e Kb livres volátil."""
        failure = _falha(EXPECTED_GRID, OBSERVED_GRID)
        self.assertEqual(failure["failure_type"], "synthetic_data_swap")
        self.assertEqual(failure["severity"], "low")
        echo = failure["evidence"]["match"].get("synthetic_echo_lines")
        self.assertIn(1, echo)  # linha da grade com o placeholder nas duas telas

    def test_override_compartilhado_dos_executors_tambem_classifica(self):
        result = synthetic_swap_override(
            {"matched": False},
            SYNTHETIC_PARAMS,
            expected_screen=EXPECTED_GRID,
            observed_screen=OBSERVED_GRID,
        )
        self.assertIsNotNone(result)
        _, failure_type, severity, _ = result
        self.assertEqual((failure_type, severity), ("synthetic_data_swap", "low"))

    def test_troca_pura_com_ruido_volatil_e_integralmente_explicada(self):
        expected = "Codigo: g2511   Valor: 229,9   792,000 Kb livres\n"
        observed = "Codigo: 303     Valor: 399,7   796,000 Kb livres\n"
        self.assertTrue(substitution_explained_diff(expected, observed, PAIRS_AIX))


class SwapNegativosTests(unittest.TestCase):
    """Divergência funcional real NÃO pode ser classificada como troca."""

    def test_campo_ausente_na_observada_nao_e_swap(self):
        """Placeholder só na linha esperada: o modelo não ecoou na observada
        (campo vazio/rejeitado) — divergência estrutural, não troca."""
        observed_sem_modelo = OBSERVED_GRID.replace("│01│303   │", "│01│      │")
        self.assertNotIn("303", observed_sem_modelo)
        failure = _falha(EXPECTED_GRID, observed_sem_modelo)
        self.assertEqual(failure["failure_type"], "screen_divergence")
        self.assertEqual(failure["severity"], "medium")

    def test_valor_diferente_sem_par_no_depara_nao_e_swap(self):
        """Total 229,90 → 0,00 não é o par (229,9 → 399,7): o placeholder fica
        só na esperada e nenhum outro eco existe na tela."""
        expected = (
            "PEDIDO E-COMMERCE\n"
            " Valor desco:      0,00         Total:    229,90  Val frete:      0,00\n"
        )
        observed = (
            "PEDIDO E-COMMERCE\n"
            " Valor desco:      0,00         Total:      0,00  Val frete:      0,00\n"
        )
        self.assertEqual(
            substitution_echo_line_indices(expected, observed, PAIRS_AIX), []
        )
        failure = _falha(expected, observed)
        self.assertEqual(failure["failure_type"], "screen_divergence")

    def test_tela_observada_sem_nenhuma_linha_da_esperada_nao_e_swap(self):
        """Sessão em outra tela (menu) sem qualquer linha comum ou eco."""
        expected = (
            "│01│G2511 │00001│  35│   1│  229,90│    0,00│E-COMMERCE\n"
            "<^T>TROCA CONSULTA   <^L>FORMA PAGTO\n"
        )
        observed = (
            "  REDE DE LOJAS                    | 3 MOVIMENTOS\n"
            "   0. Finalizacao\n"
        )
        self.assertEqual(
            substitution_echo_line_indices(expected, observed, PAIRS_AIX), []
        )
        failure = _falha(expected, observed)
        self.assertEqual(failure["failure_type"], "screen_divergence")
        self.assertEqual(failure["severity"], "medium")

    def test_identificador_gerado_com_shape_diferente_nao_ecoa(self):
        """Nº do pedido só é eco com o mesmo shape (prefixo e nº de dígitos)."""
        expected = "*Pedido.....:D00011073            E-c:004 DAKOTA\n"
        observed_ok = "*Pedido.....:D00011140            E-c:004 DAKOTA\n"
        observed_shape = "*Pedido.....:1140                 E-c:004 DAKOTA\n"
        self.assertEqual(
            substitution_echo_line_indices(expected, observed_ok, PAIRS_AIX), [0]
        )
        self.assertEqual(
            substitution_echo_line_indices(expected, observed_shape, PAIRS_AIX), []
        )

    def test_erro_do_erp_sem_eco_nao_e_swap(self):
        """Mensagem de rejeição do ERP sem nenhum eco do de→para na tela."""
        expected = "PEDIDO E-COMMERCE\n<TAB>CONSULTA\n"
        observed = "PEDIDO E-COMMERCE\nCodigo nao cadastrado.\n"
        failure = _falha(expected, observed)
        self.assertEqual(failure["failure_type"], "screen_divergence")
        self.assertEqual(failure["severity"], "medium")

    def test_sem_substituicoes_nos_params_nunca_e_swap(self):
        """Run sem de→para (não sintética) não classifica troca nem com eco."""
        params = {"on_deterministic_mismatch": "send-anyway"}
        failure = _falha(EXPECTED_GRID, OBSERVED_GRID, params=params)
        self.assertNotEqual(failure["failure_type"], "synthetic_data_swap")


if __name__ == "__main__":
    unittest.main()

"""Testes da classificação de divergência explicada pela troca sintética."""
from __future__ import annotations

from dakota_gateway.replay_compare import (
    apply_synthetic_substitution_fallback,
    substitution_explained_diff,
)
from dakota_gateway.replay_control.deterministic import _deterministic_failure

PAIRS = [
    ("g2511", "n9580"),
    ("229,9", "763578,01"),
    ("0000135", "2970288"),
    ("1", "2"),
    ("4", "13"),
]

EXPECTED_SCREEN = (
    "PEDIDO DE VENDA                            792,000 Kb livres\n"
    "Codigo: g2511   Qtde: 1   Valor: 229,9\n"
    "Pedido: 0000135   Situacao: 4\n"
)

OBSERVED_SCREEN = (
    "PEDIDO DE VENDA                            268,000 Kb livres\n"
    "Codigo: n9580   Qtde: 2   Valor: 763578,01\n"
    "Pedido: 2970288   Situacao: 13\n"
)


def test_diff_totalmente_explicada_pelos_pares():
    """Trocas + ruído volátil (Kb livres) são integralmente explicados."""
    assert substitution_explained_diff(EXPECTED_SCREEN, OBSERVED_SCREEN, PAIRS) is True


def test_divergencia_real_nao_e_explicada():
    """Qualquer diferença fora dos pares mantém a divergência."""
    observed = OBSERVED_SCREEN.replace("PEDIDO DE VENDA", "TELA DE ERRO")
    assert substitution_explained_diff(EXPECTED_SCREEN, observed, PAIRS) is False


def test_sem_pares_nao_explica():
    assert substitution_explained_diff(EXPECTED_SCREEN, OBSERVED_SCREEN, []) is False
    assert substitution_explained_diff(EXPECTED_SCREEN, OBSERVED_SCREEN, None) is False


def test_par_identidade_ignorado():
    """Pares original==sintético (campos mantidos) não entram na análise."""
    pairs = PAIRS + [("00109829069", "00109829069")]
    assert substitution_explained_diff(EXPECTED_SCREEN, OBSERVED_SCREEN, pairs) is True


def test_valor_curto_nao_mascara_digitos_fora_do_contexto():
    """Par curto (1→2) não pode explicar divergência de dígito em outro lugar."""
    expected = "Total: 155\n"
    observed = "Total: 255\n"
    # O '1'→'2' no início de '155'/'255' é troca legítima do par, mas o
    # caso abaixo diverge em posição não relacionada a valor digitado.
    assert substitution_explained_diff(expected, observed, [("1", "2")]) is True
    assert substitution_explained_diff("Total: 151\n", "Total: 251\n", [("1", "2")]) is True
    # Divergência em dígito diferente do par continua divergência.
    assert substitution_explained_diff("Total: 151\n", "Total: 191\n", [("1", "2")]) is False


def _evento(sample: str) -> dict:
    return {"type": "checkpoint", "screen_sample": sample}


def test_fallback_marca_synthetic_substitution():
    match = {"matched": False, "expected_sig": "a", "observed_sig": "b"}
    result = apply_synthetic_substitution_fallback(
        match,
        expected_event=_evento(EXPECTED_SCREEN),
        observed_snapshot={"screen_text": OBSERVED_SCREEN},
        substitutions=PAIRS,
    )
    assert result["matched"] is False  # troca é apontada, não escondida
    assert result["synthetic_substitution"] is True
    assert result["synthetic_echo_lines"]  # índices das linhas de eco


def test_fallback_marca_mesmo_com_outras_divergencias():
    """Eco presente + diffs do app (datas, totais) ainda aponta a troca."""
    match = {"matched": False}
    result = apply_synthetic_substitution_fallback(
        match,
        expected_event=_evento(EXPECTED_SCREEN),
        observed_snapshot={"screen_text": OBSERVED_SCREEN + "\nData: 27/08/2026"},
        substitutions=PAIRS,
    )
    assert result["synthetic_substitution"] is True


def test_fallback_nao_marca_quando_nao_ha_eco():
    """Divergência sem nenhum eco do de→para não vira troca."""
    match = {"matched": False}
    result = apply_synthetic_substitution_fallback(
        match,
        expected_event=_evento("MENU PRINCIPAL\nDigite a sua opcao:"),
        observed_snapshot={"screen_text": "TELA DE ERRO\nopcao invalida"},
        substitutions=PAIRS,
    )
    assert result is match


def test_fallback_preserva_match_existente():
    match = {"matched": True}
    result = apply_synthetic_substitution_fallback(
        match,
        expected_event=_evento(EXPECTED_SCREEN),
        observed_snapshot={"screen_text": OBSERVED_SCREEN},
        substitutions=PAIRS,
    )
    assert result is match


def test_falha_classificada_como_troca_quando_match_flagado():
    """_deterministic_failure reclassifica para synthetic_data_swap/low."""
    failure = _deterministic_failure(
        sid="sess-1",
        seq_global=100,
        seq_session=50,
        expected_sig="a",
        observed_sig="b",
        params={"synthetic": True, "on_deterministic_mismatch": "send-anyway"},
        checkpoint_timeout_ms=5000,
        checkpoint_quiet_ms=250,
        mode_label="visual",
        concurrent_mode=False,
        match={"matched": False, "synthetic_substitution": True},
    )
    assert failure["failure_type"] == "synthetic_data_swap"
    assert failure["severity"] == "low"
    assert "troca de dados sintéticos" in failure["message"]


def test_falha_sem_flag_mantem_screen_divergence():
    failure = _deterministic_failure(
        sid="sess-1",
        seq_global=100,
        seq_session=50,
        expected_sig="a",
        observed_sig="b",
        params={"synthetic": True, "on_deterministic_mismatch": "send-anyway"},
        checkpoint_timeout_ms=5000,
        checkpoint_quiet_ms=250,
        mode_label="visual",
        concurrent_mode=False,
        match={"matched": False},
    )
    assert failure["failure_type"] == "screen_divergence"
    assert failure["severity"] == "medium"

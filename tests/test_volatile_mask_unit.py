"""Testes unitários da máscara de voláteis e da segunda chance na comparação."""
from __future__ import annotations

from dakota_terminal.volatile import VOLATILE_PLACEHOLDER, mask_volatile_screen_text
from dakota_gateway.replay_compare import apply_volatile_mask_fallback

STATUS_LINE = "PEDIDO DE VENDA                                       792,000 Kb livres"
STATUS_LINE_OTHER = "PEDIDO DE VENDA                                       268,000 Kb livres"


def test_mask_substitui_kb_livres():
    """Valores de memória livre viram placeholder; o resto da tela não muda."""
    masked = mask_volatile_screen_text(STATUS_LINE)
    assert VOLATILE_PLACEHOLDER in masked
    assert "792,000" not in masked
    assert "PEDIDO DE VENDA" in masked


def test_mask_iguais_apos_mascara():
    """Telas que só diferem no Kb livres ficam idênticas após a máscara."""
    assert mask_volatile_screen_text(STATUS_LINE) == mask_volatile_screen_text(STATUS_LINE_OTHER)


def test_mask_nao_toca_outros_numeros():
    """Números comuns (quantidades, valores) não são mascarados."""
    text = "Quantidade: 1.234  Valor: 229,90  Pedido 00109829069"
    assert mask_volatile_screen_text(text) == text


def test_mask_aceita_ponto_como_separador():
    """Aceita separador de milhar com ponto também (792.000 Kb livres)."""
    assert mask_volatile_screen_text("x 792.000 Kb livres y") == f"x {VOLATILE_PLACEHOLDER} y"


def _evento(screen_sample: str) -> dict:
    return {"type": "checkpoint", "screen_sample": screen_sample}


def test_fallback_marca_match_quando_so_volatil_diverge():
    """Telas iguais exceto Kb livres casam via segunda chance."""
    match = {"matched": False, "expected_sig": "aaa", "observed_sig": "bbb"}
    result = apply_volatile_mask_fallback(
        match,
        expected_event=_evento(STATUS_LINE),
        observed_snapshot={"screen_text": STATUS_LINE_OTHER},
    )
    assert result["matched"] is True
    assert result["volatile_mask_applied"] is True


def test_fallback_nao_cobre_divergencia_real():
    """Divergência além do volátil continua sem match."""
    match = {"matched": False, "expected_sig": "aaa", "observed_sig": "bbb"}
    result = apply_volatile_mask_fallback(
        match,
        expected_event=_evento(STATUS_LINE),
        observed_snapshot={"screen_text": STATUS_LINE_OTHER + "\nTELA DIFERENTE"},
    )
    assert result["matched"] is False
    assert "volatile_mask_applied" not in result


def test_fallback_preserva_match_existente():
    """Match já confirmado passa inalterado (mesmo objeto, sem flag)."""
    match = {"matched": True, "expected_sig": "aaa", "observed_sig": "aaa"}
    result = apply_volatile_mask_fallback(
        match,
        expected_event=_evento(STATUS_LINE),
        observed_snapshot={"screen_text": STATUS_LINE_OTHER},
    )
    assert result is match
    assert "volatile_mask_applied" not in result


def test_fallback_sem_texto_observado_nao_faz_nada():
    """Sem screen_text no snapshot observado, retorna o match original."""
    match = {"matched": False}
    result = apply_volatile_mask_fallback(
        match,
        expected_event=_evento(STATUS_LINE),
        observed_snapshot={},
    )
    assert result is match


def test_fallback_sem_evento_esperado_nao_faz_nada():
    """Sem evento esperado, retorna o match original."""
    match = {"matched": False}
    result = apply_volatile_mask_fallback(
        match,
        expected_event=None,
        observed_snapshot={"screen_text": STATUS_LINE},
    )
    assert result is match

"""Testes unitários da máscara de voláteis e da segunda chance na comparação."""
from __future__ import annotations

from dakota_terminal.volatile import VOLATILE_PLACEHOLDER, mask_volatile_screen_text
from dakota_gateway.replay_compare import apply_volatile_mask_fallback

STATUS_LINE = "PEDIDO DE VENDA                                       792,000 Kb livres"
STATUS_LINE_OTHER = "PEDIDO DE VENDA                                       268,000 Kb livres"

# Linha de status do Recital nos dois ambientes (captura 13, runs 94-96 AIX ×
# 20-23 Linux): mesmo fluxo, rótulo de plataforma e memória livre diferentes.
STATUS_LINE_AIX = "     IBM Aix (Common)    |    OVER    |    792,000 Kb livres    |       ok"
STATUS_LINE_LINUX = "     Linux x86           |    OVER    |    024,930 Kb livres    |       ok"


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


def test_mask_rotulo_plataforma():
    """O rótulo de plataforma da linha de status é volátil: muda com o
    ambiente do servidor (AIX × Linux), não com o fluxo da aplicação.
    O preenchimento em branco até o '|' também é absorvido."""
    for label in ("IBM Aix (Common)", "Linux x86", "IBM AIX (COMMON)", "Linux X86"):
        masked = mask_volatile_screen_text(f"     {label}    |    OVER")
        assert masked == f"     {VOLATILE_PLACEHOLDER}|    OVER"


def test_mask_iguais_apos_mascara_plataforma():
    """Captura 13: a mesma tela no AIX (esperada) e no Linux (observada) só
    difere no rótulo de plataforma e no Kb livres — casam após a máscara."""
    assert mask_volatile_screen_text(STATUS_LINE_AIX) == mask_volatile_screen_text(STATUS_LINE_LINUX)


def test_mask_nao_toca_rotulo_fora_da_linha_de_status():
    """Sem o separador '|' da linha de status, o texto não é mascarado."""
    text = "Servidor Linux x86 homologado"
    assert mask_volatile_screen_text(text) == text


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


def test_fallback_cobre_rotulo_plataforma():
    """Captura 13: checkpoint cuja única divergência é plataforma + Kb livres
    casa via segunda chance (esperada AIX × observada Linux)."""
    match = {"matched": False, "expected_sig": "aaa", "observed_sig": "bbb"}
    result = apply_volatile_mask_fallback(
        match,
        expected_event=_evento(STATUS_LINE_AIX),
        observed_snapshot={"screen_text": STATUS_LINE_LINUX},
    )
    assert result["matched"] is True
    assert result["volatile_mask_applied"] is True


def test_fallback_nao_cobre_divergencia_real_alem_da_plataforma():
    """Mesmo com plataforma e Kb livres mascarados, divergência real em outro
    trecho da tela continua sem match."""
    match = {"matched": False, "expected_sig": "aaa", "observed_sig": "bbb"}
    result = apply_volatile_mask_fallback(
        match,
        expected_event=_evento(STATUS_LINE_AIX),
        observed_snapshot={"screen_text": STATUS_LINE_LINUX + "\nTELA DIFERENTE"},
    )
    assert result["matched"] is False
    assert "volatile_mask_applied" not in result


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

"""Testes da tolerância de eco de input e da referência envelhecida no replay."""
from __future__ import annotations

from dakota_gateway.replay_compare import apply_input_echo_fallback
from dakota_gateway.replay_control.deterministic import (
    _deterministic_failure,
    stale_reference_override,
)

MENU_ESPERADO = (
    "Menu de opcoes do usuario ferblo\n"
    "\n"
    "  1 - (REDE LOJAS) Sistema das Lojas\n"
    "\n"
    "  0 - Fim\n"
    "\n"
    "Digite a sua opcao:\n"
)

MENU_COM_ECO = MENU_ESPERADO.replace(
    "Digite a sua opcao:", "Digite a sua opcao: 0"
)


def _evento(sample: str, **extra) -> dict:
    ev = {"type": "deterministic_input", "screen_sample": sample}
    ev.update(extra)
    return ev


def test_eco_da_tecla_recente_casa_o_checkpoint():
    """A única diferença é o eco da opção '0' recém-digitada no prompt."""
    match = {"matched": False, "expected_sig": "a", "observed_sig": "b"}
    result = apply_input_echo_fallback(
        match,
        expected_event=_evento(MENU_ESPERADO),
        observed_snapshot={"screen_text": MENU_COM_ECO},
        recent_keys=["0"],
    )
    assert result["matched"] is True
    assert result["input_echo_tolerance"] is True


def test_eco_em_qualquer_posicao_da_linha():
    """Eco no meio da linha (cursor) também é tolerado."""
    match = {"matched": False}
    result = apply_input_echo_fallback(
        match,
        expected_event=_evento("Codigo: \n"),
        observed_snapshot={"screen_text": "Codigo: g2511\n"},
        recent_keys=["g2511"],
    )
    assert result["matched"] is True


def test_divergencia_sem_eco_nao_casa():
    """Linha divergente que não é eco de tecla mantém a divergência."""
    match = {"matched": False}
    result = apply_input_echo_fallback(
        match,
        expected_event=_evento(MENU_ESPERADO),
        observed_snapshot={"screen_text": "TELA DE ERRO\nopcao invalida\n"},
        recent_keys=["0"],
    )
    assert result is match


def test_sem_teclas_recentes_nao_tolera():
    match = {"matched": False}
    result = apply_input_echo_fallback(
        match,
        expected_event=_evento(MENU_ESPERADO),
        observed_snapshot={"screen_text": MENU_COM_ECO},
        recent_keys=[],
    )
    assert result is match


def test_teclas_de_controle_sao_ignoradas():
    """ENTER/ESC/TAB não ecoam como texto — não podem explicar divergência."""
    match = {"matched": False}
    result = apply_input_echo_fallback(
        match,
        expected_event=_evento(MENU_ESPERADO),
        observed_snapshot={"screen_text": MENU_COM_ECO},
        recent_keys=["\r", "\t", "\x1b"],
    )
    assert result is match


def test_match_existente_e_preservado():
    match = {"matched": True}
    result = apply_input_echo_fallback(
        match,
        expected_event=_evento(MENU_ESPERADO),
        observed_snapshot={"screen_text": MENU_COM_ECO},
        recent_keys=["0"],
    )
    assert result is match


def test_todas_as_linhas_divergentes_precisam_ser_eco():
    """Uma linha divergente sem eco (mesmo com outra explicada) não casa."""
    observed = MENU_COM_ECO.replace("0 - Fim", "9 - Fim")
    match = {"matched": False}
    result = apply_input_echo_fallback(
        match,
        expected_event=_evento(MENU_ESPERADO),
        observed_snapshot={"screen_text": observed},
        recent_keys=["0"],
    )
    assert result is match


TELA_APP = "PEDIDO DE VENDA\nCodigo: g2511\nTotal: 229,9\n"
TELA_SHELL = "ferblo@mig24:/home/ferblo$ date\nqua ago 27 02:38:40 2026\n"


def test_stale_rebaixa_severidade_quando_contexto_mudou():
    """Snapshot velho + telas sem nenhuma linha em comum = divergência de contexto."""
    failure_type, severity, reason = stale_reference_override(
        "screen_divergence",
        "medium",
        "checkpoint não estabilizou",
        expected_event=_evento(TELA_APP, screen_snapshot_age_ms=50935),
        expected_screen=TELA_APP,
        observed_screen=TELA_SHELL,
    )
    assert failure_type == "screen_divergence"  # tipo mantido
    assert severity == "low"
    assert "desatualizada" in reason
    assert "51s" in reason


def test_stale_nao_aplica_com_idade_baixa():
    """Captura humana normal (leu a tela alguns segundos) não é rebaixada."""
    failure_type, severity, reason = stale_reference_override(
        "screen_divergence",
        "medium",
        "checkpoint não estabilizou",
        expected_event=_evento(TELA_APP, screen_snapshot_age_ms=3843),
        expected_screen=TELA_APP,
        observed_screen=TELA_SHELL,
    )
    assert severity == "medium"
    assert reason == "checkpoint não estabilizou"


def test_stale_nao_aplica_quando_telas_compartilham_contexto():
    """Idade alta mas mesma tela (usuário leu por minutos) não é rebaixada."""
    _, severity, _ = stale_reference_override(
        "screen_divergence",
        "medium",
        "checkpoint não estabilizou",
        expected_event=_evento(TELA_APP, screen_snapshot_age_ms=120000),
        expected_screen=TELA_APP,
        observed_screen=TELA_APP.replace("229,9", "999,9"),
    )
    assert severity == "medium"


def test_stale_nao_aplica_sem_tela_observada():
    """Sem saída observada é timeout genuíno — não rebaixa."""
    _, severity, _ = stale_reference_override(
        "timeout",
        "high",
        "checkpoint não estabilizou",
        expected_event=_evento(TELA_APP, screen_snapshot_age_ms=60000),
        expected_screen=TELA_APP,
        observed_screen="",
    )
    assert severity == "high"


def test_falha_stale_rebaixada_no_registro_deterministico():
    """_deterministic_failure rebaixa para low quando a referência envelheceu."""
    failure = _deterministic_failure(
        sid="sess-1",
        seq_global=458,
        seq_session=458,
        expected_sig="a",
        observed_sig="b",
        params={"synthetic": True, "on_deterministic_mismatch": "send-anyway"},
        checkpoint_timeout_ms=5000,
        checkpoint_quiet_ms=250,
        mode_label="strict-global-deterministic",
        concurrent_mode=False,
        match={"matched": False},
        expected_screen=TELA_APP,
        observed_screen=TELA_SHELL,
        expected_event=_evento(TELA_APP, screen_snapshot_age_ms=50935),
    )
    assert failure["failure_type"] == "screen_divergence"
    assert failure["severity"] == "low"
    assert "desatualizada" in failure["message"]


def test_falha_stale_nao_rebaixada_sem_evento():
    """Sem o evento esperado (chamadas legadas) a classificação é preservada."""
    failure = _deterministic_failure(
        sid="sess-1",
        seq_global=458,
        seq_session=458,
        expected_sig="a",
        observed_sig="b",
        params={"synthetic": True, "on_deterministic_mismatch": "send-anyway"},
        checkpoint_timeout_ms=5000,
        checkpoint_quiet_ms=250,
        mode_label="strict-global-deterministic",
        concurrent_mode=False,
        match={"matched": False},
        expected_screen=TELA_APP,
        observed_screen=TELA_SHELL,
    )
    assert failure["severity"] == "medium"

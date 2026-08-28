"""Testes da tolerância de eco de input e da referência envelhecida no replay."""
from __future__ import annotations

import base64
import json
from pathlib import Path
from unittest.mock import patch

from dakota_gateway.replay import ReplayConfig
from dakota_gateway import replay_control
from dakota_gateway.replay_compare import apply_input_echo_fallback
from dakota_gateway.replay_control.deterministic import (
    _deterministic_failure,
    context_switch_override,
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


TELA_SHELL_EXIT = "(ferblo)MIG24:/dakota1/u/ferblo > exit\n"
TELA_APP_FINAL = "Fim da execucao\n\n"


def test_context_switch_rebaixa_com_shell_na_esperada():
    """0 linhas em comum + prompt de shell num dos lados = mudança de contexto."""
    failure_type, severity, reason = context_switch_override(
        "screen_divergence",
        "medium",
        "checkpoint não estabilizou",
        expected_screen=TELA_SHELL_EXIT,
        observed_screen=TELA_APP_FINAL,
    )
    assert failure_type == "screen_divergence"
    assert severity == "low"
    assert "app ↔ shell" in reason


def test_context_switch_rebaixa_com_shell_na_observada():
    _, severity, _ = context_switch_override(
        "screen_divergence",
        "medium",
        "motivo",
        expected_screen=TELA_APP_FINAL,
        observed_screen=TELA_SHELL + "ksh: exti: not found\n",
    )
    assert severity == "low"


def test_context_switch_nao_rebaixa_sem_marcador_shell():
    """Telas disjuntas sem evidência de shell podem ser erro real — não rebaixa."""
    _, severity, reason = context_switch_override(
        "screen_divergence",
        "medium",
        "motivo",
        expected_screen="TELA DE PEDIDO\nCodigo:\n",
        observed_screen="ERRO FATAL\narquivo travado\n",
    )
    assert severity == "medium"
    assert reason == "motivo"


def test_context_switch_nao_rebaixa_com_linha_em_comum():
    """Qualquer linha em comum indica mesmo contexto — não rebaixa."""
    _, severity, _ = context_switch_override(
        "screen_divergence",
        "medium",
        "motivo",
        expected_screen=TELA_SHELL_EXIT + "Menu de opcoes\n",
        observed_screen="Menu de opcoes\noutro conteudo\n",
    )
    assert severity == "medium"


def test_falha_context_switch_rebaixada_no_registro_deterministico():
    """_deterministic_failure aplica o context switch mesmo com snapshot novo."""
    failure = _deterministic_failure(
        sid="sess-1",
        seq_global=519,
        seq_session=519,
        expected_sig="a",
        observed_sig="b",
        params={"synthetic": True, "on_deterministic_mismatch": "send-anyway"},
        checkpoint_timeout_ms=5000,
        checkpoint_quiet_ms=250,
        mode_label="strict-global-deterministic",
        concurrent_mode=False,
        match={"matched": False},
        expected_screen=TELA_SHELL_EXIT,
        observed_screen=TELA_APP_FINAL,
        expected_event=_evento(TELA_SHELL_EXIT, screen_snapshot_age_ms=1202),
    )
    assert failure["failure_type"] == "screen_divergence"
    assert failure["severity"] == "low"
    assert "app ↔ shell" in failure["message"]


MENU_REF = (
    "Menu de opcoes do usuario ferblo\n"
    "  1 - (REDE LOJAS) Sistema das Lojas\n"
    "  0 - Fim\n"
    "Digite a sua opcao:\n"
)

MENU_AVANCOU = (
    "Menu de opcoes do usuario ferblo\n"
    "  1 - (REDE LOJAS) Sistema das Lojas\n"
    "  0 - Fim\n"
    "Digite a sua opcao: 0\n"
    "(ferblo)MIG24:/dakota1/u/ferblo >\n"
)


def test_content_present_rebaixa_quando_tela_avancou_com_eco():
    """Toda linha esperada presente (verbatim ou prefixo com eco) = avançou sem divergir."""
    from dakota_gateway.replay_control.deterministic import content_present_override

    failure_type, severity, reason = content_present_override(
        "screen_divergence",
        "medium",
        "checkpoint não estabilizou",
        expected_screen=MENU_REF,
        observed_screen=MENU_AVANCOU,
    )
    assert failure_type == "screen_divergence"
    assert severity == "low"
    assert "conteúdo esperado presente" in reason


def test_content_present_rebaixa_superset_com_scrollback():
    """Todas as linhas verbatim + saída extra de shell (date, erros) = rolagem."""
    from dakota_gateway.replay_control.deterministic import content_present_override

    observed = MENU_REF + "(ferblo)MIG24:/dakota11/est > date\nFri Aug 28 14:24:35 -03 2026\n"
    _, severity, _ = content_present_override(
        "screen_divergence", "medium", "motivo",
        expected_screen=MENU_REF, observed_screen=observed,
    )
    assert severity == "low"


def test_content_present_nao_rebaixa_quando_falta_linha():
    """Menu diferente (outro nível de navegação) não é rolagem — mantém medium."""
    from dakota_gateway.replay_control.deterministic import content_present_override

    outro_menu = (
        "Menu de opcoes do usuario ferblo\n"
        "  1 - Saldos\n"
        "  2 - Produto\n"
        "  3 - Documentos fiscais\n"
    )
    _, severity, reason = content_present_override(
        "screen_divergence", "medium", "motivo",
        expected_screen=MENU_REF, observed_screen=outro_menu,
    )
    assert severity == "medium"
    assert reason == "motivo"


def test_content_present_linha_curta_nao_casa_por_prefixo():
    """Linha esperada curta (<4 chars) só casa verbatim — evita falso positivo."""
    from dakota_gateway.replay_control.deterministic import content_present_override

    _, severity, _ = content_present_override(
        "screen_divergence", "medium", "motivo",
        expected_screen="0\n", observed_screen="01\n",
    )
    assert severity == "medium"


def test_content_present_tela_vazia_nao_rebaixa():
    from dakota_gateway.replay_control.deterministic import content_present_override

    _, severity, _ = content_present_override(
        "screen_divergence", "medium", "motivo",
        expected_screen=MENU_REF, observed_screen="",
    )
    assert severity == "medium"


def test_falha_content_present_no_registro_deterministico():
    """_deterministic_failure rebaixa quando a sessão só avançou além da referência."""
    failure = _deterministic_failure(
        sid="sess-1",
        seq_global=15,
        seq_session=15,
        expected_sig="a",
        observed_sig="b",
        params={"synthetic": True, "on_deterministic_mismatch": "send-anyway"},
        checkpoint_timeout_ms=5000,
        checkpoint_quiet_ms=250,
        mode_label="strict-global-deterministic",
        concurrent_mode=False,
        match={"matched": False},
        expected_screen=MENU_REF,
        observed_screen=MENU_AVANCOU,
        expected_event=_evento(MENU_REF, screen_snapshot_age_ms=994),
    )
    assert failure["failure_type"] == "screen_divergence"
    assert failure["severity"] == "low"
    assert "conteúdo esperado presente" in failure["message"]


# --- Regressão: falha de checkpoint no deterministic_input registrada 1x ---


class _Selector:
    def register(self, *args, **kwargs):
        return None

    def select(self, timeout=None):
        return []

    def close(self):
        return None


class _Session:
    instances: list["_Session"] = []

    def __init__(self, cfg, sid, target_user_override=None):
        self.master_fd = 0
        self.session_id = sid
        self.last_out_ms = 0
        self.screen_state = object()
        self._writes: list[bytes] = []
        self.instances.append(self)

    def canonical_snapshot_now(self) -> dict:
        return {
            "visual_sig": "sha256:visual-ok",
            "text_sig": "sha256:text-ok",
            "semantic_sig": "sha256:semantic-ok",
            "screen_sig": "",
        }

    def read_out(self):
        return b""

    def write_in(self, data: bytes):
        self._writes.append(bytes(data))

    def close(self):
        return None


def _strict_log_dir(tmp_path: Path, event: dict) -> str:
    entries = [
        {"type": "session_start", "session_id": "s1", "seq_global": 1, "seq_session": 1, "rows": 2, "cols": 3},
        event,
    ]
    (tmp_path / "audit-control.part001.jsonl").write_text(
        "\n".join(json.dumps(item) for item in entries),
        encoding="utf-8",
    )
    return str(tmp_path)


def test_strict_global_deterministic_input_registra_falha_uma_vez(tmp_path):
    """O checkpoint falho do deterministic_input registra 1 falha só.

    Antes da correção o wait_checkpoint gravava a falha e o except gravava de
    novo via _deterministic_failure — cada divergência aparecia em duplicata.
    """
    event = {
        "type": "deterministic_input",
        "session_id": "s1",
        "seq_global": 2,
        "seq_session": 2,
        "comparison_mode": "visual",
        "expected_visual_sig": "sha256:visual-wrong",
        "key_b64": base64.b64encode(b"A").decode("ascii"),
    }
    cfg = ReplayConfig(
        log_dir=_strict_log_dir(tmp_path, event),
        target_host="local",
        input_mode="deterministic",
        on_deterministic_mismatch="send-anyway",
        comparison_mode="visual",
        checkpoint_quiet_ms=0,
    )
    _Session.instances = []
    failures: list[dict] = []
    with patch.object(replay_control.executors, "_TargetSession", _Session), patch.object(
        replay_control.executors.selectors, "DefaultSelector", _Selector
    ):
        replay_control.replay_strict_global_controlled(
            cfg,
            params={
                "input_mode": "deterministic",
                "on_deterministic_mismatch": "send-anyway",
                "comparison_mode": "visual",
            },
            should_pause_or_cancel=lambda: None,
            on_progress=lambda *args: None,
            on_failure=failures.append,
            checkpoint_timeout_ms=20,
        )
    assert len(failures) == 1
    assert failures[0]["event_type"] == "deterministic_input"
    assert _Session.instances[0]._writes == [b"A"]

"""test_ui_replay_mode_labels_unit.py — os dois tipos de replay ("bruto"/raw e
"determinístico") precisam ser explicados em linguagem leiga nos pontos onde o
usuário escolhe entre eles: formulário de nova execução (run_new.html) e painel
de replay operacional do replay da sessão (capture_session_replay.html).
O valor técnico permanece acessível via title/parênteses.
Run: python3 -m pytest tests/test_ui_replay_mode_labels_unit.py -q
"""

from __future__ import annotations

from pathlib import Path

_TEMPLATES = Path(__file__).resolve().parent.parent / "gateway" / "control" / "templates"


def _run_new() -> str:
    return (_TEMPLATES / "run_new.html").read_text(encoding="utf-8")


def _session_replay() -> str:
    return (_TEMPLATES / "capture_session_replay.html").read_text(encoding="utf-8")


def test_run_new_input_mode_sem_verificacao_simples():
    html = _run_new()
    assert '<option value="raw">' in html
    assert "sem conferir as telas" in html, "opção raw deve explicar que não confere telas"


def test_run_new_input_mode_verificado():
    html = _run_new()
    assert '<option value="deterministic">' in html
    assert "confere a tela antes de cada tecla" in html


def test_run_new_input_mode_mantem_termo_tecnico_acessivel():
    html = _run_new()
    assert "(bruto)" in html, "termo técnico 'bruto' deve seguir acessível entre parênteses"
    assert "(determinístico)" in html


def test_run_new_mismatch_sem_jargao_deterministico_divergiu():
    html = _run_new()
    assert "determinístico divergiu" not in html, "jargão 'determinístico divergiu:' nas opções"
    assert "Se a tela divergir da gravação" in html


def test_session_replay_botoes_em_linguagem_leiga():
    html = _session_replay()
    assert "Criar replay simples" in html
    assert "Criar replay verificado" in html
    assert "Criar replay bruto" not in html
    assert "Criar replay determinístico" not in html


def test_session_replay_botoes_mantem_termo_tecnico_no_title():
    html = _session_replay()
    assert 'id="run-raw-btn"' in html and "bruto" in html
    assert 'id="run-deterministic-btn"' in html and "determinístico" in html


def test_session_replay_texto_do_painel_sem_modo_cru():
    html = _session_replay()
    assert "em modo bruto ou determinístico" not in html
    assert "simples" in html and "verificada" in html

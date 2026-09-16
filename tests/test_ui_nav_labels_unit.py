"""test_ui_nav_labels_unit.py — o menu lateral e os títulos/descrições de
página (ui_templates.py) aparecem em TODAS as telas; precisam seguir o
padrão leigo pt-BR das levas de simplificação (sem "run" como substantivo,
acentuação correta, "conformidade" no lugar de "compliance").
Run: python3 -m pytest tests/test_ui_nav_labels_unit.py -q
"""

from __future__ import annotations

from control.ui_templates import _MENU_CONFIG, ROUTES_CONFIG


def _labels():
    out = []
    for item in _MENU_CONFIG:
        out.append(item["label"])
        out.extend(child["label"] for child in item.get("children", []))
    return out


def _visible_texts():
    out = []
    for route in ROUTES_CONFIG:
        for key in ("title", "page_title", "page_description", "page_kicker"):
            if key in route:
                out.append(route[key])
    return out


def test_menu_lateral_sem_run_como_substantivo():
    for label in _labels():
        assert "run" not in label.lower(), f"rótulo do menu com 'run': {label}"


def test_menu_lateral_acentuado():
    esperado = {
        "Histórico",
        "Comparação",
        "Conformidade",
        "Sessões",
        "Políticas",
        "Cenários",
        "Regressões",
        "Tendências",
        "Fluxos sensíveis",
        "Automação",
        "Relatório de jornadas",
        "Regras de negócio",
        "Usuários",
        "Sessão atual",
        "Parâmetros",
        "Nova execução",
    }
    assert esperado.issubset(set(_labels())), (
        f"faltam rótulos leigos: {sorted(esperado - set(_labels()))}"
    )


def test_menu_lateral_sem_termos_crus_conhecidos():
    proibidos = {
        "Nova run", "Historico", "Comparacao", "Compliance", "Sessoes",
        "Politicas", "Cenarios", "Regressoes", "Tendencias", "Automacao",
        "Fluxos sensiveis", "Synthetic", "Relatorio Jornadas",
        "Regras de Negocio", "Usuarios", "Sessao atual", "Parametros",
    }
    assert not proibidos.intersection(_labels())


def test_titulos_e_descricoes_de_pagina_acentuados():
    proibidos = [
        "Execucoes", "Historico", "historico", "Comparacao", "comparacao",
        "compliance", "Compliance", "Sessoes", "sessoes", "Catalogo",
        "politicas", "cenarios", "Configuracao", "Diagnostico", "operacao",
        "memoria", "Administracao", "Usuarios", "parametros", "Gestao",
        "Inspecao", "exportacoes", "ativacao", "Sintetico", "sinteticos",
        "Validacao", "Inferencia", "Relatorio", "decisao", "Negocio",
        "Criacao", "Analise inteligente", "recomendacoes", "Reproduca",
        "Runs", "Run |", "| Nova Run", "Detalhe da Run",
    ]
    for texto in _visible_texts():
        for termo in proibidos:
            assert termo not in texto, f"texto de página com '{termo}': {texto}"

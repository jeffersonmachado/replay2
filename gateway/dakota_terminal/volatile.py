"""Máscara de trechos voláteis de tela (ruído ambiental) na comparação.

A linha de status do Recital mostra a memória livre da sessão
(ex.: ``792,000 Kb livres``) e o rótulo da plataforma do servidor
(ex.: ``IBM Aix (Common)`` × ``Linux x86``) — mudam com o ambiente de
execução, sem relação com o fluxo da aplicação, e derrubam a comparação
determinística de telas idênticas (captura 13: checkpoints da run AIX
divergiam da run Linux só por esses dois trechos).
A máscara é aplicada só como segunda chance na comparação (as assinaturas
canônicas gravadas na trilha não mudam).
"""
from __future__ import annotations

import re

# Valores voláteis conhecidos da linha de status do Recital:
# - memória livre da sessão (muda a cada execução);
# - rótulo de plataforma + preenchimento até o separador '|' (muda com o
#   servidor — AIX × Linux; o separador ancora a máscara à linha de status
#   para não tocar o texto em outro contexto).
_PATTERNS = (
    re.compile(r"\d{1,3}(?:[.,]\d{3})+\s*Kb livres"),
    re.compile(r"(?:IBM Aix \(Common\)|Linux x86)\s*(?=\|)", re.IGNORECASE),
)

VOLATILE_PLACEHOLDER = "<volatil>"


def mask_volatile_screen_text(text: str) -> str:
    """Substitui os trechos voláteis conhecidos por um placeholder fixo."""
    masked = str(text or "")
    for pattern in _PATTERNS:
        masked = pattern.sub(VOLATILE_PLACEHOLDER, masked)
    return masked

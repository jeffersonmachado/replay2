"""Máscara de trechos voláteis de tela (ruído ambiental) na comparação.

A linha de status do Recital mostra a memória livre da sessão
(ex.: ``792,000 Kb livres``) — muda a cada execução sem relação com o fluxo
da aplicação e derruba a comparação determinística de telas idênticas.
A máscara é aplicada só como segunda chance na comparação (as assinaturas
canônicas gravadas na trilha não mudam).
"""
from __future__ import annotations

import re

# Valores voláteis conhecidos: memória livre da linha de status do Recital.
_PATTERNS = (re.compile(r"\d{1,3}(?:[.,]\d{3})+\s*Kb livres"),)

VOLATILE_PLACEHOLDER = "<volatil>"


def mask_volatile_screen_text(text: str) -> str:
    """Substitui os trechos voláteis conhecidos por um placeholder fixo."""
    masked = str(text or "")
    for pattern in _PATTERNS:
        masked = pattern.sub(VOLATILE_PLACEHOLDER, masked)
    return masked

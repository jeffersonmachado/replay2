"""conftest raiz — aplica os markers do pytest.ini automaticamente.

Os markers declarados em pytest.ini (unit, p2, control, integration, slow,
selenium, external) não são escritos à mão em cada teste: este hook de coleção
os atribui por localização do arquivo e por conteúdo do módulo, de modo que
seleções como ``pytest -m p2`` e
``pytest -m "not slow and not selenium and not external"`` funcionem conforme
documentado no AGENTS.md e no TESTES.md.

Regras de atribuição:

- ``tests/acceptance/**``            → integration + slow (roda no pipeline de
  release via scripts/acceptance/, não no loop de dev)
- nome contém "selenium" ou o módulo importa selenium → selenium
- módulo referencia chromium/google-chrome ou hosts internos (10.5.8.*)
  → external (depende de ambiente específico)
- nome contém "_unit"                → unit
- nome contém "integration" ou "e2e" → integration
- nome começa com "test_p2_" ou o módulo importa
  ``dakota_gateway.synthetic``/``dakota_gateway.source_analyzer`` → p2
- nome começa com "test_control_"/"test_ui_" ou o módulo referencia
  ``control/server.py`` → control
- módulo contém ``time.sleep(N)`` com N >= 5 → slow
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

_SLEEP_LONG_RE = re.compile(r"time\.sleep\(\s*(?:[5-9](?:\.\d+)?|[1-9]\d+(?:\.\d+)?)\s*\)")
_INTERNAL_HOST_RE = re.compile(r"\b10\.5\.8\.\d+")
_P2_IMPORT_RE = re.compile(r"dakota_gateway\.(synthetic|source_analyzer)")
_CHROMIUM_RE = re.compile(r"chromium|google-chrome", re.IGNORECASE)

_source_cache: dict[str, str] = {}


def _read_source(path: Path) -> str:
    """Lê o fonte do módulo de teste uma única vez (cache por caminho)."""
    key = str(path)
    if key not in _source_cache:
        try:
            _source_cache[key] = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            _source_cache[key] = ""
    return _source_cache[key]


def _markers_for(path: Path) -> set[str]:
    """Calcula o conjunto de markers aplicáveis ao arquivo de teste."""
    name = path.name
    source = _read_source(path)
    marks: set[str] = set()
    if "acceptance" in path.parts:
        marks.update(("integration", "slow"))
    if "selenium" in name or "from selenium import" in source:
        marks.add("selenium")
    if _CHROMIUM_RE.search(source) or _INTERNAL_HOST_RE.search(source):
        marks.add("external")
    if "_unit" in name:
        marks.add("unit")
    if "integration" in name or "e2e" in name:
        marks.add("integration")
    if name.startswith("test_p2_") or _P2_IMPORT_RE.search(source):
        marks.add("p2")
    if name.startswith(("test_control_", "test_ui_")) or "control/server.py" in source:
        marks.add("control")
    if _SLEEP_LONG_RE.search(source):
        marks.add("slow")
    return marks


def pytest_collection_modifyitems(items: list) -> None:
    """Aplica os markers automáticos a cada item coletado."""
    for item in items:
        path = Path(str(item.fspath))
        for mark in sorted(_markers_for(path)):
            item.add_marker(getattr(pytest.mark, mark))

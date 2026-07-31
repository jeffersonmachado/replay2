"""PASSAGEM 1 (TDD) — puppeteer deve ser dependencia declarada e local.

Hoje o puppeteer e' instalacao GLOBAL nao declarada, resolvida via
`npm root -g` em tests/test_terminal_snapshot_css_contract.py. A spec da
cadeia de release exige:
- puppeteer declarado no package.json RAIZ (dependencies ou devDependencies)
  com versao pinned;
- package-lock.json na raiz contendo puppeteer (reprodutibilidade);
- tests/test_terminal_snapshot_css_contract.py resolvendo o node_modules
  LOCAL do repo ANTES de qualquer fallback global.

Estes testes FALHAM hoje e PASSAM apos a implementacao.

Rodar isolado:
    pytest -q tests/acceptance/test_puppeteer_declared.py
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PACKAGE_JSON = ROOT / "package.json"
PACKAGE_LOCK = ROOT / "package-lock.json"
CSS_CONTRACT = ROOT / "tests" / "test_terminal_snapshot_css_contract.py"

# Versao pinned: X.Y.Z (opcionalmente com sufixo pre-release), sem ranges.
PINNED_VERSION = re.compile(r"^\d+\.\d+\.\d+([-+][0-9A-Za-z.-]+)?$")


def _declared_puppeteer_version() -> str | None:
    pkg = json.loads(PACKAGE_JSON.read_text(encoding="utf-8"))
    for section in ("dependencies", "devDependencies"):
        version = pkg.get(section, {}).get("puppeteer")
        if version:
            return version
    return None


def test_puppeteer_declared_in_root_package_json():
    """package.json raiz deve declarar puppeteer com versao pinned (sem
    ^, ~, *, latest ou ranges)."""
    version = _declared_puppeteer_version()
    assert version is not None, (
        "puppeteer nao declarado em dependencies/devDependencies do package.json "
        "raiz (hoje: instalacao global nao declarada)"
    )
    assert PINNED_VERSION.match(version), (
        f"versao do puppeteer deve ser pinned (X.Y.Z), veio: {version!r}"
    )


def test_package_lock_contains_puppeteer():
    """package-lock.json na raiz deve existir e conter puppeteer
    (reprodutibilidade da instalacao)."""
    assert PACKAGE_LOCK.exists(), f"package-lock.json ausente na raiz: {PACKAGE_LOCK}"
    lock = json.loads(PACKAGE_LOCK.read_text(encoding="utf-8"))
    packages = lock.get("packages", {})
    assert "node_modules/puppeteer" in packages, (
        "package-lock.json nao contem node_modules/puppeteer"
    )


def test_css_contract_resolves_local_node_modules_first():
    """tests/test_terminal_snapshot_css_contract.py deve resolver o puppeteer
    do node_modules LOCAL do repo antes de qualquer fallback global
    (`npm root -g`). Teste estatico do codigo de resolucao."""
    src = CSS_CONTRACT.read_text(encoding="utf-8")
    local_idx = src.find("node_modules")
    global_idx = src.find('"root", "-g"')
    if global_idx == -1:
        global_idx = src.find("'root', '-g'")
    assert local_idx != -1, (
        "resolucao do puppeteer nao consulta o node_modules local do repo "
        "(hoje: apenas `npm root -g`)"
    )
    assert global_idx != -1, (
        "fallback global `npm root -g` nao encontrado — a resolucao mudou; "
        "revise este teste estatico"
    )
    assert local_idx < global_idx, (
        "node_modules local deve ser consultado ANTES do fallback global "
        "(`npm root -g`) no codigo de resolucao do puppeteer"
    )

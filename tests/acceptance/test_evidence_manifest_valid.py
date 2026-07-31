"""PASSAGEM 1 (TDD) — manifest de evidencias deve ser valido e verificavel.

artifacts/evidence-manifest.sha256 (hoje gerado inline em
scripts/final-acceptance.sh) viola a spec da cadeia de release:
- AUTO-INCLUI a propria entrada (hash stale no momento da geracao);
- inclui artefatos velhos/proibidos (tarball de versao antiga,
  acceptance-matrix.json, final-artifact-manifest.json e lixo
  test-all-suite-*.result.json);
- contem entradas com hash que nao confere ou arquivo ausente.

A spec exige: manifest gerado POR ULTIMO, sem auto-inclusao, sem ausentes/
velhos, validado com `sha256sum -c` antes do tarball.

Este teste le o manifest ATUAL e FALHA nas violacoes presentes (evidencia do
problema); apos a implementacao, passa na proxima geracao do manifest.

Rodar isolado:
    pytest -q tests/acceptance/test_evidence_manifest_valid.py
"""
from __future__ import annotations

import fnmatch
import hashlib
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "artifacts" / "evidence-manifest.sha256"

# Padroes proibidos no manifest de evidencias (artefatos velhos/lixo).
FORBIDDEN_PATTERNS = (
    "*.tar.gz",
    "*/acceptance-matrix.json",
    "acceptance-matrix.json",
    "*/final-artifact-manifest.json",
    "final-artifact-manifest.json",
    "*/test-all-suite-*.result.json",
    "test-all-suite-*.result.json",
)


def _entries() -> list[tuple[str, str]]:
    if not MANIFEST.exists():
        pytest.fail(f"manifest de evidencias ausente: {MANIFEST}")
    entries = []
    for line in MANIFEST.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        parts = line.split(None, 1)
        assert len(parts) == 2, f"linha malformada no manifest: {line!r}"
        entries.append((parts[0], parts[1].lstrip(" *")))
    assert entries, "manifest de evidencias vazio"
    return entries


def test_evidence_manifest_does_not_include_itself():
    """O manifest nao pode conter a propria entrada (hash stale por
    construcao — o arquivo muda ao ser gravado)."""
    self_entries = [p for _, p in _entries()
                    if p.endswith("evidence-manifest.sha256")]
    assert not self_entries, (
        f"manifest auto-inclui a propria entrada (hash stale): {self_entries}"
    )


def test_evidence_manifest_has_no_forbidden_entries():
    """Sem tarball, acceptance-matrix.json, final-artifact-manifest.json nem
    lixo test-all-suite-*.result.json no manifest de evidencias."""
    forbidden = [p for _, p in _entries()
                 if any(fnmatch.fnmatch(p, pat) for pat in FORBIDDEN_PATTERNS)]
    assert not forbidden, (
        f"entradas proibidas no manifest de evidencias: {forbidden[:10]}"
    )


def test_evidence_manifest_entries_exist_and_match():
    """Todas as entradas devem existir e conferir — validacao equivalente a
    `sha256sum -c` a partir da raiz do projeto."""
    check = subprocess.run(
        ["sha256sum", "-c", str(MANIFEST)],
        capture_output=True, text=True, timeout=120, cwd=str(ROOT),
    )
    assert check.returncode == 0, (
        "sha256sum -c reprovou o manifest de evidencias "
        f"(entradas ausentes ou com hash stale):\n{(check.stdout + check.stderr)[-800:]}"
    )

    # Verificacao redundante em Python para mensagem precisa por entrada.
    missing, mismatch = [], []
    for digest, path in _entries():
        fp = ROOT / path
        if not fp.is_file():
            missing.append(path)
            continue
        actual = hashlib.sha256(fp.read_bytes()).hexdigest()
        if actual != digest:
            mismatch.append(path)
    assert not missing, f"entradas do manifest ausentes no disco: {missing[:10]}"
    assert not mismatch, f"entradas do manifest com hash stale: {mismatch[:10]}"

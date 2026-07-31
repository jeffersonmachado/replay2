"""PASSAGEM 1 (TDD) — baseline de testes de aceitacao deve ser REAL.

artifacts/acceptance-test-baseline.sha256 hoje esta' degradada: uma unica
linha com o hash do conteudo vazio e path "-" (passe vacuoso). A spec exige:
- lista real de arquivos protegidos (>= 30 entradas);
- nenhuma entrada com path "-" ou hash de vazio (e3b0c442...);
- toda entrada existente no disco e com hash conferindo;
- gerador scripts/acceptance/regen-baseline.sh que FALHA quando a lista de
  arquivos protegidos esta' vazia (recusa gerar passe vacuoso).

Estes testes FALHAM hoje (baseline degradada + gerador inexistente) e PASSAM
apos a implementacao.

Rodar isolado:
    pytest -q tests/acceptance/test_baseline_real.py
"""
from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BASELINE = ROOT / "artifacts" / "acceptance-test-baseline.sha256"
REGEN = ROOT / "scripts" / "acceptance" / "regen-baseline.sh"

# sha256 do conteudo vazio — marca do passe vacuoso.
EMPTY_SHA256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"

MIN_ENTRIES = 30


def _parse_entries() -> list[tuple[str, str]]:
    """Devolve [(hash, relpath)] das linhas nao vazias da baseline."""
    assert BASELINE.exists(), f"baseline ausente: {BASELINE}"
    entries = []
    for line in BASELINE.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        parts = line.split(None, 1)
        assert len(parts) == 2, f"linha malformada na baseline: {line!r}"
        digest, path = parts[0], parts[1].lstrip(" *")  # formato sha256sum: "  " ou " *"
        entries.append((digest, path))
    return entries


def test_baseline_has_real_entries():
    """Baseline deve ter >= 30 entradas reais — nunca linha com path "-" nem
    hash de conteudo vazio (passe vacuoso)."""
    entries = _parse_entries()
    assert len(entries) >= MIN_ENTRIES, (
        f"baseline degradada: {len(entries)} entrada(s), esperado >= {MIN_ENTRIES} "
        f"(hoje: passe vacuoso com hash de vazio e path '-')"
    )
    for digest, path in entries:
        assert path != "-", f"entrada com path '-' (stdin) proibida: {digest}  {path}"
        assert digest != EMPTY_SHA256, (
            f"hash de conteudo vazio proibido na baseline: {digest}  {path}"
        )


def test_baseline_entries_exist_and_match():
    """Toda entrada da baseline deve existir no disco e o sha256 do conteudo
    deve conferir (equivalente a `sha256sum -c`)."""
    entries = _parse_entries()
    missing, mismatch = [], []
    for digest, path in entries:
        fp = ROOT / path
        if not fp.is_file():
            missing.append(path)
            continue
        actual = hashlib.sha256(fp.read_bytes()).hexdigest()
        if actual != digest:
            mismatch.append(path)
    assert not missing, f"arquivos listados na baseline ausentes no disco: {missing[:10]}"
    assert not mismatch, f"hashes da baseline nao conferem: {mismatch[:10]}"


def test_regen_baseline_fails_on_empty_protected_list(tmp_path):
    """O gerador scripts/acceptance/regen-baseline.sh deve RECUSAR gerar
    baseline quando a lista de arquivos protegidos esta' vazia (sandbox sem os
    arquivos do projeto): exit != 0. Roda em sandbox (cwd/HOME temporarios)
    para nao tocar nos artifacts reais. Falha hoje: o script nao existe."""
    if not REGEN.exists():
        pytest.fail(f"gerador de baseline ausente: {REGEN}")
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    env = {**os.environ, "HOME": str(sandbox)}
    try:
        r = subprocess.run(
            ["bash", str(REGEN)], cwd=str(sandbox), env=env,
            capture_output=True, text=True, timeout=120,
        )
    except FileNotFoundError as e:
        pytest.fail(f"gerador de baseline nao executavel: {e}")
    assert r.returncode != 0, (
        "regen-baseline.sh aceitou lista de arquivos protegidos vazia "
        f"(rc=0) — passe vacuoso permitido\nstdout={r.stdout[-400:]}\nstderr={r.stderr[-400:]}"
    )

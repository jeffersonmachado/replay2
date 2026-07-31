"""PASSAGEM 1 (TDD) — manifest do tree_hash deve ser compativel com sha256sum -c.

Hoje `python3 scripts/tree_hash.py --manifest` grava
artifacts/source-tree-manifest.sha256 com hash COMPOSTO
(relpath + "\\n" + size + "\\n" + content), incompativel com `sha256sum -c`.
A spec nova exige que --manifest emita `sha256(content)  <relpath>` puro;
o hash composto canonico continua disponivel na saida padrao (stdout) sem
--manifest (e em artifacts/source-tree-hash.json).

O teste de manifest FALHA hoje (hash composto) e PASSA apos a implementacao.

Rodar isolado:
    pytest -q tests/test_tree_hash_manifest_unit.py
"""
from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TREE_HASH = ROOT / "scripts" / "tree_hash.py"
MANIFEST = ROOT / "artifacts" / "source-tree-manifest.sha256"

HEX64 = re.compile(r"^[0-9a-f]{64}$")


def _run_tree_hash(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(TREE_HASH), *args],
        capture_output=True, text=True, timeout=120, cwd=str(ROOT),
    )


def test_manifest_is_sha256sum_compatible(tmp_path):
    """--manifest deve emitir `sha256(content)  <relpath>` puro: formato
    aceito por `sha256sum -c` e hash recomputado do conteudo batendo.

    O manifest real em artifacts/ e' preservado: backup antes, restore no
    finally (o tree_hash grava em caminho fixo)."""
    backup = tmp_path / "source-tree-manifest.sha256.bak"
    existed = MANIFEST.exists()
    if existed:
        shutil.copy2(MANIFEST, backup)
    try:
        r = _run_tree_hash("--manifest")
        assert r.returncode == 0, f"tree_hash --manifest falhou: {r.stderr[-400:]}"
        assert MANIFEST.exists(), f"manifest nao gerado: {MANIFEST}"

        lines = [l for l in MANIFEST.read_text(encoding="utf-8").splitlines() if l.strip()]
        assert lines, "manifest vazio"

        entries = []
        for line in lines:
            m = re.match(r"^([0-9a-f]{64})  (.+)$", line)
            assert m, f"linha fora do formato 'sha256  path' do sha256sum: {line!r}"
            entries.append((m.group(1), m.group(2)))

        # Recomputa o sha256 PURO do conteudo de algumas entradas — hoje o
        # manifest carrega hash composto (relpath+size+content) e FALHA aqui.
        for digest, rel in entries[:3]:
            actual = hashlib.sha256((ROOT / rel).read_bytes()).hexdigest()
            assert actual == digest, (
                f"hash do manifest nao e' sha256 puro do conteudo de {rel}: "
                f"manifest={digest} conteudo={actual}"
            )

        # Validacao integral pelo verificador canonico.
        check = subprocess.run(
            ["sha256sum", "-c", str(MANIFEST)],
            capture_output=True, text=True, timeout=120, cwd=str(ROOT),
        )
        assert check.returncode == 0, (
            f"sha256sum -c reprovou o manifest:\n{(check.stdout + check.stderr)[-600:]}"
        )
    finally:
        if existed:
            shutil.copy2(backup, MANIFEST)
        elif MANIFEST.exists():
            MANIFEST.unlink()


def test_composite_hash_remains_available_on_stdout():
    """O hash composto canonico da arvore continua disponivel: saida padrao
    sem --manifest imprime um sha256 hex unico (e nao grava manifest)."""
    r = _run_tree_hash()
    assert r.returncode == 0, f"tree_hash falhou: {r.stderr[-400:]}"
    out = r.stdout.strip()
    assert HEX64.match(out), f"saida padrao deveria ser um sha256 hex, veio: {out!r}"

"""Testes de regressão da publicação atômica do tarball (race de deploy).

Contexto: os deploys AIX e Linux rodam `build-tarball.sh` em paralelo no
mesmo `dist/`. Antes da correção, o tarball era escrito diretamente no nome
final; um leitor concorrente (`build-selfinstall.sh`, tar-pipe do deploy)
podia ler um payload parcial — incidente real no deploy 0.8.3
("sanity: payload gzip inválido").

A correção exige escrita em nome temporário + rename atômico (mesma FS).
"""
from __future__ import annotations

import gzip
import os
import re
import shutil
import subprocess
import tarfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "build-tarball.sh"


# ── Contrato estático ───────────────────────────────────────────────────────

def test_script_writes_to_temp_and_renames_atomically():
    """O script deve gravar o payload em nome temporário e publicar via mv."""
    src = SCRIPT.read_text(encoding="utf-8")
    assert re.search(r'TMP_OUT="\$OUT\.tmp\.\$\$"', src), (
        "build-tarball.sh deve definir TMP_OUT=\"$OUT.tmp.$$\""
    )
    # o tar/gzip não pode escrever diretamente em "$OUT"
    assert not re.search(r'tar -czf "\$OUT"', src), (
        "tar -czf não pode escrever diretamente em $OUT (leitor vê parcial)"
    )
    assert not re.search(r'gzip -c >"\$OUT"', src), (
        "fallback gzip não pode escrever diretamente em $OUT"
    )
    assert re.search(r'mv -f "\$TMP_OUT" "\$OUT"', src), (
        "publicação deve ser rename atômico: mv -f \"$TMP_OUT\" \"$OUT\""
    )


def test_script_cleans_temp_on_exit():
    """Falha no meio do build não pode deixar .tmp órfão em dist/."""
    src = SCRIPT.read_text(encoding="utf-8")
    assert "TMP_OUT" in src.split("trap")[1].split("\n\n")[0] or \
        re.search(r'rm -f "\$TMP_OUT"', src), (
            "cleanup deve remover $TMP_OUT"
        )


# ── Funcional: dois builds concorrentes, mesmo timestamp ────────────────────

def _make_fake_root(base: Path) -> Path:
    """Árvore mínima que satisfaz os gates do build-tarball.sh."""
    for d in ("bin", "lib", "screens", "examples", "scripts"):
        (base / d).mkdir(parents=True, exist_ok=True)
    (base / "install.sh").write_text("#!/bin/sh\nexit 0\n")
    (base / "uninstall.sh").write_text("#!/bin/sh\nexit 0\n")
    (base / "VERSION").write_text("0.0.0-test\n")
    # Payload não trivial para forçar writes intercalados sem a correção
    payload = os.urandom(256 * 1024)
    (base / "lib" / "payload.bin").write_bytes(payload)
    shutil.copy(SCRIPT, base / "scripts" / "build-tarball.sh")
    return base


def _run_build(fake_root: Path, timestamp: str) -> subprocess.CompletedProcess:
    env = dict(os.environ, DAKOTA_TARBALL_TIMESTAMP=timestamp)
    return subprocess.run(
        ["sh", "scripts/build-tarball.sh"],
        cwd=fake_root,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_concurrent_builds_same_timestamp_produce_valid_tarball(tmp_path):
    """Dois builds simultâneos com o mesmo timestamp: o tarball final é íntegro.

    Sem escrita atômica, os dois processos escrevem o mesmo caminho e o
    gzip final sai corrompido (interleaving de streams).
    """
    fake_root = _make_fake_root(tmp_path / "tree")
    ts = "20990101-000000"
    with ThreadPoolExecutor(max_workers=2) as pool:
        r1, r2 = list(pool.map(
            lambda _: _run_build(fake_root, ts), range(2)
        ))
    assert r1.returncode == 0, f"build 1 falhou: {r1.stderr[-500:]}"
    assert r2.returncode == 0, f"build 2 falhou: {r2.stderr[-500:]}"

    tarballs = list((fake_root / "dist").glob("*.tar.gz"))
    assert len(tarballs) == 1, f"esperado 1 tarball final, achados: {tarballs}"
    out = tarballs[0]

    # gzip íntegro de ponta a ponta
    with gzip.open(out, "rb") as fh:
        while fh.read(1 << 20):
            pass

    # conteúdo completo: payload de 256KB presente e íntegro
    with tarfile.open(out, "r:gz") as tf:
        names = tf.getnames()
        assert any(n.endswith("lib/payload.bin") for n in names), names[:10]
        member = next(m for m in tf.getmembers() if m.name.endswith("lib/payload.bin"))
        assert member.size == 256 * 1024

    # nenhum temporário órfão
    leftovers = list((fake_root / "dist").glob("*.tmp.*"))
    assert leftovers == [], f"temporários órfãos: {leftovers}"

#!/usr/bin/env python3
"""Deterministic tree hash — works without .git, in CI, and in extracted tarballs.

Usage:
  python3 scripts/tree_hash.py [--manifest]

Output:
  <sha256>  (stdout)
  Optionally writes artifacts/source-tree-manifest.sha256 with --manifest.

Algorithm:
  1. Walk all files recursively from the project root
  2. Sort by relative path (bytes)
  3. For each file: hash(relpath + "\\n" + size + "\\n" + content)
  4. SHA-256 of concatenated per-file hashes

Excludes:
  .git/, dist/, __pycache__/, .pytest_cache/, *.pyc,
  artifacts/acceptance-logs/, artifacts/visual-failure/,
  artifacts/visual-test-result.json,
  artifacts/final-acceptance-report.md, artifacts/final-acceptance-results.json,
  artifacts/manual-validation.json
"""
import hashlib
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

EXCLUDE_DIRS = {
    ".git", "dist", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    "node_modules", ".venv", "htmlcov", "log", "logs", "state",
    ".claude", ".codex", ".github", ".vscode", ".local-secrets",
    "dev", "docs",
}
EXCLUDE_DIR_PREFIXES = ("artifacts/acceptance-logs", "artifacts/visual-failure")

EXCLUDE_FILES = {
    "artifacts/visual-test-result.json",
    "artifacts/final-acceptance-report.md",
    "artifacts/final-acceptance-results.json",
    "artifacts/manual-validation.json",
    "artifacts/acceptance-log-summary.json",
    "artifacts/final-artifact-manifest.json",
    "artifacts/source-tree-manifest.sha256",
    "artifacts/source-tree-hash.json",
    "artifacts/evidence-manifest.sha256",
    # Root-level dotfiles and docs not in distributable artifact
    ".gitignore", ".hintrc", ".codex",
    "AGENTS.md",
    "AI_ASSESSMENT_ARQUITETURA.md", "ANALISE_PROFUNDA.md", "ANALISE_R_OBSERVE.md",
    "AUDITORIA_REPLAY2.md", "BENCHMARK_ARQUITETURA.md", "CAMADA_WEB_ANALISE.md",
    "CHECKLIST_EMPACOTAMENTO.md", "CONTRIBUTING.md", "DEBT_MAP.md",
    "DESENVOLVIMENTO.md", "DIAGNOSTICO_TECNICO.md", "DISCOVERY_AUDITORIA.md",
    "FILTROS_AUDITORIA.md", "FRONTEIRAS.md", "GAPS.md", "JOURNEY_AUDITORIA.md",
    "REFATORACAO_ESTABILIZACAO_RELATORIO.md", "REPLAY_FLUXO.md", "ROADMAP.md",
    "SYNTHETIC_DATA_ARQUITETURA.md", "TESTES.md", "TESTE_INTERFACE_WEB.md",
    # Build/dev configs not in distributable tarball
    "Makefile", "package.json", "pytest.ini", "tailwind.config.cjs", "dev.sh",
    # Old artifacts not in tarball
    "artifacts/acceptance-matrix.json",
    # Scripts excluded from tarball (contain credentials/hosts)
    "scripts/show-admin-credentials.sh",
}

EXCLUDE_FILE_EXTENSIONS = {".pyc", ".pyo", ".db", ".db-wal", ".db-shm", ".sqlite", ".sqlite3"}

def _should_exclude_file(filename: str) -> bool:
    if any(filename.endswith(ext) for ext in EXCLUDE_FILE_EXTENSIONS):
        return True
    if filename.endswith(".tar.gz"):
        return True
    return False


def tree_hash(root: Path, *, manifest: bool = False) -> str:
    entries = []
    manifest_lines = []

    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        # Filter excluded directories
        dirnames[:] = [
            d for d in dirnames
            if d not in EXCLUDE_DIRS
            and not any(str(Path(dirpath) / d).startswith(str(root / p)) for p in EXCLUDE_DIR_PREFIXES)
        ]

        for fn in sorted(filenames):
            fp = Path(dirpath) / fn
            # Skip excluded file extensions
            if _should_exclude_file(fn):
                continue
            rel = str(fp.relative_to(root))
            if rel in EXCLUDE_FILES:
                continue
            try:
                content = fp.read_bytes()
            except (OSError, PermissionError):
                continue
            entries.append((rel, len(content), content))

    # Sort by relative path (bytes comparison)
    entries.sort(key=lambda x: x[0].encode())

    h = hashlib.sha256()
    for rel, size, content in entries:
        file_hash = hashlib.sha256(
            rel.encode() + b"\n" + str(size).encode() + b"\n" + content
        ).hexdigest()
        h.update(file_hash.encode())
        if manifest:
            manifest_lines.append(f"{file_hash}  {rel}")

    if manifest:
        manifest_path = root / "artifacts/source-tree-manifest.sha256"
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text("\n".join(manifest_lines) + "\n")

    result = h.hexdigest()
    if result == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855":
        raise SystemExit("ERROR: empty tree hash (no files found)")
    return result


if __name__ == "__main__":
    manifest = "--manifest" in sys.argv
    try:
        h = tree_hash(ROOT, manifest=manifest)
        print(h)
    except SystemExit as e:
        print(e, file=sys.stderr)
        sys.exit(1)

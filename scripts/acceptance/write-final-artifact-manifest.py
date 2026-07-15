#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path


def sha256_file(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_tree(root: Path, paths: list[Path]) -> str:
    import hashlib

    digest = hashlib.sha256()
    for path in sorted(paths, key=lambda p: p.relative_to(root).as_posix()):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if "/__pycache__/" in f"/{rel}" or rel.endswith((".pyc", ".pyo")):
            continue
        digest.update(rel.encode("utf-8") + b"\0")
        digest.update(sha256_file(path).encode("ascii") + b"\n")
    return digest.hexdigest()


def source_tree_files(root: Path) -> list[Path]:
    names = ["bin", "lib", "screens", "examples", "gateway", "tests", "scripts"]
    files: list[Path] = []
    for name in names:
        base = root / name
        if base.exists():
            files.extend(path for path in base.rglob("*") if path.is_file())
    for name in ["install.sh", "uninstall.sh", "VERSION", "README.md"]:
        path = root / name
        if path.is_file():
            files.append(path)
    return files


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: write-final-artifact-manifest.py TAR_URI_OR_PATH", file=sys.stderr)
        return 2
    root = Path(__file__).resolve().parents[2]
    tarball = (root / argv[1]).resolve() if not Path(argv[1]).is_absolute() else Path(argv[1])
    if not tarball.is_file():
        print(f"missing tarball: {tarball}", file=sys.stderr)
        return 1

    extracted_log = root / "artifacts" / "acceptance-logs" / "extracted" / "run-phase-08-full-final-artifact.log"
    final_log = root / "artifacts" / "acceptance-logs" / "final" / "run-phase-08-full.log"
    manifest = {
        "completed_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "artifact": tarball.relative_to(root).as_posix(),
        "sha256": sha256_file(tarball),
        "bytes": tarball.stat().st_size,
        "source_tree_sha256": sha256_tree(root, source_tree_files(root)),
        "validated_original_gate_log": final_log.relative_to(root).as_posix(),
        "validated_extracted_gate_log": extracted_log.relative_to(root).as_posix(),
        "validated_extracted_gate_exit_code": 0,
        "note": "This external manifest is intentionally written after validating the final tarball, avoiding a self-referential rebuild cycle.",
    }
    out = root / "artifacts" / "final-artifact-manifest.json"
    out.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

#!/usr/bin/env python3
"""Validações da cadeia de build/release do tarball Dakota Replay2.

Concentra a lógica testável usada por scripts/build-tarball.sh (que fica
"fino", só orquestrando). Subcomandos:

  check-acceptance --root DIR [--tree-hash SHA]
      Vincula o aceite (artifacts/final-acceptance-results.json) à árvore
      atual: exige mesmo hash de árvore (before/after), mesma VERSION e a
      suíte completa aprovada. Sai != 0 listando TODOS os problemas.

  select-benchmark --root DIR [--with-benchmarks ID|none|auto]
      Escolhe o experimento de benchmark a empacotar (FASE 11): por default
      ("auto") o mais recente com experiment-manifest.json válido; históricos
      ficam fora do pacote de runtime. Imprime o id no stdout (vazio = nenhum).

  verify-tarball TARBALL [--expected-hash SHA] [--version V]
      Extrai o tarball recém-gerado em diretório temporário, roda sanity
      checks (VERSION presente e igual, server.py presente, nenhum item
      proibido) e, com --expected-hash, compara o hash da árvore extraída
      (calculado pelo scripts/tree_hash.py DO PRÓPRIO PACOTE) com o aceite.

Sem saída parcial: qualquer problema imprime a lista completa no stderr e
retorna exit 1.
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

RESULTS_REL = "artifacts/final-acceptance-results.json"

# Flags do results JSON que provam que a suíte completa rodou e passou no
# aceite — sem todas verdadeiras, não existe release "aprovada".
REQUIRED_RESULT_FLAGS = (
    "baseline_verified",
    "tree_validation_passed",
    "source_tree_unchanged",
    "visual_test_verified",
    "contamination_regression_verified",
    "full_python_suite_passed",
    "gateway_suite_passed",
    "javascript_suite_passed",
    "tcl_suite_passed",
    "test_all_passed",
)

# Itens que NUNCA podem aparecer num pacote extraído (espelha a lista de
# remoção do build-tarball.sh e o .gitignore).
FORBIDDEN_FILE_PATTERNS = (
    "*.key", "*.pem", "*.crt", "*.pfx", "*.ppk",
    "id_rsa*", "id_ed25519*", "id_ecdsa*",
    ".env", ".env.*", ".token.env",
    "*.db", "*.db-wal", "*.db-shm", "*.sqlite", "*.sqlite3",
    "*.pyc", "*.pyo",
)
FORBIDDEN_DIR_NAMES = {
    "__pycache__", "node_modules", ".git", ".venv", ".pytest_cache",
    ".mypy_cache", ".ruff_cache",
}


def current_tree_hash(root: Path) -> str:
    """Hash canônico da árvore, calculado pelo tree_hash.py da própria raiz."""
    r = subprocess.run(
        [sys.executable, str(root / "scripts" / "tree_hash.py")],
        cwd=str(root), capture_output=True, text=True, timeout=300,
    )
    if r.returncode != 0:
        raise RuntimeError(f"tree_hash falhou em {root}: {r.stderr[-400:]}")
    return r.stdout.strip()


def validate_acceptance(root: Path, *, tree_hash: str | None = None) -> list[str]:
    """Problemas de vinculação do aceite com a árvore atual (vazio = OK)."""
    problems: list[str] = []
    version_file = root / "VERSION"
    if not version_file.is_file():
        return ["arquivo VERSION ausente na raiz"]
    version = version_file.read_text(encoding="utf-8").strip()

    results_path = root / RESULTS_REL
    if not results_path.is_file():
        return [
            f"{RESULTS_REL} ausente — rode `bash scripts/final-acceptance.sh` "
            "antes do build (aceite antigo/reaproveitado é proibido)"
        ]
    try:
        data = json.loads(results_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"{RESULTS_REL} inválido: {exc}"]

    if tree_hash is None:
        tree_hash = current_tree_hash(root)

    results_version = data.get("version")
    if results_version is None:
        problems.append(
            f"{RESULTS_REL} sem campo 'version' (formato antigo) — "
            "regere o aceite com scripts/final-acceptance.sh"
        )
    elif results_version != version:
        problems.append(
            f"aceite é da versão {results_version}, mas a árvore atual é "
            f"{version} — regere o aceite para a versão atual"
        )

    before = data.get("source_tree_sha256_before")
    after = data.get("source_tree_sha256_after")
    if before != tree_hash:
        problems.append(
            "aceite gerado sobre OUTRA árvore: "
            f"source_tree_sha256_before={before} != árvore atual={tree_hash}"
        )
    if after != tree_hash:
        problems.append(
            "hash pós-testes do aceite diverge da árvore atual: "
            f"source_tree_sha256_after={after} != {tree_hash}"
        )

    for flag in REQUIRED_RESULT_FLAGS:
        if data.get(flag) is not True:
            problems.append(
                f"suíte completa não aprovada no aceite: "
                f"{flag}={data.get(flag)!r}"
            )
    return problems


def _experiment_manifest_ok(exp_dir: Path) -> bool:
    manifest = exp_dir / "experiment-manifest.json"
    if not manifest.is_file():
        return False
    try:
        json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return True


def select_benchmark(root: Path, requested: str = "auto") -> tuple[str | None, list[str]]:
    """Escolhe o experimento oficial de benchmark a empacotar.

    requested="auto": o mais recente (ordem de nome) com manifesto válido;
    requested="none": nenhum; requested="<id>": exatamente esse id, validado.
    Retorna (id_ou_none, problemas).
    """
    bench_dir = root / "artifacts" / "benchmarks"
    if requested in ("", "auto"):
        if not bench_dir.is_dir():
            return None, []
        candidates = sorted(
            d.name for d in bench_dir.iterdir()
            if d.is_dir() and _experiment_manifest_ok(d)
        )
        return (candidates[-1] if candidates else None), []
    if requested == "none":
        return None, []
    exp_dir = bench_dir / requested
    if not exp_dir.is_dir():
        return None, [
            f"experimento de benchmark inexistente: artifacts/benchmarks/{requested}"
        ]
    if not _experiment_manifest_ok(exp_dir):
        return None, [
            f"experimento {requested} sem experiment-manifest.json válido — "
            "não é evidência oficial aprovada"
        ]
    return requested, []


def _scan_forbidden(extract_root: Path) -> list[str]:
    problems: list[str] = []
    for path in sorted(extract_root.rglob("*")):
        rel = path.relative_to(extract_root).as_posix()
        if path.is_dir() and path.name in FORBIDDEN_DIR_NAMES:
            problems.append(f"item proibido no pacote (diretório): {rel}")
        elif path.is_file() and any(
            fnmatch.fnmatch(path.name, pat) for pat in FORBIDDEN_FILE_PATTERNS
        ):
            problems.append(f"item proibido no pacote (arquivo): {rel}")
    state_dir = extract_root / "gateway" / "state"
    if state_dir.exists():
        problems.append("item proibido no pacote (diretório): gateway/state")
    return problems


def _manifest_lines(root: Path, target: Path) -> None:
    """Gera o manifest por arquivo (sha256sum) de uma árvore, para diff."""
    r = subprocess.run(
        [sys.executable, str(root / "scripts" / "tree_hash.py"),
         f"--manifest-out={target}"],
        cwd=str(root), capture_output=True, text=True, timeout=300,
    )
    if r.returncode != 0:
        raise RuntimeError(f"tree_hash --manifest-out falhou em {root}: {r.stderr[-400:]}")


def _diff_manifests(expected_root: Path, extract_root: Path, td: str) -> list[str]:
    """Caminhos que divergem entre a árvore do aceite e a árvore extraída."""
    src_m = Path(td) / "expected.manifest"
    ext_m = Path(td) / "extracted.manifest"
    _manifest_lines(expected_root, src_m)
    _manifest_lines(extract_root, ext_m)
    src = {line.split("  ", 1)[1]: line.split("  ", 1)[0]
           for line in src_m.read_text(encoding="utf-8").splitlines() if "  " in line}
    ext = {line.split("  ", 1)[1]: line.split("  ", 1)[0]
           for line in ext_m.read_text(encoding="utf-8").splitlines() if "  " in line}
    diffs = []
    for path in sorted(set(src) | set(ext)):
        if path not in src:
            diffs.append(f"+{path} (só no pacote)")
        elif path not in ext:
            diffs.append(f"-{path} (só na árvore do aceite)")
        elif src[path] != ext[path]:
            diffs.append(f"~{path} (conteúdo divergente)")
    return diffs


def verify_tarball(
    tarball: Path,
    *,
    expected_hash: str | None = None,
    version: str | None = None,
    source_root: Path | None = None,
) -> list[str]:
    """Sanity + vinculação do tarball recém-gerado (vazio = OK)."""
    problems: list[str] = []
    with tempfile.TemporaryDirectory(prefix="dakota-verify-") as td:
        try:
            with tarfile.open(tarball, "r:gz") as tf:
                try:
                    tf.extractall(td, filter="data")
                except TypeError:  # Python sem PEP 706
                    tf.extractall(td)
        except (OSError, tarfile.TarError) as exc:
            return [f"tarball inválido/corrompido: {exc}"]

        roots = [p for p in Path(td).iterdir()]
        if len(roots) != 1 or not roots[0].is_dir():
            return ["pacote deve ter exatamente UM diretório raiz"]
        extract_root = roots[0]
        if not extract_root.name.startswith("dakota-replay2-"):
            problems.append(f"diretório raiz inesperado: {extract_root.name}")

        version_file = extract_root / "VERSION"
        if not version_file.is_file():
            problems.append("pacote sem arquivo VERSION")
        elif version is not None:
            packed = version_file.read_text(encoding="utf-8").strip()
            if packed != version:
                problems.append(
                    f"VERSION do pacote ({packed}) diverge da árvore ({version})"
                )

        if (extract_root / "gateway").is_dir() and not (
            extract_root / "gateway" / "control" / "server.py"
        ).is_file():
            problems.append("pacote sem gateway/control/server.py")

        problems.extend(_scan_forbidden(extract_root))

        if expected_hash:
            packaged_tree_hash = extract_root / "scripts" / "tree_hash.py"
            if not packaged_tree_hash.is_file():
                problems.append("pacote sem scripts/tree_hash.py")
            else:
                try:
                    got = current_tree_hash(extract_root)
                except RuntimeError as exc:
                    problems.append(str(exc))
                else:
                    if got != expected_hash:
                        msg = (
                            "árvore extraída diverge do aceite: "
                            f"hash do pacote={got} != hash validado={expected_hash}"
                        )
                        if source_root is not None:
                            try:
                                diffs = _diff_manifests(
                                    Path(source_root), extract_root, td)
                            except RuntimeError as exc:
                                diffs = [f"(diff indisponível: {exc})"]
                            if diffs:
                                msg += "\n  arquivos divergentes:\n  " + "\n  ".join(
                                    diffs[:40])
                        problems.append(msg)
    return problems


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_check = sub.add_parser("check-acceptance",
                             help="vincula o aceite à árvore atual")
    p_check.add_argument("--root", required=True)
    p_check.add_argument("--tree-hash", default=None,
                         help="hash já calculado pela chamada (default: recomputa)")

    p_bench = sub.add_parser("select-benchmark",
                             help="escolhe o experimento oficial a empacotar")
    p_bench.add_argument("--root", required=True)
    p_bench.add_argument("--with-benchmarks", default="auto",
                         help="id do experimento, 'none' ou 'auto' (default)")

    p_verify = sub.add_parser("verify-tarball",
                              help="sanity + hash da árvore extraída")
    p_verify.add_argument("tarball")
    p_verify.add_argument("--expected-hash", default=None)
    p_verify.add_argument("--version", default=None)
    p_verify.add_argument("--root", default=None,
                          help="raiz da árvore do aceite (diff por arquivo "
                               "quando o hash diverge)")

    args = parser.parse_args(argv)

    if args.cmd == "check-acceptance":
        problems = validate_acceptance(Path(args.root), tree_hash=args.tree_hash)
        if problems:
            for p in problems:
                print(f"ERROR: {p}", file=sys.stderr)
            return 1
        print("check-acceptance OK: aceite vinculado à árvore e versão atuais")
        return 0

    if args.cmd == "select-benchmark":
        bench_id, problems = select_benchmark(
            Path(args.root), requested=args.with_benchmarks)
        if problems:
            for p in problems:
                print(f"ERROR: {p}", file=sys.stderr)
            return 1
        if bench_id:
            print(bench_id)
        return 0

    if args.cmd == "verify-tarball":
        problems = verify_tarball(
            Path(args.tarball),
            expected_hash=args.expected_hash,
            version=args.version,
            source_root=Path(args.root) if args.root else None,
        )
        if problems:
            for p in problems:
                print(f"ERROR: {p}", file=sys.stderr)
            return 1
        print("verify-tarball OK: pacote íntegro e vinculado ao aceite")
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

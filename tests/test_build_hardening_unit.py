"""FASE 2 + FASE 11 — hardening do build: aceite vinculado à árvore e
empacotamento limpo/reproduzível.

Contexto: o pacote 0.8.85 carregou aceite de OUTRA árvore (hash registrado
4af666c6… vs hash da árvore empacotada a6903b20…; 57 checksums divergentes +
arquivos fora do manifesto). Estes testes cobrem as correções:

FASE 2 (aceite vinculado à árvore):
- build-tarball.sh exige final-acceptance-results.json da MESMA árvore
  (source_tree_sha256_before/after == hash atual) e da MESMA VERSION, com a
  suíte completa aprovada — artefato antigo/reaproveitado reprova o build;
- tree_hash.py classifica deterministicamente os subprodutos da suíte (logs
  de aceitação, caches, state, segredos locais, benchmarks pinados pelo
  evidence-manifest) — hash estável antes/depois dos testes;
- pós-build: o tarball é extraído, a árvore extraída é hasheada com o
  tree_hash.py do próprio pacote e comparada ao aceite + sanity de conteúdo.

FASE 11 (empacotamento limpo):
- benchmarks históricos NÃO entram por default — só o experimento oficial
  (mais recente com experiment-manifest.json válido) ou o id passado via
  --with-benchmarks <id>;
- tarball reproduzível (ordem estável, owner/group 0, mtime normalizado,
  gzip -n) quando o tar/gzip do ambiente suportam;
- segredos/bancos/estado local plantados nunca entram no pacote.

Rodar isolado:
    pytest -q tests/test_build_hardening_unit.py
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import build_validate  # noqa: E402
from tree_hash import tree_hash  # noqa: E402

BUILD_SCRIPT = "scripts/build-tarball.sh"
RELEASE_SCRIPTS = (
    "scripts/build-tarball.sh",
    "scripts/tree_hash.py",
    "scripts/build_validate.py",
    "scripts/acceptance/gen-evidence-manifest.sh",
)

FAKE_VERSION = "9.9.9-test"
PINNED_ENV = {
    "DAKOTA_TARBALL_TIMESTAMP": "20990101-000000",
    "SOURCE_DATE_EPOCH": "1700000000",
}


# ── Helpers ─────────────────────────────────────────────────────────────────

def _copy_release_scripts(root: Path) -> None:
    for rel in RELEASE_SCRIPTS:
        dst = root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / rel, dst)


def _make_fake_root(base: Path, version: str = FAKE_VERSION) -> Path:
    """Árvore mínima que satisfaz os gates estruturais do build-tarball.sh."""
    base.mkdir(parents=True, exist_ok=True)
    for d in ("bin", "lib", "screens", "examples", "tests", "gateway/control"):
        (base / d).mkdir(parents=True, exist_ok=True)
    (base / "bin" / "main.exp").write_text("# main\n", encoding="utf-8")
    (base / "lib" / "engine.tcl").write_text("# engine v1\n", encoding="utf-8")
    (base / "install.sh").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    (base / "uninstall.sh").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    (base / "VERSION").write_text(version + "\n", encoding="utf-8")
    (base / "gateway" / "control" / "server.py").write_text("# server\n", encoding="utf-8")
    (base / "tests" / "test_dummy_unit.py").write_text(
        "def test_ok():\n    assert True\n", encoding="utf-8"
    )
    _copy_release_scripts(base)
    return base


def _tree_hash(root: Path) -> str:
    r = subprocess.run(
        [sys.executable, "scripts/tree_hash.py"],
        cwd=root, capture_output=True, text=True, timeout=120,
    )
    assert r.returncode == 0, f"tree_hash falhou: {r.stderr[-400:]}"
    return r.stdout.strip()


def _results_payload(tree_hash: str, version: str, **overrides) -> dict:
    data = {
        "schema_version": "1.0",
        "version": version,
        "source_tree_sha256_before": tree_hash,
        "source_tree_sha256_after": tree_hash,
        "source_tree_unchanged": True,
        "baseline_verified": True,
        "tree_validation_passed": True,
        "visual_test_verified": True,
        "contamination_regression_verified": True,
        "full_python_suite_passed": True,
        "gateway_suite_passed": True,
        "javascript_suite_passed": True,
        "tcl_suite_passed": True,
        "test_all_passed": True,
    }
    data.update(overrides)
    return data


def _make_release_artifacts(root: Path, version: str = FAKE_VERSION,
                            results_overrides: dict | None = None,
                            drop_results_keys: tuple[str, ...] = ()) -> str:
    """Gera artifacts/ coerentes com a árvore (modo release do build)."""
    art = root / "artifacts"
    (art / "acceptance-logs" / "current").mkdir(parents=True, exist_ok=True)
    (art / "acceptance-logs" / "current" / "fase.log").write_text("log\n", encoding="utf-8")

    # Benchmarks: v1 e v2 com manifesto válido; v3 SEM manifesto (inválido).
    bench = art / "benchmarks"
    for exp, manifest in (("exp-oficial-v1", True), ("exp-oficial-v2", True),
                          ("exp-oficial-v3", False)):
        d = bench / exp / "runs" / "run1"
        d.mkdir(parents=True, exist_ok=True)
        (d / "application-samples.jsonl").write_text(
            f'{{"exp": "{exp}"}}\n', encoding="utf-8")
        if manifest:
            (bench / exp / "experiment-manifest.json").write_text(
                json.dumps({"experiment_id": exp}), encoding="utf-8")

    # Evidence manifest pelo gerador oficial (cwd == raiz exigido).
    r = subprocess.run(
        ["bash", "scripts/acceptance/gen-evidence-manifest.sh"],
        cwd=root, capture_output=True, text=True, timeout=120,
    )
    assert r.returncode == 0, f"gen-evidence-manifest falhou: {r.stderr[-400:]}"

    (art / "acceptance-test-baseline.sha256").write_text(
        f"{'0' * 64}  VERSION\n", encoding="utf-8")
    (art / "final-acceptance-report.md").write_text("# report\n", encoding="utf-8")
    (art / "manual-validation.json").write_text("{}\n", encoding="utf-8")
    (art / "visual-test-result.json").write_text(
        json.dumps({"passed": True}), encoding="utf-8")

    h = _tree_hash(root)
    subprocess.run(
        [sys.executable, "scripts/tree_hash.py", "--manifest"],
        cwd=root, capture_output=True, text=True, timeout=120,
        check=True,
    )
    (art / "source-tree-hash.json").write_text(
        json.dumps({"tree_sha256": h}), encoding="utf-8")

    results = _results_payload(h, version, **(results_overrides or {}))
    for key in drop_results_keys:
        results.pop(key, None)
    (art / "final-acceptance-results.json").write_text(
        json.dumps(results, indent=2), encoding="utf-8")
    return h


def _run_build(root: Path, *args: str,
               env_extra: dict | None = None) -> subprocess.CompletedProcess:
    env = dict(os.environ, **PINNED_ENV, **(env_extra or {}))
    return subprocess.run(
        ["sh", BUILD_SCRIPT, *args],
        cwd=root, env=env, capture_output=True, text=True, timeout=180,
    )


def _tarball_names(tarball: Path) -> list[str]:
    with tarfile.open(tarball, "r:gz") as tf:
        return tf.getnames()


def _only_tarball(root: Path) -> Path:
    tarballs = list((root / "dist").glob("*.tar.gz"))
    assert len(tarballs) == 1, f"esperado 1 tarball, achados: {tarballs}"
    return tarballs[0]


def _tar_supports_deterministic() -> bool:
    r = subprocess.run(
        ["tar", "--sort=name", "--owner=0", "--group=0", "--numeric-owner",
         "--mtime=@1700000000", "-cf", "/dev/null", "-T", "/dev/null"],
        capture_output=True, timeout=30,
    )
    return r.returncode == 0


# ── FASE 2 — aceite vinculado à árvore ──────────────────────────────────────

def test_build_ok_when_acceptance_matches_tree(tmp_path):
    """Caminho verde: aceite da mesma árvore+versão, suíte completa → build OK."""
    root = _make_fake_root(tmp_path / "tree")
    _make_release_artifacts(root)
    r = _run_build(root)
    assert r.returncode == 0, f"build falhou: {r.stdout[-400:]}\n{r.stderr[-600:]}"
    assert _only_tarball(root).is_file()


def test_build_fails_when_source_changes_after_acceptance(tmp_path):
    """Fonte alterada DEPOIS do aceite → build falha (árvore != aceite)."""
    root = _make_fake_root(tmp_path / "tree")
    _make_release_artifacts(root)
    with (root / "lib" / "engine.tcl").open("a", encoding="utf-8") as fh:
        fh.write("# alteracao pos-aceite\n")
    r = _run_build(root)
    assert r.returncode != 0, "build aceitou árvore alterada após o aceite"
    assert "rvore" in (r.stdout + r.stderr)


def test_build_fails_when_new_file_added_after_acceptance(tmp_path):
    """Arquivo novo não coberto pelo aceite/manifesto → detecção."""
    root = _make_fake_root(tmp_path / "tree")
    _make_release_artifacts(root)
    (root / "lib" / "novo.tcl").write_text("# arquivo novo\n", encoding="utf-8")
    r = _run_build(root)
    assert r.returncode != 0, "build aceitou arquivo novo fora do aceite"


def test_build_fails_when_file_removed_after_acceptance(tmp_path):
    """Arquivo removido depois do aceite → detecção."""
    root = _make_fake_root(tmp_path / "tree")
    _make_release_artifacts(root)
    (root / "lib" / "engine.tcl").unlink()
    r = _run_build(root)
    assert r.returncode != 0, "build aceitou remoção de arquivo após o aceite"


def test_build_fails_with_stale_acceptance_from_old_version(tmp_path):
    """Aceite gerado para outra VERSION → build falha citando a versão."""
    root = _make_fake_root(tmp_path / "tree")
    _make_release_artifacts(root, version="9.9.9-test")
    # "nova versão": VERSION muda, results JSON continua o da versão anterior
    (root / "VERSION").write_text("9.9.10-test\n", encoding="utf-8")
    r = _run_build(root)
    assert r.returncode != 0, "build aceitou results JSON de versão antiga"
    assert "vers" in (r.stdout + r.stderr).lower()


def test_build_fails_with_results_without_version_field(tmp_path):
    """Results JSON no formato antigo (sem campo version) → build falha."""
    root = _make_fake_root(tmp_path / "tree")
    _make_release_artifacts(root, drop_results_keys=("version",))
    r = _run_build(root)
    assert r.returncode != 0, "build aceitou results JSON sem campo version"
    assert "version" in (r.stdout + r.stderr).lower()


def test_build_fails_when_full_suite_not_recorded(tmp_path):
    """Suíte completa não aprovada no aceite → release não pode ser gerada."""
    root = _make_fake_root(tmp_path / "tree")
    _make_release_artifacts(root, results_overrides={"test_all_passed": False})
    r = _run_build(root)
    assert r.returncode != 0, "build aceitou aceite sem suíte completa"
    assert "test_all_passed" in (r.stdout + r.stderr)


def test_verify_tarball_detects_tree_divergence(tmp_path):
    """Tarball cujo conteúdo diverge da árvore validada → verify-tarball falha."""
    root = _make_fake_root(tmp_path / "tree")
    original_hash = _make_release_artifacts(root)
    r = _run_build(root)
    assert r.returncode == 0, f"build base falhou: {r.stderr[-400:]}"
    tarball = _only_tarball(root)

    # hash de uma árvore DIFERENTE da empacotada → divergência detectada
    (root / "lib" / "engine.tcl").write_text("# engine v2\n", encoding="utf-8")
    other_hash = _tree_hash(root)
    assert other_hash != original_hash

    problems = build_validate.verify_tarball(
        tarball, expected_hash=other_hash, version=FAKE_VERSION)
    assert any("diverg" in p for p in problems), problems

    ok = build_validate.verify_tarball(
        tarball, expected_hash=original_hash, version=FAKE_VERSION)
    assert ok == [], ok


# ── FASE 2.2 — classificação determinística dos subprodutos de teste ────────

def test_tree_hash_stable_with_test_byproducts(tmp_path):
    """Subprodutos da suíte (logs de aceitação, caches, state, segredos
    locais, evidência de benchmark pinada) NÃO podem mudar o hash da árvore:
    é o que garante before == after no pipeline."""
    root = _make_fake_root(tmp_path / "tree")
    _make_release_artifacts(root)
    before = _tree_hash(root)

    byproducts = {
        "artifacts/acceptance-logs/current/nova-fase.log": "log\n",
        "artifacts/acceptance-logs/results/test-all-x.result.json": "{}\n",
        "artifacts/visual-test-result.json": '{"passed": false}\n',
        "artifacts/final-acceptance-results.json": "{}\n",
        "lib/__pycache__/engine.cpython-312.pyc": "bytecode",
        ".pytest_cache/v/cache/lastfailed": "{}\n",
        "tests/tmp/saida.tmp": "tmp\n",
        "gateway/state/replay.db": "sqlite\n",
        "gateway/state/captures/cap1/audit.jsonl": "{}\n",
        "artifacts/benchmarks/exp-oficial-v2/runs/run1/novo.jsonl": "{}\n",
        ".coverage": "coverage\n",
        "scripts/local-teste.key": "segredo\n",
        ".env": "SEGREDO=1\n",
    }
    for rel, content in byproducts.items():
        fp = root / rel
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(content, encoding="utf-8")

    after = _tree_hash(root)
    assert before == after, (
        "subprodutos de teste mudaram o hash da árvore — classifique-os "
        "como exclusões determinísticas no tree_hash.py"
    )


def test_tree_hash_detects_modify_add_remove(tmp_path):
    """O hash canônico detecta modificação, adição e remoção de fontes."""
    root = _make_fake_root(tmp_path / "tree")
    base = tree_hash(root)

    (root / "lib" / "engine.tcl").write_text("# engine v2\n", encoding="utf-8")
    assert tree_hash(root) != base, "modificação não detectada"

    root2 = _make_fake_root(tmp_path / "tree2")
    h2 = tree_hash(root2)
    (root2 / "lib" / "extra.tcl").write_text("# extra\n", encoding="utf-8")
    assert tree_hash(root2) != h2, "adição não detectada"

    root3 = _make_fake_root(tmp_path / "tree3")
    h3 = tree_hash(root3)
    (root3 / "lib" / "engine.tcl").unlink()
    assert tree_hash(root3) != h3, "remoção não detectada"


# ── FASE 11 — empacotamento limpo ───────────────────────────────────────────

def test_tarball_contains_only_latest_valid_benchmark_by_default(tmp_path):
    """Default: SOMENTE o experimento mais recente com manifesto válido
    (exp-oficial-v2); históricos (v1) e inválidos (v3) ficam fora."""
    root = _make_fake_root(tmp_path / "tree")
    _make_release_artifacts(root)
    r = _run_build(root)
    assert r.returncode == 0, f"build falhou: {r.stderr[-600:]}"
    names = _tarball_names(_only_tarball(root))
    packed = {n.split("artifacts/benchmarks/")[1].split("/")[0]
              for n in names if "artifacts/benchmarks/" in n}
    assert packed == {"exp-oficial-v2"}, f"benchmarks no pacote: {packed}"


def test_tarball_includes_requested_benchmark_with_flag(tmp_path):
    """--with-benchmarks <id> empacota exatamente o experimento pedido."""
    root = _make_fake_root(tmp_path / "tree")
    _make_release_artifacts(root)
    r = _run_build(root, "--with-benchmarks", "exp-oficial-v1")
    assert r.returncode == 0, f"build falhou: {r.stderr[-600:]}"
    names = _tarball_names(_only_tarball(root))
    packed = {n.split("artifacts/benchmarks/")[1].split("/")[0]
              for n in names if "artifacts/benchmarks/" in n}
    assert packed == {"exp-oficial-v1"}, f"benchmarks no pacote: {packed}"


def test_build_fails_with_unknown_benchmark_id(tmp_path):
    """--with-benchmarks de id inexistente/sem manifesto → build falha."""
    root = _make_fake_root(tmp_path / "tree")
    _make_release_artifacts(root)
    r = _run_build(root, "--with-benchmarks", "exp-oficial-v3")
    assert r.returncode != 0, "build aceitou experimento sem manifesto válido"
    r = _run_build(root, "--with-benchmarks", "exp-inexistente")
    assert r.returncode != 0, "build aceitou experimento inexistente"


@pytest.mark.skipif(not _tar_supports_deterministic(),
                    reason="tar do ambiente sem --sort=name/--owner/--mtime "
                           "(reprodutibilidade exige GNU tar; AIX: ver CHECKLIST)")
def test_two_builds_same_tree_same_sha256(tmp_path):
    """Dois builds consecutivos da mesma árvore (timestamp e epoch pinados)
    produzem o MESMO sha256 de tarball."""
    root = _make_fake_root(tmp_path / "tree")
    _make_release_artifacts(root)

    r1 = _run_build(root)
    assert r1.returncode == 0, f"build 1 falhou: {r1.stderr[-400:]}"
    h1 = hashlib.sha256(_only_tarball(root).read_bytes()).hexdigest()

    import time
    time.sleep(1.1)  # garante mtimes de stage diferentes sem a normalização

    r2 = _run_build(root)
    assert r2.returncode == 0, f"build 2 falhou: {r2.stderr[-400:]}"
    h2 = hashlib.sha256(_only_tarball(root).read_bytes()).hexdigest()

    assert h1 == h2, f"builds da mesma árvore divergem: {h1} != {h2}"


def test_performance_corrections_evidence_packaged(tmp_path):
    """Regressão 0.9.0: PERFORMANCE_CORRECTIONS_REPORT.md e
    artifacts/performance-corrections/ entram no hash do aceite — precisam
    estar no pacote, senão o verify-tarball aborta o build (observado no
    pipeline real: 'árvore extraída diverge do aceite')."""
    root = _make_fake_root(tmp_path / "tree")
    (root / "PERFORMANCE_CORRECTIONS_REPORT.md").write_text(
        "# relatório\n", encoding="utf-8")
    pc = root / "artifacts" / "performance-corrections"
    pc.mkdir(parents=True)
    (pc / "baseline.json").write_text("{}\n", encoding="utf-8")
    _make_release_artifacts(root)

    r = _run_build(root)
    assert r.returncode == 0, f"build falhou: {r.stderr[-600:]}"
    names = _tarball_names(_only_tarball(root))
    assert any(n.endswith("PERFORMANCE_CORRECTIONS_REPORT.md") for n in names)
    assert any("artifacts/performance-corrections/baseline.json" in n
               for n in names)


def test_secrets_and_state_never_packaged(tmp_path):
    """segredo.key, foo.db, gateway/state/x, .env e id_rsa plantados NUNCA
    entram no tarball."""
    root = _make_fake_root(tmp_path / "tree")
    planted = {
        "scripts/segredo.key": "chave\n",
        "gateway/foo.db": "sqlite\n",
        "gateway/state/replay.db": "sqlite\n",
        "gateway/state/captures/x/audit.jsonl": "{}\n",
        ".env": "SEGREDO=1\n",
        "gateway/id_rsa": "ssh key\n",
        "lib/__pycache__/engine.cpython-312.pyc": "bytecode",
    }
    for rel, content in planted.items():
        fp = root / rel
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(content, encoding="utf-8")
    _make_release_artifacts(root)

    r = _run_build(root)
    assert r.returncode == 0, f"build falhou: {r.stderr[-600:]}"
    names = _tarball_names(_only_tarball(root))
    for rel in planted:
        assert not any(n.endswith(rel) or f"/{rel}" in n for n in names), (
            f"item proibido entrou no pacote: {rel}"
        )
    assert not any("gateway/state" in n for n in names)
    assert not any("__pycache__" in n for n in names)


def test_verify_tarball_sanity_checks(tmp_path):
    """verify-tarball reprova pacote sem VERSION e pacote com item proibido."""
    # pacote sem VERSION
    stage = tmp_path / "stage1" / "dakota-replay2-1.0.0"
    stage.mkdir(parents=True)
    (stage / "install.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    tb1 = tmp_path / "sem-version.tar.gz"
    with tarfile.open(tb1, "w:gz") as tf:
        tf.add(stage, arcname=stage.name)
    problems = build_validate.verify_tarball(tb1, version="1.0.0")
    assert any("VERSION" in p for p in problems), problems

    # pacote com segredo
    stage2 = tmp_path / "stage2" / "dakota-replay2-1.0.0"
    stage2.mkdir(parents=True)
    (stage2 / "VERSION").write_text("1.0.0\n", encoding="utf-8")
    (stage2 / "segredo.key").write_text("chave\n", encoding="utf-8")
    tb2 = tmp_path / "com-segredo.tar.gz"
    with tarfile.open(tb2, "w:gz") as tf:
        tf.add(stage2, arcname=stage2.name)
    problems = build_validate.verify_tarball(tb2, version="1.0.0")
    assert any("proibido" in p for p in problems), problems


def test_verify_tarball_rejects_wrong_version(tmp_path):
    """VERSION do pacote divergente da esperada → problema reportado."""
    stage = tmp_path / "stage" / "dakota-replay2-1.0.0"
    stage.mkdir(parents=True)
    (stage / "VERSION").write_text("1.0.1\n", encoding="utf-8")
    tb = tmp_path / "versao-errada.tar.gz"
    with tarfile.open(tb, "w:gz") as tf:
        tf.add(stage, arcname=stage.name)
    problems = build_validate.verify_tarball(tb, version="1.0.0")
    assert any("VERSION" in p for p in problems), problems


# ── Contratos estáticos da cadeia de release ────────────────────────────────

def test_final_acceptance_fails_closed_on_tree_change():
    """O pipeline deve ABORTAR (não apenas registrar) quando a árvore muda
    entre o hash antes e o hash depois dos testes."""
    text = (ROOT / "scripts" / "final-acceptance.sh").read_text(encoding="utf-8")
    assert "SOURCE_TREE_SHA256_AFTER" in text
    assert "mudou DURANTE o aceite" in text, (
        "final-acceptance.sh deve abortar com mensagem clara quando "
        "before != after (hoje só registra SOURCE_TREE_UNCHANGED=False)"
    )


def test_final_acceptance_results_embeds_version():
    """O results JSON gerado pelo pipeline deve carregar a VERSION da árvore
    — sem isso o build não consegue rejeitar aceite de versão antiga."""
    text = (ROOT / "scripts" / "final-acceptance.sh").read_text(encoding="utf-8")
    assert "'version'" in text or '"version"' in text


def test_build_tarball_calls_acceptance_and_tarball_validation():
    """build-tarball.sh deve vincular o aceite antes de empacotar e validar o
    tarball extraído depois de gerar (lógica em scripts/build_validate.py)."""
    text = (ROOT / BUILD_SCRIPT).read_text(encoding="utf-8")
    assert "build_validate.py" in text
    assert "check-acceptance" in text
    assert "verify-tarball" in text
    assert "--with-benchmarks" in text

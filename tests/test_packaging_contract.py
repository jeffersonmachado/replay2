from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_tarball_stage_includes_examples_needed_by_tcl_tests():
    script = (ROOT / "scripts" / "build-tarball.sh").read_text(encoding="utf-8")

    assert (ROOT / "examples" / "legacy_sim.tcl").is_file()
    assert '[ -d "$ROOT_DIR/examples" ]' in script
    assert '"$ROOT_DIR/examples"' in script


def test_final_acceptance_gate_is_tree_gate_only():
    """Phase 8 is tree gate only; packaging is in final-acceptance.sh and build-tarball.sh."""
    phase8 = (ROOT / "scripts" / "acceptance" / "run-phase-08-full.sh").read_text(encoding="utf-8")
    assert "python -m pytest -q tests/" in phase8 or "python-acceptance" in phase8
    assert "acceptance-test-baseline.sha256" in phase8
    # Phase 8 should NOT do packaging
    assert "build-tarball" not in phase8, "Phase 8 must not build tarballs"
    assert "tarball-inspection" not in phase8, "Phase 8 must not inspect tarballs"

    # build-tarball.sh must require all evidence artifacts
    build = (ROOT / "scripts" / "build-tarball.sh").read_text(encoding="utf-8")
    assert "visual-test-result.json" in build
    assert "source-tree-manifest.sha256" in build
    assert "MISSING_ARTIFACTS" in build

    # final-acceptance.sh must exist and handle release
    fa = ROOT / "scripts" / "final-acceptance.sh"
    assert fa.exists(), "final-acceptance.sh must exist"
    fa_text = fa.read_text(encoding="utf-8")
    assert "build-tarball.sh" in fa_text or "build-tarball" in fa_text


def test_tarball_includes_final_reports_but_not_raw_log_directories():
    script = (ROOT / "scripts" / "build-tarball.sh").read_text(encoding="utf-8")

    assert "final-acceptance-report.md" in script
    assert "final-acceptance-results.json" in script
    assert "manual-validation.json" in script
    assert '"$STAGE_DIR/logs"' in script

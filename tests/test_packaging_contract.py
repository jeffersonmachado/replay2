from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_tarball_stage_includes_examples_needed_by_tcl_tests():
    script = (ROOT / "scripts" / "build-tarball.sh").read_text(encoding="utf-8")

    assert (ROOT / "examples" / "legacy_sim.tcl").is_file()
    assert '[ -d "$ROOT_DIR/examples" ]' in script
    assert '"$ROOT_DIR/examples"' in script


def test_final_acceptance_gate_builds_and_inspects_tarball():
    script = (ROOT / "scripts" / "acceptance" / "run-phase-08-full.sh").read_text(encoding="utf-8")

    assert "bash scripts/build-tarball.sh" in script
    assert "tarball-inspection" in script
    assert "tar -tzf" in script
    assert "scripts/acceptance" in script
    assert "acceptance-test-baseline.sha256" in script
    assert "node_modules" in script
    assert "final-acceptance-report.md" in script
    assert "final-acceptance-results.json" in script
    assert "manual-validation.json" in script
    assert "acceptance-log-summary.json" in script


def test_tarball_includes_final_reports_but_not_raw_log_directories():
    script = (ROOT / "scripts" / "build-tarball.sh").read_text(encoding="utf-8")

    assert "final-acceptance-report.md" in script
    assert "final-acceptance-results.json" in script
    assert "manual-validation.json" in script
    assert "acceptance-log-summary.json" in script
    assert '"$STAGE_DIR/logs"' in script

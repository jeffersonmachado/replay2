from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_tarball_stage_includes_examples_needed_by_tcl_tests():
    script = (ROOT / "scripts" / "build-tarball.sh").read_text(encoding="utf-8")

    assert (ROOT / "examples" / "legacy_sim.tcl").is_file()
    assert '[ -d "$ROOT_DIR/examples" ]' in script
    assert '"$ROOT_DIR/examples"' in script

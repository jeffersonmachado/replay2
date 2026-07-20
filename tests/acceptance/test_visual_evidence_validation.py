"""Testes do validador de evidencia visual.

Valida que o visual-test-result.json gerado pelo teste visual
contem todos os campos obrigatorios com valores corretos.
Nao duplica a logica do validador da fase 7 — testa contra
o arquivo real gerado pelo teste visual.
"""
from __future__ import annotations
import json
import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load_evidence():
    path = ROOT / "artifacts/visual-test-result.json"
    if not path.exists():
        raise AssertionError("visual-test-result.json not found — run visual test first")
    return json.loads(path.read_text("utf-8"))


def _tree_hash():
    sys.path.insert(0, str(ROOT / "scripts"))
    from tree_hash import tree_hash
    return tree_hash(ROOT)


def test_evidence_file_exists():
    assert (ROOT / "artifacts/visual-test-result.json").exists()


def test_evidence_is_valid_json():
    v = _load_evidence()
    assert isinstance(v, dict)


def test_evidence_schema_version():
    v = _load_evidence()
    assert v.get("schema_version") == "1.0"


def test_evidence_passed_true():
    v = _load_evidence()
    assert v.get("passed") is True, f"expected passed=true, got {v.get('passed')}"


def test_evidence_has_run_id():
    v = _load_evidence()
    assert "run_id" in v
    assert "visual-" in v["run_id"] or "release-" in v["run_id"]


def test_evidence_has_timestamps():
    v = _load_evidence()
    assert "started_at" in v
    assert "finished_at" in v


def test_evidence_tree_hash_not_empty():
    v = _load_evidence()
    h = v.get("source_tree_sha256", "")
    assert len(h) == 64, f"tree hash must be 64 chars, got {len(h)}: {h}"
    assert h != "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855", "tree hash must not be empty"


def test_evidence_tree_hash_matches_official():
    v = _load_evidence()
    official = _tree_hash()
    assert v["source_tree_sha256"] == official, f"tree hash mismatch: {v['source_tree_sha256']} != {official}"


def test_evidence_chromium_started():
    v = _load_evidence()
    assert v.get("chromium_started") is True


def test_evidence_cdp_connected():
    v = _load_evidence()
    assert v.get("cdp_connected") is True


def test_evidence_document_loaded():
    v = _load_evidence()
    assert v.get("document_loaded_via_cdp") is True


def test_evidence_timeline_module_loaded():
    v = _load_evidence()
    assert v.get("timeline_module_loaded") is True


def test_evidence_timeline_render_calls():
    v = _load_evidence()
    assert v.get("timeline_render_calls", 0) >= 1, f"timeline_render_calls={v.get('timeline_render_calls')}"


def test_evidence_out_rows_created():
    v = _load_evidence()
    assert v.get("out_rows_created", 0) == 1, f"out_rows_created={v.get('out_rows_created')}"


def test_evidence_terminal_renderer_calls():
    v = _load_evidence()
    assert v.get("terminal_renderer_calls", 0) >= 1, f"terminal_renderer_calls={v.get('terminal_renderer_calls')}"


def test_evidence_renderer_completed():
    v = _load_evidence()
    assert v.get("renderer_completed") is True


def test_evidence_renderer_marker_found():
    v = _load_evidence()
    assert v.get("renderer_marker_found") is True


def test_evidence_out_card_found():
    v = _load_evidence()
    assert v.get("out_card_found") is True, "OUT card must be found in DOM"


def test_evidence_computed_styles_collected():
    v = _load_evidence()
    assert v.get("computed_styles_collected") is True


def test_evidence_screenshot_created():
    v = _load_evidence()
    assert v.get("screenshot_created") is True


def test_evidence_screenshot_bytes_positive():
    v = _load_evidence()
    assert v.get("screenshot_bytes", 0) > 0, f"screenshot_bytes={v.get('screenshot_bytes')}"


def test_evidence_screenshot_sha256_valid():
    v = _load_evidence()
    sh = v.get("screenshot_sha256", "")
    assert len(sh) == 64, f"screenshot sha256 must be 64 chars, got {len(sh)}"
    assert all(c in "0123456789abcdef" for c in sh), "screenshot sha256 must be hex"


def test_evidence_pixel_analysis_executed():
    v = _load_evidence()
    assert v.get("pixel_analysis_executed") is True


def test_evidence_pixel_validation_passed():
    v = _load_evidence()
    assert v.get("pixel_validation_passed") is True


def test_evidence_remaining_processes_zero():
    v = _load_evidence()
    assert v.get("remaining_processes", -1) == 0, f"remaining_processes={v.get('remaining_processes')}"


def test_evidence_remaining_zombies_zero():
    v = _load_evidence()
    assert v.get("remaining_zombies", -1) == 0, f"remaining_zombies={v.get('remaining_zombies')}"

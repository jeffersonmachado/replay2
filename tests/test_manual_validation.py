"""Validacao manual automatizada: sessao com resize, reverse, UTF-8, RIS, emoji.

Gera a sessao especificada e valida todas as propriedades obrigatorias.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest
from control.services.session_replay_service import prepare_session_replay_data
from dakota_terminal import TerminalEngine, snapshot_from_engine
from dakota_terminal.comparison import compare_signatures
from dakota_terminal.diffs import create_diff, apply_diff


SID = "val-001"


def _b64(data: bytes) -> str:
    import base64
    return base64.b64encode(data).decode()


def _make_validation_audit() -> list[dict]:
    ts = 1000
    seq = 0

    def ev(etype, **kw):
        nonlocal ts, seq
        seq += 1
        ts += 100
        base = {"v": "1.0", "type": etype, "seq_global": seq, "ts_ms": ts,
                "actor": "test", "session_id": SID, "seq_session": seq}
        base.update(kw)
        return base

    events = []
    # session_start 2x3
    events.append(ev("session_start", rows=2, cols=3, term="xterm", encoding="utf-8"))
    # OUT A
    events.append(ev("bytes", dir="out", n=1, data_b64=_b64(b"A")))
    # resize dividido: CSI 8;3;4t
    events.append(ev("bytes", dir="out", n=4, data_b64=_b64(b"\x1b[8;")))
    events.append(ev("bytes", dir="out", n=3, data_b64=_b64(b"3;4t")))
    # OUT B (na nova geometria 3x4)
    events.append(ev("bytes", dir="out", n=1, data_b64=_b64(b"B")))
    # reverse
    events.append(ev("bytes", dir="out", n=9, data_b64=_b64(b"\x1b[7mREV\x1b[0m")))
    # espaco com background
    events.append(ev("bytes", dir="out", n=12, data_b64=_b64(b"\x1b[42m  \x1b[0m")))
    # UTF-8 dividido: a = C3 A1
    events.append(ev("bytes", dir="out", n=1, data_b64=_b64(b"\xc3")))
    events.append(ev("bytes", dir="out", n=1, data_b64=_b64(b"\xa1")))
    # emoji dividido: 😀 = F0 9F 98 80
    events.append(ev("bytes", dir="out", n=2, data_b64=_b64(b"\xf0\x9f")))
    events.append(ev("bytes", dir="out", n=2, data_b64=_b64(b"\x98\x80")))
    # RIS dividido
    events.append(ev("bytes", dir="out", n=1, data_b64=_b64(b"\x1b")))
    events.append(ev("bytes", dir="out", n=1, data_b64=_b64(b"c")))
    # OUT final
    events.append(ev("bytes", dir="out", n=5, data_b64=_b64(b"final")))
    # session_end
    events.append(ev("session_end"))
    return events


@pytest.fixture(scope="module")
def validation_data():
    audit_events = _make_validation_audit()
    with tempfile.TemporaryDirectory() as tmpdir:
        audit_path = Path(tmpdir) / "audit-000001.jsonl"
        with open(audit_path, "w", encoding="utf-8") as f:
            for ev in audit_events:
                f.write(json.dumps(ev, ensure_ascii=False) + "\n")
        result = prepare_session_replay_data(str(tmpdir), SID)
        if result.get("error"):
            pytest.fail(f"Service error: {result['error']}")
        return result


class TestManualValidation:
    def test_geometry_resize(self, validation_data):
        geom = validation_data["geometry"]
        assert geom["rows"] == 2
        assert geom["cols"] == 3
        assert geom["geometry_source"] == "session_metadata"

        final = validation_data["final_snapshot"]
        assert final["rows"] == 3
        assert final["cols"] == 4

    def test_signatures_not_empty(self, validation_data):
        cs = validation_data.get("canonical_signatures", {})
        assert cs.get("text_sig", "").startswith("sha256:")
        assert cs.get("visual_sig", "").startswith("sha256:")
        assert cs.get("semantic_sig", "").startswith("sha256:")

    def test_semantic_sig_not_legacy(self, validation_data):
        cs = validation_data.get("canonical_signatures", {})
        assert cs.get("semantic_sig", "") != ""
        # Nao deve ser formato legado L=...;W=...
        assert not cs.get("semantic_sig", "").startswith("L=")

    def test_checkpoints_present(self, validation_data):
        cps = validation_data.get("checkpoints", [])
        assert len(cps) >= 2, f"esperado >= 2 checkpoints, obtido {len(cps)}"
        # Checkpoint inicial
        assert cps[0]["seq_global"] == 0
        assert "snapshot_compact" in cps[0]

    def test_diffs_present(self, validation_data):
        events = validation_data.get("events", [])
        out_events = [e for e in events if e.get("direction") == "out"]
        diff_count = sum(1 for e in out_events if e.get("diff"))
        assert diff_count > 0, "deve haver diffs nos eventos OUT"

    def test_comparison_modes_available(self, validation_data):
        pb = validation_data.get("playback", {})
        modes = pb.get("comparison_modes", [])
        assert "visual" in modes
        assert "text" in modes
        assert "semantic" in modes
        assert "hybrid" in modes
        assert pb.get("default_comparison_mode") == "visual"

    def test_playback_events_have_signatures(self, validation_data):
        pb = validation_data.get("playback", {})
        out_events = [e for e in pb.get("events", []) if e.get("direction") == "out"]
        assert len(out_events) > 0
        for ev in out_events:
            assert "text_sig" in ev, f"seq {ev.get('seq')} missing text_sig"
            assert "visual_sig" in ev, f"seq {ev.get('seq')} missing visual_sig"

    def test_initial_snapshot_present(self, validation_data):
        assert validation_data.get("initial_snapshot") is not None

    def test_canonical_signatures_consistent(self, validation_data):
        cs = validation_data.get("canonical_signatures", {})
        final = validation_data.get("final_snapshot", {})
        assert cs["text_sig"] == final.get("text_sig", ""), "text_sig must match final_snapshot"
        assert cs["visual_sig"] == final.get("visual_sig", ""), "visual_sig must match final_snapshot"


class TestDiffRejection:
    def test_duplicate_diff_rejected(self):
        engine = TerminalEngine(rows=2, cols=2)
        engine.feed_bytes(b"A")
        snap1 = snapshot_from_engine(engine)
        engine.feed_bytes(b"B")
        snap2 = snapshot_from_engine(engine)

        diff = create_diff(snap1, snap2, base_seq=1, seq=2)
        result = apply_diff(snap1, diff)
        assert result["cells"][1]["ch"] == "B"

        with pytest.raises(ValueError):
            apply_diff(result, diff)  # duplicado

    def test_out_of_order_diff_rejected(self):
        engine = TerminalEngine(rows=2, cols=2)
        snap0 = snapshot_from_engine(engine)
        engine.feed_bytes(b"A")
        snap1 = snapshot_from_engine(engine)
        engine.feed_bytes(b"B")
        snap2 = snapshot_from_engine(engine)

        diff_1_2 = create_diff(snap1, snap2, base_seq=1, seq=2)
        with pytest.raises(ValueError):
            apply_diff(snap0, diff_1_2)  # base errada

    def test_wrong_base_rejected(self):
        engine = TerminalEngine(rows=2, cols=2)
        snap0 = snapshot_from_engine(engine)
        engine.feed_bytes(b"A")
        snap1 = snapshot_from_engine(engine)

        diff = create_diff(snap0, snap1, base_seq=0, seq=1)
        # Modifica a base
        diff["base_text_sig"] = "sha256:wrong"
        with pytest.raises(ValueError):
            apply_diff(snap0, diff)

    def test_fake_final_signature_rejected(self):
        engine = TerminalEngine(rows=2, cols=2)
        snap0 = snapshot_from_engine(engine)
        engine.feed_bytes(b"A")
        snap1 = snapshot_from_engine(engine)

        diff = create_diff(snap0, snap1, base_seq=0, seq=1)
        diff["text_sig"] = "sha256:FAKE"
        with pytest.raises(ValueError):
            apply_diff(snap0, diff)

    def test_negative_coordinate_rejected(self):
        engine = TerminalEngine(rows=2, cols=2)
        snap0 = snapshot_from_engine(engine)
        engine.feed_bytes(b"A")
        snap1 = snapshot_from_engine(engine)

        diff = create_diff(snap0, snap1, base_seq=0, seq=1)
        diff["changes"][0]["row"] = -1
        with pytest.raises(ValueError):
            apply_diff(snap0, diff)

    def test_frontend_style_payload_rejected(self):
        """Payload hostil com rows=999999 deve ser rejeitado."""
        from dakota_terminal.geometry import validate_geometry
        with pytest.raises((ValueError, TypeError)):
            validate_geometry(999999, 999999)

"""Testes de regressao: scanner byte-a-byte, isolamento de sessoes, resize.

Validam que o TerminalEngine produz resultados deterministicos independente
do chunking, que sessoes sao isoladas, e que resize ocorre na posicao exata.
"""
from __future__ import annotations

import hashlib
import pytest
from dakota_terminal import TerminalEngine, snapshot_from_engine


def _sig_keys(snap: dict) -> dict:
    return {
        "text_sig": snap.get("text_sig", ""),
        "visual_sig": snap.get("visual_sig", ""),
        "semantic_sig": snap.get("semantic_sig", ""),
    }


def _cells_text(snap: dict) -> str:
    rows = snap["rows"]
    cols = snap["cols"]
    lines = []
    for r in range(rows):
        line = "".join(snap["cells"][r * cols + c]["ch"] for c in range(cols))
        lines.append(line)
    return "\n".join(lines)


class TestScannerChunkingDeterminism:
    """O mesmo fluxo de bytes, com chunking diferente, deve gerar o mesmo estado."""

    def _feed_and_snapshot(self, chunks: list[bytes], rows=3, cols=5) -> dict:
        engine = TerminalEngine(rows=rows, cols=cols)
        for chunk in chunks:
            engine.feed_bytes(chunk)
        return snapshot_from_engine(engine)

    def test_text_across_chunks(self):
        whole = self._feed_and_snapshot([b"Hello"])
        chunked = self._feed_and_snapshot([b"Hel", b"lo"])
        assert _cells_text(whole) == _cells_text(chunked)
        assert _sig_keys(whole) == _sig_keys(chunked)

    def test_utf8_2byte_split(self):
        # á = C3 A1
        whole = self._feed_and_snapshot([b"\xc3\xa1"])
        chunked = self._feed_and_snapshot([b"\xc3", b"\xa1"])
        assert _cells_text(whole) == _cells_text(chunked)
        assert _sig_keys(whole) == _sig_keys(chunked)

    def test_utf8_3byte_split(self):
        # € = E2 82 AC
        whole = self._feed_and_snapshot([b"\xe2\x82\xac"])
        chunked = self._feed_and_snapshot([b"\xe2", b"\x82", b"\xac"])
        assert _cells_text(whole) == _cells_text(chunked)
        assert _sig_keys(whole) == _sig_keys(chunked)

    def test_utf8_4byte_split(self):
        # 𐍈 = F0 90 8D 88
        whole = self._feed_and_snapshot([b"\xf0\x90\x8d\x88"])
        chunked = self._feed_and_snapshot([b"\xf0\x90", b"\x8d\x88"])
        assert _cells_text(whole) == _cells_text(chunked)
        assert _sig_keys(whole) == _sig_keys(chunked)

    def test_ris_across_chunks(self):
        # ESC c = RIS, depois á (C3 A1)
        whole = self._feed_and_snapshot([b"A\x1bc\xc3\xa1"])
        chunked = self._feed_and_snapshot([b"A\x1b", b"c\xc3", b"\xa1"])
        assert _cells_text(whole) == _cells_text(chunked)
        assert _sig_keys(whole) == _sig_keys(chunked)

    def test_ris_utf8_immediately_after(self):
        # RIS + á no mesmo chunk
        chunked = self._feed_and_snapshot([b"\x1bc\xc3\xa1"])
        assert _cells_text(chunked)[0] == "\xe1"  # á

    def test_csi_split(self):
        # CSI 31 m (red)
        whole = self._feed_and_snapshot([b"\x1b[31mX"])
        chunked = self._feed_and_snapshot([b"\x1b[3", b"1mX"])
        w = _cells_text(whole)
        c = _cells_text(chunked)
        assert w[0] == "X"
        assert c[0] == "X"
        # visual_sig deve ser igual (cor red)
        assert _sig_keys(whole)["visual_sig"] == _sig_keys(chunked)["visual_sig"]

    def test_osc_split(self):
        # OSC 0;title BEL
        whole = self._feed_and_snapshot([b"\x1b]0;test\x07X"])
        chunked = self._feed_and_snapshot([b"\x1b]0;te", b"st\x07X"])
        assert _cells_text(whole) == _cells_text(chunked)
        assert _sig_keys(whole) == _sig_keys(chunked)

    def test_dec_charset_split(self):
        # ESC ( 0 → DEC Special Graphics
        whole = self._feed_and_snapshot([b"\x1b(0\x0el\x0f"])
        chunked = self._feed_and_snapshot([b"\x1b(0", b"\x0el\x0f"])
        assert _sig_keys(whole) == _sig_keys(chunked)

    def test_text_before_and_after_controls(self):
        whole = self._feed_and_snapshot([b"AB\x1b[31mCD\x1b[0mEF"])
        chunked = self._feed_and_snapshot([b"AB\x1b[31mC", b"D\x1b[0mEF"])
        assert _cells_text(whole) == _cells_text(chunked)
        assert _sig_keys(whole) == _sig_keys(chunked)


class TestSessionIsolation:
    """Sessoes diferentes devem ter estado completamente independente."""

    def test_geometry_isolation(self):
        engine_a = TerminalEngine(rows=25, cols=80, term="xterm", encoding="utf-8")
        engine_b = TerminalEngine(rows=30, cols=132, term="xterm-256color", encoding="cp850")

        engine_a.feed_bytes(b"Hello")
        engine_b.feed_bytes(b"World")

        snap_a = snapshot_from_engine(engine_a)
        snap_b = snapshot_from_engine(engine_b)

        assert snap_a["rows"] == 25
        assert snap_a["cols"] == 80
        assert snap_b["rows"] == 30
        assert snap_b["cols"] == 132
        assert snap_a["term"] == "xterm"
        assert snap_b["term"] == "xterm-256color"
        assert snap_a["encoding"] == "utf-8"
        assert snap_b["encoding"] == "cp850"

    def test_encoding_isolation(self):
        engine_a = TerminalEngine(rows=5, cols=10, encoding="cp850")
        engine_b = TerminalEngine(rows=5, cols=10, encoding="utf-8")

        # 0x82 = é em CP850, byte inválido em UTF-8
        engine_a.feed_bytes(b"\x82")
        engine_b.feed_bytes(b"\xc3\xa9")  # é em UTF-8

        snap_a = snapshot_from_engine(engine_a)
        snap_b = snapshot_from_engine(engine_b)

        # Ambos devem ter 'é' na primeira célula
        assert snap_a["cells"][0]["ch"] == "\xe9"
        assert snap_b["cells"][0]["ch"] == "\xe9"

    def test_resize_isolation(self):
        engine_a = TerminalEngine(rows=2, cols=3)
        engine_b = TerminalEngine(rows=2, cols=3)

        # Resize apenas na sessao A
        engine_a.feed_bytes(b"A")
        engine_a.feed_bytes(b"\x1b[8;3;4t")  # resize para 3x4
        engine_a.feed_bytes(b"B")

        engine_b.feed_bytes(b"X")
        engine_b.feed_bytes(b"Y")

        snap_a = snapshot_from_engine(engine_a)
        snap_b = snapshot_from_engine(engine_b)

        assert snap_a["rows"] == 3
        assert snap_a["cols"] == 4
        assert snap_b["rows"] == 2
        assert snap_b["cols"] == 3

    def test_ris_isolation(self):
        engine_a = TerminalEngine(rows=2, cols=3)
        engine_b = TerminalEngine(rows=2, cols=3)

        engine_a.feed_bytes(b"ABC")
        engine_b.feed_bytes(b"XYZ")
        engine_a.feed_bytes(b"\x1bc")  # RIS na sessao A
        engine_a.feed_bytes(b"D")

        snap_a = snapshot_from_engine(engine_a)
        snap_b = snapshot_from_engine(engine_b)

        # Apos RIS, deve ter apenas D
        assert snap_a["cells"][0]["ch"] == "D"
        # Sessao B nao afetada
        assert snap_b["cells"][0]["ch"] == "X"

    def test_warnings_isolated(self):
        engine_a = TerminalEngine(rows=2, cols=3, encoding="utf-8")
        engine_b = TerminalEngine(rows=2, cols=3, encoding="utf-8")

        # Alimenta byte invalido na sessao A
        engine_a.feed_bytes(b"\xff")
        engine_b.feed_bytes(b"OK")

        # Ambas as sessoes tem estados independentes
        warnings_a = engine_a.decoder.warnings
        assert len(warnings_a) >= 1


class TestResizeExactPosition:
    """Resize deve ocorrer na posicao exata dos bytes, nao depois."""

    def test_resize_same_chunk(self):
        engine = TerminalEngine(rows=2, cols=3)
        engine.feed_bytes(b"A")
        snap_before = snapshot_from_engine(engine)
        assert snap_before["rows"] == 2
        assert snap_before["cols"] == 3

        engine.feed_bytes(b"\x1b[8;3;4tB")
        snap_after = snapshot_from_engine(engine)

        # B deve ser processado na geometria nova
        assert snap_after["rows"] == 3
        assert snap_after["cols"] == 4
        # A preservado
        assert snap_after["cells"][0]["ch"] == "A"
        # B na posicao correta (linha 0, coluna 1 na nova geometria)
        assert snap_after["cells"][1]["ch"] == "B"

    def test_resize_split_across_chunks(self):
        engine = TerminalEngine(rows=2, cols=3)
        engine.feed_bytes(b"A")
        engine.feed_bytes(b"\x1b[8;")  # CSI parcial
        engine.feed_bytes(b"3;4t")     # completa resize
        engine.feed_bytes(b"B")

        snap = snapshot_from_engine(engine)
        assert snap["rows"] == 3
        assert snap["cols"] == 4
        assert snap["cells"][0]["ch"] == "A"
        assert snap["cells"][1]["ch"] == "B"

    def test_multiple_resizes(self):
        engine = TerminalEngine(rows=2, cols=3)
        engine.feed_bytes(b"A")
        engine.feed_bytes(b"\x1b[8;3;4t")  # 2x3 → 3x4
        engine.feed_bytes(b"B")
        engine.feed_bytes(b"\x1b[8;2;5t")  # 3x4 → 2x5
        engine.feed_bytes(b"C")

        snap = snapshot_from_engine(engine)
        assert snap["rows"] == 2
        assert snap["cols"] == 5
        assert snap["cells"][0]["ch"] == "A"
        assert snap["cells"][1]["ch"] == "B"
        assert snap["cells"][2]["ch"] == "C"

    def test_text_after_resize(self):
        engine = TerminalEngine(rows=2, cols=3)
        engine.feed_bytes(b"\x1b[8;3;4tHello")
        snap = snapshot_from_engine(engine)
        assert snap["rows"] == 3
        assert snap["cols"] == 4
        assert snap["cells"][0]["ch"] == "H"
        assert snap["cells"][1]["ch"] == "e"


class TestAdversarialDiffValidation:
    """Diffs invalidos devem ser rejeitados."""

    def _snap(self, rows=2, cols=2, text=""):
        engine = TerminalEngine(rows=rows, cols=cols)
        if text:
            engine.feed_bytes(text.encode("utf-8"))
        return snapshot_from_engine(engine)

    def test_diff_duplicate_rejected(self):
        from dakota_terminal.diffs import create_diff, apply_diff, validate_diff
        prev = self._snap()
        curr = self._snap(text="A")
        diff = create_diff(prev, curr, base_seq=0, seq=1)

        result = apply_diff(prev, diff)
        assert result["cells"][0]["ch"] == "A"

        # Aplicar o mesmo diff de novo deve lancar ValueError (base_seq nao bate)
        with pytest.raises(ValueError):
            apply_diff(result, diff)

    def test_diff_out_of_order_rejected(self):
        from dakota_terminal.diffs import create_diff, apply_diff
        snap0 = self._snap()
        snap1 = self._snap(text="A")
        snap2 = self._snap(text="AB")

        diff_1_2 = create_diff(snap1, snap2, base_seq=1, seq=2)

        # Aplicar diff_1_2 sobre snap0 (base errada) deve falhar
        with pytest.raises(ValueError):
            apply_diff(snap0, diff_1_2)

    def test_negative_coordinate_rejected(self):
        from dakota_terminal.diffs import create_diff, apply_diff
        prev = self._snap()
        curr = self._snap(text="A")
        diff = create_diff(prev, curr, base_seq=0, seq=1)

        # Injeta coordenada negativa
        diff["changes"][0]["row"] = -1
        with pytest.raises(ValueError):
            apply_diff(prev, diff)

    def test_fake_final_signature_rejected(self):
        from dakota_terminal.diffs import create_diff, apply_diff
        prev = self._snap()
        curr = self._snap(text="A")
        diff = create_diff(prev, curr, base_seq=0, seq=1)

        # Injeta assinatura final falsa
        diff["text_sig"] = "sha256:FAKE_SIGNATURE"
        with pytest.raises(ValueError):
            apply_diff(prev, diff)

    def test_huge_resize_rejected(self):
        from dakota_terminal.diffs import validate_diff
        diff = {
            "version": 1, "changes": [],
            "geometry_changed": True, "rows": 9999, "cols": 9999,
        }
        assert not validate_diff({}, diff)

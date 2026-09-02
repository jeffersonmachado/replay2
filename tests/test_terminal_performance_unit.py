#!/usr/bin/env python3
"""Testes-oráculo da FASE 6 (performance do terminal virtual e snapshots).

Garantem que as otimizações de custo (DEFAULT_CELL compartilhada, passagem
única nas assinaturas) NÃO divergem funcionalmente:

- assinaturas text_sig/visual_sig/semantic_sig e o JSON do snapshot têm
  valores de oráculo gravados a partir do código ANTES da otimização;
- chunkings diferentes do mesmo byte stream produzem estado final idêntico
  (snapshot completo + 3 assinaturas + serialização JSON byte a byte);
- a célula vazia padrão é imutável e compartilhável com segurança.
"""
from __future__ import annotations

import base64
import hashlib
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GATEWAY_DIR = ROOT / "gateway"
sys.path.insert(0, str(GATEWAY_DIR))

from dakota_terminal import TerminalEngine  # noqa: E402
from dakota_terminal.model import Cell, blank_cell  # noqa: E402
from dakota_terminal.snapshot import encode_snapshot  # noqa: E402
from dakota_gateway.screen import TerminalScreenState  # noqa: E402

VECTORS = ROOT / "tests" / "fixtures" / "terminal_vectors"
REAL_JOURNEY = ROOT / "tests" / "fixtures" / "capture8_replay_fixture.json"


def _snapshot_of(chunks: list[bytes], *, rows: int = 25, cols: int = 80, encoding: str = "utf-8") -> dict:
    engine = TerminalEngine(rows=rows, cols=cols, encoding=encoding)
    for chunk in chunks:
        engine.feed_bytes(chunk)
    return engine.snapshot()


# ── Streams representativos (oráculos capturados no código pré-otimização) ──

_MENU_SGR_BOX = (
    "\x1b[2J\x1b[H\x1b[7m+========================================+\x1b[0m\r\n"
    "|  SISTEMA LEGADO - MENU PRINCIPAL       |\r\n"
    "+========================================+\r\n"
    "  Cliente : ______  Pedido: 000123\r\n"
    "\x1b[1;31m  F3=Ajuda  F10=Sair\x1b[0m\r\n"
    "┌─────┬─────┐\r\n│ aá😀 │ b   │\r\n└─────┴─────┘\r\n"
).encode("utf-8")

_SCROLL_BODY = "| %04d | G2511%03d | SAPATO COURO PRETO %26d | %8d | %7.2f |\r\n"
_SCROLL_64K = (
    "\x1b[H" + "".join(
        _SCROLL_BODY % (i, i % 977, (i * 7) % 10**26, i % 50 + 1, (i * 13.7) % 900 + 9.9)
        for i in range(120)
    )
).encode("utf-8")

_UTF8_RAW = "Aá 😀 ção \x1b[7mR\x1b[0m fim\r\n".encode("utf-8")

ORACLE_STREAMS = {
    "menu_sgr_box": {"encoding": "utf-8", "chunks": [_MENU_SGR_BOX]},
    "scroll_64k": {"encoding": "utf-8", "chunks": [_SCROLL_64K]},
    "utf8_split": {"encoding": "utf-8", "chunks": [_UTF8_RAW[:3], _UTF8_RAW[3:7], _UTF8_RAW[7:9], _UTF8_RAW[9:]]},
    "cp850": {"encoding": "cp850", "chunks": [bytes([0x81, 0x82, 0x87, 0xA0, 0xA4, 0xB0, 0xB3, 0xC4]) + b"\r\nOK"]},
    "clear_rewrite": {"encoding": "utf-8", "chunks": [b"linha1\r\nlinha2\r\nlinha3\x1b[2J\x1b[Hnovo topo\r\nabc\x1b[Kfim"]},
    "resize": {"encoding": "utf-8", "chunks": [b"antes\r\n\x1b[8;10;40tdepois do resize\r\n"]},
    "reverse_spaces": {"encoding": "utf-8", "chunks": [b"\x1b[7m   \x1b[0mX\x1b[5mY\x1b[0m"]},
}

# Valores capturados rodando o código ANTES da otimização (2026-09-02).
# Qualquer divergência aqui é regressão funcional, não ruído.
ORACLE_SIGS = {
    "menu_sgr_box": {
        "text_sig": "sha256:8d520c58371c34834acee0fde174e592245291100e0afb6e355a9984db8ec188",
        "visual_sig": "sha256:13fce9ae678c2fcc288b8bfe1dafdb441be429a0d186d0ed1d375a0109f9e607",
        "semantic_sig": "sha256:f6791b4c7bd8d36c5381d91c6f63470aedcf20d9743e5ff77704a011de88be93",
        "snapshot_json_sha256": "733cf71e7a268d7fb1733d7446ca448909f87a50e8c87e16e9395aaa9ea76431",
        "cursor": {"col": 0, "row": 8, "visible": True, "wrap_pending": False},
    },
    "scroll_64k": {
        "text_sig": "sha256:e75c5942a711660968d1c9d369fe947e50a93066ca43f97e59f498b9612532c0",
        "visual_sig": "sha256:f052a815dd63a1f666b2005a3b9bd77f6e8802dd4dd063cadb527e352a03c514",
        "semantic_sig": "sha256:0220f2e7934bb571b393da58992dbb596e1826bcb944bd655427f1cd397b6a86",
        "snapshot_json_sha256": "101915ac3e5bc245d42a7fefe31011c5c6349aeda4185ce80812c7d3092358b2",
        "cursor": {"col": 0, "row": 24, "visible": True, "wrap_pending": False},
    },
    "utf8_split": {
        "text_sig": "sha256:b5773ac66630f4c997dfe210de95f6014ae850e5e01ac08e1bf26b75b57c7b32",
        "visual_sig": "sha256:7dfcebadac9e23b34e43766b996bfd4e9a74b39d12e71d9a5648dcfac29c0bc0",
        "semantic_sig": "sha256:0cb205d0e2424a4b07b3938359f2bb6ee96c91863e30329ef29f6c04d9e3a05f",
        "snapshot_json_sha256": "77b928a72ba0ff5d817972a4d7e0bc1a5713b0d8d2ae7f1fdfc23cab098f09d8",
        "cursor": {"col": 0, "row": 1, "visible": True, "wrap_pending": False},
    },
    "cp850": {
        "text_sig": "sha256:dc015bcb642b980efcfe0f4e4301da13b1de5435196d469ecb913be5b131c2e0",
        "visual_sig": "sha256:06691c1ad4b343626adef7a2e6e694ead7eaeed79176e9f5708c58ae021754bf",
        "semantic_sig": "sha256:ddf6bb41ae17cd8b503b05df4c357dbb39e4f8fb749eb1f2734b621e39cfd377",
        "snapshot_json_sha256": "55f62acf176f50e51ccc3566efb3dcbe8e9ff151b8e202079f1ee25cdf2f6a5d",
        "cursor": {"col": 2, "row": 1, "visible": True, "wrap_pending": False},
    },
    "clear_rewrite": {
        "text_sig": "sha256:f4fce87f16190873e08c9dd2a9d50592bf63a11bf657287bcd5748776cc0adf9",
        "visual_sig": "sha256:8308db869ca10c57d730f9d3a960a564844cc9eda49b8a119df2840b68713e2c",
        "semantic_sig": "sha256:2be43cca4b561674d04ab55dc06f3eb60638f530ba9d211a764702c9e5e4e543",
        "snapshot_json_sha256": "405fbcea8422cfb4c063ce43088712d824ce19609376ee6907ab7677cf624fe1",
        "cursor": {"col": 6, "row": 1, "visible": True, "wrap_pending": False},
    },
    "resize": {
        "text_sig": "sha256:c89830fc05a42adb059197d27e2fc42c669db85504fb073d24433f2bc03f1400",
        "visual_sig": "sha256:84ff286ff5cc48f186b6df2f2189b98c5671bb484c93faec56ebb744eeb4d5f1",
        "semantic_sig": "sha256:8bf42c74eea787cfe4312dab8150fbfbe0d7881ba1faf647dd6df06a90046430",
        "snapshot_json_sha256": "76b0c3ff51077925c7eb16cc465c62daa49a4406814ec8c7a4711409e694ffc1",
        "cursor": {"col": 0, "row": 2, "visible": True, "wrap_pending": False},
        "rows": 10,
        "cols": 40,
    },
    "reverse_spaces": {
        "text_sig": "sha256:92272a92435d0444bbdbbdae6c921671329353a528b40e65719888f6b0fbd167",
        "visual_sig": "sha256:f0a218651894afa95186432425a5b0ba248bd5fb52f52976cd72d4427779a559",
        "semantic_sig": "sha256:8e2f8ebdffcd51739e95cea57bec57bf12847199ecf5a0bc4248e1e67a790061",
        "snapshot_json_sha256": "d6ab37bc4622d4ffac933cdff398dccfaa2b90419f3d08aff796f987b21d74ca",
        "cursor": {"col": 5, "row": 0, "visible": True, "wrap_pending": False},
    },
}

# Caminho de captura do gateway (TerminalScreenState.feed_bytes + snapshot por
# evento) sobre a fixture real da captura 8 — oráculo pré-otimização.
ORACLE_REAL_CAPTURE8 = {
    "text_sig": "sha256:24c5b81473dc6632548f29b31df8f450ce2e94ca87fc58cb35d4e6671b3d4e9c",
    "visual_sig": "sha256:aca50891b92080948c834db9ae076a6d2e50877afd0a19dafb33b41e74f83906",
    "semantic_sig": "sha256:c055b81cace108c8233f06520d681f3432b63fa077bd224e886ab8ff03a3b223",
    "screen_sig": "L=3;W=12",
    "norm_sha256": "2e2209f0e70a3f2c7367c0d187c024d154658429ef448fc30810b0bfdf5df03f",
}


class DefaultCellSharingTests(unittest.TestCase):
    """DEFAULT_CELL: célula vazia imutável, compartilhada entre posições."""

    def test_blank_cell_sem_atributos_e_instancia_compartilhada(self):
        self.assertIs(blank_cell(), blank_cell())

    def test_celula_e_imutavel(self):
        cell = blank_cell()
        with self.assertRaises(Exception):
            cell.ch = "X"  # type: ignore[misc]

    def test_reset_compartilha_celula_vazia_entre_posicoes(self):
        engine = TerminalEngine(rows=4, cols=10)
        self.assertIs(engine.cells[0][0], engine.cells[3][9])

    def test_scroll_linha_nova_compartilha_celula_vazia(self):
        engine = TerminalEngine(rows=3, cols=5)
        engine.feed_bytes(b"a\r\nb\r\nc\r\nd")  # força scroll
        linhas = {id(engine.cells[r][c]) for r in range(3) for c in range(5)}
        self.assertLessEqual(len(linhas), 4, "células vazias devem ser a mesma instância")

    def test_escrita_nao_contamina_outras_posicoes(self):
        engine = TerminalEngine(rows=2, cols=4)
        engine.feed_bytes(b"Z")
        self.assertEqual(engine.cells[0][0].ch, "Z")
        self.assertEqual(engine.cells[0][1].ch, " ")
        self.assertEqual(engine.cells[1][3].ch, " ")
        self.assertIs(engine.cells[0][1], engine.cells[1][3])

    def test_blank_cell_com_atributos_nao_e_compartilhada(self):
        from dakota_terminal.attributes import Attributes

        attrs = Attributes(reverse=True)
        a = blank_cell(attrs)
        self.assertTrue(a.reverse)
        self.assertIsNot(a, blank_cell())


class ChunkingInvarianceTests(unittest.TestCase):
    """Chunkings diferentes do mesmo byte stream → estado final idêntico."""

    def _assert_chunkings(self, raw: bytes, *, encoding: str = "utf-8", rows: int = 25, cols: int = 80):
        whole = _snapshot_of([raw], encoding=encoding, rows=rows, cols=cols)
        by_byte = _snapshot_of([raw[i : i + 1] for i in range(len(raw))], encoding=encoding, rows=rows, cols=cols)
        thirds = max(1, len(raw) // 3)
        in_thirds = _snapshot_of(
            [raw[:thirds], raw[thirds : 2 * thirds], raw[2 * thirds :]],
            encoding=encoding, rows=rows, cols=cols,
        )
        self.assertEqual(whole, by_byte)
        self.assertEqual(whole, in_thirds)
        # Serialização persistida byte a byte
        self.assertEqual(encode_snapshot(whole), encode_snapshot(by_byte))
        self.assertEqual(encode_snapshot(whole), encode_snapshot(in_thirds))
        for key in ("text_sig", "visual_sig", "semantic_sig"):
            self.assertEqual(whole[key], by_byte[key])
            self.assertEqual(whole[key], in_thirds[key])

    def test_chunkings_nos_streams_oraculo(self):
        for name, spec in ORACLE_STREAMS.items():
            with self.subTest(stream=name):
                raw = b"".join(spec["chunks"])
                rows, cols = (10, 40) if name == "resize" else (25, 80)
                self._assert_chunkings(raw, encoding=spec["encoding"], rows=rows, cols=cols)

    def test_chunkings_em_todos_os_vetores(self):
        vector_paths = sorted(VECTORS.glob("*.json"))
        self.assertGreaterEqual(len(vector_paths), 24)
        for path in vector_paths:
            with self.subTest(vector=path.name):
                vector = json.loads(path.read_text(encoding="utf-8"))
                raw = b"".join(base64.b64decode(c) for c in vector.get("chunks_b64", []))
                self._assert_chunkings(
                    raw,
                    encoding=vector.get("encoding", "utf-8"),
                    rows=vector.get("rows", 25),
                    cols=vector.get("cols", 80),
                )


class OracleSignatureTests(unittest.TestCase):
    """Assinaturas e snapshot JSON idênticos aos valores pré-otimização."""

    def test_assinaturas_oraculo(self):
        for name, expected in ORACLE_SIGS.items():
            with self.subTest(stream=name):
                spec = ORACLE_STREAMS[name]
                snap = _snapshot_of(spec["chunks"], encoding=spec["encoding"])
                self.assertEqual(snap["text_sig"], expected["text_sig"])
                self.assertEqual(snap["visual_sig"], expected["visual_sig"])
                self.assertEqual(snap["semantic_sig"], expected["semantic_sig"])
                self.assertEqual(snap["cursor"], expected["cursor"])
                if "rows" in expected:
                    self.assertEqual(snap["rows"], expected["rows"])
                    self.assertEqual(snap["cols"], expected["cols"])

    def test_snapshot_json_byte_a_byte(self):
        """Formato de persistência não mudou → JSON serializado byte-idêntico."""
        for name, expected in ORACLE_SIGS.items():
            with self.subTest(stream=name):
                spec = ORACLE_STREAMS[name]
                snap = _snapshot_of(spec["chunks"], encoding=spec["encoding"])
                blob = encode_snapshot(snap)
                digest = hashlib.sha256(blob.encode("utf-8")).hexdigest()
                self.assertEqual(digest, expected["snapshot_json_sha256"])

    def test_fixture_real_caminho_do_gateway(self):
        doc = json.loads(REAL_JOURNEY.read_text(encoding="utf-8"))
        state = TerminalScreenState(rows=25, cols=80, encoding="utf-8")
        last = None
        for ev in doc["events"]:
            if ev.get("type") == "bytes" and ev.get("dir") == "out":
                state.feed_bytes(base64.b64decode(ev["data_b64"]), seq_global=ev.get("seq_global", 0))
                last = state.snapshot()
        self.assertIsNotNone(last)
        for key, expected in ORACLE_REAL_CAPTURE8.items():
            self.assertEqual(getattr(last, key), expected, f"{key} divergiu no caminho do gateway")


class CellContractTests(unittest.TestCase):
    """Contrato de Cell que a otimização precisa preservar."""

    def test_to_dict_round_trip(self):
        cell = Cell(ch="A", fg=1, bg=2, bold=True, reverse=True)
        clone = Cell(**cell.to_dict())
        self.assertEqual(cell, clone)
        self.assertEqual(clone.to_dict(), cell.to_dict())

    def test_celula_default_to_dict_estavel(self):
        esperado = {
            "ch": " ", "fg": "default", "bg": "default",
            "bold": False, "dim": False, "underline": False,
            "blink": False, "reverse": False, "hidden": False,
        }
        self.assertEqual(blank_cell().to_dict(), esperado)


if __name__ == "__main__":
    unittest.main()

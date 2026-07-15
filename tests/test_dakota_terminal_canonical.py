#!/usr/bin/env python3
from __future__ import annotations

import base64
import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GATEWAY_DIR = ROOT / "gateway"
sys.path.insert(0, str(GATEWAY_DIR))

from dakota_terminal import TerminalEngine, serialize_text_state, serialize_visual_state
from dakota_terminal.diffs import first_cell_diff
from dakota_gateway.screen import TerminalScreenState, build_screen_snapshot_from_bytes


VECTORS = ROOT / "tests" / "fixtures" / "terminal_vectors"
JS_RUNNER = ROOT / "tests" / "terminal_tools" / "js_snapshot.cjs"


def python_snapshot(vector: dict) -> dict:
    engine = TerminalEngine(
        rows=vector.get("rows", 25),
        cols=vector.get("cols", 80),
        term=vector.get("term", "xterm"),
        encoding=vector.get("encoding", "utf-8"),
    )
    for chunk in vector.get("chunks_b64", []):
        engine.feed_bytes(base64.b64decode(chunk))
    snap = engine.snapshot()
    return {
        "version": snap["version"],
        "engine_version": snap["engine_version"],
        "rows": snap["rows"],
        "cols": snap["cols"],
        "term": snap["term"],
        "encoding": snap["encoding"],
        "cursor": snap["cursor"],
        "saved_cursor": snap["saved_cursor"],
        "attributes": snap["attributes"],
        "g0_charset": snap["g0_charset"],
        "g1_charset": snap["g1_charset"],
        "active_charset": snap["active_charset"],
        "scroll_region": snap["scroll_region"],
        "cells": snap["cells"],
        "text_sig": snap["text_sig"],
        "visual_sig": snap["visual_sig"],
    }


def js_snapshot(path: Path) -> dict:
    proc = subprocess.run(["node", str(JS_RUNNER), str(path)], cwd=ROOT, text=True, capture_output=True, check=True)
    return json.loads(proc.stdout)


class DakotaTerminalCanonicalTests(unittest.TestCase):
    def test_signature_contracts(self):
        a = TerminalEngine(rows=1, cols=1)
        a.feed_bytes(b"A")
        b = TerminalEngine(rows=1, cols=2)
        b.feed_bytes(b"A")
        self.assertNotEqual(a.snapshot()["text_sig"], b.snapshot()["text_sig"])
        self.assertNotEqual(a.snapshot()["visual_sig"], b.snapshot()["visual_sig"])

        normal = TerminalEngine(rows=1, cols=1)
        normal.feed_bytes(b"A")
        reverse = TerminalEngine(rows=1, cols=1)
        reverse.feed_bytes(b"\x1b[7mA")
        self.assertEqual(normal.snapshot()["text_sig"], reverse.snapshot()["text_sig"])
        self.assertNotEqual(normal.snapshot()["visual_sig"], reverse.snapshot()["visual_sig"])

        direct = TerminalEngine(rows=1, cols=3)
        direct.feed_bytes(b"ABC")
        overwritten = TerminalEngine(rows=1, cols=3)
        overwritten.feed_bytes(b"AXC\bB")
        self.assertEqual(direct.snapshot()["text_sig"], overwritten.snapshot()["text_sig"])
        self.assertEqual(direct.snapshot()["visual_sig"], overwritten.snapshot()["visual_sig"])

    def test_chunking_is_snapshot_stable(self):
        whole = TerminalEngine(rows=3, cols=20)
        chunked = TerminalEngine(rows=3, cols=20)
        raw = "Aá 😀 \x1b[7mR\x1b[0m".encode("utf-8")
        whole.feed_bytes(raw)
        for chunk in [raw[:1], raw[1:2], raw[2:5], raw[5:8], raw[8:]]:
            chunked.feed_bytes(chunk)
        self.assertEqual(whole.snapshot(), chunked.snapshot())

    def test_serialization_is_ascii_and_deterministic(self):
        engine = TerminalEngine(rows=1, cols=2)
        engine.feed_bytes("Á😀".encode("utf-8"))
        snap = engine.snapshot()
        self.assertEqual(serialize_text_state(snap), serialize_text_state(dict(snap)))
        self.assertEqual(serialize_visual_state(snap), serialize_visual_state(dict(snap)))
        serialize_visual_state(snap).decode("ascii")

    def test_screen_py_facade_exposes_canonical_signatures(self):
        snap = build_screen_snapshot_from_bytes(b"\x1b[7mA ", rows=1, cols=2)
        self.assertTrue(snap.text_sig.startswith("sha256:"))
        self.assertTrue(snap.visual_sig.startswith("sha256:"))
        # semantic_sig agora e a canonica do TerminalEngine (sha256:...)
        self.assertTrue(snap.semantic_sig.startswith("sha256:"),
                       f"semantic_sig deve ser canonica, nao: {snap.semantic_sig[:30]}")
        # screen_sig permanece legado
        self.assertNotEqual(snap.semantic_sig, snap.screen_sig,
                           "semantic_sig canonica NAO deve ser igual ao screen_sig legado")
        state = TerminalScreenState(rows=1, cols=2)
        state.feed_bytes(b"\x1b[7mA ")
        state_snap = state.snapshot()
        self.assertTrue(state_snap.semantic_sig.startswith("sha256:"))
        self.assertEqual(state_snap.canonical_snapshot["visual_sig"], snap.visual_sig)

    def test_python_and_javascript_match_all_shared_vectors(self):
        vector_paths = sorted(VECTORS.glob("*.json"))
        self.assertGreaterEqual(len(vector_paths), 24)
        for path in vector_paths:
            with self.subTest(vector=path.name):
                vector = json.loads(path.read_text(encoding="utf-8"))
                py = python_snapshot(vector)
                js = js_snapshot(path)
                expected = vector.get("expected", {})
                self.assertEqual(expected.get("cursor"), py["cursor"])
                self.assertEqual(expected.get("text_sig"), py["text_sig"])
                self.assertEqual(expected.get("visual_sig"), py["visual_sig"])
                if py != js:
                    diff = first_cell_diff(py, js)
                    self.fail(
                        f"{vector.get('name', path.name)} diverged; first_cell={diff}; "
                        f"cursor_py={py['cursor']} cursor_js={js['cursor']} "
                        f"text={py['text_sig']} vs {js['text_sig']} visual={py['visual_sig']} vs {js['visual_sig']}"
                    )


if __name__ == "__main__":
    unittest.main()

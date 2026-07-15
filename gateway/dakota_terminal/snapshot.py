from __future__ import annotations

import json
from dataclasses import dataclass

from .signatures import text_sig, visual_sig, semantic_sig


@dataclass(frozen=True)
class CanonicalSnapshot:
    rows: int
    cols: int
    cells: list
    cursor: dict
    saved_cursor: dict
    attributes: dict
    g0_charset: str
    g1_charset: str
    active_charset: str
    scroll_region: dict
    autowrap: bool
    origin_mode: bool
    insert_mode: bool
    tab_stops: list
    parser_state: dict
    scanner_state: object
    decoder_state: dict
    pending_bytes: str
    warnings: list
    seq_global: int
    text_sig: str
    visual_sig: str
    semantic_sig: str
    engine_version: str
    snapshot_version: str
    signature_version: str


@dataclass(frozen=True)
class RenderSnapshot:
    rows: int
    cols: int
    term: str
    encoding: str
    cells: list | None
    runs: list | None
    cursor: dict
    seq_global: int
    text_sig: str
    visual_sig: str
    semantic_sig: str
    engine_version: str
    snapshot_version: str
    signature_version: str


def snapshot_from_engine(engine) -> dict:
    cells = [cell.to_dict() for row in engine.cells for cell in row]
    snap = {
        "version": 1,
        "engine_version": engine.engine_version,
        "signature_version": "1.0",
        "snapshot_version": "1.0",
        "rows": engine.rows,
        "cols": engine.cols,
        "term": engine.term,
        "encoding": engine.encoding,
        "cursor": {
            "row": engine.cursor_row,
            "col": engine.cursor_col,
            "visible": engine.cursor_visible,
            "wrap_pending": engine.wrap_pending,
        },
        "saved_cursor": {"row": engine.saved_row, "col": engine.saved_col},
        "attributes": engine.attrs.to_dict(),
        "g0_charset": "dec_special" if engine.g0_charset == "0" else "ascii",
        "g1_charset": "dec_special" if engine.g1_charset == "0" else "ascii",
        "active_charset": "g1" if engine.shift_out else "g0",
        "scroll_region": {"top": engine.scroll_top, "bottom": engine.scroll_bottom},
        "autowrap": engine.autowrap,
        "tab_stops": sorted(engine.tab_stops),
        "parser": {"partial": engine.partial_escape},
        "scanner_state": engine._escape_state,
        "decoder_warnings": list(engine.decoder.warnings),
        "seq_global": int(getattr(engine, "seq_global", 0) or 0),
        "cells": cells,
    }
    snap["text_sig"] = text_sig(snap)
    snap["visual_sig"] = visual_sig(snap)
    snap["semantic_sig"] = semantic_sig(snap)
    return snap


def encode_canonical_snapshot(snapshot: dict) -> dict:
    if not isinstance(snapshot, dict):
        raise TypeError("canonical snapshot encoding requires a canonical snapshot dict")
    return {
        "version": snapshot.get("version", 1),
        "rows": snapshot["rows"],
        "cols": snapshot["cols"],
        "cells": snapshot.get("cells", []),
        "cursor": snapshot.get("cursor", {}),
        "saved_cursor": snapshot.get("saved_cursor", {}),
        "attributes": snapshot.get("attributes", {}),
        "g0_charset": snapshot.get("g0_charset", "ascii"),
        "g1_charset": snapshot.get("g1_charset", "ascii"),
        "active_charset": snapshot.get("active_charset", "g0"),
        "scroll_region": snapshot.get("scroll_region", {}),
        "autowrap": bool(snapshot.get("autowrap", True)),
        "origin_mode": bool(snapshot.get("origin_mode", False)),
        "insert_mode": bool(snapshot.get("insert_mode", False)),
        "tab_stops": list(snapshot.get("tab_stops", [])),
        "parser_state": snapshot.get("parser", snapshot.get("parser_state", {})),
        "scanner_state": snapshot.get("scanner_state"),
        "decoder_state": snapshot.get("decoder_state", {}),
        "pending_bytes": snapshot.get("pending_bytes", ""),
        "warnings": list(snapshot.get("decoder_warnings", snapshot.get("warnings", []))),
        "seq_global": int(snapshot.get("seq_global", 0)),
        "text_sig": snapshot.get("text_sig", ""),
        "visual_sig": snapshot.get("visual_sig", ""),
        "semantic_sig": snapshot.get("semantic_sig", ""),
        "engine_version": snapshot.get("engine_version", "1.0"),
        "snapshot_version": snapshot.get("snapshot_version", "1.0"),
        "signature_version": snapshot.get("signature_version", "1.0"),
    }


def decode_canonical_snapshot(payload: dict) -> CanonicalSnapshot:
    if not isinstance(payload, dict):
        raise TypeError("canonical snapshot payload must be an object")
    return CanonicalSnapshot(
        rows=int(payload["rows"]),
        cols=int(payload["cols"]),
        cells=list(payload.get("cells", [])),
        cursor=dict(payload.get("cursor", {})),
        saved_cursor=dict(payload.get("saved_cursor", {})),
        attributes=dict(payload.get("attributes", {})),
        g0_charset=str(payload.get("g0_charset", "ascii")),
        g1_charset=str(payload.get("g1_charset", "ascii")),
        active_charset=str(payload.get("active_charset", "g0")),
        scroll_region=dict(payload.get("scroll_region", {})),
        autowrap=bool(payload.get("autowrap", True)),
        origin_mode=bool(payload.get("origin_mode", False)),
        insert_mode=bool(payload.get("insert_mode", False)),
        tab_stops=list(payload.get("tab_stops", [])),
        parser_state=dict(payload.get("parser_state", {})),
        scanner_state=payload.get("scanner_state"),
        decoder_state=dict(payload.get("decoder_state", {})),
        pending_bytes=str(payload.get("pending_bytes", "")),
        warnings=list(payload.get("warnings", [])),
        seq_global=int(payload.get("seq_global", 0)),
        text_sig=str(payload.get("text_sig", "")),
        visual_sig=str(payload.get("visual_sig", "")),
        semantic_sig=str(payload.get("semantic_sig", "")),
        engine_version=str(payload.get("engine_version", "1.0")),
        snapshot_version=str(payload.get("snapshot_version", "1.0")),
        signature_version=str(payload.get("signature_version", "1.0")),
    )


def encode_render_snapshot(snapshot: dict) -> dict:
    if isinstance(snapshot, RenderSnapshot):
        return snapshot.__dict__.copy()
    if not isinstance(snapshot, dict):
        raise TypeError("render snapshot encoding requires a snapshot dict")
    return {
        "version": snapshot.get("version", 1),
        "rows": snapshot["rows"],
        "cols": snapshot["cols"],
        "term": snapshot.get("term", "xterm"),
        "encoding": snapshot.get("encoding", "utf-8"),
        "cells": snapshot.get("cells"),
        "runs": snapshot.get("runs"),
        "cursor": snapshot.get("cursor", {}),
        "seq_global": int(snapshot.get("seq_global", 0)),
        "text_sig": snapshot.get("text_sig", ""),
        "visual_sig": snapshot.get("visual_sig", ""),
        "semantic_sig": snapshot.get("semantic_sig", ""),
        "engine_version": snapshot.get("engine_version", ""),
        "snapshot_version": snapshot.get("snapshot_version", "1.0"),
        "signature_version": snapshot.get("signature_version", "1.0"),
    }


def decode_render_snapshot(payload: dict) -> RenderSnapshot:
    if not isinstance(payload, dict):
        raise TypeError("render snapshot payload must be an object")
    return RenderSnapshot(
        rows=int(payload["rows"]),
        cols=int(payload["cols"]),
        term=str(payload.get("term", "xterm")),
        encoding=str(payload.get("encoding", "utf-8")),
        cells=payload.get("cells"),
        runs=payload.get("runs"),
        cursor=dict(payload.get("cursor", {})),
        seq_global=int(payload.get("seq_global", 0)),
        text_sig=str(payload.get("text_sig", "")),
        visual_sig=str(payload.get("visual_sig", "")),
        semantic_sig=str(payload.get("semantic_sig", "")),
        engine_version=str(payload.get("engine_version", "")),
        snapshot_version=str(payload.get("snapshot_version", "1.0")),
        signature_version=str(payload.get("signature_version", "1.0")),
    )


# ── Compact encoding for transport ─────────────────────────────────────────

def encode_snapshot_compact(snapshot: dict) -> dict:
    """Codifica snapshot em formato compacto run-length por atributos.

    Retorna dict serializavel com:
    - version, rows, cols, term, encoding
    - cursor, text_sig, visual_sig
    - attribute_table: lista de atributos unicos
    - runs: lista de {row, col, length, text, attr_index}
    """
    cells = snapshot.get("cells", [])
    rows = snapshot.get("rows", 25)
    cols = snapshot.get("cols", 80)

    # Constrói tabela de atributos
    attr_index: dict[str, int] = {}
    attr_table: list[dict] = []

    def _attr_key(cell: dict) -> str:
        return f"{cell.get('fg')}|{cell.get('bg')}|{int(cell.get('bold',False))}|{int(cell.get('dim',False))}|{int(cell.get('underline',False))}|{int(cell.get('blink',False))}|{int(cell.get('reverse',False))}|{int(cell.get('hidden',False))}"

    def _attr_dict(cell: dict) -> dict:
        return {
            "fg": cell.get("fg", "default"),
            "bg": cell.get("bg", "default"),
            "bold": bool(cell.get("bold")),
            "dim": bool(cell.get("dim")),
            "underline": bool(cell.get("underline")),
            "blink": bool(cell.get("blink")),
            "reverse": bool(cell.get("reverse")),
            "hidden": bool(cell.get("hidden")),
        }

    for cell in cells:
        key = _attr_key(cell)
        if key not in attr_index:
            attr_index[key] = len(attr_table)
            attr_table.append(_attr_dict(cell))

    runs = []
    r = 0
    while r < rows:
        c = 0
        while c < cols:
            idx = r * cols + c
            cell = cells[idx] if idx < len(cells) else {"ch": " "}
            attr = attr_index[_attr_key(cell)]

            # Encontra run com mesmo atributo
            run_text = []
            run_len = 0
            while c + run_len < cols:
                cidx = r * cols + c + run_len
                cc = cells[cidx] if cidx < len(cells) else {"ch": " "}
                if attr_index[_attr_key(cc)] != attr:
                    break
                run_text.append(cc.get("ch", " "))
                run_len += 1

            runs.append({
                "row": r,
                "col": c,
                "length": run_len,
                "text": "".join(run_text),
                "attr": attr,
            })
            c += run_len
        r += 1

    return {
        "version": 1,
        "rows": rows,
        "cols": cols,
        "term": snapshot.get("term", "xterm"),
        "encoding": snapshot.get("encoding", "utf-8"),
        "cursor": snapshot.get("cursor", {"row": 0, "col": 0, "visible": True, "wrap_pending": False}),
        "text_sig": snapshot.get("text_sig", ""),
        "visual_sig": snapshot.get("visual_sig", ""),
        "attribute_table": attr_table,
        "runs": runs,
    }


def decode_snapshot_compact(payload: dict) -> dict:
    """Decodifica formato compacto de volta para snapshot canonico."""
    rows = payload.get("rows", 25)
    cols = payload.get("cols", 80)
    attr_table = payload.get("attribute_table", [])
    runs = payload.get("runs", [])

    # Inicializa celulas vazias
    cells = [{
        "ch": " ", "fg": "default", "bg": "default",
        "bold": False, "dim": False, "underline": False,
        "blink": False, "reverse": False, "hidden": False,
    } for _ in range(rows * cols)]

    for run in runs:
        r = run["row"]
        c = run["col"]
        length = run["length"]
        text = run.get("text", "")
        attr_idx = run.get("attr", 0)
        attrs = attr_table[attr_idx] if attr_idx < len(attr_table) else {}

        for offset in range(min(length, len(text))):
            idx = r * cols + c + offset
            if idx < len(cells):
                cells[idx] = {
                    "ch": text[offset] if offset < len(text) else " ",
                    "fg": attrs.get("fg", "default"),
                    "bg": attrs.get("bg", "default"),
                    "bold": bool(attrs.get("bold")),
                    "dim": bool(attrs.get("dim")),
                    "underline": bool(attrs.get("underline")),
                    "blink": bool(attrs.get("blink")),
                    "reverse": bool(attrs.get("reverse")),
                    "hidden": bool(attrs.get("hidden")),
                }

    result = {
        "version": 1,
        "engine_version": payload.get("engine_version", ""),
        "snapshot_version": payload.get("snapshot_version", ""),
        "signature_version": payload.get("signature_version", ""),
        "rows": rows,
        "cols": cols,
        "term": payload.get("term", "xterm"),
        "encoding": payload.get("encoding", "utf-8"),
        "cursor": payload.get("cursor", {"row": 0, "col": 0, "visible": True, "wrap_pending": False}),
        "cells": cells,
        "text_sig": payload.get("text_sig", ""),
        "visual_sig": payload.get("visual_sig", ""),
    }
    return result


def encode_snapshot(snapshot: dict) -> str:
    """Serializa snapshot para string JSON."""
    return json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"))


def decode_snapshot(payload: str) -> dict:
    """Decodifica string JSON para snapshot."""
    return json.loads(payload)

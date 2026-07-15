from __future__ import annotations

import json

from .signatures import text_sig, visual_sig


def snapshot_from_engine(engine) -> dict:
    cells = [cell.to_dict() for row in engine.cells for cell in row]
    snap = {
        "version": 1,
        "engine_version": engine.engine_version,
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
        "decoder_warnings": list(engine.decoder.warnings),
        "cells": cells,
    }
    snap["text_sig"] = text_sig(snap)
    snap["visual_sig"] = visual_sig(snap)
    return snap


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
        "engine_version": "1.0",
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

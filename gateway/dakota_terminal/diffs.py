from __future__ import annotations

from copy import deepcopy

from .signatures import text_sig, visual_sig, semantic_sig

MAX_ROWS = 200
MAX_COLS = 500
MAX_CELLS = 100000
MAX_DIFF_CHANGES = 100000


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _valid_sig(value) -> bool:
    return isinstance(value, str) and len(value) == 71 and value.startswith("sha256:")


def _cell_with_defaults(change: dict) -> dict:
    return {
        "ch": change.get("ch", " "),
        "fg": change.get("fg", "default"),
        "bg": change.get("bg", "default"),
        "bold": bool(change.get("bold")),
        "dim": bool(change.get("dim")),
        "underline": bool(change.get("underline")),
        "blink": bool(change.get("blink")),
        "reverse": bool(change.get("reverse")),
        "hidden": bool(change.get("hidden")),
    }


def first_cell_diff(left: dict, right: dict) -> dict | None:
    for idx, (a, b) in enumerate(zip(left.get("cells", []), right.get("cells", []))):
        if a != b:
            cols = int(left.get("cols") or right.get("cols") or 1)
            return {"row": idx // cols, "col": idx % cols, "left": a, "right": b}
    if len(left.get("cells", [])) != len(right.get("cells", [])):
        return {"row": None, "col": None, "left": len(left.get("cells", [])), "right": len(right.get("cells", []))}
    return None


def create_diff(previous: dict, current: dict, base_seq: int = 0, seq: int = 0, ts_ms: int = 0) -> dict:
    """Cria um diff entre dois snapshots com identidade sequencial.

    Args:
        previous: snapshot base
        current: snapshot alvo
        base_seq: seq_global do snapshot base
        seq: seq_global apos aplicar o diff
        ts_ms: timestamp do evento

    Retorna diff com:
    - version, base_seq_global, seq_global, timestamp_ms
    - base_text_sig, base_visual_sig
    - text_sig, visual_sig
    - geometry_changed, rows, cols
    - cursor, resize
    - changes: lista de alteracoes por celula
    """
    prev_cells = previous.get("cells", [])
    curr_cells = current.get("cells", [])

    changes = []
    for idx, (pc, cc) in enumerate(zip(prev_cells, curr_cells)):
        if pc != cc:
            cols = int(current.get("cols") or previous.get("cols") or 1)
            changes.append({
                "row": idx // cols,
                "col": idx % cols,
                "ch": cc.get("ch", " "),
                "fg": cc.get("fg", "default"),
                "bg": cc.get("bg", "default"),
                "bold": bool(cc.get("bold")),
                "dim": bool(cc.get("dim")),
                "underline": bool(cc.get("underline")),
                "blink": bool(cc.get("blink")),
                "reverse": bool(cc.get("reverse")),
                "hidden": bool(cc.get("hidden")),
            })

    # Celulas adicionais (resize para maior)
    if len(curr_cells) > len(prev_cells):
        for idx in range(len(prev_cells), len(curr_cells)):
            cc = curr_cells[idx]
            cols = int(current.get("cols") or previous.get("cols") or 1)
            changes.append({
                "row": idx // cols,
                "col": idx % cols,
                "ch": cc.get("ch", " "),
                "fg": cc.get("fg", "default"),
                "bg": cc.get("bg", "default"),
                "bold": bool(cc.get("bold")),
                "dim": bool(cc.get("dim")),
                "underline": bool(cc.get("underline")),
                "blink": bool(cc.get("blink")),
                "reverse": bool(cc.get("reverse")),
                "hidden": bool(cc.get("hidden")),
            })

    geo_changed = (
        previous.get("rows") != current.get("rows")
        or previous.get("cols") != current.get("cols")
    )

    resize_info = None
    if geo_changed:
        resize_info = {
            "from_rows": previous.get("rows"),
            "from_cols": previous.get("cols"),
            "to_rows": current.get("rows"),
            "to_cols": current.get("cols"),
        }

    return {
        "version": 1,
        "base_seq_global": base_seq,
        "seq_global": seq,
        "timestamp_ms": ts_ms,
        "base_rows": previous.get("rows", 25),
        "base_cols": previous.get("cols", 80),
        "base_text_sig": previous.get("text_sig", ""),
        "base_visual_sig": previous.get("visual_sig", ""),
        "base_semantic_sig": previous.get("semantic_sig", ""),
        "text_sig": current.get("text_sig", ""),
        "visual_sig": current.get("visual_sig", ""),
        "semantic_sig": current.get("semantic_sig", ""),
        "geometry_changed": geo_changed,
        "rows": current.get("rows", previous.get("rows", 25)),
        "cols": current.get("cols", previous.get("cols", 80)),
        "cursor": current.get("cursor", {"row": 0, "col": 0, "visible": True, "wrap_pending": False}),
        "resize": resize_info,
        "changes": changes,
    }


def apply_diff(snapshot: dict, diff: dict) -> dict:
    """Aplica um diff a um snapshot, retornando novo snapshot com assinaturas verificadas.

    Recalcula text_sig, visual_sig e semantic_sig apos aplicar as mudancas
    e compara com as assinaturas declaradas no diff. Rejeita se houver divergencia.
    """
    if not validate_diff(snapshot, diff):
        raise ValueError("apply_diff: diff validation failed")

    result = deepcopy(snapshot)
    result_cells = result.get("cells", [])
    rows = diff.get("rows", snapshot.get("rows", 25))
    cols = diff.get("cols", snapshot.get("cols", 80))

    # Ajusta geometria se necessario
    if diff.get("geometry_changed"):
        expected = rows * cols
        while len(result_cells) < expected:
            result_cells.append(_cell_with_defaults({}))
        result_cells = result_cells[:expected]
        result["rows"] = rows
        result["cols"] = cols

    # Valida e aplica mudancas
    seen_coords = set()
    for change in diff.get("changes", []):
        row = change.get("row")
        col = change.get("col")
        coord = (row, col)
        seen_coords.add(coord)

        idx = row * cols + col
        result_cells[idx] = _cell_with_defaults(change)

    result["cells"] = result_cells
    result["cursor"] = diff.get("cursor", result.get("cursor"))
    result["rows"] = rows
    result["cols"] = cols

    # Recalcula assinaturas e verifica contra o diff
    computed_text = text_sig(result)
    computed_visual = visual_sig(result)
    computed_semantic = semantic_sig(result)

    declared_text = diff.get("text_sig", "")
    declared_visual = diff.get("visual_sig", "")
    declared_semantic = diff.get("semantic_sig", "")

    if declared_text and computed_text != declared_text:
        raise ValueError(
            f"apply_diff: text_sig mismatch: declared={declared_text[:30]}... computed={computed_text[:30]}..."
        )
    if declared_visual and computed_visual != declared_visual:
        raise ValueError(
            f"apply_diff: visual_sig mismatch: declared={declared_visual[:30]}... computed={computed_visual[:30]}..."
        )
    if declared_semantic and computed_semantic != declared_semantic:
        raise ValueError(
            f"apply_diff: semantic_sig mismatch: declared={declared_semantic[:30]}... computed={computed_semantic[:30]}..."
        )

    # So agora atribui as assinaturas (ja verificadas)
    result["text_sig"] = computed_text
    result["visual_sig"] = computed_visual
    result["semantic_sig"] = computed_semantic

    if diff.get("seq_global") is not None:
        result["seq_global"] = diff["seq_global"]

    return result


def validate_diff(snapshot: dict, diff: dict) -> bool:
    """Valida que um diff pode ser aplicado ao snapshot.

    Verifica:
    - version compativel
    - base_text_sig bate com snapshot atual
    - base_visual_sig bate (se disponivel)
    - base_seq_global consistente
    - geometria compativel
    """
    if not isinstance(snapshot, dict) or not isinstance(diff, dict):
        return False
    if diff.get("version") != 1:
        return False
    for key in ("base_seq_global", "seq_global", "timestamp_ms", "base_rows", "base_cols", "rows", "cols"):
        if not _is_int(diff.get(key)):
            return False
    if diff["seq_global"] <= diff["base_seq_global"]:
        return False
    snapshot_seq = int(snapshot.get("seq_global") or 0)
    if snapshot_seq > 0 and snapshot_seq != diff["base_seq_global"]:
        return False
    if snapshot.get("rows") != diff["base_rows"] or snapshot.get("cols") != diff["base_cols"]:
        return False
    rows = diff["rows"]
    cols = diff["cols"]
    if rows < 1 or cols < 1 or rows > MAX_ROWS or cols > MAX_COLS or rows * cols > MAX_CELLS:
        return False
    geometry_changed = diff.get("geometry_changed")
    if not isinstance(geometry_changed, bool):
        return False
    base_rows = diff["base_rows"]
    base_cols = diff["base_cols"]
    actual_geometry_changed = rows != base_rows or cols != base_cols
    if actual_geometry_changed and geometry_changed is not True:
        return False
    if not actual_geometry_changed and geometry_changed is not False:
        return False
    resize = diff.get("resize")
    if geometry_changed:
        if not isinstance(resize, dict):
            return False
        expected_resize = {
            "from_rows": base_rows,
            "from_cols": base_cols,
            "to_rows": rows,
            "to_cols": cols,
        }
        for key, value in expected_resize.items():
            if resize.get(key) != value:
                return False
    elif resize is not None:
        return False
    for base_key, snap_key in (
        ("base_text_sig", "text_sig"),
        ("base_visual_sig", "visual_sig"),
        ("base_semantic_sig", "semantic_sig"),
    ):
        if not _valid_sig(diff.get(base_key, "")):
            return False
        if diff.get(base_key) != snapshot.get(snap_key):
            return False
    for key in ("text_sig", "visual_sig", "semantic_sig"):
        if not _valid_sig(diff.get(key, "")):
            return False
    changes = diff.get("changes")
    if not isinstance(changes, list) or len(changes) > MAX_DIFF_CHANGES:
        return False
    seen_coords = set()
    for change in changes:
        if not isinstance(change, dict):
            return False
        row = change.get("row")
        col = change.get("col")
        if not _is_int(row) or not _is_int(col):
            return False
        if row < 0 or col < 0 or row >= rows or col >= cols:
            return False
        coord = (row, col)
        if coord in seen_coords:
            return False
        seen_coords.add(coord)
        if "ch" in change and not isinstance(change["ch"], str):
            return False
        for attr in ("bold", "dim", "underline", "blink", "reverse", "hidden"):
            if attr in change and not isinstance(change[attr], bool):
                return False
    cursor = diff.get("cursor")
    if cursor is not None:
        if not isinstance(cursor, dict):
            return False
        crow = cursor.get("row", 0)
        ccol = cursor.get("col", 0)
        if not _is_int(crow) or not _is_int(ccol) or crow < 0 or ccol < 0 or crow >= rows or ccol >= cols:
            return False
    try:
        candidate = deepcopy(snapshot)
        candidate_cells = list(candidate.get("cells", []))
        expected_cells = rows * cols
        if len(candidate_cells) < expected_cells:
            candidate_cells.extend(_cell_with_defaults({}) for _ in range(expected_cells - len(candidate_cells)))
        candidate_cells = candidate_cells[:expected_cells]
        for change in changes:
            candidate_cells[change["row"] * cols + change["col"]] = _cell_with_defaults(change)
        candidate["cells"] = candidate_cells
        candidate["rows"] = rows
        candidate["cols"] = cols
        candidate["cursor"] = diff.get("cursor", candidate.get("cursor"))
        if text_sig(candidate) != diff["text_sig"]:
            return False
        if visual_sig(candidate) != diff["visual_sig"]:
            return False
        if semantic_sig(candidate) != diff["semantic_sig"]:
            return False
    except Exception:
        return False
    return True


def estimate_diff_size(diff: dict) -> int:
    """Estima o tamanho em bytes de um diff serializado."""
    import json
    return len(json.dumps(diff, separators=(",", ":")))

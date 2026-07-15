from __future__ import annotations

from copy import deepcopy


def first_cell_diff(left: dict, right: dict) -> dict | None:
    for idx, (a, b) in enumerate(zip(left.get("cells", []), right.get("cells", []))):
        if a != b:
            cols = int(left.get("cols") or right.get("cols") or 1)
            return {"row": idx // cols, "col": idx % cols, "left": a, "right": b}
    if len(left.get("cells", [])) != len(right.get("cells", [])):
        return {"row": None, "col": None, "left": len(left.get("cells", [])), "right": len(right.get("cells", []))}
    return None


def create_diff(previous: dict, current: dict) -> dict:
    """Cria um diff entre dois snapshots.

    Retorna um dict com:
    - version: versao do formato de diff
    - base_text_sig: text_sig do snapshot base
    - base_visual_sig: visual_sig do snapshot base
    - text_sig: text_sig apos aplicar o diff
    - visual_sig: visual_sig apos aplicar o diff
    - geometry_changed: bool
    - rows, cols: geometria final
    - cursor: posicao do cursor apos diff
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

    return {
        "version": 1,
        "base_text_sig": previous.get("text_sig", ""),
        "base_visual_sig": previous.get("visual_sig", ""),
        "text_sig": current.get("text_sig", ""),
        "visual_sig": current.get("visual_sig", ""),
        "geometry_changed": geo_changed,
        "rows": current.get("rows", previous.get("rows", 25)),
        "cols": current.get("cols", previous.get("cols", 80)),
        "cursor": current.get("cursor", {"row": 0, "col": 0, "visible": True, "wrap_pending": False}),
        "changes": changes,
    }


def apply_diff(snapshot: dict, diff: dict) -> dict:
    """Aplica um diff a um snapshot, retornando novo snapshot."""
    result = deepcopy(snapshot)
    result_cells = result.get("cells", [])
    rows = diff.get("rows", snapshot.get("rows", 25))
    cols = diff.get("cols", snapshot.get("cols", 80))

    # Ajusta geometria se necessario
    if diff.get("geometry_changed"):
        expected = rows * cols
        while len(result_cells) < expected:
            result_cells.append({"ch": " ", "fg": "default", "bg": "default",
                                "bold": False, "dim": False, "underline": False,
                                "blink": False, "reverse": False, "hidden": False})
        result_cells = result_cells[:expected]
        result["rows"] = rows
        result["cols"] = cols

    for change in diff.get("changes", []):
        idx = change["row"] * cols + change["col"]
        if idx < len(result_cells):
            result_cells[idx] = {
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

    result["cells"] = result_cells
    result["cursor"] = diff.get("cursor", result.get("cursor"))
    return result


def validate_diff(snapshot: dict, diff: dict) -> bool:
    """Valida que um diff pode ser aplicado ao snapshot."""
    base_sig = diff.get("base_text_sig", "")
    snap_sig = snapshot.get("text_sig", "")
    if base_sig and snap_sig and base_sig != snap_sig:
        return False
    return True


def estimate_diff_size(diff: dict) -> int:
    """Estima o tamanho em bytes de um diff serializado."""
    import json
    return len(json.dumps(diff, separators=(",", ":")))

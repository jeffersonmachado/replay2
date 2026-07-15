from __future__ import annotations


DEC_SPECIAL_GRAPHICS_MAP = {
    "l": "┌", "k": "┐", "m": "└", "j": "┘",
    "q": "─", "x": "│", "t": "├", "u": "┤",
    "v": "┴", "w": "┬", "n": "┼",
}


def parse_csi_params(params: str) -> list[int]:
    clean = str(params or "").replace("?", "")
    if clean == "":
        return [0]
    out: list[int] = []
    for part in clean.split(";"):
        try:
            out.append(int(part or "0"))
        except ValueError:
            out.append(0)
    return out


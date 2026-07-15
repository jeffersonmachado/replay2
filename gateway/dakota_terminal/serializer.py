from __future__ import annotations

from .model import Cell


MAGIC_TEXT = "DKT-TEXT"
MAGIC_VISUAL = "DKT-VISUAL"
FORMAT_VERSION = 1


def _codepoints(ch: str) -> str:
    return "+".join(str(ord(c)) for c in (ch or " "))


def _cell_flags(cell: Cell) -> int:
    flags = 0
    if cell.bold:
        flags |= 1 << 0
    if cell.dim:
        flags |= 1 << 1
    if cell.underline:
        flags |= 1 << 2
    if cell.blink:
        flags |= 1 << 3
    if cell.reverse:
        flags |= 1 << 4
    if cell.hidden:
        flags |= 1 << 5
    return flags


def serialize_text_state(snapshot: dict) -> bytes:
    parts = [
        MAGIC_TEXT,
        str(FORMAT_VERSION),
        str(snapshot["rows"]),
        str(snapshot["cols"]),
        str(snapshot.get("encoding", "utf-8")),
        str(snapshot.get("term", "xterm")),
    ]
    parts.extend(_codepoints(cell["ch"]) for cell in snapshot["cells"])
    return ("\n".join(parts) + "\n").encode("ascii")


def serialize_visual_state(snapshot: dict) -> bytes:
    parts = [
        MAGIC_VISUAL,
        str(FORMAT_VERSION),
        str(snapshot["rows"]),
        str(snapshot["cols"]),
        str(snapshot.get("encoding", "utf-8")),
        str(snapshot.get("term", "xterm")),
    ]
    for raw in snapshot["cells"]:
        cell = Cell(**raw)
        parts.append(f"{_codepoints(cell.ch)}|{cell.fg}|{cell.bg}|{_cell_flags(cell)}")
    return ("\n".join(parts) + "\n").encode("ascii")


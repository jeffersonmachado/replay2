from __future__ import annotations

import os
from dataclasses import dataclass


MIN_ROWS = 1
MAX_ROWS = 200
MIN_COLS = 1
MAX_COLS = 500
MAX_CELLS = 100000
SUPPORTED_ENCODINGS = {
    "utf-8": "utf-8",
    "utf8": "utf-8",
    "ascii": "utf-8",
    "cp850": "cp850",
    "ibm850": "cp850",
    "850": "cp850",
    "cp437": "cp437",
    "ibm437": "cp437",
    "437": "cp437",
    "iso-8859-1": "iso-8859-1",
    "latin-1": "iso-8859-1",
    "latin1": "latin1",
    "cp1252": "windows-1252",
    "windows-1252": "windows-1252",
    "1252": "windows-1252",
}


class TerminalGeometryError(ValueError):
    pass


@dataclass(frozen=True)
class TerminalGeometry:
    rows: int
    cols: int


def validate_terminal_geometry(rows: object, cols: object, *, max_rows: int = MAX_ROWS, max_cols: int = MAX_COLS, max_cells: int = MAX_CELLS) -> TerminalGeometry:
    if isinstance(rows, bool) or isinstance(cols, bool):
        raise TerminalGeometryError("terminal geometry must be numeric")
    if not isinstance(rows, int) or not isinstance(cols, int):
        raise TerminalGeometryError("terminal geometry must be integer")
    if rows < MIN_ROWS or cols < MIN_COLS:
        raise TerminalGeometryError("terminal geometry must be positive")
    if rows > max_rows:
        raise TerminalGeometryError("terminal rows exceed limit")
    if cols > max_cols:
        raise TerminalGeometryError("terminal cols exceed limit")
    if rows > (2**63 - 1) // cols:
        raise TerminalGeometryError("terminal geometry multiplication is unsafe")
    if rows * cols > max_cells:
        raise TerminalGeometryError("terminal cell count exceeds limit")
    return TerminalGeometry(rows=rows, cols=cols)


def coerce_terminal_geometry(rows: object, cols: object, fallback: TerminalGeometry = TerminalGeometry(25, 80)) -> TerminalGeometry:
    try:
        return validate_terminal_geometry(int(rows), int(cols))
    except Exception:
        return fallback


def normalize_encoding(value: object, fallback: str = "utf-8") -> str:
    enc = str(value or "").strip().lower()
    if not enc:
        return fallback
    return SUPPORTED_ENCODINGS.get(enc, fallback)


def is_supported_encoding(value: object) -> bool:
    enc = str(value or "").strip().lower()
    return bool(enc) and enc in SUPPORTED_ENCODINGS


def geometry_from_environment() -> tuple[TerminalGeometry | None, str]:
    try:
        rows = int(os.environ.get("LINES") or 0)
        cols = int(os.environ.get("COLUMNS") or 0)
        return validate_terminal_geometry(rows, cols), "environment"
    except Exception:
        return None, ""


def geometry_from_tty(fd: int = 0) -> tuple[TerminalGeometry | None, str]:
    try:
        size = os.get_terminal_size(fd)
        return validate_terminal_geometry(int(size.lines), int(size.columns)), "tty"
    except Exception:
        pass
    return None, ""

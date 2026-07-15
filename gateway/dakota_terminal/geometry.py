from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Geometry:
    rows: int = 25
    cols: int = 80


def validate_geometry(rows: int = 25, cols: int = 80) -> Geometry:
    """Valida geometria do terminal com verificacoes estritas de tipo.

    Aceita somente int puro. Rejeita float, string, bool, None."""
    if type(rows) is not int:
        raise TypeError(f"rows must be int, got {type(rows).__name__}")
    if type(cols) is not int:
        raise TypeError(f"cols must be int, got {type(cols).__name__}")
    if rows < 1 or cols < 1:
        raise ValueError("terminal geometry must be positive")
    if rows > 200:
        raise ValueError("terminal rows exceed limit")
    if cols > 500:
        raise ValueError("terminal cols exceed limit")
    if rows * cols > 100000:
        raise ValueError("terminal cell count exceeds limit")
    return Geometry(rows, cols)


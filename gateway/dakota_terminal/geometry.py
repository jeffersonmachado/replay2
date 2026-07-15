from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Geometry:
    rows: int = 25
    cols: int = 80


def validate_geometry(rows: int = 25, cols: int = 80) -> Geometry:
    r = int(rows or 25)
    c = int(cols or 80)
    if r < 1 or c < 1:
        raise ValueError("terminal geometry must be positive")
    if r > 200:
        raise ValueError("terminal rows exceed limit")
    if c > 500:
        raise ValueError("terminal cols exceed limit")
    if r * c > 100000:
        raise ValueError("terminal cell count exceeds limit")
    return Geometry(r, c)


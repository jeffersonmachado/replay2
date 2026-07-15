from __future__ import annotations

from dataclasses import dataclass

from .attributes import Attributes, DEFAULT_COLOR


@dataclass(frozen=True)
class Cell:
    ch: str = " "
    fg: str | int = DEFAULT_COLOR
    bg: str | int = DEFAULT_COLOR
    bold: bool = False
    dim: bool = False
    underline: bool = False
    blink: bool = False
    reverse: bool = False
    hidden: bool = False

    @classmethod
    def from_attrs(cls, ch: str, attrs: Attributes) -> "Cell":
        return cls(ch=ch or " ", **attrs.to_dict())

    def clone(self) -> "Cell":
        return Cell(**self.to_dict())

    def to_dict(self) -> dict:
        return {
            "ch": self.ch,
            "fg": self.fg,
            "bg": self.bg,
            "bold": self.bold,
            "dim": self.dim,
            "underline": self.underline,
            "blink": self.blink,
            "reverse": self.reverse,
            "hidden": self.hidden,
        }

    def attrs(self) -> Attributes:
        return Attributes(
            fg=self.fg,
            bg=self.bg,
            bold=self.bold,
            dim=self.dim,
            underline=self.underline,
            blink=self.blink,
            reverse=self.reverse,
            hidden=self.hidden,
        )


def blank_cell(attrs: Attributes | None = None) -> Cell:
    return Cell.from_attrs(" ", attrs or Attributes())


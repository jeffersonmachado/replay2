from __future__ import annotations

from dataclasses import dataclass


DEFAULT_COLOR = "default"


@dataclass(frozen=True)
class Attributes:
    fg: str | int = DEFAULT_COLOR
    bg: str | int = DEFAULT_COLOR
    bold: bool = False
    dim: bool = False
    underline: bool = False
    blink: bool = False
    reverse: bool = False
    hidden: bool = False

    def clone(self) -> "Attributes":
        return Attributes(**self.to_dict())

    def to_dict(self) -> dict:
        return {
            "fg": self.fg,
            "bg": self.bg,
            "bold": self.bold,
            "dim": self.dim,
            "underline": self.underline,
            "blink": self.blink,
            "reverse": self.reverse,
            "hidden": self.hidden,
        }

    def flags(self) -> int:
        flags = 0
        if self.bold:
            flags |= 1 << 0
        if self.dim:
            flags |= 1 << 1
        if self.underline:
            flags |= 1 << 2
        if self.blink:
            flags |= 1 << 3
        if self.reverse:
            flags |= 1 << 4
        if self.hidden:
            flags |= 1 << 5
        return flags

    def with_sgr(self, params: list[int]) -> "Attributes":
        fg: str | int = self.fg
        bg: str | int = self.bg
        bold = self.bold
        dim = self.dim
        underline = self.underline
        blink = self.blink
        reverse = self.reverse
        hidden = self.hidden

        if not params:
            params = [0]
        for p in params:
            if p == 0:
                fg = DEFAULT_COLOR
                bg = DEFAULT_COLOR
                bold = dim = underline = blink = reverse = hidden = False
            elif p == 1:
                bold = True
            elif p == 2:
                dim = True
            elif p == 4:
                underline = True
            elif p == 5:
                blink = True
            elif p == 7:
                reverse = True
            elif p == 8:
                hidden = True
            elif p == 22:
                bold = False
                dim = False
            elif p == 24:
                underline = False
            elif p == 25:
                blink = False
            elif p == 27:
                reverse = False
            elif p == 28:
                hidden = False
            elif 30 <= p <= 37:
                fg = p - 30
            elif p == 39:
                fg = DEFAULT_COLOR
            elif 40 <= p <= 47:
                bg = p - 40
            elif p == 49:
                bg = DEFAULT_COLOR
        return Attributes(fg=fg, bg=bg, bold=bold, dim=dim, underline=underline, blink=blink, reverse=reverse, hidden=hidden)


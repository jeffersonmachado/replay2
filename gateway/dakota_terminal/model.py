from __future__ import annotations

from dataclasses import dataclass

from .attributes import Attributes, DEFAULT_COLOR


# Nota de compatibilidade: NÃO usar slots=True — o AIX roda Python 3.9 e
# dataclass(slots=...) só existe a partir do 3.10 (incidente deploy 0.9.0:
# control plane não subiu no MIG24). Guardado por teste de regressão.
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
    if attrs is None:
        # Célula vazia sem atributos especiais: instância imutável (frozen)
        # compartilhada. A engine nunca muta Cell — toda escrita substitui a
        # referência na matriz (copy-on-write natural), então o
        # compartilhamento é seguro e elimina a alocação de
        # Attributes+dict+Cell por posição vazia (reset/scroll/erase).
        return DEFAULT_CELL
    return Cell.from_attrs(" ", attrs)


# Célula vazia padrão: imutável (dataclass frozen), reutilizada por todas as
# posições em branco sem atributos especiais.
DEFAULT_CELL = Cell()


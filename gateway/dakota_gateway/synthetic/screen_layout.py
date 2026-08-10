"""Layout posicional de telas extraído do código-fonte legado (.prg).

Complementa o ``ScreenExtractor`` (que monta o catálogo de telas da KB) com
uma leitura focada em POSIÇÃO: onde cada ``@ row,col GET var`` está na tela e
qual label o antecede. É o que permite mapear um input da captura para um
campo pela posição do cursor no momento da digitação.

Dois formatos de SAY são reconhecidos:

- Clássico: ``@ 04,00 SAY "Pedido"``;
- Recital/Dakota: ``@ 04,00 SAY [*] + fTraduz(p_idioma,"Pedido","P",12,...)``
  — o label visível é o 2º argumento do ``fTraduz`` (1ª string literal).

A associação label↔GET é por proximidade: o label de um GET é o SAY mais
próximo na mesma linha, à esquerda (maior coluna menor que a do GET). Nos
fontes reais os SAYs e GETs ficam em blocos separados por centenas de linhas
(ex.: est361.prg), então a associação sequencial "último SAY visto" não
funciona — só a posicional.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from ..source_analyzer.screen_extractor import _normalize_field_name

# @ 04,00 say [*] + fTraduz(p_idioma,"Pedido","P",12,.t.,"")
_RE_SAY_FTRADUZ = re.compile(
    r"@\s*(\d+)\s*,\s*(\d+)\s+say\s+(?:\[\*\]\s*\+\s*)?"
    r"fTraduz\(\s*\w+\s*,\s*\"([^\"]+)\"",
    re.IGNORECASE,
)

# @ 04,00 say "Pedido" (clássico)
_RE_SAY_QUOTED = re.compile(
    r"@\s*(\d+)\s*,\s*(\d+)\s+say\s+['\"]([^'\"]+)['\"]",
    re.IGNORECASE,
)

# @ 04,13 get cPedido pict ...
_RE_GET_POS = re.compile(
    r"@\s*(\d+)\s*,\s*(\d+)\s+get\s+([\w.]+)",
    re.IGNORECASE,
)

# PICTURE literal na linha do GET: pict "99" / pict '999.999,99' / pict "@!"
# (pict com variável — pict p_mascdoc — não carrega largura e é ignorado)
_RE_PICT_LITERAL = re.compile(
    r"pict\w*\s+(?:'([^']*)'|\"([^\"]*)\")",
    re.IGNORECASE,
)

# Distância máxima label→GET na mesma linha para associar (colunas).
_MAX_LABEL_GAP = 40


@dataclass
class PositionedField:
    """Um GET posicionado na tela, com seu label associado."""
    row: int = 0
    col: int = 0
    var: str = ""      # nome bruto do GET (ex.: cPedido)
    field: str = ""    # nome normalizado (ex.: pedido)
    label: str = ""    # label associado por proximidade (ex.: "Pedido")
    picture: str = ""  # PICTURE literal do GET (ex.: "99", "999.999,99")


def extract_layout(
    source_path: str | Path,
    line_start: int | None = None,
    line_end: int | None = None,
) -> list[PositionedField]:
    """Extrai os GETs posicionados de um fonte .prg (ou de um trecho dele).

    ``line_start``/``line_end`` são 1-based e inclusivos, no mesmo formato
    do ``source_line_start/end`` persistido nos bindings da KB. Sem eles,
    o arquivo inteiro é varrido.
    """
    path = Path(source_path)
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []

    lines = text.split("\n")
    if line_start is not None and line_end is not None:
        lines = lines[max(0, line_start - 1):line_end]

    says: list[tuple[int, int, str]] = []  # (row, col, label)
    gets: list[tuple[int, int, str, str]] = []  # (row, col, var, picture)
    for line in lines:
        m = _RE_SAY_FTRADUZ.search(line) or _RE_SAY_QUOTED.search(line)
        if m:
            label = m.group(3).strip().rstrip(":.").strip()
            if label:
                says.append((int(m.group(1)), int(m.group(2)), label))
        g = _RE_GET_POS.search(line)
        if g:
            pic = ""
            pm = _RE_PICT_LITERAL.search(line[g.end():])
            if pm:
                pic = pm.group(1) or pm.group(2) or ""
            gets.append((int(g.group(1)), int(g.group(2)), g.group(3), pic))

    fields: list[PositionedField] = []
    seen: set[tuple[int, int, str]] = set()
    for row, col, var, picture in gets:
        label = ""
        best_col = -1
        for srow, scol, slabel in says:
            if srow == row and scol < col and scol > best_col \
                    and (col - scol) <= _MAX_LABEL_GAP:
                best_col = scol
                label = slabel
        key = (row, col, var.upper())
        if key in seen:
            continue
        seen.add(key)
        fields.append(PositionedField(
            row=row, col=col, var=var,
            field=_normalize_field_name(var), label=label,
            picture=picture,
        ))
    return fields


def layout_labels(fields: list[PositionedField]) -> list[str]:
    """Labels únicos (len ≥ 3) de um layout — para casar tela ↔ fonte."""
    out: list[str] = []
    for pf in fields:
        if len(pf.label) >= 3 and pf.label not in out:
            out.append(pf.label)
    return out


def field_at(
    fields: list[PositionedField],
    row: int,
    col: int,
    *,
    exact: bool = False,
) -> PositionedField | None:
    """Campo na posição (row, col) do cursor.

    ``exact=True`` exige coluna idêntica à do GET (usado para valores de
    1-2 dígitos, que também podem ser opção de menu — só a posição exata
    do campo é evidência forte o bastante para reclassificá-los). Caso
    contrário vale o GET mais próximo à esquerda na mesma linha (o cursor
    avança enquanto o usuário digita), limitado a ``_MAX_LABEL_GAP``.
    """
    best: PositionedField | None = None
    for pf in fields:
        if pf.row != row or pf.col > col:
            continue
        if exact and pf.col != col:
            continue
        if col - pf.col > _MAX_LABEL_GAP:
            continue
        if best is None or pf.col > best.col:
            best = pf
    return best

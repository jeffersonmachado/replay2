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

# dbedit(16,00,21,69, vCamTmp, "fEst360Tabela", vPictTmp, vColTmp) — grade
# xbase: os dados de item/pagamento da tela são digitados em células da
# grade, não em `@ row,col GET`; sem este parser, inputs de grade ficam
# sem mapeamento (captura 13: produto/qtd/valor do pedido).
# top/left precisam ser numéricos; bottom/right podem ser expressão
# (ex.: lLin) — aí a grade assume o limite inferior da tela.
_RE_DBEDIT = re.compile(
    r"dbedit\(\s*(\d+)\s*,\s*(\d+)\s*,\s*([^,]+?)\s*,\s*([^,]+?)\s*,"
    r"\s*(\w+)\s*(?:,\s*[\"'](\w+)[\"']\s*)?(?:,\s*(\w+)\s*)?(?:,\s*(\w+)\s*)?\)",
    re.IGNORECASE,
)

# vCamTmp[02] = "modelo" — coluna da grade (string literal)
_RE_ARR_STR = re.compile(
    r"(\w+)\s*\[\s*(\d+)\s*\]\s*=\s*\"([^\"]*)\"",
)

# vColTmp[02] = fTraduz(p_idioma,"Modelo","P",6,.f.,"") — cabeçalho da
# coluna: o 4º argumento do fTraduz é a largura reservada ao título.
_RE_ARR_FTRADUZ = re.compile(
    r"(\w+)\s*\[\s*(\d+)\s*\]\s*=\s*(?:\[\*\]\s*\+\s*)?"
    r"fTraduz\(\s*\w+\s*,\s*\"([^\"]+)\"\s*,\s*\"?\w+\"?\s*,\s*(\d+)",
    re.IGNORECASE,
)

# use &earqtmp6 in 0 alias arqtmp6 — alias temporário que a grade edita.
_RE_USE_ALIAS = re.compile(
    r"\buse\s+\S+\s+in\s+0\s+alias\s+(\w+)", re.IGNORECASE,
)

# replace campo with est366->valor — tabela real que alimenta a grade.
_RE_REPLACE_FROM = re.compile(r"\bwith\s+(\w+)\s*->", re.IGNORECASE)

# Largura padrão de coluna de grade quando nem PICTURE nem cabeçalho
# informam tamanho (mantém o alinhamento das colunas seguintes).
_GRID_DEFAULT_WIDTH = 8

# Separador entre colunas do dbedit (1 coluna em branco).
_GRID_COL_SEP = 1


@dataclass
class PositionedField:
    """Um GET posicionado na tela, com seu label associado."""
    row: int = 0
    col: int = 0
    var: str = ""      # nome bruto do GET (ex.: cPedido)
    field: str = ""    # nome normalizado (ex.: pedido)
    label: str = ""    # label associado por proximidade (ex.: "Pedido")
    picture: str = ""  # PICTURE literal do GET (ex.: "99", "999.999,99")
    # Célula de grade dbedit: row_end/col_end delimitam o span da célula
    # (row..row_end × col..col_end). None em GETs clássicos.
    row_end: int | None = None
    col_end: int | None = None
    # Origem do campo: "form" (GET clássico do formulário) ou "grid"
    # (célula de grade dbedit). grid_source é a tabela real que alimenta
    # a grade (ex.: est366 na grade de pagamento do est361.prg), quando
    # identificada pelo preenchimento do alias temporário.
    origin: str = "form"
    grid_source: str = ""

    @property
    def is_grid_cell(self) -> bool:
        return self.row_end is not None and self.col_end is not None


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
    fields.extend(_extract_grids(lines))
    return fields


def _picture_width(picture: str) -> int:
    """Largura de exibição de uma PICTURE literal.

    ``@...`` (cláusulas de função, ex.: ``@!``, ``@R``) não carrega largura
    em si — retorna 0 e quem chama usa o cabeçalho da coluna.
    """
    pic = (picture or "").strip()
    if not pic or pic.startswith("@"):
        return 0
    return len(pic)


def _extract_grids(lines: list[str]) -> list[PositionedField]:
    """Extrai células de grades ``dbedit`` como PositionedField com span.

    Cada coluna vira uma célula ``row..row_end × col..col_end``: top/left
    vêm da chamada ``dbedit``; a largura da coluna é
    ``max(largura do cabeçalho, largura da PICTURE)`` e as colunas são
    separadas por 1 posição — geometria conferida contra capturas reais
    (est361.prg: grade de itens e grade de pagamento).

    Os arrays de coluna (``vCam*``/``vPict*``/``vCol*``) são ``public`` e
    definidos em outra função do mesmo arquivo; a associação chamada↔bloco
    é por proximidade de linha (a definição mais perto da chamada).
    """
    # 1. Coleta atribuições de arrays: nome → índice → [(linha, ...)]
    # Guarda TODAS as redefinições — arrays public são reutilizados em
    # rotinas diferentes do mesmo arquivo (est361.prg redefine vCamTmp
    # para a grade de histórico); a chamada dbedit usa a definição mais
    # próxima da sua linha.
    arr_str: dict[str, dict[int, list[tuple[int, str]]]] = {}
    arr_hdr: dict[str, dict[int, list[tuple[int, str, int]]]] = {}
    for lineno, line in enumerate(lines, 1):
        m = _RE_ARR_STR.search(line)
        if m:
            arr_str.setdefault(m.group(1), {}).setdefault(
                int(m.group(2)), []).append((lineno, m.group(3)))
        h = _RE_ARR_FTRADUZ.search(line)
        if h:
            arr_hdr.setdefault(h.group(1), {}).setdefault(
                int(h.group(2)), []).append((lineno, h.group(3), int(h.group(4))))

    def nearest(table: dict[int, list[tuple]], idx: int, call_line: int):
        """Definição mais próxima da linha da chamada dbedit."""
        entries = table.get(idx) or []
        if not entries:
            return None
        entry_line, *rest = min(entries,
                                key=lambda e: abs(e[0] - call_line))
        return (entry_line, *rest)

    # Varredura auxiliar: aliases temporários (use &x in 0 alias Y), pontos
    # `select <alias>` e preenchimentos `replace ... with tabela->campo` —
    # usados para achar a tabela real que alimenta cada grade (a grade edita
    # um alias tmp; o `with X->` logo após o `select <alias>` denuncia a
    # tabela de origem).
    alias_use: list[tuple[int, str]] = []  # (linha do use, alias)
    select_lines: list[tuple[int, str]] = []  # (linha, alias)
    replace_hits: list[tuple[int, str]] = []
    for scan_line, scan_text in enumerate(lines, 1):
        u = _RE_USE_ALIAS.search(scan_text)
        if u:
            alias_use.append((scan_line, u.group(1).lower()))
        s = re.match(r"\s*select\s+(\w+)", scan_text, re.IGNORECASE)
        if s:
            select_lines.append((scan_line, s.group(1).lower()))
        for r in _RE_REPLACE_FROM.finditer(scan_text):
            replace_hits.append((scan_line, r.group(1).lower()))
    temp_aliases = {a for _, a in alias_use}

    def grid_source_table(cam: dict[int, list[tuple[int, str]]],
                          call_line: int) -> str:
        """Tabela que alimenta a grade: o `with X->` mais frequente nos
        blocos `select <alias>` do alias temporário criado junto dos arrays
        de coluna da grade. Vazio quando não há candidato único."""
        def_lines = [e[0] for entries in cam.values() for e in entries]
        if not def_lines:
            return ""
        anchor = min(def_lines, key=lambda li: abs(li - call_line))
        # Alias temporário criado mais perto da definição dos arrays
        # (criar_dbf + use &tmp alias X ficam colados às colunas).
        near = [a for ul, a in alias_use if abs(ul - anchor) <= 80]
        if not near:
            return ""
        alias = min(near, key=lambda a: min(abs(ul - anchor)
                    for ul, au in alias_use if au == a))
        counts: dict[str, int] = {}
        for sline, salias in select_lines:
            if salias != alias:
                continue
            for rline, tbl in replace_hits:
                if sline < rline <= sline + 60 and tbl not in temp_aliases:
                    counts[tbl] = counts.get(tbl, 0) + 1
        if not counts:
            return ""
        top_n = max(counts.values())
        best = [t for t, n in counts.items() if n == top_n]
        return best[0] if len(best) == 1 else ""

    cells: list[PositionedField] = []
    for lineno, line in enumerate(lines, 1):
        d = _RE_DBEDIT.search(line)
        if not d:
            continue
        top, left = int(d.group(1)), int(d.group(2))
        try:
            bottom = int(str(d.group(3)).strip())
        except ValueError:
            bottom = 24  # limite inferior da tela quando bottom é expressão
        cam_name = d.group(5)
        pict_name = d.group(7) or ""
        col_name = d.group(8) or ""
        cam = arr_str.get(cam_name) or {}
        if not cam:
            continue
        picts = arr_str.get(pict_name) or {}
        hdrs = arr_hdr.get(col_name) or {}
        grid_source = grid_source_table(cam, lineno)

        start = left
        for idx in sorted(cam):
            cam_entry = nearest(cam, idx, lineno)
            if cam_entry is None:
                continue
            expr = cam_entry[1].strip()
            pict_entry = nearest(picts, idx, lineno)
            hdr_entry = nearest(hdrs, idx, lineno)
            picture = pict_entry[1].strip() if pict_entry else ""
            label = hdr_entry[1].strip() if hdr_entry else ""
            hdr_w = hdr_entry[2] if hdr_entry else 0
            width = max(_picture_width(picture), hdr_w)
            if width <= 0:
                width = _GRID_DEFAULT_WIDTH
            # Coluna com expressão (ex.: "right(item,2)") é só exibição —
            # não vira campo, mas ocupa sua largura para alinhar as próximas.
            if re.match(r"^\w+$", expr):
                cells.append(PositionedField(
                    row=top, col=start, var=expr,
                    field=_normalize_field_name(expr), label=label,
                    picture=picture,
                    row_end=bottom, col_end=start + width - 1,
                    origin="grid", grid_source=grid_source,
                ))
            start += width + _GRID_COL_SEP
    return cells


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
    value: str | None = None,
) -> PositionedField | None:
    """Campo na posição (row, col) do cursor.

    Ordem de prioridade:
    1. GET clássico na posição exata (mesma linha e coluna);
    2. célula de grade ``dbedit`` cujo span contém a posição — a posição
       gravada é a do cursor APÓS o eco da tecla, então cai dentro do
       span, não necessariamente no início da célula. Grades de rotinas
       diferentes se sobrepõem na mesma região da tela (est361.prg tem 8+
       dbedits sobrepostos); o desempate é:
       a. valor decimal (``229,9``) → só células com PICTURE decimal;
       b. célula mais estreita (a grade ativa tende a ter a célula mais
          justa para o dado);
       c. primeira na ordem do arquivo;
    3. (só ``exact=False``) GET mais próximo à esquerda na mesma linha,
       limitado a ``_MAX_LABEL_GAP`` — o cursor avança enquanto o usuário
       digita.

    ``exact=True`` é usado para valores de 1-2 dígitos, que também podem
    ser opção de menu: só posição exata de GET ou célula de grade é
    evidência forte o bastante para reclassificá-los.
    """
    left_best: PositionedField | None = None
    grid_hits: list[PositionedField] = []
    for pf in fields:
        if pf.is_grid_cell:
            if pf.row <= row <= (pf.row_end or pf.row) \
                    and pf.col <= col <= (pf.col_end or pf.col):
                grid_hits.append(pf)
            continue
        if pf.row != row or pf.col > col:
            continue
        if pf.col == col:
            return pf  # GET exato vence qualquer aproximação
        if exact:
            continue
        if col - pf.col > _MAX_LABEL_GAP:
            continue
        if left_best is None or pf.col > left_best.col:
            left_best = pf
    if grid_hits:
        hits = grid_hits
        stripped = (value or "").strip()
        if stripped:
            # Célula numérica (PICTURE com 9) rejeita valor não numérico —
            # cursor parado sobre a grade ao digitar tecla de ação não é
            # dado daquela célula.
            compat = [
                pf for pf in hits
                if "9" not in (pf.picture or "") or "@" in (pf.picture or "")
                or re.match(r"^[+-]?\d+([,.]\d+)?$", stripped)
            ]
            if compat:
                hits = compat
            elif any("9" in (pf.picture or "") and "@" not in (pf.picture or "")
                     for pf in hits):
                # Todas as células candidatas são numéricas e o valor não é:
                # o cursor estava só parado sobre a grade (ex.: tecla de
                # ação, opção de menu) — não há célula para este valor.
                return left_best
        if re.match(r"^\d+[,.]\d+$", stripped):
            # Decimal (229,9) → só células com PICTURE decimal
            decimal = [pf for pf in hits if "," in (pf.picture or "")]
            if decimal:
                hits = decimal
        elif stripped.isdigit():
            # Dígitos → prefere PICTURE numérica, se houver (códigos de
            # grade tipo comb/tam podem ser textuais — aí nada é filtrado)
            numeric = [pf for pf in hits if "9" in (pf.picture or "")]
            if numeric:
                hits = numeric
        hits.sort(key=lambda pf: (pf.col_end or pf.col) - pf.col)
        return hits[0]
    return left_best

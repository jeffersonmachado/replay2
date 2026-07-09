"""Screen Extractor — extrai definicoes de telas de codigo-fonte legado (v0.2.1).

Suporta:
- Multiplas telas por arquivo (READ, CLEAR, RETURN, novo TITLE como delimitadores)
- TITLE/TITULO/CAPTION antes ou durante a tela
- @ row,col SAY "Label:" GET campo (inline e multi-linha)
- GET m.campo, GET cNome, GET nValor (workarea + notacao hungara)
- PICTURE/VALID/WHEN inline no GET e em linhas separadas
- Menus numerados sem GET
- program_name do arquivo .prg se nao houver PROCEDURE/FUNCTION/PROGRAM
- source_lines corretos por tela
"""
from __future__ import annotations

import re
from pathlib import Path

from .entity_catalog import ScreenDefinition, FieldDefinition

# ── Regex patterns ──

# GET standalone com PICTURE/VALID/WHEN — captura gulosa, pos-processa
_RE_GET_FULL = re.compile(
    r"@\s+(\d+)\s*,\s*(\d+)\s+GET\s+([\w.]+)"
    r"(?:\s+PICTURE\s+(?:'([^']*)'|\"([^\"]*)\"|(@\S+)))?"
    r"(?:\s+(VALID|WHEN)\s+.+)?",
    re.IGNORECASE,
)

# Extrai clausulas individuais do restante da linha GET
_RE_CLAUSE_PICTURE = re.compile(r"PICTURE\s+(?:'([^']*)'|\"([^\"]*)\"|(@\S+))", re.IGNORECASE)
_RE_CLAUSE_VALID = re.compile(r"VALID\s+(.+?)(?=\s+(?:WHEN|ERROR|MESSAGE|PICTURE|RANGE|COLOR)\b|$)", re.IGNORECASE)
_RE_CLAUSE_WHEN = re.compile(r"WHEN\s+(.+?)(?=\s+(?:VALID|ERROR|MESSAGE|PICTURE|RANGE|COLOR)\b|$)", re.IGNORECASE)

# SAY "Label:" GET campo (inline, basico)
_RE_SAY_GET_INLINE = re.compile(
    r"@\s+(\d+)\s*,\s*(\d+)\s+SAY\s+['\"]([^'\"]+)['\"]\s+GET\s+([\w.]+)",
    re.IGNORECASE,
)
_RE_SAY_STANDALONE = re.compile(
    r"@\s+(\d+)\s*,\s*(\d+)\s+SAY\s+['\"]([^'\"]+)['\"]",
    re.IGNORECASE,
)
_RE_GET_STANDALONE = re.compile(
    r"@\s+(\d+)\s*,\s*(\d+)\s+GET\s+([\w.]+)",
    re.IGNORECASE,
)
_RE_TITLE = re.compile(
    r"(?:TITLE|TITULO|CAPTION)\s+['\"]([^'\"]+)['\"]",
    re.IGNORECASE,
)
_RE_PROGRAM_BLOCK = re.compile(
    r"(?:PROCEDURE|FUNCTION|PROGRAM)\s+(\w+)",
    re.IGNORECASE,
)
_RE_PICTURE = re.compile(
    r"PICTURE\s+(?:['\"]([^'\"]+)['\"]|(@\S+))",
    re.IGNORECASE,
)
_RE_VALID = re.compile(
    r"VALID\s+(.+?)(?:\s+(?:ERROR|MESSAGE|WHEN|PICTURE|RANGE|COLOR)\b|$)",
    re.IGNORECASE,
)
_RE_WHEN = re.compile(
    r"WHEN\s+(.+?)(?:\s+(?:VALID|ERROR|MESSAGE|PICTURE|RANGE|COLOR)\b|$)",
    re.IGNORECASE,
)
_RE_MENU_OPTION = re.compile(
    r"""@\s+\d+\s*,\s*\d+\s+SAY\s+['"]\s*(\d+)\s*[\.\-\)]\s*([^'"]*)['"]""",
    re.IGNORECASE,
)
_RE_SCREEN_LINE = re.compile(
    r"@\s+\d+\s*,\s*\d+\s+(?:SAY|GET|PROMPT)",
    re.IGNORECASE,
)

# Delimitadores de fim de tela
_RE_SCREEN_END = re.compile(
    r"^\s*(?:READ|CLEAR\s+GETS?|CLEAR\s+SCREEN|CLS|RETURN|DEFINE\s+WINDOW)\b",
    re.IGNORECASE,
)

_VAR_PREFIXES = {"m", "c", "n", "l", "d", "a", "p", "t", "x", "g"}

# Prefixo hungaro: cNome→nome, nValor→valor, dData→data, lAtivo→ativo
_HUNGARIAN_MAP = {"c": "", "n": "", "d": "", "l": "", "m": ""}


def _normalize_field_name(name: str) -> str:
    """Normaliza nome de campo: workarea prefix + notacao hungara."""
    # Workarea: m.nome → nome, cliente.cpf → cpf
    if "." in name:
        parts = name.split(".", 1)
        if parts[0].lower() in _VAR_PREFIXES and len(parts[0]) <= 2:
            return parts[1]
        # alias.campo → campo
        return parts[1]

    # Hungaro: cNome→nome, nValor→valor, dData→data, lAtivo→ativo
    if len(name) >= 2 and name[0].lower() in _HUNGARIAN_MAP:
        second = name[1]
        # So normaliza se segundo char for maiusculo (padrao hungaro)
        if second.isupper():
            return name[1].lower() + name[2:] if len(name) > 2 else name[1].lower()

    return name


def _infer_program_from_file(source_file: str) -> str:
    if not source_file:
        return ""
    return Path(source_file).stem


def _finalize_screen(current_screen, screen_start, line_no, screens, pending_title):
    """Finaliza a tela atual e a adiciona a lista."""
    if current_screen and (current_screen.fields or current_screen.title):
        current_screen.source_lines = (screen_start, line_no)
        screens.append(current_screen)
        return None, 0, "", ""
    return current_screen, screen_start, pending_title, ""


class ScreenExtractor:

    @staticmethod
    def extract(content: str, source_file: str = "") -> list[ScreenDefinition]:
        screens: list[ScreenDefinition] = []
        lines = content.split("\n")
        current_screen: ScreenDefinition | None = None
        current_program: str = _infer_program_from_file(source_file)
        screen_start = 0
        pending_title: str = ""
        last_say_label: str = ""

        for line_no, line in enumerate(lines, 1):
            stripped = line.strip()

            # ── PROCEDURE/FUNCTION/PROGRAM ──
            pm = _RE_PROGRAM_BLOCK.search(line)
            if pm:
                current_program = pm.group(1)
                continue

            # ── Fim de tela: READ, CLEAR, RETURN, novo DEFINE WINDOW ──
            if current_screen and _RE_SCREEN_END.match(stripped):
                current_screen, screen_start, pending_title, last_say_label = \
                    _finalize_screen(current_screen, screen_start, line_no, screens, pending_title)
                continue

            # ── TITLE: registra linha do titulo para source_lines ──
            tm = _RE_TITLE.search(line)
            if tm:
                title_text = tm.group(1)
                # Se ja existe tela com campos, fecha e inicia nova
                if current_screen and current_screen.fields:
                    current_screen, screen_start, pending_title, last_say_label = \
                        _finalize_screen(current_screen, screen_start, line_no - 1, screens, pending_title)

                if current_screen is None:
                    pending_title = title_text
                    screen_start = line_no  # TITLE define o inicio da tela
                elif not current_screen.title:
                    current_screen.title = title_text
                    # Ajusta source_lines.start para incluir TITLE se ainda for o default
                    if current_screen.source_lines[0] > line_no:
                        current_screen.source_lines = (line_no, current_screen.source_lines[1])
                if not _RE_SCREEN_LINE.search(line):
                    continue

            # ── Menu numerado ──
            menu_match = _RE_MENU_OPTION.search(line)
            if menu_match and not _RE_GET_STANDALONE.search(line):
                if current_screen is None:
                    current_screen = ScreenDefinition(
                        program_name=current_program, source_file=source_file,
                        source_lines=(line_no, line_no),
                    )
                    screen_start = line_no
                    if pending_title:
                        current_screen.title = pending_title
                        pending_title = ""
                current_screen.source_lines = (screen_start, line_no)
                continue

            # ── SAY "Label:" GET campo (inline) ──
            inline = _RE_SAY_GET_INLINE.search(line)
            if inline:
                row, col = int(inline.group(1)), int(inline.group(2))
                label, raw_field = inline.group(3), inline.group(4)
                current_screen, screen_start, pending_title = \
                    _ensure_screen(current_screen, current_program, source_file,
                                   line_no, screen_start, pending_title)
                field_name = _normalize_field_name(raw_field)
                _add_field(current_screen, field_name, label, row, col)

                # PICTURE/VALID/WHEN inline apos o GET (na mesma linha)
                _enrich_field_from_line(current_screen.fields[-1], line[inline.end():])
                last_say_label = label
                continue

            # ── SAY "Label:" standalone ──
            say_match = _RE_SAY_STANDALONE.search(line)
            if say_match:
                row, col = int(say_match.group(1)), int(say_match.group(2))
                label = say_match.group(3)
                current_screen, screen_start, pending_title = \
                    _ensure_screen(current_screen, current_program, source_file,
                                   line_no, screen_start, pending_title)
                last_say_label = label
                continue

            # ── GET campo standalone (com PICTURE/VALID/WHEN inline) ──
            get_full = _RE_GET_FULL.search(line)
            if get_full:
                row, col = int(get_full.group(1)), int(get_full.group(2))
                raw_field = get_full.group(3)
                current_screen, screen_start, pending_title = \
                    _ensure_screen(current_screen, current_program, source_file,
                                   line_no, screen_start, pending_title)
                field_name = _normalize_field_name(raw_field)
                prompt_text = last_say_label if last_say_label else field_name
                field = FieldDefinition(name=field_name, prompt=prompt_text, row=row, col=col)

                # PICTURE (grupos 4, 5 ou 6)
                pic = get_full.group(4) or get_full.group(5) or get_full.group(6)
                if pic:
                    field.picture = pic

                # Extrai VALID e WHEN do restante da linha usando as clausulas
                rest = line[get_full.end():] if get_full.end() < len(line) else ""
                if not rest:
                    rest = line  # fallback: busca na linha inteira

                vm = _RE_CLAUSE_VALID.search(rest) or _RE_CLAUSE_VALID.search(line)
                if vm and not field.valid_expr:
                    field.valid_expr = vm.group(1).strip()

                wm = _RE_CLAUSE_WHEN.search(rest) or _RE_CLAUSE_WHEN.search(line)
                if wm and not field.when_expr:
                    field.when_expr = wm.group(1).strip()

                # Fallback: PICTURE no restante
                if not field.picture:
                    pm2 = _RE_CLAUSE_PICTURE.search(rest)
                    if pm2:
                        field.picture = pm2.group(1) or pm2.group(2) or pm2.group(3)

                if field_name.upper() not in {f.name.upper() for f in current_screen.fields}:
                    current_screen.fields.append(field)
                continue

            # ── GET campo standalone (basico, fallback) ──
            get_match = _RE_GET_STANDALONE.search(line)
            if get_match:
                row, col = int(get_match.group(1)), int(get_match.group(2))
                raw_field = get_match.group(3)
                current_screen, screen_start, pending_title = \
                    _ensure_screen(current_screen, current_program, source_file,
                                   line_no, screen_start, pending_title)
                field_name = _normalize_field_name(raw_field)
                prompt_text = last_say_label if last_say_label else field_name
                _add_field(current_screen, field_name, prompt_text, row, col)
                continue

            # ── PICTURE, VALID, WHEN em linhas separadas ──
            if current_screen and current_screen.fields:
                _enrich_field_from_line(current_screen.fields[-1], line)

            # ── Fim de bloco por codigo de negocio ──
            if current_screen and current_screen.fields:
                s = stripped
                if re.match(r"^(?:USE|INSERT|APPEND|REPLACE|SEEK|SELECT|DO\s|IF\s|SCAN)\b",
                            s, re.IGNORECASE):
                    current_screen, screen_start, pending_title, last_say_label = \
                        _finalize_screen(current_screen, screen_start, line_no, screens, pending_title)

        # ── Finaliza ultima tela ──
        if current_screen and (current_screen.fields or current_screen.title):
            screens.append(current_screen)
        elif pending_title and not screens:
            screens.append(ScreenDefinition(
                title=pending_title, program_name=current_program,
                source_file=source_file, source_lines=(1, len(lines)),
            ))
        return screens


def _ensure_screen(cur, prog, src, line_no, start, pending_title):
    if cur is None:
        cur = ScreenDefinition(program_name=prog, source_file=src,
                               source_lines=(line_no, line_no))
        # Preserva screen_start do TITLE se ja foi definido
        if start > 0 and start < line_no:
            cur.source_lines = (start, line_no)
        else:
            start = line_no
        if pending_title:
            cur.title = pending_title
            pending_title = ""
    cur.source_lines = (cur.source_lines[0], line_no)
    return cur, start, pending_title


def _add_field(screen, name, prompt, row, col):
    if name.upper() not in {f.name.upper() for f in screen.fields}:
        screen.fields.append(FieldDefinition(name=name, prompt=prompt, row=row, col=col))


def _enrich_field_from_line(field, line):
    """Extrai PICTURE/VALID/WHEN do restante da linha e enriquece o campo."""
    pic = _RE_PICTURE.search(line)
    if pic and not field.picture:
        field.picture = pic.group(1) or pic.group(2)
    vm = _RE_VALID.search(line)
    if vm and not field.valid_expr:
        field.valid_expr = vm.group(1).strip()
    wm = _RE_WHEN.search(line)
    if wm and not field.when_expr:
        field.when_expr = wm.group(1).strip()

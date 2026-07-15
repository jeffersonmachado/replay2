from __future__ import annotations

from .attributes import Attributes
from .decoder import IncrementalDecoder, normalize_encoding
from .geometry import validate_geometry
from .model import Cell, blank_cell
from .parser import DEC_SPECIAL_GRAPHICS_MAP, parse_csi_params
from .snapshot import snapshot_from_engine


class TerminalEngine:
    engine_version = "1.0"

    def __init__(self, *, rows: int = 25, cols: int = 80, term: str = "xterm", encoding: str = "utf-8"):
        geom = validate_geometry(rows, cols)
        self.rows = geom.rows
        self.cols = geom.cols
        self.term = str(term or "xterm")
        self.encoding = normalize_encoding(encoding)
        self.decoder = IncrementalDecoder(self.encoding)
        self.bytes_seen = 0
        self.reset(reset_decoder=False)

    def reset(self, *, reset_decoder: bool = True) -> None:
        if reset_decoder:
            self.decoder.reset()
        self.cells = [[blank_cell() for _ in range(self.cols)] for _ in range(self.rows)]
        self.cursor_row = 0
        self.cursor_col = 0
        self.cursor_visible = True
        self.saved_row = 0
        self.saved_col = 0
        self.attrs = Attributes()
        self.g0_charset = "B"
        self.g1_charset = "B"
        self.shift_out = False
        self.scroll_top = 0
        self.scroll_bottom = self.rows - 1
        self.autowrap = True
        self.wrap_pending = False
        self.partial_escape = ""
        self.tab_stops = set(range(8, self.cols, 8))

    def feed_bytes(self, data: bytes) -> None:
        raw = bytes(data or b"")
        self.bytes_seen += len(raw)
        self.feed_text(self.decoder.feed(raw))

    def finish(self) -> None:
        tail = self.decoder.finalize()
        if tail:
            self.feed_text(tail)

    def feed_text(self, raw_text: str) -> None:
        text = self.partial_escape + str(raw_text or "")
        self.partial_escape = ""
        i = 0
        while i < len(text):
            ch = text[i]
            if ch == "\x1b":
                consumed = self._handle_escape(text, i)
                if consumed is None:
                    self.partial_escape = text[i:]
                    return
                i = consumed
                continue
            if ch == "\x00" or ch == "\x07":
                i += 1
                continue
            if ch == "\r":
                self.wrap_pending = False
                self.cursor_col = 0
            elif ch == "\n":
                had_wrap = self.wrap_pending
                self.wrap_pending = False
                if had_wrap:
                    self.cursor_col = 0
                self._linefeed()
            elif ch == "\b":
                self.wrap_pending = False
                self.cursor_col = max(0, self.cursor_col - 1)
            elif ch == "\t":
                self.wrap_pending = False
                stops = [s for s in self.tab_stops if s > self.cursor_col]
                if stops:
                    self.cursor_col = min(self.cols - 1, stops[0])
            elif ch == "\x0e":
                self.shift_out = True
            elif ch == "\x0f":
                self.shift_out = False
            elif ch >= " ":
                chars = ch
                if 0xD800 <= ord(ch) <= 0xDBFF and i + 1 < len(text) and 0xDC00 <= ord(text[i + 1]) <= 0xDFFF:
                    chars = ch + text[i + 1]
                    i += 1
                self._write_char(chars)
            i += 1

    def _handle_escape(self, text: str, i: int) -> int | None:
        if i + 1 >= len(text):
            return None
        nxt = text[i + 1]
        if nxt == "[":
            j = i + 2
            while j < len(text) and not ("@" <= text[j] <= "~"):
                j += 1
            if j >= len(text):
                return None
            self._handle_csi(text[i + 2:j], text[j])
            return j + 1
        if nxt == "]":
            j = i + 2
            while j < len(text) and text[j] != "\x07" and not (text[j] == "\x1b" and j + 1 < len(text) and text[j + 1] == "\\"):
                j += 1
            if j >= len(text):
                return None
            return j + 2 if text[j] == "\x1b" else j + 1
        if nxt in {"(", ")"}:
            if i + 2 >= len(text):
                return None
            if nxt == "(":
                self.g0_charset = text[i + 2]
            else:
                self.g1_charset = text[i + 2]
            return i + 3
        if nxt == "c":
            self.reset(reset_decoder=True)
        elif nxt == "D":
            self._linefeed()
        elif nxt == "E":
            self.cursor_col = 0
            self._linefeed()
        elif nxt == "M":
            self._reverse_index()
        elif nxt == "7":
            self.saved_row, self.saved_col = self.cursor_row, self.cursor_col
        elif nxt == "8":
            self._set_cursor(self.saved_row, self.saved_col)
        return i + 2

    def _handle_csi(self, params: str, final: str) -> None:
        parts = parse_csi_params(params)
        p1 = parts[0] if parts else 0
        p2 = parts[1] if len(parts) > 1 else 0
        if final == "m":
            self.attrs = self.attrs.with_sgr(parts)
        elif final in {"H", "f"}:
            self._set_cursor((p1 or 1) - 1, (p2 or 1) - 1)
        elif final == "J":
            self._erase_display(p1)
        elif final == "K":
            self._erase_line(p1)
        elif final == "A":
            self._set_cursor(self.cursor_row - (p1 or 1), self.cursor_col)
        elif final == "B":
            self._set_cursor(self.cursor_row + (p1 or 1), self.cursor_col)
        elif final == "C":
            self._set_cursor(self.cursor_row, self.cursor_col + (p1 or 1))
        elif final == "D":
            self._set_cursor(self.cursor_row, self.cursor_col - (p1 or 1))
        elif final == "s":
            self.saved_row, self.saved_col = self.cursor_row, self.cursor_col
        elif final == "u":
            self._set_cursor(self.saved_row, self.saved_col)
        elif final == "r":
            top = max(0, (p1 or 1) - 1)
            bottom = min(self.rows - 1, (p2 or self.rows) - 1)
            if top < bottom:
                self.scroll_top, self.scroll_bottom = top, bottom
                self._set_cursor(0, 0)

    def _set_cursor(self, row: int, col: int) -> None:
        self.cursor_row = max(0, min(self.rows - 1, row))
        self.cursor_col = max(0, min(self.cols - 1, col))
        self.wrap_pending = False

    def _linefeed(self) -> None:
        if self.cursor_row == self.scroll_bottom:
            self._scroll_up()
        else:
            self.cursor_row = min(self.rows - 1, self.cursor_row + 1)

    def _scroll_up(self) -> None:
        self.cells.pop(self.scroll_top)
        self.cells.insert(self.scroll_bottom, [blank_cell() for _ in range(self.cols)])
        self.cursor_row = self.scroll_bottom

    def _reverse_index(self) -> None:
        if self.cursor_row == self.scroll_top:
            self.cells.pop(self.scroll_bottom)
            self.cells.insert(self.scroll_top, [blank_cell() for _ in range(self.cols)])
        else:
            self.cursor_row = max(0, self.cursor_row - 1)

    def _erase_display(self, mode: int) -> None:
        if mode == 0:
            ranges = ((r, self.cursor_col if r == self.cursor_row else 0, self.cols - 1) for r in range(self.cursor_row, self.rows))
        elif mode == 1:
            ranges = ((r, 0, self.cursor_col if r == self.cursor_row else self.cols - 1) for r in range(0, self.cursor_row + 1))
        else:
            ranges = ((r, 0, self.cols - 1) for r in range(self.rows))
        for r, start, end in ranges:
            for c in range(start, end + 1):
                self.cells[r][c] = blank_cell()

    def _erase_line(self, mode: int) -> None:
        if mode == 0:
            start, end = self.cursor_col, self.cols - 1
        elif mode == 1:
            start, end = 0, self.cursor_col
        else:
            start, end = 0, self.cols - 1
        for c in range(start, end + 1):
            self.cells[self.cursor_row][c] = blank_cell()

    def _write_char(self, ch: str) -> None:
        if self.wrap_pending and self.autowrap:
            self.cursor_col = 0
            self._linefeed()
            self.wrap_pending = False
        if self.cursor_row >= self.rows:
            self._scroll_up()
        charset = self.g1_charset if self.shift_out else self.g0_charset
        rendered = DEC_SPECIAL_GRAPHICS_MAP.get(ch, ch) if charset == "0" else ch
        self.cells[self.cursor_row][self.cursor_col] = Cell.from_attrs(rendered, self.attrs)
        self.cursor_col += 1
        if self.cursor_col >= self.cols:
            self.cursor_col = self.cols - 1
            if self.autowrap:
                self.wrap_pending = True

    def resize(self, rows: int, cols: int) -> None:
        geom = validate_geometry(rows, cols)
        old = self.cells
        self.rows, self.cols = geom.rows, geom.cols
        self.cells = [[old[r][c] if r < len(old) and c < len(old[r]) else blank_cell() for c in range(self.cols)] for r in range(self.rows)]
        self.scroll_top = 0
        self.scroll_bottom = self.rows - 1
        self.tab_stops = set(range(8, self.cols, 8))
        self._set_cursor(self.cursor_row, self.cursor_col)

    def text(self) -> str:
        return "\n".join("".join(cell.ch for cell in row) for row in self.cells)

    def snapshot(self) -> dict:
        return snapshot_from_engine(self)


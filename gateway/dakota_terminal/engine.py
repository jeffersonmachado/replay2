from __future__ import annotations

from .attributes import Attributes
from .decoder import TerminalDecoder, normalize_encoding
from .geometry import validate_geometry
from .model import Cell, blank_cell
from .parser import DEC_SPECIAL_GRAPHICS_MAP, parse_csi_params
from .snapshot import snapshot_from_engine


class TerminalEngine:
    engine_version = "1.0"

    def __init__(self, *, rows: int = 25, cols: int = 80, term: str = "xterm", encoding: str = "utf-8", session_id: str = ""):
        geom = validate_geometry(rows, cols)
        self.rows = geom.rows
        self.cols = geom.cols
        self.term = str(term or "xterm")
        self.encoding = normalize_encoding(encoding)
        self.decoder = TerminalDecoder(self.encoding, session_id=session_id)
        self.bytes_seen = 0
        self.seq_global = 0
        self._decode_seq_global = 0
        self._decode_direction = "out"
        # Estado do scanner byte-a-byte
        self._escape_state: str = "normal"  # normal, esc, csi, osc, esc_seq
        self._csi_params: str = ""
        self._osc_params: str = ""
        self._text_buffer: bytearray = bytearray()
        self.reset(reset_decoder=False)

    def reset(self, *, reset_decoder: bool = True) -> None:
        if reset_decoder:
            self.decoder.reset(seq_global=self._decode_seq_global, direction=self._decode_direction)
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
        # Reset scanner state
        self._escape_state = "normal"
        self._csi_params = ""
        self._osc_params = ""
        self._text_buffer = bytearray()

    def _flush_text_buffer(self) -> None:
        """Decodifica buffer de texto acumulado e alimenta feed_text."""
        if self._text_buffer:
            text = self.decoder.feed(
                bytes(self._text_buffer),
                seq_global=self._decode_seq_global,
                direction=self._decode_direction,
            )
            self._text_buffer = bytearray()
            if text:
                self.feed_text(text)

    def feed_bytes(self, data: bytes, *, seq_global: int = 0, direction: str = "out", session_id: str | None = None) -> None:
        """Scanner byte-a-byte: separa texto de sequencias de controle.

        Bytes normais sao acumulados em buffer de texto e decodificados
        incrementalmente. Sequencias de controle (ESC, CSI, OSC) sao
        processadas como texto (via feed_text) para que o parser existente
        as interprete corretamente. RIS (ESC c) reseta o estado.
        """
        raw = bytes(data or b"")
        self._decode_seq_global = int(seq_global or 0)
        if self._decode_seq_global > 0:
            self.seq_global = self._decode_seq_global
        self._decode_direction = str(direction or "out")
        if session_id is not None:
            self.decoder.session_id = str(session_id or "")
        self.bytes_seen += len(raw)

        for b in raw:
            byte_int = b  # 0-255

            if self._escape_state == "normal":
                if byte_int == 0x1b:  # ESC
                    self._flush_text_buffer()
                    self._escape_state = "esc"
                elif byte_int < 0x20 and byte_int not in (0x09, 0x0a, 0x0d):  # C0 controls (exceto TAB, LF, CR)
                    self._flush_text_buffer()
                    self.feed_text(chr(byte_int))
                else:
                    self._text_buffer.append(byte_int)

            elif self._escape_state == "esc":
                if byte_int == 0x5b:  # '['
                    self._escape_state = "csi"
                    self._csi_params = ""
                elif byte_int == 0x5d:  # ']'
                    self._escape_state = "osc"
                    self._osc_params = ""
                elif byte_int == 0x63:  # 'c' → RIS
                    self.reset(reset_decoder=True)
                    self._escape_state = "normal"
                else:
                    # Outra sequencia ESC: manda como texto para o parser
                    self.feed_text("\x1b" + chr(byte_int))
                    self._escape_state = "normal"

            elif self._escape_state == "csi":
                self._csi_params += chr(byte_int)
                if 0x40 <= byte_int <= 0x7e:  # final byte
                    self.feed_text("\x1b[" + self._csi_params)
                    self._escape_state = "normal"
                    self._csi_params = ""

            elif self._escape_state == "osc":
                self._osc_params += chr(byte_int)
                if byte_int == 0x07:  # BEL terminator
                    self.feed_text("\x1b]" + self._osc_params)
                    self._escape_state = "normal"
                    self._osc_params = ""
                elif byte_int == 0x1b:  # Possible ST terminator (ESC \)
                    self._escape_state = "osc_st"

            elif self._escape_state == "osc_st":
                if byte_int == 0x5c:  # '\' → ST terminator
                    self._osc_params += "\x1b\\"
                    self.feed_text("\x1b]" + self._osc_params)
                    self._escape_state = "normal"
                    self._osc_params = ""
                else:
                    # Not a ST, voltar para OSC
                    self._osc_params += "\x1b" + chr(byte_int)
                    self._escape_state = "osc"

        # Flush remaining text at end of chunk
        self._flush_text_buffer()

    def finish(self, *, seq_global: int = 0, direction: str = "out", session_id: str | None = None) -> None:
        final_seq = int(seq_global or 0)
        if final_seq > 0:
            self.seq_global = final_seq
        if session_id is not None:
            self.decoder.session_id = str(session_id or "")
        tail = self.decoder.finalize(seq_global=final_seq, direction=str(direction or "out"))
        if tail:
            self.feed_text(tail)

    def finalize(self) -> None:
        self.finish()

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
        elif final == "t" and p1 == 8 and p2 > 0:
            # CSI 8;rows;cols t — resize terminal
            if len(parts) >= 3:
                resize_cols = parts[2]
                try:
                    self.resize(p2, resize_cols)
                except (ValueError, TypeError):
                    pass

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

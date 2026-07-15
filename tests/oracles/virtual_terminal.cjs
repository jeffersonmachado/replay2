/**
 * Virtual Terminal — incremental ANSI/DEC terminal emulator.
 *
 * Responsibilities:
 *   feed(term, chunk)   — incremental byte feed (callable multiple times)
 *   term.cells          — canonical matrix (rows × cols)
 *   renderPlainText     — diagnostic text (preserves empty lines, trailing spaces)
 *   renderHtml          — HTML with reverse-video spans
 *
 * Never pass renderPlainText output back into feed().
 *
 * Works as both ES module (import) and plain script (window.DakVT).
 */
(function (root, factory) {
  if (typeof module === 'object' && typeof module.exports === 'object') {
    module.exports = factory();
  } else if (typeof define === 'function' && define.amd) {
    define([], factory);
  } else {
    root.DakVT = factory();
  }
}(typeof self !== 'undefined' ? self : typeof window !== 'undefined' ? window : this, function () {
'use strict';

const DEC_SPECIAL_GRAPHICS_MAP = {
  l: '\u250C', k: '\u2510', m: '\u2514', j: '\u2518',
  q: '\u2500', x: '\u2502', t: '\u251C', u: '\u2524',
  v: '\u2534', w: '\u252C', n: '\u253C',
};

const TERMINAL_GEOMETRY_LIMITS = Object.freeze({
  minRows: 1,
  maxRows: 200,
  minCols: 1,
  maxCols: 500,
  maxCells: 100000,
});

function makeCell(ch) {
  return { ch: ch || ' ', fg: null, bg: null, bold: false, dim: false, underline: false, blink: false, reverse: false, hidden: false };
}

function validateTerminalGeometry(rows, cols, limits) {
  var lim = Object.assign({}, TERMINAL_GEOMETRY_LIMITS, limits || {});
  var r = Number(rows);
  var c = Number(cols);
  if (typeof rows === 'string' || typeof cols === 'string') throw new RangeError('terminal geometry must be numeric');
  if (!Number.isFinite(r) || !Number.isFinite(c)) throw new RangeError('terminal geometry must be finite');
  if (!Number.isInteger(r) || !Number.isInteger(c)) throw new RangeError('terminal geometry must be integer');
  if (r < lim.minRows || c < lim.minCols) throw new RangeError('terminal geometry must be positive');
  if (r > lim.maxRows) throw new RangeError('terminal rows exceed limit');
  if (c > lim.maxCols) throw new RangeError('terminal cols exceed limit');
  if (r > Math.floor(Number.MAX_SAFE_INTEGER / c)) throw new RangeError('terminal geometry multiplication is unsafe');
  if (r * c > lim.maxCells) throw new RangeError('terminal cell count exceeds limit');
  return { rows: r, cols: c, limits: lim };
}

function createVirtualTerminal(rows = 25, cols = 80) {
  const geometry = validateTerminalGeometry(rows, cols);
  rows = geometry.rows;
  cols = geometry.cols;
  const cells = Array.from({ length: rows }, () =>
    Array.from({ length: cols }, () => makeCell(' '))
  );
  return {
    rows,
    cols,
    term: 'xterm',
    encoding: 'utf-8',
    cursorRow: 0,
    cursorCol: 0,
    savedRow: 0,
    savedCol: 0,
    graphicsMode: false,  // legacy — current effective graphics charset
    g0Charset: 'B',       // G0: 'B' = US ASCII, '0' = DEC Special Graphics
    g1Charset: 'B',       // G1: same
    shiftOut: false,      // true = using G1 (SO/^N), false = using G0 (SI/^O)
    partialEscape: '',
    wrapPending: false,
    cells,
  };
}

function vtReset(term) {
  for (let r = 0; r < term.rows; r++)
    for (let c = 0; c < term.cols; c++)
      term.cells[r][c] = makeCell(' ');
  term.cursorRow = 0; term.cursorCol = 0;
  term.savedRow = 0; term.savedCol = 0;
  term.graphicsMode = false; term.wrapPending = false;
  term.g0Charset = 'B'; term.g1Charset = 'B'; term.shiftOut = false;
  term.partialEscape = '';
  term._fg = undefined; term._bg = undefined;
  term._bold = false; term._dim = false; term._underline = false;
  term._blink = false; term._reverse = false; term._hidden = false;
  resetDecoder(term);
}

function vtScroll(term) {
  term.cells.shift();
  term.cells.push(Array.from({ length: term.cols }, () => makeCell(' ')));
  term.cursorRow = term.rows - 1;
}

function vtSetCursor(term, row, col) {
  term.cursorRow = Math.max(0, Math.min(term.rows - 1, row));
  term.cursorCol = Math.max(0, Math.min(term.cols - 1, col));
  term.wrapPending = false;
}

function vtWriteChar(term, ch) {
  // Handle wrapPending: if the last char was at the rightmost column, wrap first
  if (term.wrapPending) {
    term.cursorCol = 0;
    term.cursorRow += 1;
    if (term.cursorRow >= term.rows) vtScroll(term);
    term.wrapPending = false;
  }
  if (term.cursorRow >= term.rows) vtScroll(term);
  if (term.cursorCol >= term.cols) {
    term.wrapPending = true;
    return;
  }
  const effectiveCharset = term.shiftOut ? term.g1Charset : term.g0Charset;
  const rendered = (effectiveCharset === '0' && DEC_SPECIAL_GRAPHICS_MAP[ch]) ? DEC_SPECIAL_GRAPHICS_MAP[ch] : ch;
  const cell = term.cells[term.cursorRow][term.cursorCol];
  cell.ch = rendered;
  // Copy current SGR state into the cell
  cell.reverse = term._reverse || false;
  cell.bold = term._bold || false;
  cell.dim = term._dim || false;
  cell.underline = term._underline || false;
  cell.blink = term._blink || false;
  cell.hidden = term._hidden || false;
  if (term._fg !== undefined) cell.fg = term._fg;
  if (term._bg !== undefined) cell.bg = term._bg;
  term.cursorCol += 1;
  if (term.cursorCol >= term.cols) {
    term.cursorCol = term.cols - 1;
    term.wrapPending = true;
  }
}

function vtTab(term) {
  const tabStop = 8;
  const nextStop = (Math.floor(term.cursorCol / tabStop) + 1) * tabStop;
  if (nextStop < term.cols) {
    term.cursorCol = nextStop;
  }
}

function vtEraseDisplay(term, mode) {
  if (mode === 0) {
    for (let r = term.cursorRow; r < term.rows; r++) {
      const startCol = r === term.cursorRow ? term.cursorCol : 0;
      for (let c = startCol; c < term.cols; c++) {
        term.cells[r][c] = makeCell(' ');
      }
    }
  } else if (mode === 1) {
    for (let r = 0; r <= term.cursorRow; r++) {
      const endCol = r === term.cursorRow ? term.cursorCol : term.cols - 1;
      for (let c = 0; c <= endCol; c++) {
        term.cells[r][c] = makeCell(' ');
      }
    }
  } else {
    for (let r = 0; r < term.rows; r++) {
      for (let c = 0; c < term.cols; c++) {
        term.cells[r][c] = makeCell(' ');
      }
    }
  }
}

function vtEraseLine(term, mode) {
  if (mode === 0) {
    for (let c = term.cursorCol; c < term.cols; c++) {
      term.cells[term.cursorRow][c] = makeCell(' ');
    }
  } else if (mode === 1) {
    for (let c = 0; c <= term.cursorCol; c++) {
      term.cells[term.cursorRow][c] = makeCell(' ');
    }
  } else {
    for (let c = 0; c < term.cols; c++) {
      term.cells[term.cursorRow][c] = makeCell(' ');
    }
  }
}

function vtHandleCsi(term, params, finalChar) {
  const parts = String(params || '').split(';').map((part) => Number(part || 0));
  const p1 = parts[0] || 0;
  const p2 = parts[1] || 0;
  if (finalChar === 'm') {
    for (const p of parts) {
      if (p === 0) {
        term._fg = undefined; term._bg = undefined;
        term._bold = false; term._dim = false; term._blink = false;
        term._underline = false; term._reverse = false; term._hidden = false;
      }
      if (p === 1) term._bold = true;
      if (p === 2) term._dim = true;   // dim/faint
      if (p === 4) term._underline = true;
      if (p === 5) term._blink = true;   // blink
      if (p === 7) term._reverse = true;
      if (p === 8) term._hidden = true;
      if (p === 22) { term._bold = false; term._dim = false; } // normal intensity
      if (p === 24) term._underline = false;
      if (p === 25) term._blink = false;
      if (p === 27) term._reverse = false;
      if (p === 28) term._hidden = false;
      if (p >= 30 && p <= 37) term._fg = p - 30;
      if (p === 39) term._fg = undefined;
      if (p >= 40 && p <= 47) term._bg = p - 40;
      if (p === 49) term._bg = undefined;
    }
    return;
  }
  if (finalChar === 'H' || finalChar === 'f') {
    vtSetCursor(term, Math.max(0, (p1 || 1) - 1), Math.max(0, (p2 || 1) - 1));
    return;
  }
  if (finalChar === 'J') { vtEraseDisplay(term, p1); return; }
  if (finalChar === 'K') { vtEraseLine(term, p1); return; }
  if (finalChar === 'A') { vtSetCursor(term, term.cursorRow - (p1 || 1), term.cursorCol); return; }
  if (finalChar === 'B') { vtSetCursor(term, term.cursorRow + (p1 || 1), term.cursorCol); return; }
  if (finalChar === 'C') { vtSetCursor(term, term.cursorRow, term.cursorCol + (p1 || 1)); return; }
  if (finalChar === 'D') { vtSetCursor(term, term.cursorRow, term.cursorCol - (p1 || 1)); return; }
  if (finalChar === 's') { term.savedRow = term.cursorRow; term.savedCol = term.cursorCol; return; }
  if (finalChar === 'u') { vtSetCursor(term, term.savedRow, term.savedCol); return; }
  // Unknown CSI — consumed silently
}

function vtInd(term) {
  // IND — index: move down one line, scroll if needed, preserve column
  term.cursorRow += 1;
  if (term.cursorRow >= term.rows) { vtScroll(term); term.cursorRow = term.rows - 1; }
}

function vtNel(term) {
  // NEL — next line: column 0, move down one line
  term.cursorCol = 0;
  term.cursorRow += 1;
  if (term.cursorRow >= term.rows) vtScroll(term);
}

function vtRi(term) {
  // RI — reverse index: move up one line, scroll down if at top
  if (term.cursorRow <= 0) {
    term.cells.pop();
    term.cells.unshift(Array.from({ length: term.cols }, () => makeCell(' ')));
  } else {
    term.cursorRow -= 1;
  }
}

/**
 * Feed raw bytes into the terminal. Can be called incrementally.
 * Handles split CSI sequences, split DEC charset, isolated ESC, etc.
 */
function feed(term, input) {
  let text = String(input || '');
  if (term.partialEscape) {
    text = term.partialEscape + text;
    term.partialEscape = '';
  }
  let i = 0;
  while (i < text.length) {
    const ch = text[i];
    if (ch === '\x1b') {
      const next = text[i + 1] || '';
      if (next === '[') {
        let j = i + 2;
        while (j < text.length && !/[@-~]/.test(text[j])) j += 1;
        if (j < text.length) {
          vtHandleCsi(term, text.slice(i + 2, j), text[j]);
          i = j + 1;
          continue;
        }
        term.partialEscape = text.slice(i);
        return;
      }
      if (next === ']') {
        // OSC — skip to terminator
        let j = i + 2;
        while (j < text.length && text[j] !== '\x07' && !(text[j] === '\x1b' && text[j + 1] === '\\')) j += 1;
        if (j < text.length) {
          i = text[j] === '\x1b' ? j + 2 : j + 1;
          continue;
        }
        term.partialEscape = text.slice(i);
        return;
      }
      if (next === '(' || next === ')') {
        if (i + 2 < text.length) {
          const charset = text[i + 2];
          if (next === '(') { term.g0Charset = charset; } // designate G0
          else { term.g1Charset = charset; }              // designate G1
          term.graphicsMode = (next === '(' ? charset : term.g0Charset) === '0'; // legacy compat
          i += 3;
          continue;
        }
        term.partialEscape = text.slice(i);
        return;
      }
      if (next === '7') { term.savedRow = term.cursorRow; term.savedCol = term.cursorCol; i += 2; continue; }
      if (next === '8') { vtSetCursor(term, term.savedRow, term.savedCol); i += 2; continue; }
      if (next === '=' || next === '>') { i += 2; continue; }
      if (next === 'M') { vtRi(term); i += 2; continue; } // RI — reverse index
      if (next === 'c') {
        vtReset(term);
        i += 2; continue;
      }
      if (next === 'D') { vtInd(term); i += 2; continue; } // IND — index
      if (next === 'E') { vtNel(term); i += 2; continue; } // NEL — next line
      if (!next) { term.partialEscape = text.slice(i); return; } // isolated ESC at end
      // Unknown ESC + one char — consume both
      i += 2;
      continue;
    }
    if (ch === '\t') { term.wrapPending = false; vtTab(term); i += 1; continue; }
    if (ch === '\n') {
      const hadWrapPending = term.wrapPending;
      term.wrapPending = false;
      if (hadWrapPending) term.cursorCol = 0;
      term.cursorRow += 1;
      if (term.cursorRow >= term.rows) vtScroll(term);
      i += 1;
      continue;
    }
    if (ch === '\r') { term.wrapPending = false; term.cursorCol = 0; i += 1; continue; }
    if (ch === '\b') { term.wrapPending = false; term.cursorCol = Math.max(0, term.cursorCol - 1); i += 1; continue; }
    if (ch === '\x0e') { term.shiftOut = true; i += 1; continue; }  // SO — use G1
    if (ch === '\x0f') { term.shiftOut = false; i += 1; continue; } // SI — use G0
    if (ch >= ' ') {
      var outCh = ch;
      if (ch >= '\uD800' && ch <= '\uDBFF' && i + 1 < text.length && text[i + 1] >= '\uDC00' && text[i + 1] <= '\uDFFF') {
        outCh = ch + text[i + 1];
        i += 1;
      }
      vtWriteChar(term, outCh);
    }
    i += 1;
  }
}

/**
 * Canonical plain-text rendering of the terminal matrix.
 * Preserves empty lines and trailing spaces — dimensions are not altered.
 */
function renderPlainText(term) {
  return term.cells.map((row) => row.map((c) => c.ch).join('')).join('\n');
}

/**
 * HTML rendering with semantic span classes for cell attributes.
 * Groups consecutive cells with identical effective attributes.
 */
function renderHtml(term) {
  const lines = term.cells.map((row) => {
    let out = '';
    let inSpan = null;
    for (const cell of row) {
      const ch = escapeHtml(cell.ch);
      // Calcula cores efetivas (null = default)
      let effectiveFg = cell.reverse ? cell.bg : cell.fg;
      let effectiveBg = cell.reverse ? cell.fg : cell.bg;
      const classes = [];
      if (effectiveFg !== null && effectiveFg !== undefined) classes.push('vt-fg-' + effectiveFg);
      if (effectiveBg !== null && effectiveBg !== undefined) classes.push('vt-bg-' + effectiveBg);
      if (cell.bold) classes.push('vt-bold');
      if (cell.dim) classes.push('vt-dim');
      if (cell.underline) classes.push('vt-underline');
      if (cell.blink) classes.push('vt-blink');
      if (cell.reverse) classes.push('vt-reverse');
      if (cell.hidden) classes.push('vt-hidden');
      const cls = classes.join(' ') || '';
      if (cls !== inSpan) {
        if (inSpan) out += '</span>';
        if (cls) out += '<span class="' + cls + '">';
        inSpan = cls || null;
      }
      out += ch;
    }
    if (inSpan) out += '</span>';
    return out;
  });
  return lines.join('\n');
}

/**
 * Compact text rendering for timeline preview — strips trailing whitespace
 * and collapses excessive blank lines. Use ONLY for diagnostic display.
 */
function renderCompactText(term) {
  const lines = term.cells.map((row) =>
    row.map((c) => c.ch).join('').replace(/[ \t]+$/g, '')
  );
  let lastEmpty = false;
  const result = [];
  for (const line of lines) {
    const trimmed = line.trimEnd();
    if (!trimmed) {
      if (!lastEmpty) result.push('');
      lastEmpty = true;
    } else {
      result.push(trimmed);
      lastEmpty = false;
    }
  }
  while (result.length && !result[result.length - 1]) result.pop();
  return result.join('\n');
}

function escapeHtml(text) {
  return String(text || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

/**
 * Timing helper — returns timestamp_ms or ts_ms as fallback.
 */
function eventTimestamp(ev) {
  return Number(ev?.timestamp_ms ?? ev?.ts_ms ?? 0);
}

/**
 * Calculate delay to next event: next.timestamp - current.timestamp, divided by speed.
 */
function calcDelay(currentEvent, nextEvent, speed = 1) {
  const currTs = eventTimestamp(currentEvent);
  const nextTs = eventTimestamp(nextEvent);
  if (!currTs || !nextTs || nextTs <= currTs) return 50;
  return Math.max(5, Math.min(2000, (nextTs - currTs) / speed));
}

/**
 * Returns true if the terminal screen is entirely blank.
 */
function isBlank(term) {
  for (let r = 0; r < term.rows; r++) {
    for (let c = 0; c < term.cols; c++) {
      if (term.cells[r][c].ch !== ' ') return false;
    }
  }
  return true;
}

/**
 * Incremental UTF-8 decoder. Feed bytes one at a time via feedUtf8.
 * Returns the decoded string so far (may be incomplete at chunk boundary).
 */
function createUtf8Decoder() {
  var state = { buf: [], needed: 0 };
  return {
    feed: function(byte) {
      state.buf.push(byte);
      if (state.needed === 0) {
        if (byte < 0x80) { state.needed = 1; }
        else if ((byte & 0xE0) === 0xC0) { state.needed = 2; }
        else if ((byte & 0xF0) === 0xE0) { state.needed = 3; }
        else if ((byte & 0xF8) === 0xF0) { state.needed = 4; }
        else { state.buf = []; return '\uFFFD'; }
      }
      if (state.buf.length === state.needed) {
        var bytes = state.buf;
        state.buf = []; state.needed = 0;
        try {
          var decoder = new TextDecoder('utf-8', { fatal: true });
          return decoder.decode(new Uint8Array(bytes));
        } catch (e) {
          return '\uFFFD';
        }
      }
      return '';
    },
    reset: function() { state.buf = []; state.needed = 0; }
  };
}

/**
 * Feed base64-encoded bytes into the terminal using an incremental decoder.
 * Solves the split-UTF-8 problem: C3 in one event + A1 in next = á.
 *
 * @param {Object} term - virtual terminal
 * @param {string} dataB64 - base64-encoded bytes
 * @param {string} [encoding='utf-8'] - character encoding (utf-8, cp850, cp437, iso-8859-1, windows-1252)
 */
function feedBase64(term, dataB64, encoding) {
  if (!dataB64) return;
  var enc = encoding || 'utf-8';
  term.encoding = (enc === 'utf8') ? 'utf-8' : enc;

  // Para UTF-8, usar decoder incremental que resolve bytes divididos
  if (enc === 'utf-8' || enc === 'utf8') {
    if (!term._utf8decoder) term._utf8decoder = createUtf8Decoder();
    var decoder = term._utf8decoder;
    try {
      var raw = atob(dataB64);
      for (var i = 0; i < raw.length; i++) {
        var ch = raw.charCodeAt(i);
        if (ch < 0x20 || ch === 0x7F) {
          decoder.reset();
          feed(term, String.fromCharCode(ch));
        } else {
          var decoded = decoder.feed(ch);
          if (decoded) feed(term, decoded);
        }
      }
    } catch (e) {
      // Invalid base64 — ignore
    }
    return;
  }

  // Para encodings single-byte (cp850, cp437, iso-8859-1, windows-1252),
  // cada byte é um caractere completo — não há divisão multi-byte
  try {
    var raw = atob(dataB64);
    var text = decodeSingleByte(raw, enc);
    feed(term, text);
  } catch (e) {
    // Invalid base64 — ignore
  }
}

function bytesFromBase64(dataB64) {
  if (!dataB64) return new Uint8Array(0);
  var raw = atob(dataB64);
  var bytes = new Uint8Array(raw.length);
  for (var i = 0; i < raw.length; i++) bytes[i] = raw.charCodeAt(i) & 0xFF;
  return bytes;
}

function decodeBase64Text(dataB64, encoding) {
  if (!dataB64) return '';
  var enc = String(encoding || 'utf-8').toLowerCase();
  var raw = atob(dataB64);
  if (enc === 'utf-8' || enc === 'utf8' || enc === 'ascii') {
    try { return new TextDecoder('utf-8', { fatal: false }).decode(bytesFromBase64(dataB64)); }
    catch (e) { return raw; }
  }
  return decodeSingleByte(raw, enc);
}

/**
 * Decodifica string binaria usando encoding single-byte.
 * Suporta: cp850, cp437, iso-8859-1, windows-1252.
 */
function decodeSingleByte(binaryStr, encoding) {
  if (!binaryStr) return '';
  try {
    // Tenta usar TextDecoder quando disponivel
    var decoder = new TextDecoder(encoding, { fatal: false });
    var bytes = new Uint8Array(binaryStr.length);
    for (var i = 0; i < binaryStr.length; i++) {
      bytes[i] = binaryStr.charCodeAt(i) & 0xFF;
    }
    return decoder.decode(bytes);
  } catch (e) {
    // Fallback: tabela manual para CP850 (Latin-1 extendido)
    return fallbackDecode(binaryStr, encoding);
  }
}

/**
 * Fallback manual para encodings single-byte.
 * CP850 e CP437 mapeiam bytes 0x80-0xFF para caracteres especificos.
 */
var CP850_TABLE = null;
var CP437_TABLE = null;

function buildCp850Table() {
  // CP850: mapeamento dos bytes 0x80-0xFF
  var t = {};
  var chars = '\u00C7\u00FC\u00E9\u00E2\u00E4\u00E0\u00E5\u00E7\u00EA\u00EB\u00E8\u00EF\u00EE\u00EC\u00C4\u00C5' +
    '\u00C9\u00E6\u00C6\u00F4\u00F6\u00F2\u00FB\u00F9\u00FF\u00D6\u00DC\u00F8\u00A3\u00D8\u00D7\u0192' +
    '\u00E1\u00ED\u00F3\u00FA\u00F1\u00D1\u00AA\u00BA\u00BF\u00AE\u00AC\u00BD\u00BC\u00A1\u00AB\u00BB' +
    '\u2591\u2592\u2593\u2502\u2524\u00C1\u00C2\u00C0\u00A9\u2563\u2551\u2557\u255D\u00A2\u00A5\u2510' +
    '\u2514\u2534\u252C\u251C\u2500\u253C\u00E3\u00C3\u255A\u2554\u2569\u2566\u2560\u2550\u256C\u00A4' +
    '\u00F0\u00D0\u00CA\u00CB\u00C8\u0131\u00CD\u00CE\u00CF\u2518\u250C\u2588\u2584\u00A6\u00CC\u2580' +
    '\u00D3\u00DF\u00D4\u00D2\u00F5\u00D5\u00B5\u00FE\u00DE\u00DA\u00DB\u00D9\u00FD\u00DD\u00AF\u00B4' +
    '\u00AD\u00B1\u2017\u00BE\u00B6\u00A7\u00F7\u00B8\u00B0\u00A8\u00B7\u00B9\u00B3\u00B2\u25A0\u00A0';
  for (var i = 0; i < 128; i++) {
    t[0x80 + i] = chars.charAt(i);
  }
  return t;
}

function buildCp437Table() {
  var t = {};
  var chars = '\u00C7\u00FC\u00E9\u00E2\u00E4\u00E0\u00E5\u00E7\u00EA\u00EB\u00E8\u00EF\u00EE\u00EC\u00C4\u00C5' +
    '\u00C9\u00E6\u00C6\u00F4\u00F6\u00F2\u00FB\u00F9\u00FF\u00D6\u00DC\u00A2\u00A3\u00A5\u20A7\u0192' +
    '\u00E1\u00ED\u00F3\u00FA\u00F1\u00D1\u00AA\u00BA\u00BF\u2310\u00AC\u00BD\u00BC\u00A1\u00AB\u00BB' +
    '\u2591\u2592\u2593\u2502\u2524\u2561\u2562\u2556\u2555\u2563\u2551\u2557\u255D\u255C\u255B\u2510' +
    '\u2514\u2534\u252C\u251C\u2500\u253C\u255E\u255F\u255A\u2554\u2569\u2566\u2560\u2550\u256C\u2567' +
    '\u2568\u2564\u2565\u2559\u2558\u2552\u2553\u256B\u256A\u2518\u250C\u2588\u2584\u258C\u2590\u2580' +
    '\u03B1\u00DF\u0393\u03C0\u03A3\u03C3\u00B5\u03C4\u03A6\u0398\u03A9\u03B4\u221E\u03C6\u03B5\u2229' +
    '\u2261\u00B1\u2265\u2264\u2320\u2321\u00F7\u2248\u00B0\u2219\u00B7\u221A\u207F\u00B2\u25A0\u00A0';
  for (var i = 0; i < 128; i++) {
    t[0x80 + i] = chars.charAt(i);
  }
  return t;
}

function fallbackDecode(binaryStr, encoding) {
  var table;
  if (encoding === 'cp850' || encoding === 'ibm850') {
    if (!CP850_TABLE) CP850_TABLE = buildCp850Table();
    table = CP850_TABLE;
  } else if (encoding === 'cp437' || encoding === 'ibm437') {
    if (!CP437_TABLE) CP437_TABLE = buildCp437Table();
    table = CP437_TABLE;
  }
  // iso-8859-1 e windows-1252: bytes 0x80-0x9F são caracteres de controle
  // mas muitos sistemas usam como parte de windows-1252

  var result = '';
  for (var i = 0; i < binaryStr.length; i++) {
    var byte = binaryStr.charCodeAt(i) & 0xFF;
    if (byte < 0x80) {
      result += String.fromCharCode(byte);
    } else if (table && table[byte]) {
      result += table[byte];
    } else if (encoding === 'iso-8859-1' || encoding === 'latin1') {
      // ISO-8859-1: byte direto para Unicode
      result += String.fromCharCode(byte);
    } else {
      // windows-1252: bytes 0x80-0x9F têm mapeamento especial
      result += decodeWindows1252Byte(byte);
    }
  }
  return result;
}

var WIN1252_TABLE = null;
function buildWin1252Table() {
  var t = {};
  var chars = '\u20AC\u0081\u201A\u0192\u201E\u2026\u2020\u2021\u02C6\u2030\u0160\u2039\u0152\u008D\u017D\u008F' +
    '\u0090\u2018\u2019\u201C\u201D\u2022\u2013\u2014\u02DC\u2122\u0161\u203A\u0153\u009D\u017E\u0178';
  for (var i = 0; i < 32; i++) {
    t[0x80 + i] = chars.charAt(i);
  }
  return t;
}

function decodeWindows1252Byte(byte) {
  if (byte < 0x80) return String.fromCharCode(byte);
  if (byte >= 0xA0) return String.fromCharCode(byte);
  if (!WIN1252_TABLE) WIN1252_TABLE = buildWin1252Table();
  return WIN1252_TABLE[byte] || String.fromCharCode(byte);
}

/**
 * Reseta o decoder do terminal — usado ao reiniciar playback ou mudar de sessao.
 */
function resetDecoder(term) {
  if (term._utf8decoder) {
    term._utf8decoder.reset();
  }
}

/**
 * Generate a deterministic text signature from the canonical matrix.
 * Includes geometry (rows, cols) and all cells including spaces.
 */
function screenSig(term) {
  return 'sha256:' + sha256Hex(serializeTextState(renderSnapshot(term)));
}

/**
 * Generate a deterministic visual signature from cell attributes.
 * Includes geometry and all cell attributes.
 */
function visualSig(term) {
  return 'sha256:' + sha256Hex(serializeVisualState(renderSnapshot(term)));
}

function cellFlags(cell) {
  var flags = 0;
  if (cell.bold) flags |= 1 << 0;
  if (cell.dim) flags |= 1 << 1;
  if (cell.underline) flags |= 1 << 2;
  if (cell.blink) flags |= 1 << 3;
  if (cell.reverse) flags |= 1 << 4;
  if (cell.hidden) flags |= 1 << 5;
  return flags;
}

function codepoints(ch) {
  return Array.from(String(ch || ' ')).map((c) => String(c.codePointAt(0))).join('+');
}

function serializeTextState(snapshot) {
  var parts = [
    'DKT-TEXT',
    '1',
    String(snapshot.rows),
    String(snapshot.cols),
    String(snapshot.encoding || 'utf-8'),
    String(snapshot.term || 'xterm'),
  ];
  for (const cell of snapshot.cells || []) parts.push(codepoints(cell.ch));
  return parts.join('\n') + '\n';
}

function serializeVisualState(snapshot) {
  var parts = [
    'DKT-VISUAL',
    '1',
    String(snapshot.rows),
    String(snapshot.cols),
    String(snapshot.encoding || 'utf-8'),
    String(snapshot.term || 'xterm'),
  ];
  for (const cell of snapshot.cells || []) {
    parts.push(codepoints(cell.ch) + '|' + cell.fg + '|' + cell.bg + '|' + cellFlags(cell));
  }
  return parts.join('\n') + '\n';
}

/**
 * Render canonical snapshot with full cell attributes.
 * Returns a serializable object (not HTML), suitable for:
 * - deterministic signatures
 * - cloning
 * - serialization to JSON
 * - rendering to HTML on demand
 */
function renderSnapshot(term) {
  var cells = [];
  for (var r = 0; r < term.rows; r++) {
    for (var c = 0; c < term.cols; c++) {
      var cell = term.cells[r][c];
      cells.push({
        ch: cell.ch,
        fg: cell.fg !== null && cell.fg !== undefined ? cell.fg : 'default',
        bg: cell.bg !== null && cell.bg !== undefined ? cell.bg : 'default',
        bold: !!cell.bold,
        dim: !!cell.dim,
        underline: !!cell.underline,
        blink: !!cell.blink,
        reverse: !!cell.reverse,
        hidden: !!cell.hidden,
      });
    }
  }
  var snapshot = {
    version: 1,
    engine_version: '1.0',
    rows: term.rows,
    cols: term.cols,
    term: term.term || 'xterm',
    encoding: term.encoding || 'utf-8',
    cursor: {
      row: term.cursorRow,
      col: term.cursorCol,
      visible: true,
      wrap_pending: !!term.wrapPending,
    },
    saved_cursor: { row: term.savedRow, col: term.savedCol },
    attributes: {
      fg: term._fg !== null && term._fg !== undefined ? term._fg : 'default',
      bg: term._bg !== null && term._bg !== undefined ? term._bg : 'default',
      bold: !!term._bold,
      dim: !!term._dim,
      underline: !!term._underline,
      blink: !!term._blink,
      reverse: !!term._reverse,
      hidden: !!term._hidden,
    },
    g0_charset: term.g0Charset === '0' ? 'dec_special' : 'ascii',
    g1_charset: term.g1Charset === '0' ? 'dec_special' : 'ascii',
    active_charset: term.shiftOut ? 'g1' : 'g0',
    scroll_region: { top: 0, bottom: term.rows - 1 },
    cells: cells,
  };
  snapshot.text_sig = 'sha256:' + sha256Hex(serializeTextState(snapshot));
  snapshot.visual_sig = 'sha256:' + sha256Hex(serializeVisualState(snapshot));
  return snapshot;
}

/**
 * Render HTML from a canonical snapshot (produced by renderSnapshot).
 * Groups consecutive cells with identical effective attributes.
 */
function renderSnapshotHtml(snapshot) {
  if (!snapshot || !snapshot.cells) return '';
  var lines = [];
  var idx = 0;
  for (var r = 0; r < snapshot.rows; r++) {
    var lineOut = '';
    var inSpan = null;
    for (var c = 0; c < snapshot.cols; c++) {
      var cell = snapshot.cells[idx++];
      var ch = escapeHtml(cell.ch);
      var effectiveFg = cell.reverse ? cell.bg : cell.fg;
      var effectiveBg = cell.reverse ? cell.fg : cell.bg;
      var classes = [];
      if (effectiveFg !== 'default' && effectiveFg !== null && effectiveFg !== undefined) classes.push('vt-fg-' + effectiveFg);
      if (effectiveBg !== 'default' && effectiveBg !== null && effectiveBg !== undefined) classes.push('vt-bg-' + effectiveBg);
      if (cell.bold) classes.push('vt-bold');
      if (cell.dim) classes.push('vt-dim');
      if (cell.underline) classes.push('vt-underline');
      if (cell.blink) classes.push('vt-blink');
      if (cell.reverse) classes.push('vt-reverse');
      if (cell.hidden) classes.push('vt-hidden');
      var cls = classes.join(' ') || '';
      if (cls !== inSpan) {
        if (inSpan) lineOut += '</span>';
        if (cls) lineOut += '<span class="' + cls + '">';
        inSpan = cls || null;
      }
      lineOut += ch;
    }
    if (inSpan) lineOut += '</span>';
    lines.push(lineOut);
  }
  return lines.join('\n');
}

function sha256Hex(str) {
  if (typeof require === 'function') {
    try { return require('node:crypto').createHash('sha256').update(str, 'utf8').digest('hex'); }
    catch (e) {}
  }
  return sha256HexPure(str);
}

function utf8Bytes(str) {
  if (typeof TextEncoder !== 'undefined') return Array.from(new TextEncoder().encode(str));
  var bytes = [];
  for (var i = 0; i < str.length; i++) {
    var code = str.charCodeAt(i);
    if (code >= 0xD800 && code <= 0xDBFF && i + 1 < str.length) {
      var next = str.charCodeAt(i + 1);
      if (next >= 0xDC00 && next <= 0xDFFF) {
        code = 0x10000 + ((code - 0xD800) << 10) + (next - 0xDC00);
        i += 1;
      }
    }
    if (code < 0x80) {
      bytes.push(code);
    } else if (code < 0x800) {
      bytes.push(0xC0 | (code >>> 6), 0x80 | (code & 0x3F));
    } else if (code < 0x10000) {
      bytes.push(0xE0 | (code >>> 12), 0x80 | ((code >>> 6) & 0x3F), 0x80 | (code & 0x3F));
    } else {
      bytes.push(0xF0 | (code >>> 18), 0x80 | ((code >>> 12) & 0x3F), 0x80 | ((code >>> 6) & 0x3F), 0x80 | (code & 0x3F));
    }
  }
  return bytes;
}

function sha256HexPure(str) {
  var bytes = utf8Bytes(String(str));
  var bitLenHi = Math.floor(bytes.length / 0x20000000);
  var bitLenLo = (bytes.length << 3) >>> 0;
  bytes.push(0x80);
  while ((bytes.length % 64) !== 56) bytes.push(0);
  for (var s = 24; s >= 0; s -= 8) bytes.push((bitLenHi >>> s) & 0xFF);
  for (var t = 24; t >= 0; t -= 8) bytes.push((bitLenLo >>> t) & 0xFF);

  var h0 = 0x6a09e667, h1 = 0xbb67ae85, h2 = 0x3c6ef372, h3 = 0xa54ff53a;
  var h4 = 0x510e527f, h5 = 0x9b05688c, h6 = 0x1f83d9ab, h7 = 0x5be0cd19;
  var k = [
    0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5,0x3956c25b,0x59f111f1,0x923f82a4,0xab1c5ed5,
    0xd807aa98,0x12835b01,0x243185be,0x550c7dc3,0x72be5d74,0x80deb1fe,0x9bdc06a7,0xc19bf174,
    0xe49b69c1,0xefbe4786,0x0fc19dc6,0x240ca1cc,0x2de92c6f,0x4a7484aa,0x5cb0a9dc,0x76f988da,
    0x983e5152,0xa831c66d,0xb00327c8,0xbf597fc7,0xc6e00bf3,0xd5a79147,0x06ca6351,0x14292967,
    0x27b70a85,0x2e1b2138,0x4d2c6dfc,0x53380d13,0x650a7354,0x766a0abb,0x81c2c92e,0x92722c85,
    0xa2bfe8a1,0xa81a664b,0xc24b8b70,0xc76c51a3,0xd192e819,0xd6990624,0xf40e3585,0x106aa070,
    0x19a4c116,0x1e376c08,0x2748774c,0x34b0bcb5,0x391c0cb3,0x4ed8aa4a,0x5b9cca4f,0x682e6ff3,
    0x748f82ee,0x78a5636f,0x84c87814,0x8cc70208,0x90befffa,0xa4506ceb,0xbef9a3f7,0xc67178f2
  ];
  var w = new Array(64);
  function rotr(x, n) { return (x >>> n) | (x << (32 - n)); }
  function hex(x) { return ('00000000' + (x >>> 0).toString(16)).slice(-8); }

  for (var i = 0; i < bytes.length; i += 64) {
    for (var j = 0; j < 16; j++) {
      var p = i + j * 4;
      w[j] = ((bytes[p] << 24) | (bytes[p + 1] << 16) | (bytes[p + 2] << 8) | bytes[p + 3]) >>> 0;
    }
    for (var n = 16; n < 64; n++) {
      var s0 = (rotr(w[n - 15], 7) ^ rotr(w[n - 15], 18) ^ (w[n - 15] >>> 3)) >>> 0;
      var s1 = (rotr(w[n - 2], 17) ^ rotr(w[n - 2], 19) ^ (w[n - 2] >>> 10)) >>> 0;
      w[n] = (w[n - 16] + s0 + w[n - 7] + s1) >>> 0;
    }
    var a = h0, b = h1, c = h2, d = h3, e = h4, f = h5, g = h6, h = h7;
    for (var r = 0; r < 64; r++) {
      var S1 = (rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25)) >>> 0;
      var ch = ((e & f) ^ ((~e) & g)) >>> 0;
      var temp1 = (h + S1 + ch + k[r] + w[r]) >>> 0;
      var S0 = (rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22)) >>> 0;
      var maj = ((a & b) ^ (a & c) ^ (b & c)) >>> 0;
      var temp2 = (S0 + maj) >>> 0;
      h = g; g = f; f = e; e = (d + temp1) >>> 0;
      d = c; c = b; b = a; a = (temp1 + temp2) >>> 0;
    }
    h0 = (h0 + a) >>> 0; h1 = (h1 + b) >>> 0; h2 = (h2 + c) >>> 0; h3 = (h3 + d) >>> 0;
    h4 = (h4 + e) >>> 0; h5 = (h5 + f) >>> 0; h6 = (h6 + g) >>> 0; h7 = (h7 + h) >>> 0;
  }
  return hex(h0) + hex(h1) + hex(h2) + hex(h3) + hex(h4) + hex(h5) + hex(h6) + hex(h7);
}

return {
  TERMINAL_GEOMETRY_LIMITS, validateTerminalGeometry,
  createVirtualTerminal, feed, feedBase64, renderPlainText, renderHtml, renderCompactText,
  renderSnapshot, renderSnapshotHtml, resetDecoder, decodeBase64Text,
  eventTimestamp, calcDelay, isBlank, screenSig, visualSig, serializeTextState, serializeVisualState,
};
}));

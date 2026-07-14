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

function makeCell(ch) {
  return { ch: ch || ' ', fg: null, bg: null, bold: false, dim: false, underline: false, blink: false, reverse: false, hidden: false };
}

function createVirtualTerminal(rows = 25, cols = 80) {
  const cells = Array.from({ length: rows }, () =>
    Array.from({ length: cols }, () => makeCell(' '))
  );
  return {
    rows,
    cols,
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
  if (term.cursorCol >= term.cols) term.wrapPending = true;
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
        // RIS — full reset
        for (let r = 0; r < term.rows; r++)
          for (let c = 0; c < term.cols; c++)
            term.cells[r][c] = makeCell(' ');
        term.cursorRow = 0; term.cursorCol = 0;
        term.graphicsMode = false; term.wrapPending = false;
        term.g0Charset = 'B'; term.g1Charset = 'B'; term.shiftOut = false;
        term._fg = undefined; term._bg = undefined;
        term._bold = false; term._underline = false;
        term._reverse = false; term._hidden = false;
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
      term.wrapPending = false;
      term.cursorCol = 0;    // LF reseta coluna
      term.cursorRow += 1;
      if (term.cursorRow >= term.rows) vtScroll(term);
      i += 1;
      continue;
    }
    if (ch === '\r') { term.wrapPending = false; term.cursorCol = 0; i += 1; continue; }
    if (ch === '\b') { term.wrapPending = false; term.cursorCol = Math.max(0, term.cursorCol - 1); i += 1; continue; }
    if (ch === '\x0e') { term.shiftOut = true; i += 1; continue; }  // SO — use G1
    if (ch === '\x0f') { term.shiftOut = false; i += 1; continue; } // SI — use G0
    if (ch >= ' ') vtWriteChar(term, ch);
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
  var parts = ['v1', String(term.rows), String(term.cols)];
  for (var r = 0; r < term.rows; r++) {
    var row = '';
    for (var c = 0; c < term.cols; c++) {
      row += term.cells[r][c].ch;
    }
    parts.push(row);
  }
  return simpleHash(parts.join('\n'));
}

/**
 * Generate a deterministic visual signature from cell attributes.
 * Includes geometry and all cell attributes.
 */
function visualSig(term) {
  var parts = ['v1', String(term.rows), String(term.cols)];
  for (var r = 0; r < term.rows; r++) {
    var rowParts = [];
    for (var c = 0; c < term.cols; c++) {
      var cell = term.cells[r][c];
      var flags = 0;
      if (cell.reverse) flags |= 1;
      if (cell.bold) flags |= 2;
      if (cell.underline) flags |= 4;
      if (cell.dim) flags |= 8;
      if (cell.hidden) flags |= 16;
      if (cell.blink) flags |= 32;
      var fg = cell.fg !== null ? cell.fg : -1;
      var bg = cell.bg !== null ? cell.bg : -1;
      rowParts.push(cell.ch + ':' + flags + ':' + fg + ':' + bg);
    }
    parts.push(rowParts.join(','));
  }
  return simpleHash(parts.join('\n'));
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
  return {
    version: 1,
    rows: term.rows,
    cols: term.cols,
    cells: cells,
  };
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

function simpleHash(str) {
  // djb2 hash para compatibilidade (determinístico, cross-platform)
  var hash = 5381;
  for (var i = 0; i < str.length; i++) {
    hash = ((hash << 5) + hash) + str.charCodeAt(i);
    hash = hash & hash; // Convert to 32bit integer
  }
  return (hash >>> 0).toString(16);
}

return {
  createVirtualTerminal, feed, feedBase64, renderPlainText, renderHtml, renderCompactText,
  renderSnapshot, renderSnapshotHtml, resetDecoder,
  eventTimestamp, calcDelay, isBlank, screenSig, visualSig,
};
}));

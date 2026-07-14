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
  return { ch: ch || ' ', fg: 0, bg: 0, bold: false, underline: false, reverse: false, hidden: false };
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
    graphicsMode: false,
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
  const rendered = term.graphicsMode && DEC_SPECIAL_GRAPHICS_MAP[ch] ? DEC_SPECIAL_GRAPHICS_MAP[ch] : ch;
  const cell = term.cells[term.cursorRow][term.cursorCol];
  cell.ch = rendered;
  // Copy current SGR state into the cell
  cell.reverse = term._reverse || false;
  cell.bold = term._bold || false;
  cell.underline = term._underline || false;
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
        term._bold = false; term._dim = false;
        term._underline = false; term._reverse = false; term._hidden = false;
      }
      if (p === 1) term._bold = true;
      if (p === 2) term._dim = true;   // dim/faint
      if (p === 4) term._underline = true;
      if (p === 5) {} // blink — no-op
      if (p === 7) term._reverse = true;
      if (p === 8) term._hidden = true;
      if (p === 22) { term._bold = false; term._dim = false; } // normal intensity
      if (p === 24) term._underline = false;
      if (p === 27) term._reverse = false;
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
          term.graphicsMode = text[i + 2] === '0';
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
      // Calcula cores efetivas
      let effectiveFg = cell.reverse ? (cell.bg || 0) : (cell.fg || 0);
      let effectiveBg = cell.reverse ? (cell.fg || 0) : (cell.bg || 0);
      const classes = [];
      if (effectiveFg) classes.push('vt-fg-' + effectiveFg);
      if (effectiveBg) classes.push('vt-bg-' + effectiveBg);
      if (cell.bold) classes.push('vt-bold');
      if (cell.dim) classes.push('vt-dim');
      if (cell.underline) classes.push('vt-underline');
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
 * Feed base64-encoded bytes into the terminal using an incremental UTF-8 decoder.
 * Solves the split-UTF-8 problem: C3 in one event + A1 in next = á.
 */
function feedBase64(term, dataB64) {
  if (!dataB64) return;
  if (!term._utf8decoder) term._utf8decoder = createUtf8Decoder();
  var decoder = term._utf8decoder;
  try {
    var raw = atob(dataB64);
    for (var i = 0; i < raw.length; i++) {
      var ch = raw.charCodeAt(i);
      // Control characters pass through directly
      if (ch < 0x20 || ch === 0x7F) {
        // Flush decoder before control char
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
}

/**
 * Generate a deterministic text signature from the canonical matrix.
 */
function screenSig(term) {
  var lines = term.cells.map(function(row) {
    return row.map(function(c) { return c.ch; }).join('').replace(/[ \t]+$/g, '');
  });
  // Simple hash
  var str = lines.join('\n');
  var hash = 0;
  for (var i = 0; i < str.length; i++) {
    var ch = str.charCodeAt(i);
    hash = ((hash << 5) - hash) + ch;
    hash |= 0;
  }
  return (hash >>> 0).toString(16);
}

/**
 * Generate a deterministic visual signature from cell attributes.
 * Captures reverse, bold, underline, dim, hidden, fg, bg.
 */
function visualSig(term) {
  var parts = [];
  for (var r = 0; r < term.rows; r++) {
    for (var c = 0; c < term.cols; c++) {
      var cell = term.cells[r][c];
      if (cell.ch === ' ' && !cell.reverse && !cell.bold && !cell.underline && !cell.dim && !cell.hidden && !cell.fg && !cell.bg) {
        continue; // empty cell sem atributos: nao contribui
      }
      var flags = 0;
      if (cell.reverse) flags |= 1;
      if (cell.bold) flags |= 2;
      if (cell.underline) flags |= 4;
      if (cell.dim) flags |= 8;
      if (cell.hidden) flags |= 16;
      parts.push(r + ',' + c + ':' + cell.ch + ',' + flags + ',' + (cell.fg || 0) + ',' + (cell.bg || 0));
    }
  }
  var str = parts.join(';');
  var hash = 0;
  for (var i = 0; i < str.length; i++) {
    hash = ((hash << 5) - hash) + str.charCodeAt(i);
    hash |= 0;
  }
  return (hash >>> 0).toString(16);
}

return {
  createVirtualTerminal, feed, feedBase64, renderPlainText, renderHtml, renderCompactText,
  eventTimestamp, calcDelay, isBlank, screenSig, visualSig,
};
}));

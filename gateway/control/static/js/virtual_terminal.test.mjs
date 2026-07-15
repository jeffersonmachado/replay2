/**
 * virtual_terminal.test.mjs — 16 tests for the virtual terminal emulator.
 * Run: node --test gateway/control/static/js/virtual_terminal.test.mjs
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const oraclePath = path.resolve(__dirname, '../../../../tests/oracles/virtual_terminal.cjs');
const require = createRequire(import.meta.url);
const vt = require(oraclePath);

function loadBrowserSandboxVt() {
  const source = readFileSync(oraclePath, 'utf8');
  const sandbox = {
    self: {},
    TextEncoder,
    TextDecoder,
    Uint8Array,
    ArrayBuffer,
    console,
  };
  vm.runInNewContext(source, sandbox, { filename: 'virtual_terminal.cjs' });
  return sandbox.self.DakVT;
}

const {
  createVirtualTerminal,
  feed,
  feedBase64,
  renderPlainText,
  renderHtml,
  renderCompactText,
  renderSnapshot,
  renderSnapshotHtml,
  eventTimestamp,
  calcDelay,
  isBlank,
  screenSig,
  visualSig,
  validateTerminalGeometry,
} = vt;

// ── helpers ────────────────────────────────────────────────────────────────

function esc(s) {
  return s.replace(/ESC/g, '\x1b');
}

function cellMatrix(term) {
  return term.cells;
}

// ── TEST 1 — reverse video does not create markers ─────────────────────────
test('TEST 1 — reverse video does not create markers', () => {
  const t = createVirtualTerminal(25, 80);
  feed(t, esc('ESC[7mClientesESC[0m'));
  const text = renderPlainText(t);
  assert.ok(!text.includes('\u2588'), 'no block characters');
  assert.equal(text.substring(0, 8).trim(), 'Clientes');

  // 8 cells written
  let count = 0;
  let revCount = 0;
  for (const cell of t.cells[0]) {
    if (cell.ch !== ' ') { count++; if (cell.reverse === true) revCount++; }
  }
  assert.equal(count, 8, '8 cells written');
  assert.equal(revCount, 8, 'all 8 cells have reverse=true');
});

// ── TEST 2 — reverse spaces preserved ──────────────────────────────────────
test('TEST 2 — reverse spaces preserved', () => {
  const t = createVirtualTerminal(25, 80);
  feed(t, esc('ESC[7mClientes   ESC[0m'));
  const line = t.cells[0].map((c) => c.ch).join('');
  assert.equal(line.substring(0, 11), 'Clientes   ');
  assert.equal(t.cells[0][8].ch, ' ');
  assert.equal(t.cells[0][8].reverse, true, 'trailing space has reverse=true');
  assert.equal(t.cells[0][9].ch, ' ');
  assert.equal(t.cells[0][9].reverse, true);
  assert.equal(t.cells[0][10].ch, ' ');
  assert.equal(t.cells[0][10].reverse, true);
});

// ── TEST 3 — empty lines preserved ─────────────────────────────────────────
test('TEST 3 — empty lines preserved', () => {
  const t = createVirtualTerminal(25, 80);
  feed(t, 'A\n\nB');
  const lines = renderPlainText(t).split('\n');
  assert.equal(lines[0][0], 'A');
  // line 1 must be all spaces (empty but preserved)
  assert.ok(lines[1].trim() === '', 'line 1 is empty');
  // B was written at row 2, cursorCol continued from row 0 after two \n
  assert.ok(lines[2].includes('B'), 'line 2 contains B');
  assert.equal(lines.length, 25, '25 lines total');
});

// ── TEST 4 — ESC isolated between chunks ───────────────────────────────────
test('TEST 4 — ESC isolated between chunks', () => {
  const t = createVirtualTerminal(25, 80);
  feed(t, '\x1b');          // isolated ESC
  feed(t, '[7mX');          // completes CSI
  feed(t, '\x1b');          // isolated ESC
  feed(t, '[0m');           // completes CSI
  assert.equal(t.cells[0][0].ch, 'X');
  assert.equal(t.cells[0][0].reverse, true, 'reverse=true after ESC[7m');
  const html = renderHtml(t);
  assert.ok(!html.includes('[7m'), 'no literal [7m in html');
});

// ── TEST 5 — CSI split in multiple points ──────────────────────────────────
test('TEST 5 — CSI split in multiple points', () => {
  const t1 = createVirtualTerminal(25, 80);
  feed(t1, esc('ESC[7mABCESC[0m'));

  const t2 = createVirtualTerminal(25, 80);
  feed(t2, '\x1b');
  feed(t2, '[');
  feed(t2, '7');
  feed(t2, 'm');
  feed(t2, 'ABC');
  feed(t2, '\x1b');
  feed(t2, '[');
  feed(t2, '0');
  feed(t2, 'm');

  assert.equal(renderPlainText(t1), renderPlainText(t2), 'identical output');
  assert.equal(t1.cells[0][0].reverse, t2.cells[0][0].reverse);
  assert.equal(t1.cells[0][0].ch, t2.cells[0][0].ch);
});

// ── TEST 6 — DEC charset split ────────────────────────────────────────────
test('TEST 6 — DEC charset split', () => {
  const t = createVirtualTerminal(25, 80);
  feed(t, '\x1b');
  feed(t, '(');
  feed(t, '0');
  feed(t, 'lqqk');
  const line = t.cells[0].map((c) => c.ch).join('');
  assert.equal(line.substring(0, 4), '\u250C\u2500\u2500\u2510', 'box top');
});

// ── TEST 7 — CSI 2J does not move cursor ───────────────────────────────────
test('TEST 7 — CSI 2J does not move cursor', () => {
  const t = createVirtualTerminal(25, 80);
  t.cursorRow = 2;
  t.cursorCol = 4;
  t.cells[2][4] = { ch: 'X', fg: 0, bg: 0, bold: false, underline: false, reverse: false, hidden: false };
  feed(t, esc('ESC[2J'));
  assert.equal(t.cursorRow, 2, 'cursorRow preserved after 2J');
  assert.equal(t.cursorCol, 4, 'cursorCol preserved after 2J');
  // Screen cleared
  assert.equal(t.cells[2][4].ch, ' ', 'cell cleared');
});

// ── TEST 8 — CSI 0J, 1J, 2J ───────────────────────────────────────────────
test('TEST 8 — CSI 0J, 1J and 2J', () => {
  // 0J: clear from cursor to end
  const t0 = createVirtualTerminal(3, 5);
  for (let r = 0; r < 3; r++) for (let c = 0; c < 5; c++) t0.cells[r][c] = { ch: 'X', fg: 0, bg: 0, bold: false, underline: false, reverse: true, hidden: false };
  t0.cursorRow = 1; t0.cursorCol = 2;
  feed(t0, esc('ESC[0J'));
  assert.equal(t0.cells[0][0].ch, 'X'); // row 0 untouched
  assert.equal(t0.cells[0][4].ch, 'X');
  assert.equal(t0.cells[1][0].ch, 'X'); // row 1 before cursor
  assert.equal(t0.cells[1][1].ch, 'X');
  assert.equal(t0.cells[1][2].ch, ' ', 'cleared');
  assert.equal(t0.cells[1][4].ch, ' ', 'cleared');
  assert.equal(t0.cells[2][0].ch, ' ', 'row 2 cleared');

  // 1J: clear from start to cursor
  const t1 = createVirtualTerminal(3, 5);
  for (let r = 0; r < 3; r++) for (let c = 0; c < 5; c++) t1.cells[r][c] = { ch: 'X', fg: 0, bg: 0, bold: false, underline: false, reverse: true, hidden: false };
  t1.cursorRow = 1; t1.cursorCol = 2;
  feed(t1, esc('ESC[1J'));
  assert.equal(t1.cells[0][0].ch, ' ', 'row 0 cleared');
  assert.equal(t1.cells[1][2].ch, ' ', 'cursor cell cleared');
  assert.equal(t1.cells[1][3].ch, 'X', 'row 1 after cursor untouched');
  assert.equal(t1.cells[2][0].ch, 'X', 'row 2 untouched');

  // 2J: clear entire screen
  const t2 = createVirtualTerminal(3, 5);
  for (let r = 0; r < 3; r++) for (let c = 0; c < 5; c++) t2.cells[r][c] = { ch: 'X', fg: 0, bg: 0, bold: false, underline: false, reverse: true, hidden: false };
  t2.cursorRow = 1; t2.cursorCol = 2;
  feed(t2, esc('ESC[2J'));
  for (let r = 0; r < 3; r++) for (let c = 0; c < 5; c++) assert.equal(t2.cells[r][c].ch, ' ');
  assert.equal(t2.cursorRow, 1);
  assert.equal(t2.cursorCol, 2);
});

// ── TEST 9 — CSI 0K, 1K, 2K ───────────────────────────────────────────────
test('TEST 9 — CSI 0K, 1K and 2K', () => {
  // 0K: clear from cursor to end of line
  const t0 = createVirtualTerminal(3, 5);
  for (let c = 0; c < 5; c++) { t0.cells[0][c] = { ch: 'X', fg: 0, bg: 0, bold: false, underline: false, reverse: true, hidden: false }; }
  t0.cursorRow = 0; t0.cursorCol = 2;
  feed(t0, esc('ESC[0K'));
  assert.equal(t0.cells[0][0].ch, 'X'); assert.equal(t0.cells[0][0].reverse, true);
  assert.equal(t0.cells[0][1].ch, 'X');
  assert.equal(t0.cells[0][2].ch, ' ');
  assert.equal(t0.cells[0][4].ch, ' ');

  // 1K: clear from start to cursor
  const t1 = createVirtualTerminal(3, 5);
  for (let c = 0; c < 5; c++) t1.cells[0][c] = { ch: 'X', fg: 0, bg: 0, bold: false, underline: false, reverse: true, hidden: false };
  t1.cursorRow = 0; t1.cursorCol = 2;
  feed(t1, esc('ESC[1K'));
  assert.equal(t1.cells[0][0].ch, ' ');
  assert.equal(t1.cells[0][2].ch, ' ');
  assert.equal(t1.cells[0][3].ch, 'X');

  // 2K: clear entire line
  const t2 = createVirtualTerminal(3, 5);
  for (let c = 0; c < 5; c++) t2.cells[0][c] = { ch: 'X', fg: 0, bg: 0, bold: false, underline: false, reverse: true, hidden: false };
  feed(t2, esc('ESC[2K'));
  for (let c = 0; c < 5; c++) assert.equal(t2.cells[0][c].ch, ' ');
});

// ── TEST 10 — scroll synchronized ──────────────────────────────────────────
test('TEST 10 — scroll synchronized', () => {
  const t = createVirtualTerminal(3, 5);
  // Fill screen
  for (let r = 0; r < 3; r++) {
    for (let c = 0; c < 5; c++) {
      t.cells[r][c] = { ch: 'X', fg: 0, bg: 0, bold: false, underline: false, reverse: true, hidden: false };
    }
  }
  t.cursorRow = 2; t.cursorCol = 0;
  feed(t, '\n'); // triggers scroll
  assert.equal(t.cells[0][0].ch, 'X', 'row shifted up');
  assert.equal(t.cells[0][0].reverse, true, 'attr shifted up with char');
  assert.equal(t.cells[2][0].ch, ' ', 'new row blank');
  assert.equal(t.cells[2][0].reverse, false, 'new row attr blank');
});

// ── TEST 11 — fixed dimensions ─────────────────────────────────────────────
test('TEST 11 — fixed dimensions', () => {
  const t = createVirtualTerminal(25, 80);
  assert.equal(t.rows, 25);
  assert.equal(t.cols, 80);
  assert.equal(t.cells.length, 25);
  for (const row of t.cells) assert.equal(row.length, 80);

  feed(t, 'A\n\nB');
  const text = renderPlainText(t);
  assert.equal(text.split('\n').length, 25, 'still 25 lines after render');
  // Empty line preserved: second line should be all spaces
  const lines = text.split('\n');
  assert.ok(lines[1].trim() === '' && lines[1].length === 80, 'empty line preserved at full width');
});

// ── TEST 12 — timing ──────────────────────────────────────────────────────
test('TEST 12 — timing', () => {
  const events = [
    { timestamp_ms: 1000 },
    { timestamp_ms: 1250 },
    { timestamp_ms: 2250 },
  ];
  assert.equal(calcDelay(events[0], events[1], 1), 250);
  assert.equal(calcDelay(events[1], events[2], 1), 1000);
  assert.equal(calcDelay(events[0], events[1], 2), 125);
  assert.equal(calcDelay(events[1], events[2], 2), 500);
  // Fallback
  assert.equal(calcDelay({}, {}, 1), 50);
  // ts_ms fallback
  assert.equal(calcDelay({ ts_ms: 100 }, { ts_ms: 200 }, 1), 100);
});

// ── TEST 13 — grouping without duplication ─────────────────────────────────
test('TEST 13 — grouping without duplication', () => {
  // Simulated grouping logic: events with gap > 700ms form new group
  const events = [
    { timestamp_ms: 1000, text: 'A' },
    { timestamp_ms: 1100, text: 'B' },
    { timestamp_ms: 3000, text: 'C' },
  ];
  const groups = [];
  let currentGroup = null;

  for (const ev of events) {
    if (!currentGroup) {
      currentGroup = { events: [ev], startTs: eventTimestamp(ev) };
    } else if (eventTimestamp(ev) - currentGroup.startTs <= 700) {
      currentGroup.events.push(ev);
    } else {
      groups.push(currentGroup);
      currentGroup = { events: [ev], startTs: eventTimestamp(ev) };
    }
  }
  if (currentGroup) groups.push(currentGroup);

  assert.equal(groups.length, 2);
  assert.deepEqual(groups[0].events.map((e) => e.text), ['A', 'B']);
  assert.deepEqual(groups[1].events.map((e) => e.text), ['C']);
});

// ── TEST 14 — one-byte events not discarded ────────────────────────────────
test('TEST 14 — one-byte events not discarded', () => {
  const t = createVirtualTerminal(25, 80);
  feed(t, '\x1b');
  assert.equal(t.partialEscape, '\x1b', 'ESC stored as partial');
  feed(t, '[');
  assert.equal(t.partialEscape, '\x1b[', 'ESC[ stored as partial');
  feed(t, '7');
  assert.equal(t.partialEscape, '\x1b[7', 'ESC[7 stored as partial');
  feed(t, 'm');
  assert.equal(t.partialEscape, '', 'completed CSI consumed');
  feed(t, 'X');
  assert.equal(t.cells[0][0].ch, 'X');
  assert.equal(t.cells[0][0].reverse, true, 'reverse after ESC[7m');
});

// ── TEST 15 — snapshot not re-fed ──────────────────────────────────────────
test('TEST 15 — snapshot not re-fed', () => {
  const t = createVirtualTerminal(3, 10);
  feed(t, 'ABC\r\nDEF');
  const snapshot = renderPlainText(t);
  // Snapshot contains the rendered matrix — must not be fed back to vtFeed
  assert.ok(snapshot.includes('ABC'), 'snapshot contains ABC');
  assert.ok(snapshot.includes('DEF'), 'snapshot contains DEF');
  // Verify: \r\n moves to next line, col 0. After ABC at row0, \r\n goes to row1,col0, DEF at row1,col0-2
  assert.equal(t.cells[0][0].ch, 'A');
  assert.equal(t.cells[1][0].ch, 'D');
  assert.equal(t.cursorRow, 1);
  assert.equal(t.cursorCol, 3);
});

// ── TEST 16 — same flow with different chunking ────────────────────────────
test('TEST 16 — same flow with different chunking', () => {
  const input = esc('ESC[7mClientes   ESC[0m');

  const t1 = createVirtualTerminal(25, 80);
  feed(t1, input);

  const t2 = createVirtualTerminal(25, 80);
  // Split into arbitrary chunks
  const chunks = [];
  for (let i = 0; i < input.length; i += 3) {
    chunks.push(input.slice(i, i + 3));
  }
  for (const chunk of chunks) feed(t2, chunk);

  assert.equal(renderPlainText(t1), renderPlainText(t2), 'identical matrix');
  assert.equal(renderHtml(t1), renderHtml(t2), 'identical html');
  assert.equal(t1.cells[0][8].reverse, t2.cells[0][8].reverse, 'same attr');
  assert.equal(t1.cells[0][8].ch, t2.cells[0][8].ch, 'same char');
});

// ── TEST 17 — wrapPending + LF não pula linha duplo ────────────────────────
test('TEST 17 — wrapPending + LF no double advance', () => {
  const t = createVirtualTerminal(3, 10);
  feed(t, '1234567890'); // fills row 0, sets wrapPending
  feed(t, '\n');         // LF should clear wrapPending, advance to row 1
  feed(t, 'X');          // X goes to row 1 col 0
  const lines = renderPlainText(t).split('\n');
  assert.equal(lines[0], '1234567890', 'row 0 filled');
  assert.equal(lines[1][0], 'X', 'X at row 1 col 0, not row 2');
});

// ── TEST 18 — RIS (ESC c) full reset ───────────────────────────────────────
test('TEST 18 — RIS full terminal reset', () => {
  const t = createVirtualTerminal(25, 80);
  feed(t, esc('ESC[7mABC ESCcDEF'));
  const text = renderPlainText(t);
  assert.ok(!text.includes('ABC'), 'ABC cleared by RIS');
  assert.ok(text.includes('DEF'), 'DEF written after RIS');
  assert.equal(t.cursorRow, 0, 'cursor at row 0');
  assert.equal(t.cursorCol, 3, 'cursor at col 3 after DEF');
  assert.equal(t.graphicsMode, false, 'graphics mode reset');
});

// ── TEST 19 — IND (ESC D) index ────────────────────────────────────────────
test('TEST 19 — IND index down', () => {
  const t = createVirtualTerminal(5, 10);
  feed(t, 'ABC');
  feed(t, esc('ESCD'));
  feed(t, 'DEF');
  const lines = renderPlainText(t).split('\n');
  assert.equal(lines[0].substring(0, 3), 'ABC', 'ABC on row 0');
  assert.equal(lines[1].substring(3, 6), 'DEF', 'DEF on row 1 same column');
});

// ── TEST 20 — NEL (ESC E) next line ────────────────────────────────────────
test('TEST 20 — NEL next line column zero', () => {
  const t = createVirtualTerminal(5, 10);
  feed(t, 'ABC');
  feed(t, esc('ESCE'));
  feed(t, 'DEF');
  const lines = renderPlainText(t).split('\n');
  assert.equal(lines[0].substring(0, 3), 'ABC', 'ABC on row 0');
  assert.equal(lines[1].substring(0, 3), 'DEF', 'DEF on row 1 col 0');
});

// ── TEST 21 — SGR 2 is dim, not bold-off ───────────────────────────────────
test('TEST 21 — SGR 2 means dim not bold-off', () => {
  const t = createVirtualTerminal(25, 80);
  feed(t, esc('ESC[1mESC[2mA'));
  // bold was set (1), then dim (2) — bold should remain, dim added
  assert.equal(t.cells[0][0].ch, 'A');
  assert.equal(t.cells[0][0].bold, true);
  assert.equal(t.cells[0][0].dim, true);
});

// ── TEST 22 — SGR 22 normal intensity ──────────────────────────────────────
test('TEST 22 — SGR 22 resets bold and dim', () => {
  const t = createVirtualTerminal(25, 80);
  // Not testing internal flags directly — verify text renders
  feed(t, esc('ESC[1mESC[2mESC[22mA'));
  assert.equal(t.cells[0][0].ch, 'A');
  assert.equal(t.cells[0][0].bold, false);
  assert.equal(t.cells[0][0].dim, false);
});

// ── TEST 23 — feedBase64 UTF-8 split across events ─────────────────────────
test('TEST 23 — feedBase64 handles UTF-8 split across two events', () => {
  const t = createVirtualTerminal(25, 80);
  // C3 = first byte of á (U+00E1), A1 = second byte
  feedBase64(t, 'ww==');  // b64 of byte 0xC3
  feedBase64(t, 'oQ==');  // b64 of byte 0xA1
  assert.equal(t.cells[0][0].ch, 'á', 'UTF-8 split: C3+A1 = á');
});

// ── TEST 24 — feedBase64 UTF-8 3-byte split ────────────────────────────────
test('TEST 24 — feedBase64 handles 3-byte UTF-8 split', () => {
  const t = createVirtualTerminal(25, 80);
  // € = E2 82 AC
  feedBase64(t, '4g==');  // E2
  feedBase64(t, 'gg==');  // 82
  feedBase64(t, 'rA==');  // AC
  assert.equal(t.cells[0][0].ch, '€', 'UTF-8 3-byte: E2+82+AC = €');
});

// ── TEST 25 — feedBase64 UTF-8 4-byte ─────────────────────────────────────
test('TEST 25 — feedBase64 handles 4-byte UTF-8 as one code point', () => {
  const t = createVirtualTerminal(25, 80);
  // U+1F642 🙂 = F0 9F 99 82
  feedBase64(t, '8A==');
  feedBase64(t, 'nw==');
  feedBase64(t, 'mQ==');
  feedBase64(t, 'gg==');
  assert.equal(t.cells[0][0].ch, '🙂', 'UTF-8 4-byte sequence decoded as one character');
  assert.equal(t.cursorCol, 1, 'surrogate pair occupies one terminal cell in this emulator policy');
});

// ── TEST 26 — feedBase64 CP850 encoding ─────────────────────────────────────
test('TEST 26 — feedBase64 with CP850 encoding', () => {
  const t = createVirtualTerminal(25, 80);
  // 0x82 = é em CP850
  feedBase64(t, 'gg==', 'cp850');
  assert.equal(t.cells[0][0].ch, 'é', 'CP850 0x82 = é');
});

// ── TEST 27 — feedBase64 CP437 encoding ─────────────────────────────────────
test('TEST 27 — feedBase64 with CP437 encoding', () => {
  const t = createVirtualTerminal(25, 80);
  // 0x82 = é em CP437
  feedBase64(t, 'gg==', 'cp437');
  assert.equal(t.cells[0][0].ch, 'é', 'CP437 0x82 = é');
});

// ── TEST 28 — feedBase64 ISO-8859-1 encoding ───────────────────────────────
test('TEST 28 — feedBase64 with ISO-8859-1 encoding', () => {
  const t = createVirtualTerminal(25, 80);
  // 0xE9 = é em ISO-8859-1
  feedBase64(t, '6Q==', 'iso-8859-1');
  assert.equal(t.cells[0][0].ch, 'é', 'ISO-8859-1 0xE9 = é');
});

// ── TEST 29 — renderSnapshot preserves all attributes ──────────────────────
test('TEST 29 — renderSnapshot preserves cell attributes (reverse, fg, bg)', () => {
  const t = createVirtualTerminal(25, 80);
  feed(t, esc('ESC[7mESC[31;44;1mClientes   ESC[0m'));
  const snap = renderSnapshot(t);
  assert.equal(snap.version, 1);
  assert.equal(snap.rows, 25);
  assert.equal(snap.cols, 80);
  // Verifica primeiras celulas
  const cells = snap.cells;
  // "Clientes   " = 11 chars; pegar as primeiras 8 (C,l,i,e,n,t,e,s)
  let textWritten = 0;
  for (let i = 0; i < 80; i++) {
    if (cells[i].ch !== ' ') { textWritten++; }
  }
  assert.equal(textWritten, 8, '8 non-space cells (Clientes)');
  // Primeira celula: 'C' com reverse, bold, fg=1 (red), bg=4 (blue)
  assert.equal(cells[0].ch, 'C');
  assert.equal(cells[0].reverse, true);
  assert.equal(cells[0].bold, true);
  assert.equal(cells[0].fg, 1);
  assert.equal(cells[0].bg, 4);
  // Espaços apos Clientes tambem tem reverse (8 caracteres escritos + espacos)
  assert.equal(cells[8].ch, ' ');
  assert.equal(cells[8].reverse, true, 'space after text has reverse');
});

// ── TEST 30 — renderSnapshotHtml has span classes ──────────────────────────
test('TEST 30 — renderSnapshotHtml produces spans with color classes', () => {
  const t = createVirtualTerminal(25, 80);
  feed(t, esc('ESC[7mClientesESC[0m'));
  const snap = renderSnapshot(t);
  const html = renderSnapshotHtml(snap);
  assert.ok(html.includes('vt-reverse'), 'HTML includes vt-reverse class');
  assert.ok(html.includes('Clientes'), 'HTML includes text');
});

// ── TEST 31 — TAB at column 0 goes to column 8 ─────────────────────────────
test('TEST 31 — TAB at column 0 goes to column 8', () => {
  const t = createVirtualTerminal(25, 80);
  feed(t, '\t');
  assert.equal(t.cursorCol, 8, 'TAB from col 0 goes to col 8');
});

// ── TEST 32 — TAB at column 7 goes to column 8 ─────────────────────────────
test('TEST 32 — TAB at column 7 goes to column 8', () => {
  const t = createVirtualTerminal(25, 80);
  for (let i = 0; i < 7; i++) feed(t, 'A');
  assert.equal(t.cursorCol, 7);
  feed(t, '\t');
  assert.equal(t.cursorCol, 8);
});

// ── TEST 33 — TAB near last column does not exceed bounds ──────────────────
test('TEST 33 — TAB near last column stays within bounds', () => {
  const t = createVirtualTerminal(25, 10);
  for (let i = 0; i < 9; i++) feed(t, 'A');
  assert.equal(t.cursorCol, 9);
  feed(t, '\t');
  assert.ok(t.cursorCol < t.cols, 'TAB within bounds');
});

// ── TEST 34 — text_sig different for different geometries ──────────────────
test('TEST 34 — text_sig differs for different geometries', () => {
  const t1 = createVirtualTerminal(1, 1);
  feed(t1, 'A');
  const t2 = createVirtualTerminal(1, 2);
  feed(t2, 'A');
  // 1x1 com A vs 1x2 com A + espaco
  assert.notEqual(screenSig(t1), screenSig(t2), 'text_sig differs by geometry');
});

// ── TEST 35 — visual_sig differs for reverse vs normal ─────────────────────
test('TEST 35 — visual_sig differs: A normal vs A reverse', () => {
  const t1 = createVirtualTerminal(1, 1);
  feed(t1, 'A');
  const t2 = createVirtualTerminal(1, 1);
  feed(t2, esc('ESC[7mA'));
  // Mesmo texto, atributo diferente
  assert.equal(t1.cells[0][0].ch, t2.cells[0][0].ch, 'same character');
  assert.notEqual(visualSig(t1), visualSig(t2), 'visual_sig differs');
});

// ── TEST 35b — browser sandbox signatures use pure SHA-256 ────────────────
test('TEST 35b — browser sandbox signatures do not require node:crypto', () => {
  const browserVt = loadBrowserSandboxVt();
  const nodeTerm = createVirtualTerminal(2, 4);
  const browserTerm = browserVt.createVirtualTerminal(2, 4);
  feed(nodeTerm, esc('ESC[7mAESC[0mB'));
  browserVt.feed(browserTerm, esc('ESC[7mAESC[0mB'));

  assert.equal(browserVt.screenSig(browserTerm), screenSig(nodeTerm));
  assert.equal(browserVt.visualSig(browserTerm), visualSig(nodeTerm));
  assert.ok(!browserVt.screenSig(browserTerm).startsWith('sha256-unavailable'));
  assert.match(browserVt.visualSig(browserTerm), /^sha256:[0-9a-f]{64}$/);

  const browserTiny = browserVt.createVirtualTerminal(1, 1);
  const browserWide = browserVt.createVirtualTerminal(1, 2);
  browserVt.feed(browserTiny, 'A');
  browserVt.feed(browserWide, 'A');
  assert.notEqual(browserVt.screenSig(browserTiny), browserVt.screenSig(browserWide));
});

// ── TEST 36 — SGR 28 resets hidden ─────────────────────────────────────────
test('TEST 36 — SGR 28 resets hidden', () => {
  const t = createVirtualTerminal(25, 80);
  feed(t, esc('ESC[8mESC[28mA'));
  assert.equal(t.cells[0][0].ch, 'A');
  assert.equal(t.cells[0][0].hidden, false, 'hidden reset by SGR 28');
});

// ── TEST 37 — SGR 25 resets blink ──────────────────────────────────────────
test('TEST 37 — SGR 25 resets blink', () => {
  const t = createVirtualTerminal(25, 80);
  feed(t, esc('ESC[5mESC[25mA'));
  assert.equal(t.cells[0][0].ch, 'A');
  assert.equal(t.cells[0][0].blink, false, 'blink reset by SGR 25');
});

// ── TEST 38 — geometry: fractional rows/cols rejected by validation ────────
test('TEST 38 — createVirtualTerminal rejects invalid geometry before allocation', () => {
  assert.equal(validateTerminalGeometry(25, 80).rows, 25);
  assert.equal(createVirtualTerminal(30, 132).cells[0].length, 132);
  assert.equal(createVirtualTerminal(200, 500).cells.length, 200);
  for (const args of [[2.5, 3.5], [0, 80], [-1, 80], [NaN, 80], [Infinity, 80], ['25', 80], [201, 80], [200, 501]]) {
    assert.throws(() => createVirtualTerminal(args[0], args[1]), RangeError, `rejects ${args[0]}x${args[1]}`);
  }
  assert.throws(() => validateTerminalGeometry(200, 500, { maxCells: 99999 }), RangeError, 'rejects excessive total cells');
});

// ── TEST 39 — CR then LF preserves semantics ───────────────────────────────
test('TEST 39 — CR then LF: CR returns to col 0, LF advances row', () => {
  const t = createVirtualTerminal(5, 10);
  feed(t, 'ABCDE');
  assert.equal(t.cursorCol, 5);
  feed(t, '\r');
  assert.equal(t.cursorCol, 0, 'CR returns to col 0');
  feed(t, '\n');
  assert.equal(t.cursorRow, 1, 'LF advances row');
  assert.equal(t.cursorCol, 0, 'CR set col 0 before LF');
});

// ── TEST 40 — LF in middle column: advances row, preserves col ─────────────
test('TEST 40 — LF in middle column: advances row, preserves column', () => {
  const t = createVirtualTerminal(5, 10);
  feed(t, 'ABCDE');
  assert.equal(t.cursorCol, 5);
  feed(t, '\n');
  assert.equal(t.cursorRow, 1);
  assert.equal(t.cursorCol, 5, 'LF/IND preserves column');
});

test('TEST 41 — RIS clears all SGR flags and decoder state', () => {
  const t = createVirtualTerminal(25, 80);
  feed(t, esc('ESC[1;2;4;5;7;8;31;44mAESCcB'));
  assert.equal(t.cells[0][0].ch, 'B');
  assert.equal(t.cells[0][0].bold, false);
  assert.equal(t.cells[0][0].dim, false);
  assert.equal(t.cells[0][0].underline, false);
  assert.equal(t.cells[0][0].blink, false);
  assert.equal(t.cells[0][0].reverse, false);
  assert.equal(t.cells[0][0].hidden, false);
  assert.equal(t.cells[0][0].fg, null);
  assert.equal(t.cells[0][0].bg, null);
  assert.equal(t.partialEscape, '');
});

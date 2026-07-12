/**
 * virtual_terminal.test.mjs — 16 tests for the virtual terminal emulator.
 * Run: node --test gateway/control/static/js/virtual_terminal.test.mjs
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const vt = require('./virtual_terminal.cjs');

const {
  createVirtualTerminal,
  feed,
  renderPlainText,
  renderHtml,
  renderCompactText,
  eventTimestamp,
  calcDelay,
  isBlank,
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

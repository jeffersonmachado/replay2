/**
 * terminal_snapshot_renderer.test.mjs — validação do renderer de snapshots
 * Run: node --test gateway/control/static/js/components/terminal_snapshot_renderer.test.mjs
 */
import test from 'node:test';
import assert from 'node:assert/strict';

// Importa funções do renderer via eval do módulo CommonJS
import { createRequire } from 'node:module';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import vm from 'node:vm';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const srcPath = path.resolve(__dirname, 'terminal_snapshot_renderer.js');

// Carrega módulo ES em sandbox para acessar exports
const source = readFileSync(srcPath, 'utf8');
const sandbox = {
  exports: {},
  module: { exports: {} },
  console,
  Object,
  Array,
  TextEncoder,
  // Mock escapeHtml
  escapeHtml: function(text) {
    return String(text || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#039;');
  },
};
// Transforma ES exports → CommonJS para uso em testes
const cjsSource = source
  .replace(/export function/g, 'function')
  .replace(/export const/g, 'const')
  .replace(/import \{([^}]+)\} from "[^"]+";/g, '// import removed for test');
vm.runInNewContext(cjsSource, sandbox, { filename: 'terminal_snapshot_renderer.js' });

const ctx = sandbox;

// Helpers
function cell(ch, attrs) {
  const a = attrs || {};
  return { ch: ch || ' ', fg: a.fg || 'default', bg: a.bg || 'default',
    bold: !!a.bold, dim: !!a.dim, underline: !!a.underline,
    blink: !!a.blink, reverse: !!a.reverse, hidden: !!a.hidden };
}

function makeSnapshot(rows, cols, cells) {
  const r = rows || 25;
  const c = cols || 80;
  const all = [];
  for (let i = 0; i < r * c; i++) {
    all.push(cells && i < cells.length ? cells[i] : cell(' '));
  }
  const snap = { version: 1, rows: r, cols: c, cells: all, term: 'xterm', encoding: 'utf-8' };
  snap.text_sig = ctx.computeTextSignature(snap);
  snap.visual_sig = ctx.computeVisualSignature(snap);
  snap.semantic_sig = ctx.computeSemanticSignature(snap);
  return snap;
}

// ── Tests ─────────────────────────────────────────────────────────────────

test('validateSnapshotPayload accepts valid snapshot', () => {
  assert.ok(ctx.validateSnapshotPayload(makeSnapshot(25, 80)));
});

test('validateSnapshotPayload rejects null', () => {
  assert.equal(ctx.validateSnapshotPayload(null), false);
});

test('validateSnapshotPayload rejects missing cells/runs', () => {
  assert.equal(ctx.validateSnapshotPayload({ version: 1, rows: 25, cols: 80 }), false);
});

test('validateSnapshotPayload rejects huge geometry', () => {
  assert.equal(ctx.validateSnapshotPayload({ version: 1, rows: 999999, cols: 999999, cells: [] }), false);
});

test('validateSnapshotPayload rejects zero rows', () => {
  assert.equal(ctx.validateSnapshotPayload({ version: 1, rows: 0, cols: 80, cells: [] }), false);
});

test('validateSnapshotPayload rejects negative cols', () => {
  assert.equal(ctx.validateSnapshotPayload({ version: 1, rows: 25, cols: -1, cells: [] }), false);
});

test('validateSnapshotPayload rejects float rows', () => {
  assert.equal(ctx.validateSnapshotPayload({ version: 1, rows: 2.5, cols: 80, cells: [] }), false);
});

test('decodeSnapshotPayload handles compact format', () => {
  const payload = {
    version: 1, rows: 2, cols: 2,
    attribute_table: [{ fg: 'default', bg: 'default', bold: false, dim: false, underline: false, blink: false, reverse: false, hidden: false }],
    runs: [{ row: 0, col: 0, length: 2, text: 'AB', attr: 0 }],
  };
  const result = ctx.decodeSnapshotPayload(payload);
  assert.ok(result);
  assert.equal(result.cells.length, 4);
  assert.equal(result.cells[0].ch, 'A');
  assert.equal(result.cells[1].ch, 'B');
});

test('decodeSnapshotPayload handles cells directly', () => {
  const snap = makeSnapshot(2, 2, [cell('X'), cell('Y')]);
  const result = ctx.decodeSnapshotPayload(snap);
  assert.equal(result.cells[0].ch, 'X');
});

test('decodeSnapshotPayload rejects huge geometry', () => {
  const result = ctx.decodeSnapshotPayload({ version: 1, rows: 999999, cols: 999999, runs: [], attribute_table: [] });
  assert.equal(result, null);
});

test('applyDiff applies cell changes', () => {
  const snap = makeSnapshot(2, 2);
  const diff = {
    version: 1,
    changes: [{ row: 0, col: 0, ch: 'X', fg: 'red', bg: 'default', bold: false, dim: false, underline: false, blink: false, reverse: false, hidden: false }],
    rows: 2, cols: 2,
  };
  const result = ctx.applyDiff(snap, diff);
  assert.equal(result.cells[0].ch, 'X');
  assert.equal(result.cells[0].fg, 'red');
});

test('applyDiff recalculates signatures instead of copying declared values', () => {
  const snap = makeSnapshot(2, 2);
  const expected = makeSnapshot(2, 2, [cell('X')]);
  const diff = {
    version: 1,
    base_seq_global: 0,
    seq_global: 1,
    base_rows: 2,
    base_cols: 2,
    base_text_sig: snap.text_sig,
    base_visual_sig: snap.visual_sig,
    base_semantic_sig: snap.semantic_sig,
    text_sig: expected.text_sig,
    visual_sig: expected.visual_sig,
    semantic_sig: expected.semantic_sig,
    geometry_changed: false,
    resize: null,
    changes: [{ row: 0, col: 0, ch: 'X' }],
    rows: 2,
    cols: 2,
  };
  const result = ctx.applyDiff(snap, diff);
  assert.equal(result.text_sig, expected.text_sig);
  assert.equal(result.visual_sig, expected.visual_sig);
  assert.equal(result.semantic_sig, expected.semantic_sig);
  assert.throws(() => ctx.applyDiff(snap, { ...diff, visual_sig: 'sha256:' + '1'.repeat(64) }), /signature mismatch/);
});

test('applyDiff handles geometry change', () => {
  const snap = makeSnapshot(1, 1);
  const diff = {
    version: 1, changes: [], rows: 2, cols: 2,
    geometry_changed: true,
  };
  const result = ctx.applyDiff(snap, diff);
  assert.equal(result.rows, 2);
  assert.equal(result.cols, 2);
  assert.equal(result.cells.length, 4);
});

test('cloneSnapshot deep copies cells', () => {
  const snap = makeSnapshot(1, 2, [cell('A'), cell('B')]);
  const cloned = ctx.cloneSnapshot(snap);
  cloned.cells[0].ch = 'Z';
  assert.equal(snap.cells[0].ch, 'A', 'original unchanged');
  assert.equal(cloned.cells[0].ch, 'Z', 'clone changed');
});

test('renderSnapshotToHtml produces spans with classes', () => {
  const snap = makeSnapshot(1, 2, [cell('A', { fg: 'red', reverse: true }), cell('B')]);
  const html = ctx.renderSnapshotToHtml(snap);
  assert.ok(html.includes('vt-reverse'), 'includes reverse class');
  // reverse swaps fg/bg: effective bg becomes red
  assert.ok(html.includes('vt-bg-red'), 'includes effective bg-red from reverse');
});

test('renderSnapshotToHtml groups consecutive same-attrs cells', () => {
  const snap = makeSnapshot(1, 3, [cell('A', { fg: 'red' }), cell('B', { fg: 'red' }), cell('C')]);
  const html = ctx.renderSnapshotToHtml(snap);
  // A and B should be in one span, C in another
  const spanCount = (html.match(/<span/g) || []).length;
  assert.ok(spanCount <= 3, 'groups consecutive cells');
});

test('renderSnapshotToText preserves geometry', () => {
  const snap = makeSnapshot(2, 3, [
    cell('A'), cell('B'), cell('C'),
    cell('D'), cell('E'), cell('F'),
  ]);
  const text = ctx.renderSnapshotToText(snap);
  const lines = text.split('\n');
  assert.equal(lines.length, 2, '2 rows');
  assert.equal(lines[0], 'ABC');
  assert.equal(lines[1], 'DEF');
});

test('renderSnapshotToHtml handles empty snapshot', () => {
  assert.equal(ctx.renderSnapshotToHtml(null), '');
  assert.equal(ctx.renderSnapshotToHtml({}), '');
});

test('validateDiffPayload accepts valid diff', () => {
  assert.ok(ctx.validateDiffPayload({ version: 1, changes: [] }));
});

test('validateDiffPayload rejects invalid', () => {
  assert.equal(ctx.validateDiffPayload({ version: 2, changes: [] }), false);
  assert.equal(ctx.validateDiffPayload({ version: 1 }), false);
  assert.equal(ctx.validateDiffPayload(null), false);
});

test('Unicode outside BMP not split into surrogate pairs', () => {
  // 😀 = U+1F600 (surrogate pair in UTF-16: D83D DE00)
  const payload = {
    version: 1, rows: 1, cols: 3,
    attribute_table: [{ fg: 'default', bg: 'default', bold: false, dim: false, underline: false, blink: false, reverse: false, hidden: false }],
    runs: [{ row: 0, col: 0, length: 3, text: '😀A!', attr: 0 }],
  };
  const result = ctx.decodeSnapshotPayload(payload);
  assert.equal(result.cells[0].ch, '😀', 'emoji in one cell');
  assert.equal(result.cells[1].ch, 'A');
  assert.equal(result.cells[2].ch, '!');
});

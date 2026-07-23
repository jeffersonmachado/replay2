/**
 * capture_replay_timeline.test.mjs — tests for timeline grouping (seção 25)
 * v0.3.19+: Testa arquitetura snapshot/diff, não VT.
 * Run: node --test gateway/control/static/js/components/capture_replay_timeline.test.mjs
 *
 * Importa o MÓDULO REAL de produção (capture_replay_timeline.js), o mesmo
 * carregado dinamicamente por capture_session_replay.html, com os renderers
 * reais de terminal_snapshot_renderer.js como dependências injetadas.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { buildTimelineItems } from './capture_replay_timeline.js';
import {
  decodeSnapshotPayload,
  applyDiff,
  renderSnapshotToHtml,
  renderSnapshotToText,
} from './terminal_snapshot_renderer.js';
import { decodeEventForDisplay, normalizeDisplayEncoding, eventTimestamp } from './timeline_core.js';

function makeDeps() {
  return {
    renderSnapshotToHtml,
    renderSnapshotToText,
    decodeSnapshotPayload,
    applyDiff,
    eventTimestamp,
    formatEventContent: (ev) => ev.summary || ev.data_decoded || '',
  };
}

function makeCell(ch, attrs) {
  var a = attrs || {};
  return { ch: ch || " ", fg: a.fg || "default", bg: a.bg || "default",
    bold: !!a.bold, dim: !!a.dim, underline: !!a.underline,
    blink: !!a.blink, reverse: !!a.reverse, hidden: !!a.hidden };
}

function makeSnapshot(rows, cols, cells) {
  var r = rows || 25;
  var c = cols || 80;
  var all = [];
  for (var i = 0; i < r * c; i++) {
    all.push(cells && i < cells.length ? cells[i] : makeCell(" "));
  }
  return { version: 1, rows: r, cols: c, cells: all,
    text_sig: "sha256:abc123", visual_sig: "sha256:def456" };
}

function makeCompactSnapshot(snap) {
  return { version: 1, rows: snap.rows, cols: snap.cols, cells: snap.cells,
    text_sig: snap.text_sig, visual_sig: snap.visual_sig };
}

// Diff não-canônico (sem sigs/seqs): o applyDiff real valida bounds das
// mudanças contra o snapshot corrente e não exige assinaturas.
function makeDiff(baseSnap, currSnap, seqBase, seqCurr) {
  var changes = [];
  for (var i = 0; i < Math.min(baseSnap.cells.length, currSnap.cells.length); i++) {
    if (JSON.stringify(baseSnap.cells[i]) !== JSON.stringify(currSnap.cells[i])) {
      changes.push({
        row: Math.floor(i / currSnap.cols), col: i % currSnap.cols,
        ch: currSnap.cells[i].ch, fg: currSnap.cells[i].fg,
        bg: currSnap.cells[i].bg, bold: currSnap.cells[i].bold,
        dim: currSnap.cells[i].dim, underline: currSnap.cells[i].underline,
        blink: currSnap.cells[i].blink, reverse: currSnap.cells[i].reverse,
        hidden: currSnap.cells[i].hidden,
      });
    }
  }
  return { version: 1, rows: currSnap.rows, cols: currSnap.cols,
    geometry_changed: baseSnap.rows !== currSnap.rows || baseSnap.cols !== currSnap.cols,
    changes: changes };
}

test('groups OUT events by proximity', () => {
  const events = [
    { seq_global: 1, ts_ms: 1000, type: 'bytes', direction: 'out', data_decoded: 'A', n_bytes: 1 },
    { seq_global: 2, ts_ms: 1100, type: 'bytes', direction: 'out', data_decoded: 'B', n_bytes: 1 },
    { seq_global: 3, ts_ms: 2000, type: 'bytes', direction: 'out', data_decoded: 'C', n_bytes: 1 },
  ];
  const items = buildTimelineItems(events, makeDeps());
  // First two events within 700ms → one group. Third event outside → second group.
  assert.equal(items.length, 2, 'two groups');
  assert.equal(items[0].content_kind, 'terminal_snapshot');
  assert.equal(items[0].chunk_count, 2);
  assert.equal(items[1].content_kind, 'terminal_snapshot');
  assert.equal(items[1].chunk_count, 1);
});

test('IN events create boundaries between groups', () => {
  const events = [
    { seq_global: 1, ts_ms: 1000, type: 'bytes', direction: 'out', data_decoded: 'A', n_bytes: 1 },
    { seq_global: 2, ts_ms: 1100, type: 'bytes', direction: 'in',  data_decoded: 'x', n_bytes: 1 },
    { seq_global: 3, ts_ms: 1200, type: 'bytes', direction: 'out', data_decoded: 'B', n_bytes: 1 },
  ];
  const items = buildTimelineItems(events, makeDeps());
  // IN event splits the two OUT events into separate groups
  const snapshots = items.filter(i => i.content_kind === 'terminal_snapshot');
  assert.equal(snapshots.length, 2, 'two snapshot groups separated by IN event');
});

test('empty events array returns empty', () => {
  const items = buildTimelineItems([], makeDeps());
  assert.equal(items.length, 0);
});

test('snapshot preserves geometry', () => {
  var snap = makeSnapshot(25, 80);
  var events = [
    { seq_global: 1, ts_ms: 1000, type: 'bytes', direction: 'out',
      snapshot_compact: makeCompactSnapshot(snap), text_sig: snap.text_sig, visual_sig: snap.visual_sig, n_bytes: 5 },
  ];
  var items = buildTimelineItems(events, makeDeps());
  assert.equal(items.length, 1);
  assert.equal(items[0].content_kind, 'terminal_snapshot');
  var lines = items[0].summary.split('\n');
  assert.equal(lines.length, 25, '25 rows');
  assert.equal(lines[0].length, 80, '80 cols');
});

test('clear-screen produces empty snapshot (not discarded)', () => {
  var snap1 = makeSnapshot(25, 80, [makeCell('H'), makeCell('e'), makeCell('l'), makeCell('l'), makeCell('o')]);
  var snap2 = makeSnapshot(25, 80);
  var diff = makeDiff(snap1, snap2, 1, 2);
  var events = [
    { seq_global: 1, ts_ms: 1000, type: 'bytes', direction: 'out',
      snapshot_compact: makeCompactSnapshot(snap1), text_sig: snap1.text_sig, visual_sig: snap1.visual_sig, n_bytes: 5 },
    { seq_global: 2, ts_ms: 2000, type: 'bytes', direction: 'out',
      diff: diff, text_sig: snap2.text_sig, visual_sig: snap2.visual_sig, n_bytes: 4 },
  ];
  var items = buildTimelineItems(events, makeDeps());
  var snapshots = items.filter(function(i) { return i.content_kind === 'terminal_snapshot'; });
  assert.equal(snapshots.length, 2, 'two snapshots (clear screen preserved)');
  var clearSnap = snapshots[1].summary;
  assert.ok(clearSnap.trim() === '', 'clear screen snapshot is empty but present');
  assert.equal(clearSnap.split('\n').length, 25, 'still 25 rows');
});

test('snapshot includes text_sig and visual_sig', () => {
  var snap = makeSnapshot(25, 80, [makeCell('A'), makeCell('B'), makeCell('C')]);
  snap.text_sig = 'sha256:text123';
  snap.visual_sig = 'sha256:vis456';
  var events = [
    { seq_global: 1, ts_ms: 1000, type: 'bytes', direction: 'out',
      snapshot_compact: makeCompactSnapshot(snap), text_sig: snap.text_sig, visual_sig: snap.visual_sig, n_bytes: 3 },
  ];
  var items = buildTimelineItems(events, makeDeps());
  assert.equal(items.length, 1);
  assert.equal(items[0].content_kind, 'terminal_snapshot');
  assert.ok(items[0].text_sig, 'text_sig present');
  assert.ok(items[0].visual_sig, 'visual_sig present');
  assert.equal(typeof items[0].text_sig, 'string');
  assert.equal(typeof items[0].visual_sig, 'string');
  assert.ok(items[0].text_sig !== items[0].visual_sig, 'text_sig deve diferir de visual_sig');
});

test('text_sig stable for same text, visual_sig changes with attributes', () => {
  var snap1 = makeSnapshot(25, 80, [makeCell('P'), makeCell('l'), makeCell('a'), makeCell('i'), makeCell('n')]);
  snap1.text_sig = 'sha256:text_plain';
  snap1.visual_sig = 'sha256:vis_normal';

  var snap2 = makeSnapshot(25, 80, [
    makeCell('P', {reverse: true}), makeCell('l', {reverse: true}),
    makeCell('a', {reverse: true}), makeCell('i', {reverse: true}), makeCell('n', {reverse: true})
  ]);
  snap2.text_sig = 'sha256:text_plain';  // mesmo texto
  snap2.visual_sig = 'sha256:vis_reverse';  // visual diferente

  var items1 = buildTimelineItems([
    { seq_global: 1, ts_ms: 1000, type: 'bytes', direction: 'out',
      snapshot_compact: makeCompactSnapshot(snap1), text_sig: snap1.text_sig, visual_sig: snap1.visual_sig, n_bytes: 5 },
  ], makeDeps());

  var items2 = buildTimelineItems([
    { seq_global: 1, ts_ms: 1000, type: 'bytes', direction: 'out',
      snapshot_compact: makeCompactSnapshot(snap2), text_sig: snap2.text_sig, visual_sig: snap2.visual_sig, n_bytes: 5 },
  ], makeDeps());

  assert.equal(items1[0].text_sig, items2[0].text_sig, 'text_sig identical for same chars');
  assert.notEqual(items1[0].visual_sig, items2[0].visual_sig, 'visual_sig differs');
});

test('snapshot includes canonical cell data (snapshot field)', () => {
  var snap = makeSnapshot(25, 80, [
    makeCell('A', {reverse: true}), makeCell('B', {reverse: true}), makeCell('C', {reverse: true})
  ]);
  var events = [
    { seq_global: 1, ts_ms: 1000, type: 'bytes', direction: 'out',
      snapshot_compact: makeCompactSnapshot(snap), text_sig: snap.text_sig, visual_sig: snap.visual_sig, n_bytes: 10 },
  ];
  var items = buildTimelineItems(events, makeDeps());
  assert.equal(items.length, 1);
  assert.ok(items[0].snapshot, 'snapshot field present');
  assert.equal(items[0].snapshot.rows, 25);
  assert.equal(items[0].snapshot.cols, 80);
  assert.equal(items[0].snapshot.cells[0].ch, 'A');
  assert.equal(items[0].snapshot.cells[0].reverse, true, 'cell 0 has reverse');
});

test('snapshot_html is present for terminal_snapshot', () => {
  var snap = makeSnapshot(25, 80, [
    makeCell('A', {reverse: true}), makeCell('B', {reverse: true}), makeCell('C', {reverse: true})
  ]);
  var events = [
    { seq_global: 1, ts_ms: 1000, type: 'bytes', direction: 'out',
      snapshot_compact: makeCompactSnapshot(snap), text_sig: snap.text_sig, visual_sig: snap.visual_sig, n_bytes: 10 },
  ];
  var items = buildTimelineItems(events, makeDeps());
  assert.ok(items[0].snapshot_html, 'snapshot_html present');
  assert.ok(items[0].snapshot_html.includes('vt-reverse'), 'includes reverse class');
});

test('diff applies correctly between snapshots', () => {
  var snap1 = makeSnapshot(5, 10);
  var snap2 = makeSnapshot(5, 10, [makeCell('X', {fg: 'red'})]);
  var diff = makeDiff(snap1, snap2, 1, 2);

  var events = [
    { seq_global: 1, ts_ms: 1000, type: 'bytes', direction: 'out',
      snapshot_compact: makeCompactSnapshot(snap1), text_sig: snap1.text_sig, visual_sig: snap1.visual_sig, n_bytes: 0 },
    { seq_global: 2, ts_ms: 1100, type: 'bytes', direction: 'out',
      diff: diff, text_sig: snap2.text_sig, visual_sig: snap2.visual_sig, n_bytes: 1 },
  ];
  var items = buildTimelineItems(events, makeDeps());
  // Both events within 700ms → 1 group
  assert.equal(items.length, 1);
  assert.equal(items[0].snapshot.cells[0].ch, 'X');
  assert.equal(items[0].snapshot.cells[0].fg, 'red');
});

test('encoding UTF-8 split via snapshot works', () => {
  var snap = makeSnapshot(25, 80, [makeCell('á'), makeCell('!')]);
  var events = [
    { seq_global: 1, ts_ms: 1000, type: 'bytes', direction: 'out',
      snapshot_compact: makeCompactSnapshot(snap), text_sig: snap.text_sig, visual_sig: snap.visual_sig, n_bytes: 3 },
  ];
  var items = buildTimelineItems(events, makeDeps());
  assert.equal(items.length, 1);
  assert.equal(items[0].snapshot.cells[0].ch, 'á');
  assert.equal(items[0].snapshot.cells[1].ch, '!');
});

test('encoding CP850 via snapshot works', () => {
  var snap = makeSnapshot(25, 80, [makeCell('é')]);
  var events = [
    { seq_global: 1, ts_ms: 1000, type: 'bytes', direction: 'out',
      snapshot_compact: makeCompactSnapshot(snap), text_sig: snap.text_sig, visual_sig: snap.visual_sig, n_bytes: 1 },
  ];
  var items = buildTimelineItems(events, makeDeps());
  assert.equal(items.length, 1);
  assert.equal(items[0].snapshot.cells[0].ch, 'é');
});

test('grouped snapshot preserves chunks via snapshot', () => {
  var snap1 = makeSnapshot(25, 80, [makeCell('A')]);
  var snap2 = makeSnapshot(25, 80, [makeCell('A'), makeCell('B')]);
  var diff = makeDiff(snap1, snap2, 1, 2);

  var events = [
    { seq_global: 1, ts_ms: 1000, type: 'bytes', direction: 'out',
      snapshot_compact: makeCompactSnapshot(snap1), text_sig: snap1.text_sig, visual_sig: snap1.visual_sig, n_bytes: 1 },
    { seq_global: 2, ts_ms: 1100, type: 'bytes', direction: 'out',
      diff: diff, text_sig: snap2.text_sig, visual_sig: snap2.visual_sig, n_bytes: 1 },
  ];
  var items = buildTimelineItems(events, makeDeps());
  assert.equal(items.length, 1, 'both events grouped');
  assert.equal(items[0].snapshot.cells[0].ch, 'A');
  assert.equal(items[0].snapshot.cells[1].ch, 'B');
  assert.equal(items[0].chunk_count, 2);
});

test('grouped three events via snapshot works', () => {
  var snap1 = makeSnapshot(25, 80, [makeCell('á')]);
  var snap2 = makeSnapshot(25, 80, [makeCell('á'), makeCell('!')]);
  var diff = makeDiff(snap1, snap2, 1, 3);

  var events = [
    { seq_global: 1, ts_ms: 1000, type: 'bytes', direction: 'out',
      snapshot_compact: makeCompactSnapshot(snap1), text_sig: snap1.text_sig, visual_sig: snap1.visual_sig, n_bytes: 1 },
    { seq_global: 2, ts_ms: 1100, type: 'bytes', direction: 'out',
      diff: diff, text_sig: snap2.text_sig, visual_sig: snap2.visual_sig, n_bytes: 1 },
    { seq_global: 3, ts_ms: 1200, type: 'bytes', direction: 'out',
      diff: diff, text_sig: snap2.text_sig, visual_sig: snap2.visual_sig, n_bytes: 1 },
  ];
  var items = buildTimelineItems(events, makeDeps());
  assert.equal(items.length, 1, 'all three events grouped');
  assert.equal(items[0].chunk_count, 3);
});

test('detailed display decodes UTF-8 and falls back unknown encoding to UTF-8', () => {
  assert.equal(normalizeDisplayEncoding('x-unknown-codepage'), 'utf-8');
  const event = { type: 'bytes', data_b64: 'w6E=', encoding: 'x-unknown-codepage' };
  assert.equal(decodeEventForDisplay(event, 'utf-8', 'display'), 'á');
});

test('detailed display decodes CP850 and CP437 without browser TextDecoder support', () => {
  assert.equal(decodeEventForDisplay({ type: 'bytes', data_b64: 'gg==', encoding: 'cp850' }), 'é');
  assert.equal(decodeEventForDisplay({ type: 'bytes', data_b64: 'xNo=', encoding: 'cp437' }), '─┌');
});

test('detailed display sanitizes ANSI after decoding', () => {
  const event = { type: 'bytes', data_b64: Buffer.from('\x1b[31má\x1b[0m', 'utf8').toString('base64'), encoding: 'utf-8' };
  assert.equal(decodeEventForDisplay(event, 'utf-8', 'display'), 'á');
  assert.equal(decodeEventForDisplay(event, 'utf-8', 'raw'), '\x1b[31má\x1b[0m');
});

/**
 * checkpoint_seek.test.mjs — tests for checkpoint-based seek logic
 * Run: node --test gateway/control/static/js/components/checkpoint_seek.test.mjs
 *
 * Importa o MÓDULO REAL de produção (checkpoint_seek.js), usado por
 * capture_session_replay.html no seekToEvent, e o applyDiff real de
 * terminal_snapshot_renderer.js.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { findBestCheckpoint } from './checkpoint_seek.js';
import { applyDiff } from './terminal_snapshot_renderer.js';

// ── Helpers ─────────────────────────────────────────────────────────────────

function makeCell(ch, attrs) {
  const a = attrs || {};
  return { ch: ch || ' ', fg: a.fg || 'default', bg: a.bg || 'default',
    bold: !!a.bold, dim: !!a.dim, underline: !!a.underline,
    blink: !!a.blink, reverse: !!a.reverse, hidden: !!a.hidden };
}

function makeSnapshot(rows, cols, cells, textSig, visualSig) {
  const r = rows || 2;
  const c = cols || 2;
  const all = [];
  for (let i = 0; i < r * c; i++) {
    all.push(cells && i < cells.length ? cells[i] : makeCell(' '));
  }
  return { version: 1, rows: r, cols: c, cells: all,
    text_sig: textSig || 'sha256:txt', visual_sig: visualSig || 'sha256:vis' };
}

function makeCompact(snap) {
  return { version: 1, rows: snap.rows, cols: snap.cols, cells: snap.cells,
    text_sig: snap.text_sig, visual_sig: snap.visual_sig };
}

// Diff não-canônico (sem sigs/seqs): o applyDiff real valida bounds das
// mudanças contra o snapshot corrente e não exige assinaturas.
function makeDiff(baseSnap, currSnap, baseSeq, seq) {
  const changes = [];
  for (let i = 0; i < Math.min(baseSnap.cells.length, currSnap.cells.length); i++) {
    if (JSON.stringify(baseSnap.cells[i]) !== JSON.stringify(currSnap.cells[i])) {
      changes.push({
        row: Math.floor(i / currSnap.cols), col: i % currSnap.cols,
        ch: currSnap.cells[i].ch, fg: currSnap.cells[i].fg, bg: currSnap.cells[i].bg,
        bold: currSnap.cells[i].bold, dim: currSnap.cells[i].dim,
        underline: currSnap.cells[i].underline, blink: currSnap.cells[i].blink,
        reverse: currSnap.cells[i].reverse, hidden: currSnap.cells[i].hidden,
      });
    }
  }
  return { version: 1, rows: currSnap.rows, cols: currSnap.cols,
    geometry_changed: baseSnap.rows !== currSnap.rows || baseSnap.cols !== currSnap.cols,
    changes: changes };
}

// ── Tests ───────────────────────────────────────────────────────────────────

test('findBestCheckpoint returns null for empty list', () => {
  const result = findBestCheckpoint([], 50);
  assert.equal(result, null);
});

test('findBestCheckpoint returns only checkpoint <= target', () => {
  const checkpoints = [
    { seq_global: 0, snapshot_compact: makeCompact(makeSnapshot(2, 2)) },
    { seq_global: 100, snapshot_compact: makeCompact(makeSnapshot(2, 2)) },
    { seq_global: 200, snapshot_compact: makeCompact(makeSnapshot(2, 2)) },
  ];
  const result = findBestCheckpoint(checkpoints, 150);
  assert.equal(result.seq_global, 100);
});

test('findBestCheckpoint returns exact match', () => {
  const checkpoints = [
    { seq_global: 0 },
    { seq_global: 100 },
    { seq_global: 200 },
  ];
  const result = findBestCheckpoint(checkpoints, 100);
  assert.equal(result.seq_global, 100);
});

test('findBestCheckpoint returns last if target beyond all', () => {
  const checkpoints = [
    { seq_global: 0 },
    { seq_global: 100 },
  ];
  const result = findBestCheckpoint(checkpoints, 999);
  assert.equal(result.seq_global, 100);
});

test('findBestCheckpoint returns first if target before second', () => {
  const checkpoints = [
    { seq_global: 0 },
    { seq_global: 50 },
  ];
  const result = findBestCheckpoint(checkpoints, 10);
  assert.equal(result.seq_global, 0);
});

test('seek from checkpoint + apply diffs reaches target', () => {
  // Setup: checkpoint at seq 0, diffs at seq 1, 2, 3
  const snap0 = makeSnapshot(2, 2, [makeCell('A')], 'sha256:t0', 'sha256:v0');
  const snap1 = makeSnapshot(2, 2, [makeCell('B')], 'sha256:t1', 'sha256:v1');
  const snap2 = makeSnapshot(2, 2, [makeCell('C')], 'sha256:t2', 'sha256:v2');
  const snap3 = makeSnapshot(2, 2, [makeCell('D')], 'sha256:t3', 'sha256:v3');

  const checkpoints = [
    { seq_global: 0, snapshot_compact: makeCompact(snap0) },
  ];

  const events = [
    { seq: 1, seq_global: 1, direction: 'out', diff: makeDiff(snap0, snap1, 0, 1) },
    { seq: 2, seq_global: 2, direction: 'out', diff: makeDiff(snap1, snap2, 1, 2) },
    { seq: 3, seq_global: 3, direction: 'out', diff: makeDiff(snap2, snap3, 2, 3) },
  ];

  // Seek to event index 2 (seq_global=3)
  const targetSeqGlobal = events[2].seq_global;
  const bestCp = findBestCheckpoint(checkpoints, targetSeqGlobal);
  assert.equal(bestCp.seq_global, 0);

  // Start from checkpoint snapshot
  let currentSnap = JSON.parse(JSON.stringify(bestCp.snapshot_compact));
  assert.equal(currentSnap.cells[0].ch, 'A');

  // Apply diffs up to target (applyDiff real)
  for (const ev of events) {
    if (ev.seq_global <= targetSeqGlobal) {
      currentSnap = applyDiff(currentSnap, ev.diff);
    }
  }

  assert.equal(currentSnap.cells[0].ch, 'D');
});

test('seek with multiple checkpoints uses closest', () => {
  const checkpoints = [
    { seq_global: 0 },
    { seq_global: 100 },
    { seq_global: 200 },
    { seq_global: 300 },
  ];

  assert.equal(findBestCheckpoint(checkpoints, 250).seq_global, 200);
  assert.equal(findBestCheckpoint(checkpoints, 199).seq_global, 100);
  assert.equal(findBestCheckpoint(checkpoints, 0).seq_global, 0);
});

test('seek does not apply event beyond target', () => {
  const snap0 = makeSnapshot(2, 2, [makeCell('A')], 'sha256:t0', 'sha256:v0');
  const snap1 = makeSnapshot(2, 2, [makeCell('B')], 'sha256:t1', 'sha256:v1');
  const snap2 = makeSnapshot(2, 2, [makeCell('C')], 'sha256:t2', 'sha256:v2');

  const events = [
    { seq_global: 1, diff: makeDiff(snap0, snap1, 0, 1) },
    { seq_global: 2, diff: makeDiff(snap1, snap2, 1, 2) },
  ];

  let currentSnap = JSON.parse(JSON.stringify(snap0));
  const target = 1;

  for (const ev of events) {
    if (ev.seq_global <= target) {
      currentSnap = applyDiff(currentSnap, ev.diff);
    }
  }

  // Only applied seq 1, not seq 2
  assert.equal(currentSnap.cells[0].ch, 'B');
});

test('seek to non-existent seq returns null checkpoint', () => {
  const checkpoints = [{ seq_global: 100 }];
  const result = findBestCheckpoint(checkpoints, 50);
  assert.equal(result, null);
});

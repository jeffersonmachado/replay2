/**
 * checkpoint_seek.test.mjs — tests for checkpoint-based seek logic
 * Run: node --test gateway/control/static/js/components/checkpoint_seek.test.mjs
 */
import test from 'node:test';
import assert from 'node:assert/strict';

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
  return {
    version: 1, changes: changes,
    base_seq_global: baseSeq, seq_global: seq,
    base_text_sig: baseSnap.text_sig, base_visual_sig: baseSnap.visual_sig,
    text_sig: currSnap.text_sig, visual_sig: currSnap.visual_sig,
    rows: currSnap.rows, cols: currSnap.cols,
    geometry_changed: baseSnap.rows !== currSnap.rows || baseSnap.cols !== currSnap.cols,
  };
}

function findBestCheckpoint(checkpoints, targetSeqGlobal) {
  let best = null;
  for (const cp of checkpoints) {
    const cpSeq = cp.seq_global || 0;
    if (cpSeq <= targetSeqGlobal && (!best || cpSeq > (best.seq_global || 0))) {
      best = cp;
    }
  }
  return best;
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

  // Apply diffs up to target
  for (const ev of events) {
    if (ev.seq_global <= targetSeqGlobal) {
      // Apply diff manually
      for (const chg of (ev.diff.changes || [])) {
        const idx = chg.row * ev.diff.cols + chg.col;
        if (idx < currentSnap.cells.length) {
          currentSnap.cells[idx] = {
            ch: chg.ch, fg: chg.fg, bg: chg.bg,
            bold: chg.bold, dim: chg.dim, underline: chg.underline,
            blink: chg.blink, reverse: chg.reverse, hidden: chg.hidden,
          };
        }
      }
      currentSnap.text_sig = ev.diff.text_sig;
      currentSnap.visual_sig = ev.diff.visual_sig;
    }
  }

  assert.equal(currentSnap.cells[0].ch, 'D');
  assert.equal(currentSnap.text_sig, 'sha256:t3');
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
      for (const chg of (ev.diff.changes || [])) {
        const idx = chg.row * ev.diff.cols + chg.col;
        if (idx < currentSnap.cells.length) {
          currentSnap.cells[idx] = {
            ch: chg.ch, fg: chg.fg, bg: chg.bg,
            bold: chg.bold, dim: chg.dim, underline: chg.underline,
            blink: chg.blink, reverse: chg.reverse, hidden: chg.hidden,
          };
        }
      }
      currentSnap.text_sig = ev.diff.text_sig;
    }
  }

  // Only applied seq 1, not seq 2
  assert.equal(currentSnap.cells[0].ch, 'B');
  assert.equal(currentSnap.text_sig, 'sha256:t1');
});

test('seek to non-existent seq returns null checkpoint', () => {
  const checkpoints = [{ seq_global: 100 }];
  const result = findBestCheckpoint(checkpoints, 50);
  assert.equal(result, null);
});

/**
 * replay_snapshot_state.test.mjs — tests for replay snapshot state management
 * Run: node --test gateway/control/static/js/components/replay_snapshot_state.test.mjs
 *
 * Importa o MÓDULO REAL de produção (replay_snapshot_state.js). As fixtures
 * usam as assinaturas reais (computeTextSignature/computeVisualSignature/
 * computeSemanticSignature) para que os diffs canônicos passem na validação
 * estrita do terminal_snapshot_renderer.js.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { ReplaySnapshotState, createReplayState } from './replay_snapshot_state.js';
import {
  computeTextSignature,
  computeVisualSignature,
  computeSemanticSignature,
} from './terminal_snapshot_renderer.js';

// ── Helpers ─────────────────────────────────────────────────────────────────

function makeCell(ch, attrs) {
  const a = attrs || {};
  return { ch: ch || ' ', fg: a.fg || 'default', bg: a.bg || 'default',
    bold: !!a.bold, dim: !!a.dim, underline: !!a.underline,
    blink: !!a.blink, reverse: !!a.reverse, hidden: !!a.hidden };
}

function signSnapshot(snap) {
  snap.text_sig = computeTextSignature(snap);
  snap.visual_sig = computeVisualSignature(snap);
  snap.semantic_sig = computeSemanticSignature(snap);
  return snap;
}

function makeSnapshot(rows, cols, cells) {
  const r = rows || 2;
  const c = cols || 2;
  const all = [];
  for (let i = 0; i < r * c; i++) {
    all.push(cells && i < cells.length ? cells[i] : makeCell(' '));
  }
  return signSnapshot({ version: 1, rows: r, cols: c, cells: all });
}

// Diff canônico válido: seqs, geometria-base e assinaturas reais (base = baseSnap,
// resultado = currSnap, que é o que o applyDiff produz ao aplicar as mudanças).
function makeDiff(baseSnap, currSnap, baseSeq, seq) {
  const changes = [];
  for (let i = 0; i < Math.min(baseSnap.cells.length, currSnap.cells.length); i++) {
    const pc = baseSnap.cells[i];
    const cc = currSnap.cells[i];
    if (JSON.stringify(pc) !== JSON.stringify(cc)) {
      changes.push({
        row: Math.floor(i / currSnap.cols), col: i % currSnap.cols,
        ch: cc.ch, fg: cc.fg, bg: cc.bg,
        bold: cc.bold, dim: cc.dim, underline: cc.underline,
        blink: cc.blink, reverse: cc.reverse, hidden: cc.hidden,
      });
    }
  }
  return {
    version: 1, changes: changes,
    base_seq_global: baseSeq || 0, seq_global: seq || 1,
    rows: currSnap.rows, cols: currSnap.cols,
    base_rows: baseSnap.rows, base_cols: baseSnap.cols,
    geometry_changed: baseSnap.rows !== currSnap.rows || baseSnap.cols !== currSnap.cols,
    base_text_sig: baseSnap.text_sig, base_visual_sig: baseSnap.visual_sig,
    base_semantic_sig: baseSnap.semantic_sig,
    text_sig: currSnap.text_sig, visual_sig: currSnap.visual_sig,
    semantic_sig: currSnap.semantic_sig,
  };
}

// ── Tests ───────────────────────────────────────────────────────────────────

test('factory creates ReplaySnapshotState', () => {
  const state = createReplayState();
  assert.ok(state instanceof ReplaySnapshotState);
});

test('initial state is not initialized', () => {
  const state = new ReplaySnapshotState();
  assert.equal(state.isInitialized, false);
  assert.equal(state.currentSeqGlobal, 0);
  assert.equal(state.applyCount, 0);
});

test('loadInitialSnapshot succeeds with valid payload', () => {
  const state = new ReplaySnapshotState();
  const snap = makeSnapshot(2, 2, [makeCell('A'), makeCell('B')]);
  const ok = state.loadInitialSnapshot(snap);
  assert.equal(ok, true);
  assert.equal(state.isInitialized, true);
  assert.equal(state.rows, 2);
  assert.equal(state.cols, 2);
});

test('loadInitialSnapshot fails with null', () => {
  const state = new ReplaySnapshotState();
  const ok = state.loadInitialSnapshot(null);
  assert.equal(ok, false);
  assert.equal(state.errorCount, 1);
});

test('loadInitialSnapshot fails with invalid payload', () => {
  const state = new ReplaySnapshotState();
  const ok = state.loadInitialSnapshot({ version: 2, rows: 25, cols: 80 });
  assert.equal(ok, false);
});

test('applyCheckpoint replaces state', () => {
  const state = new ReplaySnapshotState();
  state.loadInitialSnapshot(makeSnapshot(2, 2, [makeCell('A')]));
  assert.equal(state.snapshot.cells[0].ch, 'A');

  const ckSnap = makeSnapshot(2, 2, [makeCell('Z')]);
  const ok = state.applyCheckpoint(ckSnap, 100);
  assert.equal(ok, true);
  assert.equal(state.snapshot.cells[0].ch, 'Z');
  assert.equal(state.currentSeqGlobal, 100);
  assert.equal(state.currentCheckpointSeqGlobal, 100);
});

test('applyCheckpoint fails with null', () => {
  const state = new ReplaySnapshotState();
  state.loadInitialSnapshot(makeSnapshot(2, 2));
  const ok = state.applyCheckpoint(null, 50);
  assert.equal(ok, false);
  assert.equal(state.errorCount, 1);
});

test('applyEventDiff applies cell change', () => {
  const state = new ReplaySnapshotState();
  const snap1 = makeSnapshot(2, 2, [makeCell('A')]);
  state.loadInitialSnapshot(snap1);

  const snap2 = makeSnapshot(2, 2, [makeCell('X')]);
  const diff = makeDiff(snap1, snap2, 0, 1);

  const ok = state.applyEventDiff(diff, 1);
  assert.equal(ok, true);
  assert.equal(state.snapshot.cells[0].ch, 'X');
  assert.equal(state.currentSeqGlobal, 1);
  assert.equal(state.applyCount, 1);
});

test('applyEventDiff rejects null diff', () => {
  const state = new ReplaySnapshotState();
  state.loadInitialSnapshot(makeSnapshot(2, 2));
  const ok = state.applyEventDiff(null, 5);
  assert.equal(ok, false);
  assert.equal(state.errorCount, 1);
});

test('applyEventDiff rejects base_seq_global mismatch', () => {
  const state = new ReplaySnapshotState();
  const snap1 = makeSnapshot(2, 2);
  state.loadInitialSnapshot(snap1);
  state._currentSeqGlobal = 5; // estado diz seq 5

  const snap2 = makeSnapshot(2, 2, [makeCell('X')]);
  const diff = makeDiff(snap1, snap2, 0, 1); // diff diz base_seq=0

  const ok = state.applyEventDiff(diff, 1);
  assert.equal(ok, false);
  assert.equal(state.errorCount, 1);
});

test('applyEventDiff rejects base_text_sig mismatch', () => {
  const state = new ReplaySnapshotState();
  const snap1 = makeSnapshot(2, 2, [makeCell('A')]);
  state.loadInitialSnapshot(snap1);

  const snap2 = makeSnapshot(2, 2, [makeCell('X')]);
  const diff = makeDiff(snap1, snap2, 0, 1);
  diff.base_text_sig = 'sha256:' + '0'.repeat(64); // força assinatura errada (formato válido)

  const ok = state.applyEventDiff(diff, 1);
  assert.equal(ok, false);
});

test('applyEventDiff rejects base_visual_sig mismatch', () => {
  const state = new ReplaySnapshotState();
  const snap1 = makeSnapshot(2, 2, [makeCell('A')]);
  state.loadInitialSnapshot(snap1);

  const snap2 = makeSnapshot(2, 2, [makeCell('X')]);
  const diff = makeDiff(snap1, snap2, 0, 1);
  diff.base_visual_sig = 'sha256:' + '0'.repeat(64); // força assinatura errada (formato válido)

  const ok = state.applyEventDiff(diff, 1);
  assert.equal(ok, false);
});

test('applyEventDiff only called once per event', () => {
  const state = new ReplaySnapshotState();
  const snap1 = makeSnapshot(2, 2, [makeCell('A')]);
  state.loadInitialSnapshot(snap1);

  const snap2 = makeSnapshot(2, 2, [makeCell('B')]);
  const diff = makeDiff(snap1, snap2, 0, 1);

  // applyEventDiff increments applyCount
  assert.equal(state.applyCount, 0);
  state.applyEventDiff(diff, 1);
  assert.equal(state.applyCount, 1);
  // apply again with same diff should fail (base_seq mismatch)
  state.applyEventDiff(diff, 1);
  assert.equal(state.applyCount, 1); // still 1
});

test('checkpoint + diffs produces correct final state', () => {
  const state = new ReplaySnapshotState();

  // Checkpoint inicial
  const ckSnap = makeSnapshot(2, 2, [makeCell('A'), makeCell('B')]);
  state.applyCheckpoint(ckSnap, 0);

  // Diff 1: muda A → X
  const snap2 = makeSnapshot(2, 2, [makeCell('X'), makeCell('B')]);
  const diff1 = makeDiff(ckSnap, snap2, 0, 1);
  state.applyEventDiff(diff1, 1);
  assert.equal(state.snapshot.cells[0].ch, 'X');
  assert.equal(state.snapshot.cells[1].ch, 'B');

  // Diff 2: muda B → Y
  const snap3 = makeSnapshot(2, 2, [makeCell('X'), makeCell('Y')]);
  const diff2 = makeDiff(snap2, snap3, 1, 2);
  state.applyEventDiff(diff2, 2);
  assert.equal(state.snapshot.cells[0].ch, 'X');
  assert.equal(state.snapshot.cells[1].ch, 'Y');
  assert.equal(state.currentSeqGlobal, 2);
  assert.equal(state.applyCount, 2);
});

test('geometry change via checkpoint preserves cells', () => {
  const state = new ReplaySnapshotState();
  state.loadInitialSnapshot(makeSnapshot(2, 2, [makeCell('A')]));

  // Checkpoint com resize para 3x3
  const ckSnap = makeSnapshot(3, 3);
  ckSnap.cells[0] = makeCell('A');
  signSnapshot(ckSnap);
  state.applyCheckpoint(ckSnap, 10);

  assert.equal(state.rows, 3);
  assert.equal(state.cols, 3);
  assert.equal(state.snapshot.cells[0].ch, 'A');
  assert.equal(state.snapshot.cells.length, 9);
});

test('renderHtml returns HTML string', () => {
  const state = new ReplaySnapshotState();
  state.loadInitialSnapshot(makeSnapshot(1, 2, [makeCell('A', { fg: 'red' }), makeCell('B')]));
  const html = state.renderHtml();
  assert.equal(typeof html, 'string');
  assert.ok(html.includes('vt-fg-red'));
  assert.ok(html.includes('A'));
});

test('renderText returns text preserving geometry', () => {
  const state = new ReplaySnapshotState();
  state.loadInitialSnapshot(makeSnapshot(1, 2, [makeCell('A'), makeCell('B')]));
  assert.equal(state.renderText(), 'AB');
});

test('clone produces independent copy', () => {
  const state = new ReplaySnapshotState();
  state.loadInitialSnapshot(makeSnapshot(2, 2, [makeCell('A')]));
  const cloned = state.clone();
  assert.equal(cloned.snapshot.cells[0].ch, 'A');

  // Modifica clone - original unchanged
  cloned.snapshot.cells[0].ch = 'Z';
  assert.equal(state.snapshot.cells[0].ch, 'A');
});

test('reset clears all state', () => {
  const state = new ReplaySnapshotState();
  state.loadInitialSnapshot(makeSnapshot(2, 2));
  state.reset();
  assert.equal(state.isInitialized, false);
  assert.equal(state.currentSeqGlobal, 0);
  assert.equal(state.applyCount, 0);
});

test('textSig and visualSig reflect current state', () => {
  const state = new ReplaySnapshotState();
  const snap = makeSnapshot(2, 2, [makeCell('A')]);
  state.loadInitialSnapshot(snap);
  assert.equal(state.textSig, snap.text_sig);
  assert.equal(state.visualSig, snap.visual_sig);
});

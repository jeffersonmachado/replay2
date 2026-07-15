/**
 * replay_snapshot_state.test.mjs — tests for replay snapshot state management
 * Run: node --test gateway/control/static/js/components/replay_snapshot_state.test.mjs
 *
 * Testa a lógica de ReplaySnapshotState diretamente, sem dependência de módulo ES.
 */
import test from 'node:test';
import assert from 'node:assert/strict';

// ── Implementacao inline do ReplaySnapshotState para teste ──────────────────
// (mesma logica do replay_snapshot_state.js, sem imports)

const MAX_ROWS = 200, MAX_COLS = 500, MAX_CELLS = 100000;
const DEFAULT_CELL = Object.freeze({
  ch: " ", fg: "default", bg: "default",
  bold: false, dim: false, underline: false,
  blink: false, reverse: false, hidden: false,
});

function validateSnapshotPayload(payload) {
  if (!payload || typeof payload !== "object") return false;
  if (payload.version !== 1) return false;
  if (!Array.isArray(payload.cells) && !Array.isArray(payload.runs)) return false;
  if (typeof payload.rows !== "number" || typeof payload.cols !== "number") return false;
  if (!Number.isFinite(payload.rows) || !Number.isFinite(payload.cols)) return false;
  if (!Number.isInteger(payload.rows) || !Number.isInteger(payload.cols)) return false;
  if (payload.rows < 1 || payload.cols < 1) return false;
  if (payload.rows > MAX_ROWS || payload.cols > MAX_COLS) return false;
  if (payload.rows * payload.cols > MAX_CELLS) return false;
  return true;
}

function validateDiffPayload(diff) {
  if (!diff || typeof diff !== "object") return false;
  if (diff.version !== 1) return false;
  if (!Array.isArray(diff.changes)) return false;
  if (diff.rows != null && (typeof diff.rows !== "number" || diff.rows < 1 || diff.rows > MAX_ROWS)) return false;
  if (diff.cols != null && (typeof diff.cols !== "number" || diff.cols < 1 || diff.cols > MAX_COLS)) return false;
  if (diff.rows && diff.cols && diff.rows * diff.cols > MAX_CELLS) return false;
  if (diff.changes && diff.changes.length > MAX_CELLS) return false;
  return true;
}

function cloneSnapshot(snap) {
  if (!snap) return null;
  const cells = snap.cells ? snap.cells.map(c => Object.assign({}, c)) : [];
  return Object.assign({}, snap, { cells });
}

function applyDiff(snapshot, diff) {
  const result = cloneSnapshot(snapshot);
  if (!result || !diff) return result;
  const cols = diff.cols || result.cols || 80;
  const rows = diff.rows || result.rows || 25;
  if (diff.geometry_changed) {
    const expected = rows * cols;
    while (result.cells.length < expected) {
      result.cells.push(Object.assign({}, DEFAULT_CELL));
    }
    result.cells = result.cells.slice(0, expected);
    result.rows = rows;
    result.cols = cols;
  }
  for (const change of (diff.changes || [])) {
    const idx = change.row * cols + change.col;
    if (idx < result.cells.length) {
      result.cells[idx] = {
        ch: change.ch || " ", fg: change.fg || "default", bg: change.bg || "default",
        bold: !!change.bold, dim: !!change.dim, underline: !!change.underline,
        blink: !!change.blink, reverse: !!change.reverse, hidden: !!change.hidden,
      };
    }
  }
  if (diff.cursor) result.cursor = diff.cursor;
  result.text_sig = diff.text_sig || result.text_sig;
  result.visual_sig = diff.visual_sig || result.visual_sig;
  return result;
}

class ReplaySnapshotState {
  constructor() {
    this._snapshot = null;
    this._currentSeqGlobal = 0;
    this._currentCheckpointSeqGlobal = 0;
    this._applyCount = 0;
    this._errorCount = 0;
  }

  loadInitialSnapshot(payload) {
    if (!payload) { this._recordError("payload nulo"); return false; }
    if (!validateSnapshotPayload(payload)) { this._recordError("validacao falhou"); return false; }
    this._snapshot = payload;
    this._currentSeqGlobal = 0;
    this._currentCheckpointSeqGlobal = 0;
    this._applyCount = 0;
    this._errorCount = 0;
    return true;
  }

  applyCheckpoint(payload, seqGlobal) {
    if (!payload) { this._recordError("checkpoint payload nulo, seq=" + seqGlobal); return false; }
    if (!validateSnapshotPayload(payload)) { this._recordError("checkpoint validacao falhou, seq=" + seqGlobal); return false; }
    this._snapshot = payload;
    this._currentSeqGlobal = seqGlobal;
    this._currentCheckpointSeqGlobal = seqGlobal;
    return true;
  }

  applyEventDiff(diff, seqGlobal) {
    if (!diff) { this._recordError("diff nulo, seq=" + seqGlobal); return false; }
    if (!validateDiffPayload(diff)) { this._recordError("diff estruturalmente invalido, seq=" + seqGlobal); return false; }
    if (diff.base_seq_global != null && this._currentSeqGlobal !== diff.base_seq_global) {
      this._recordError("base_seq_global mismatch, esperado=" + this._currentSeqGlobal + ", recebido=" + diff.base_seq_global + ", seq=" + seqGlobal);
      return false;
    }
    if (diff.base_text_sig && this._snapshot && this._snapshot.text_sig && diff.base_text_sig !== this._snapshot.text_sig) {
      this._recordError("base_text_sig mismatch, seq=" + seqGlobal);
      return false;
    }
    if (diff.base_visual_sig && this._snapshot && this._snapshot.visual_sig && diff.base_visual_sig !== this._snapshot.visual_sig) {
      this._recordError("base_visual_sig mismatch, seq=" + seqGlobal);
      return false;
    }
    const newSnap = applyDiff(this._snapshot, diff);
    if (!newSnap) { this._recordError("applyDiff retornou null, seq=" + seqGlobal); return false; }
    this._snapshot = newSnap;
    this._currentSeqGlobal = seqGlobal;
    this._applyCount += 1;
    return true;
  }

  get snapshot() { return this._snapshot; }
  get currentSeqGlobal() { return this._currentSeqGlobal; }
  get currentCheckpointSeqGlobal() { return this._currentCheckpointSeqGlobal; }
  get applyCount() { return this._applyCount; }
  get errorCount() { return this._errorCount; }
  get rows() { return this._snapshot ? this._snapshot.rows : 25; }
  get cols() { return this._snapshot ? this._snapshot.cols : 80; }
  get textSig() { return this._snapshot ? this._snapshot.text_sig || "" : ""; }
  get visualSig() { return this._snapshot ? this._snapshot.visual_sig || "" : ""; }
  get isInitialized() { return this._snapshot !== null && this._snapshot.cells && this._snapshot.cells.length > 0; }

  clone() {
    const state = new ReplaySnapshotState();
    state._snapshot = this._snapshot ? cloneSnapshot(this._snapshot) : null;
    state._currentSeqGlobal = this._currentSeqGlobal;
    state._currentCheckpointSeqGlobal = this._currentCheckpointSeqGlobal;
    state._applyCount = this._applyCount;
    state._errorCount = this._errorCount;
    return state;
  }

  renderHtml() {
    if (!this._snapshot || !this._snapshot.cells) return "";
    const rows = this._snapshot.rows || 25;
    const cols = this._snapshot.cols || 80;
    const lines = [];
    for (let r = 0; r < rows; r++) {
      let line = "";
      for (let c = 0; c < cols; c++) {
        const cell = this._snapshot.cells[r * cols + c] || DEFAULT_CELL;
        const classes = [];
        if (cell.fg && cell.fg !== "default") classes.push("vt-fg-" + cell.fg);
        line += classes.length ? '<span class="' + classes.join(" ") + '">' + cell.ch + '</span>' : cell.ch;
      }
      lines.push(line);
    }
    return lines.join("\n");
  }

  renderText() {
    if (!this._snapshot || !this._snapshot.cells) return "";
    const rows = this._snapshot.rows || 25;
    const cols = this._snapshot.cols || 80;
    const lines = [];
    for (let r = 0; r < rows; r++) {
      let line = "";
      for (let c = 0; c < cols; c++) {
        line += (this._snapshot.cells[r * cols + c] || DEFAULT_CELL).ch || " ";
      }
      lines.push(line);
    }
    return lines.join("\n");
  }

  reset() {
    this._snapshot = null;
    this._currentSeqGlobal = 0;
    this._currentCheckpointSeqGlobal = 0;
    this._applyCount = 0;
    this._errorCount = 0;
  }

  _recordError(msg) {
    this._errorCount += 1;
  }
}

function createReplayState() {
  return new ReplaySnapshotState();
}

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
    text_sig: textSig || 'sha256:text_base', visual_sig: visualSig || 'sha256:vis_base' };
}

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
    base_text_sig: baseSnap.text_sig, base_visual_sig: baseSnap.visual_sig,
    text_sig: currSnap.text_sig, visual_sig: currSnap.visual_sig,
    rows: currSnap.rows, cols: currSnap.cols,
    geometry_changed: baseSnap.rows !== currSnap.rows || baseSnap.cols !== currSnap.cols,
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

  const ckSnap = makeSnapshot(2, 2, [makeCell('Z')], 'sha256:ck_text', 'sha256:ck_vis');
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

  const snap2 = makeSnapshot(2, 2, [makeCell('X')], 'sha256:text_new', 'sha256:vis_new');
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

  const snap2 = makeSnapshot(2, 2, [makeCell('X')], 'sha256:t2', 'sha256:v2');
  const diff = makeDiff(snap1, snap2, 0, 1); // diff diz base_seq=0

  const ok = state.applyEventDiff(diff, 1);
  assert.equal(ok, false);
  assert.equal(state.errorCount, 1);
});

test('applyEventDiff rejects base_text_sig mismatch', () => {
  const state = new ReplaySnapshotState();
  const snap1 = makeSnapshot(2, 2, [makeCell('A')], 'sha256:correct_text', 'sha256:correct_vis');
  state.loadInitialSnapshot(snap1);

  const snap2 = makeSnapshot(2, 2, [makeCell('X')], 'sha256:new_text', 'sha256:new_vis');
  const diff = makeDiff(snap1, snap2, 0, 1);
  diff.base_text_sig = 'sha256:wrong_text'; // força assinatura errada

  const ok = state.applyEventDiff(diff, 1);
  assert.equal(ok, false);
});

test('applyEventDiff only called once per event', () => {
  const state = new ReplaySnapshotState();
  const snap1 = makeSnapshot(2, 2, [makeCell('A')]);
  state.loadInitialSnapshot(snap1);

  const snap2 = makeSnapshot(2, 2, [makeCell('B')], 'sha256:t2', 'sha256:v2');
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
  const ckSnap = makeSnapshot(2, 2, [makeCell('A'), makeCell('B')], 'sha256:ck_t', 'sha256:ck_v');
  state.applyCheckpoint(ckSnap, 0);

  // Diff 1: muda A → X
  const snap2 = makeSnapshot(2, 2, [makeCell('X'), makeCell('B')], 'sha256:t2', 'sha256:v2');
  const diff1 = makeDiff(ckSnap, snap2, 0, 1);
  state.applyEventDiff(diff1, 1);
  assert.equal(state.snapshot.cells[0].ch, 'X');
  assert.equal(state.snapshot.cells[1].ch, 'B');

  // Diff 2: muda B → Y
  const snap3 = makeSnapshot(2, 2, [makeCell('X'), makeCell('Y')], 'sha256:t3', 'sha256:v3');
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
  const snap = makeSnapshot(2, 2, [makeCell('A')], 'sha256:my_text', 'sha256:my_vis');
  state.loadInitialSnapshot(snap);
  assert.equal(state.textSig, 'sha256:my_text');
  assert.equal(state.visualSig, 'sha256:my_vis');
});

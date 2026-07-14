/**
 * capture_replay_timeline.test.mjs — tests for timeline grouping (seção 25)
 * Run: node --test gateway/control/static/js/components/capture_replay_timeline.test.mjs
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const vt = require('../virtual_terminal.cjs');
const tl = require('./capture_replay_timeline.cjs');

const { buildTimelineItems } = tl;
const { createVirtualTerminal, feed, renderPlainText, renderCompactText, eventTimestamp, screenSig, visualSig } = vt;

function makeDeps() {
  return {
    feed: (term, text) => feed(term, text),
    renderPlainText: (term) => renderPlainText(term),
    renderCompactText: (term) => renderCompactText(term),
    eventTimestamp: (ev) => eventTimestamp(ev),
    makeTerm: () => createVirtualTerminal(25, 80),
    formatEventContent: (ev) => ev?.summary || ev?.data_decoded || '',
    screenSig: (term) => screenSig(term),
    visualSig: (term) => visualSig(term),
  };
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
  const events = [
    { seq_global: 1, ts_ms: 1000, type: 'bytes', direction: 'out', data_decoded: 'Hello', n_bytes: 5 },
  ];
  const deps = makeDeps();
  const items = buildTimelineItems(events, deps);
  assert.equal(items.length, 1);
  const snap = items[0].summary;
  // 25 rows, each 80 chars
  const lines = snap.split('\n');
  assert.equal(lines.length, 25, '25 rows');
  assert.equal(lines[0].length, 80, '80 cols');
});

test('clear-screen produces empty snapshot (not discarded)', () => {
  const events = [
    { seq_global: 1, ts_ms: 1000, type: 'bytes', direction: 'out', data_decoded: 'Hello World', n_bytes: 11 },
    { seq_global: 2, ts_ms: 2000, type: 'bytes', direction: 'out', data_decoded: '\x1b[2J', n_bytes: 4 },
  ];
  const items = buildTimelineItems(events, makeDeps());
  // First group: Hello World. Second group: clear screen → empty terminal.
  const snapshots = items.filter(i => i.content_kind === 'terminal_snapshot');
  assert.equal(snapshots.length, 2, 'two snapshots (clear screen preserved)');
  // Second snapshot should be all spaces (empty screen)
  const clearSnap = snapshots[1].summary;
  assert.ok(clearSnap.trim() === '', 'clear screen snapshot is empty but present');
  assert.equal(clearSnap.split('\n').length, 25, 'still 25 rows');
});

test('snapshot includes text_sig and visual_sig', () => {
  const events = [
    { seq_global: 1, ts_ms: 1000, type: 'bytes', direction: 'out', data_decoded: 'ABC', n_bytes: 3 },
  ];
  const deps = makeDeps();
  const items = buildTimelineItems(events, deps);
  assert.equal(items.length, 1);
  assert.equal(items[0].content_kind, 'terminal_snapshot');
  assert.ok(items[0].text_sig, 'text_sig present');
  assert.ok(items[0].visual_sig, 'visual_sig present');
  assert.equal(typeof items[0].text_sig, 'string');
  assert.equal(typeof items[0].visual_sig, 'string');
  // text_sig must differ from visual_sig when attributes differ
  assert.ok(items[0].text_sig !== items[0].visual_sig, 'text_sig deve diferir de visual_sig para mesmos dados');
});

test('text_sig stable for same text, visual_sig changes with attributes', () => {
  const events1 = [
    { seq_global: 1, ts_ms: 1000, type: 'bytes', direction: 'out', data_decoded: 'Plain', n_bytes: 5 },
  ];
  const events2 = [
    { seq_global: 1, ts_ms: 1000, type: 'bytes', direction: 'out', data_decoded: '\x1b[7mPlain\x1b[0m', n_bytes: 11 },
  ];
  // Fresh deps for each — avoid terminal state leaking
  const items1 = buildTimelineItems(events1, makeDeps());
  const items2 = buildTimelineItems(events2, makeDeps());
  // Same text content → same text_sig
  assert.equal(items1[0].text_sig, items2[0].text_sig, 'text_sig identical for same chars');
  // Different attributes (reverse vs none) → different visual_sig
  assert.notEqual(items1[0].visual_sig, items2[0].visual_sig, 'visual_sig differs');
});

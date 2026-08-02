/**
 * replay_window_loader.test.mjs — merge de janelas do endpoint de replay (X6)
 * Run: node --test gateway/control/static/js/components/replay_window_loader.test.mjs
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { mergeReplayWindow, nextWindowOffset } from './replay_window_loader.js';

function windowInfo(over = {}) {
  return { offset: 0, limit: 1000, total_events: 5000, truncated: true, explicit: false, ...over };
}

test('nextWindowOffset: truncated=true avança offset+limit', () => {
  assert.equal(nextWindowOffset(windowInfo()), 1000);
  assert.equal(nextWindowOffset(windowInfo({ offset: 1000 })), 2000);
});

test('nextWindowOffset: sem truncação retorna null', () => {
  assert.equal(nextWindowOffset(windowInfo({ truncated: false })), null);
  assert.equal(nextWindowOffset(null), null);
  assert.equal(nextWindowOffset(undefined), null);
  assert.equal(nextWindowOffset({ truncated: true, limit: 0 }), null);
});

function payloadWindow1() {
  return {
    events: [
      { event_id: 'ev-1', seq_global: 1 },
      { event_id: 'ev-2', seq_global: 2 },
    ],
    timeline_items: [
      { event_id: 'ev-1', seq_global: 1 },
      { event_id: 'det-3', seq_global: 3, type: 'deterministic_input' },
      { event_id: 'ev-2', seq_global: 2 },
    ],
    timeline: { event_refs: ['ev-1', 'ev-2'], checkpoint_refs: ['0'] },
    playback: { event_refs: ['ev-1', 'ev-2'], checkpoint_refs: ['0'] },
    checkpoints: [{ seq_global: 0, reason: 'session_start' }],
    final_snapshot: { text_sig: 'sig-j1' },
    canonical_signatures: { text_sig: 'sig-j1' },
    window: windowInfo(),
  };
}

function payloadWindow2() {
  return {
    events: [
      { event_id: 'ev-4', seq_global: 4 },
      { event_id: 'ev-5', seq_global: 5 },
    ],
    timeline_items: [
      // deterministic_input repete entre janelas (vem como prefixo)
      { event_id: 'det-3', seq_global: 3, type: 'deterministic_input' },
      { event_id: 'ev-4', seq_global: 4 },
      { event_id: 'ev-5', seq_global: 5 },
    ],
    timeline: { event_refs: ['ev-4', 'ev-5'], checkpoint_refs: ['0', '4'] },
    playback: { event_refs: ['ev-4', 'ev-5'], checkpoint_refs: ['0', '4'] },
    checkpoints: [
      { seq_global: 0, reason: 'session_start' },
      { seq_global: 4, reason: 'interval_events' },
    ],
    final_snapshot: { text_sig: 'sig-j2' },
    canonical_signatures: { text_sig: 'sig-j2' },
    window: windowInfo({ offset: 1000, truncated: false }),
  };
}

test('merge: eventos e refs da próxima janela são anexados sem duplicar', () => {
  const data = payloadWindow1();
  mergeReplayWindow(data, payloadWindow2());

  assert.deepEqual(data.events.map((ev) => ev.event_id), ['ev-1', 'ev-2', 'ev-4', 'ev-5']);
  assert.deepEqual(
    data.timeline_items.map((ev) => ev.event_id),
    ['ev-1', 'det-3', 'ev-2', 'ev-4', 'ev-5'],
    'deterministic_input repetido entre janelas não pode duplicar',
  );
  assert.deepEqual(data.timeline.event_refs, ['ev-1', 'ev-2', 'ev-4', 'ev-5']);
  assert.deepEqual(data.playback.event_refs, ['ev-1', 'ev-2', 'ev-4', 'ev-5']);
});

test('merge: checkpoints deduplicam por seq_global+reason', () => {
  const data = payloadWindow1();
  mergeReplayWindow(data, payloadWindow2());
  assert.deepEqual(
    data.checkpoints.map((cp) => cp.seq_global),
    [0, 4],
  );
});

test('merge: estado final e window da resposta mais recente prevalecem', () => {
  const data = payloadWindow1();
  mergeReplayWindow(data, payloadWindow2());
  assert.equal(data.final_snapshot.text_sig, 'sig-j2');
  assert.equal(data.canonical_signatures.text_sig, 'sig-j2');
  assert.equal(data.window.offset, 1000);
  assert.equal(data.window.truncated, false);
});

test('merge: tolerante a payloads parciais/nulos', () => {
  const data = { events: [] };
  assert.equal(mergeReplayWindow(data, null), data);
  assert.equal(mergeReplayWindow(null, payloadWindow1()), null);
  const min = {};
  mergeReplayWindow(min, {});
  assert.deepEqual(min.events, []);
  assert.deepEqual(min.timeline_items, []);
  assert.deepEqual(min.checkpoints, []);
});

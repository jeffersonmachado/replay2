import test from "node:test";
import assert from "node:assert/strict";

test("timeline and playback payloads use event_refs instead of duplicating full events", () => {
  const payload = {
    initial_snapshot: {},
    events: [{ event_id: "e1", diff: { version: 1, changes: [] } }],
    checkpoints: [{ checkpoint_id: "c1" }],
    timeline: { event_refs: ["e1"] },
    playback: { event_refs: ["e1"] },
    final_snapshot: {},
  };
  assert.deepEqual(payload.timeline.event_refs, ["e1"]);
  assert.deepEqual(payload.playback.event_refs, ["e1"]);
  assert.equal(Object.hasOwn(payload.timeline, "events"), false);
  assert.equal(Object.hasOwn(payload.playback, "events"), false);
  const serialized = JSON.stringify(payload);
  assert.equal((serialized.match(/"changes"/g) || []).length, 1);
});

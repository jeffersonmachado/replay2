import test from "node:test";
import assert from "node:assert/strict";

import {
  validateSnapshotPayload,
  validateDiffPayload,
  decodeSnapshotPayload,
  applyDiff,
  computeTextSignature,
  computeVisualSignature,
  computeSemanticSignature,
} from "../../../gateway/control/static/js/components/terminal_snapshot_renderer.js";

const goodSig = "sha256:" + "0".repeat(64);

function withSigs(snapshot) {
  snapshot.term = snapshot.term || "xterm";
  snapshot.encoding = snapshot.encoding || "utf-8";
  snapshot.text_sig = computeTextSignature(snapshot);
  snapshot.visual_sig = computeVisualSignature(snapshot);
  snapshot.semantic_sig = computeSemanticSignature(snapshot);
  return snapshot;
}

test("snapshot validation rejects hostile numeric shapes before allocation", () => {
  for (const payload of [
    { version: 1, rows: 2.5, cols: 2, cells: [] },
    { version: 1, rows: NaN, cols: 2, cells: [] },
    { version: 1, rows: Infinity, cols: 2, cells: [] },
    { version: 1, rows: "2", cols: 2, cells: [] },
    { version: 1, rows: 201, cols: 2, cells: [] },
    { version: 1, rows: 200, cols: 501, cells: [] },
    { version: 1, rows: 2, cols: 2, cells: [] },
  ]) {
    assert.equal(validateSnapshotPayload(payload), false);
    assert.equal(decodeSnapshotPayload(payload), null);
  }
});

test("diff validation rejects hostile sequence, resize and coordinate payloads", () => {
  const base = {
    version: 1,
    rows: 2,
    cols: 2,
    seq_global: 0,
    text_sig: goodSig,
    visual_sig: goodSig,
    semantic_sig: goodSig,
    cells: Array.from({ length: 4 }, () => ({ ch: " " })),
  };
  const valid = {
    version: 1,
    base_seq_global: 0,
    seq_global: 1,
    base_text_sig: goodSig,
    base_visual_sig: goodSig,
    base_semantic_sig: goodSig,
    text_sig: goodSig,
    visual_sig: goodSig,
    semantic_sig: goodSig,
    rows: 2,
    cols: 2,
    changes: [{ row: 0, col: 0, ch: "A" }],
  };
  for (const patch of [
    { seq_global: "1" },
    { seq_global: 0 },
    { rows: 1000, cols: 1000 },
    { changes: [{ row: -1, col: 0, ch: "A" }] },
    { changes: [{ row: 99, col: 0, ch: "A" }] },
    { changes: [{ row: 0, col: 0, ch: "A" }, { row: 0, col: 0, ch: "B" }] },
    { text_sig: undefined },
  ]) {
    const diff = Object.assign({}, valid, patch);
    assert.equal(validateDiffPayload(diff, base), false);
  }
});

test("applyDiff refuses to copy declared signatures blindly", () => {
  const base = withSigs({
    version: 1,
    rows: 1,
    cols: 1,
    seq_global: 0,
    cells: [{ ch: " " }],
  });
  const diff = {
    version: 1,
    base_seq_global: 0,
    seq_global: 1,
    base_rows: 1,
    base_cols: 1,
    base_text_sig: base.text_sig,
    base_visual_sig: base.visual_sig,
    base_semantic_sig: base.semantic_sig,
    text_sig: "sha256:" + "1".repeat(64),
    visual_sig: "sha256:" + "2".repeat(64),
    semantic_sig: "sha256:" + "3".repeat(64),
    geometry_changed: false,
    resize: null,
    rows: 1,
    cols: 1,
    changes: [],
  };
  assert.throws(() => applyDiff(base, diff), /signature mismatch/);
});

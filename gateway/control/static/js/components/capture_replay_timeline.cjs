/**
 * capture_replay_timeline.cjs — timeline grouping logic
 *
 * v0.3.19+: NAO interpreta mais ANSI. Usa snapshots/diffs do backend
 * (TerminalEngine Python). O JS terminal so eh usado como oraculo de testes.
 */
(function (root, factory) {
  if (typeof module === 'object' && typeof module.exports === 'object') {
    module.exports = factory();
  } else if (typeof define === 'function' && define.amd) {
    define([], factory);
  } else {
    root.DakTimeline = factory();
  }
}(typeof self !== 'undefined' ? self : typeof window !== 'undefined' ? window : this, function () {
'use strict';

function buildTimelineItems(events, deps) {
  var renderSnapshotToHtml = deps.renderSnapshotToHtml;
  var renderSnapshotToText = deps.renderSnapshotToText;
  var decodeSnapshotPayload = deps.decodeSnapshotPayload;
  var applyDiff = deps.applyDiff;
  var eventTimestamp = deps.eventTimestamp;
  var formatEventContent = deps.formatEventContent;
  var items = [];
  var pendingGroup = null;
  var currentSnapshot = null;

  var flushGroup = function() {
    if (!pendingGroup) return;
    var snap = pendingGroup.lastSnapshot;
    var snapText = "";
    var snapHtml = "";
    if (snap && snap.cells) {
      snapText = typeof renderSnapshotToText === "function" ? renderSnapshotToText(snap) : "";
      snapHtml = typeof renderSnapshotToHtml === "function" ? renderSnapshotToHtml(snap) : "";
    }
    items.push({
      type: "bytes", direction: "out",
      n_bytes: pendingGroup.totalBytes,
      chunk_count: pendingGroup.events.length,
      seq_global: pendingGroup.startSeq,
      seq_global_end: pendingGroup.lastSeq,
      timestamp_ms: pendingGroup.startTs,
      end_timestamp_ms: pendingGroup.lastTs,
      summary: snapText,
      content_kind: "terminal_snapshot",
      snapshot: snap,
      snapshot_html: snapHtml,
      text_sig: pendingGroup.lastTextSig || null,
      visual_sig: pendingGroup.lastVisualSig || null,
    });
    pendingGroup = null;
  };

  for (var i = 0; i < events.length; i++) {
    var ev = events[i];
    var isOut = ev && ev.type === "bytes" && ev.direction === "out";
    if (!isOut) {
      flushGroup();
      if (ev && ev.type === "bytes" && ev.direction === "in") {
        var content = ev.data_decoded || "";
        items.push(Object.assign({}, ev, {
          summary: content === "\x1b" ? "Esc" : content === "\r" ? "Enter" : content,
          data_decoded: content,
        }));
      } else if (ev) {
        var fmt = typeof formatEventContent === "function" ? formatEventContent(ev) : (ev.summary || "");
        items.push(Object.assign({}, ev, { summary: fmt, data_decoded: ev.data_decoded || "" }));
      }
      continue;
    }

    var tsMs = typeof eventTimestamp === "function" ? eventTimestamp(ev) : (ev.ts_ms || 0);
    var seq = Number(ev.seq_global || 0);

    var snap = null;
    if (ev.snapshot_compact && typeof decodeSnapshotPayload === "function") {
      snap = decodeSnapshotPayload(ev.snapshot_compact);
    } else if (ev.snapshot && ev.snapshot.cells) {
      snap = ev.snapshot;
    }

    if (ev.diff && currentSnapshot && typeof applyDiff === "function") {
      currentSnapshot = applyDiff(currentSnapshot, ev.diff);
    } else if (snap) {
      currentSnapshot = snap;
    }

    var belongsToGroup = pendingGroup && (tsMs - pendingGroup.lastTs) <= 700 && pendingGroup.events.length < 8;
    if (!belongsToGroup) flushGroup();

    if (!pendingGroup) {
      pendingGroup = {
        events: [], startTs: tsMs, lastTs: tsMs,
        startSeq: seq, lastSeq: seq, totalBytes: 0,
        lastSnapshot: currentSnapshot,
        lastTextSig: ev.text_sig || null,
        lastVisualSig: ev.visual_sig || null,
      };
    } else {
      pendingGroup.lastTs = tsMs;
      pendingGroup.lastSeq = seq;
      pendingGroup.lastSnapshot = currentSnapshot;
      pendingGroup.lastTextSig = ev.text_sig || pendingGroup.lastTextSig;
      pendingGroup.lastVisualSig = ev.visual_sig || pendingGroup.lastVisualSig;
    }
    pendingGroup.events.push(ev);
    pendingGroup.totalBytes += Number(ev.n_bytes || ev.n || 0);
  }
  flushGroup();
  return items;
}

return { buildTimelineItems: buildTimelineItems };
}));

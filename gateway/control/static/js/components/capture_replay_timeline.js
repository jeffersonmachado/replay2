/**
 * capture_replay_timeline.js — lógica de agrupamento da timeline de replay.
 *
 * v0.3.19+: NÃO interpreta ANSI. Usa snapshots/diffs do backend
 * (TerminalEngine Python). O JS de terminal só é usado como oráculo de testes.
 *
 * Usado por: templates/capture_session_replay.html (import dinâmico).
 * As dependências de renderização são injetadas via `deps`:
 *   - renderSnapshotToHtml, renderSnapshotToText, decodeSnapshotPayload, applyDiff
 *   - eventTimestamp, formatEventContent
 */
export function buildTimelineItems(events, deps) {
  const renderSnapshotToHtml = deps.renderSnapshotToHtml;
  const renderSnapshotToText = deps.renderSnapshotToText;
  const decodeSnapshotPayload = deps.decodeSnapshotPayload;
  const applyDiff = deps.applyDiff;
  const eventTimestamp = deps.eventTimestamp;
  const formatEventContent = deps.formatEventContent;
  const items = [];
  let pendingGroup = null;
  let currentSnapshot = null;

  const flushGroup = () => {
    if (!pendingGroup) return;
    const snap = pendingGroup.lastSnapshot;
    const snapText = snap && snap.cells && typeof renderSnapshotToText === "function" ? renderSnapshotToText(snap) : "";
    const snapHtml = snap && snap.cells && typeof renderSnapshotToHtml === "function" ? renderSnapshotToHtml(snap) : "";
    items.push({
      type: "bytes",
      direction: "out",
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

  for (const ev of events || []) {
    const isOut = ev && ev.type === "bytes" && ev.direction === "out";
    if (!isOut) {
      flushGroup();
      if (ev && ev.type === "bytes" && ev.direction === "in") {
        const content = ev.data_decoded || "";
        items.push(Object.assign({}, ev, {
          summary: content === "\x1b" ? "Esc" : content === "\r" ? "Enter" : content,
          data_decoded: content,
        }));
      } else if (ev) {
        const fmt = typeof formatEventContent === "function" ? formatEventContent(ev) : (ev.summary || "");
        items.push(Object.assign({}, ev, { summary: fmt, data_decoded: ev.data_decoded || "" }));
      }
      continue;
    }

    const tsMs = typeof eventTimestamp === "function" ? eventTimestamp(ev) : (ev.ts_ms || 0);
    const seq = Number(ev.seq_global || 0);

    let snap = null;
    const snapshotPayload = ev.render_snapshot || ev.snapshot_compact;
    if (snapshotPayload && typeof decodeSnapshotPayload === "function") {
      snap = decodeSnapshotPayload(snapshotPayload);
    } else if (ev.snapshot && ev.snapshot.cells) {
      snap = ev.snapshot;
    }

    if (ev.diff && currentSnapshot && typeof applyDiff === "function") {
      currentSnapshot = applyDiff(currentSnapshot, ev.diff);
    } else if (snap) {
      currentSnapshot = snap;
    }

    const belongsToGroup = pendingGroup && (tsMs - pendingGroup.lastTs) <= 700 && pendingGroup.events.length < 8;
    if (!belongsToGroup) flushGroup();

    if (!pendingGroup) {
      pendingGroup = {
        events: [],
        startTs: tsMs,
        lastTs: tsMs,
        startSeq: seq,
        lastSeq: seq,
        totalBytes: 0,
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

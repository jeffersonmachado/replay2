/**
 * capture_replay_timeline.cjs — timeline grouping logic (seção 25)
 *
 * Exports buildTimelineItems for use in capture_session_replay.html
 * and for testing in capture_replay_timeline.test.mjs
 *
 * Dependencies (injected): feed, renderPlainText, renderCompactText,
 *   eventTimestamp, makeTerm, formatEventContentLocal
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

/**
 * @param {Array} events — raw timeline events
 * @param {Object} deps — injected dependencies
 * @param {Function} deps.feed — virtual terminal feed function
 * @param {Function} deps.renderPlainText — renderPlainText(term) -> string
 * @param {Function} deps.renderCompactText — renderCompactText(term) -> string
 * @param {Function} deps.eventTimestamp — eventTimestamp(ev) -> number
 * @param {Function} deps.makeTerm — () -> new virtual terminal
 * @param {Function} [deps.formatEventContent] — formatEventContent(ev, term?) -> string
 */
function buildTimelineItems(events, deps) {
  const { feed, renderPlainText, renderCompactText, eventTimestamp, makeTerm, formatEventContent } = deps;
  const items = [];
  let pendingGroup = null;
  const sessionTerminal = makeTerm();

  const flushGroup = () => {
    if (!pendingGroup) return;
    const snap = renderPlainText ? renderPlainText(sessionTerminal) : renderCompactText(sessionTerminal);
    if (snap !== null && snap !== undefined) {
      items.push({
        type: 'bytes',
        direction: 'out',
        n_bytes: pendingGroup.totalBytes,
        chunk_count: pendingGroup.events.length,
        seq_global: pendingGroup.startSeq,
        seq_global_end: pendingGroup.lastSeq,
        timestamp_ms: pendingGroup.startTs,
        end_timestamp_ms: pendingGroup.lastTs,
        summary: snap,
        data_decoded: snap,
        content_kind: 'terminal_snapshot',
        text_sig: deps.screenSig ? deps.screenSig(sessionTerminal) : null,
        visual_sig: deps.visualSig ? deps.visualSig(sessionTerminal) : null,
      });
    }
    pendingGroup = null;
  };

  for (const ev of events) {
    const isOut = ev && ev.type === 'bytes' && ev.direction === 'out';
    if (!isOut) {
      flushGroup();
      if (ev && ev.type === 'bytes' && ev.direction === 'in') {
        const content = ev.data_decoded || '';
        items.push({
          ...ev,
          summary: content === '\x1b' ? 'Esc' : content === '\r' ? 'Enter' : content,
          data_decoded: content,
        });
      } else if (ev) {
        const fmt = typeof formatEventContent === 'function' ? formatEventContent(ev) : (ev.summary || '');
        items.push({ ...ev, summary: fmt, data_decoded: ev.data_decoded || '' });
      }
      continue;
    }

    const rawText = ev.data_decoded || ev.summary || '';
    const tsMs = typeof eventTimestamp === 'function' ? eventTimestamp(ev) : (ev.ts_ms || 0);
    const seq = Number(ev.seq_global || 0);

    const belongsToGroup = pendingGroup &&
      (tsMs - pendingGroup.lastTs) <= 700 &&
      pendingGroup.events.length < 8;

    if (!belongsToGroup) {
      flushGroup();
    }

    if (!pendingGroup) {
      pendingGroup = {
        events: [],
        startTs: tsMs,
        lastTs: tsMs,
        startSeq: seq,
        lastSeq: seq,
        totalBytes: 0,
      };
    } else {
      pendingGroup.lastTs = tsMs;
      pendingGroup.lastSeq = seq;
    }

    feed(sessionTerminal, rawText);
    pendingGroup.events.push(ev);
    pendingGroup.totalBytes += Number(ev.n_bytes || ev.n || 0);
  }

  flushGroup();
  return items;
}

return { buildTimelineItems };

}));

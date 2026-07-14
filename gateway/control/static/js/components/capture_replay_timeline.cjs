/**
 * capture_replay_timeline.cjs — timeline grouping logic
 *
 * Exports buildTimelineItems for use in capture_session_replay.html
 * and for testing in capture_replay_timeline.test.mjs
 *
 * Dependencies (injected): feedBase64, renderPlainText, renderSnapshot,
 *   renderSnapshotHtml, eventTimestamp, makeTerm, formatEventContentLocal
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
 * @param {Function} deps.feedBase64 — feedBase64(term, dataB64, encoding) incremental feed
 * @param {Function} deps.feed — feed(term, text) — fallback legado
 * @param {Function} deps.renderPlainText — renderPlainText(term) -> string
 * @param {Function} deps.renderSnapshot — renderSnapshot(term) -> canonical snapshot object
 * @param {Function} deps.renderSnapshotHtml — renderSnapshotHtml(snapshot) -> HTML string
 * @param {Function} deps.eventTimestamp — eventTimestamp(ev) -> number
 * @param {Function} deps.makeTerm — () -> new virtual terminal
 * @param {Function} [deps.formatEventContent] — formatEventContent(ev, term?) -> string
 * @param {Function} [deps.screenSig] — screenSig(term) -> string
 * @param {Function} [deps.visualSig] — visualSig(term) -> string
 * @param {string} [deps.encoding] — encoding da sessao (utf-8, cp850, etc.)
 */
function buildTimelineItems(events, deps) {
  var feedBase64 = deps.feedBase64;
  var feed = deps.feed;
  var renderPlainText = deps.renderPlainText;
  var renderSnapshot = deps.renderSnapshot;
  var renderSnapshotHtml = deps.renderSnapshotHtml;
  var eventTimestamp = deps.eventTimestamp;
  var makeTerm = deps.makeTerm;
  var formatEventContent = deps.formatEventContent;
  var encoding = deps.encoding || 'utf-8';
  var items = [];
  var pendingGroup = null;
  var sessionTerminal = makeTerm();

  var flushGroup = function() {
    if (!pendingGroup) return;
    // Snapshot canonico com celulas e atributos
    var snapshot = renderSnapshot ? renderSnapshot(sessionTerminal) : null;
    var snapText = renderPlainText ? renderPlainText(sessionTerminal) : '';
    var snapHtml = null;
    if (renderSnapshotHtml && snapshot) {
      snapHtml = renderSnapshotHtml(snapshot);
    }
    if (snapText !== null && snapText !== undefined) {
      var item = {
        type: 'bytes',
        direction: 'out',
        n_bytes: pendingGroup.totalBytes,
        chunk_count: pendingGroup.events.length,
        seq_global: pendingGroup.startSeq,
        seq_global_end: pendingGroup.lastSeq,
        timestamp_ms: pendingGroup.startTs,
        end_timestamp_ms: pendingGroup.lastTs,
        summary: snapText,
        data_decoded: snapText,
        data_b64: pendingGroup.dataB64List ? pendingGroup.dataB64List.join('') : '',
        content_kind: 'terminal_snapshot',
        snapshot: snapshot,
        snapshot_html: snapHtml,
        text_sig: deps.screenSig ? deps.screenSig(sessionTerminal) : null,
        visual_sig: deps.visualSig ? deps.visualSig(sessionTerminal) : null,
      };
      items.push(item);
    }
    pendingGroup = null;
  };

  for (var i = 0; i < events.length; i++) {
    var ev = events[i];
    var isOut = ev && ev.type === 'bytes' && ev.direction === 'out';
    if (!isOut) {
      flushGroup();
      if (ev && ev.type === 'bytes' && ev.direction === 'in') {
        var content = ev.data_decoded || '';
        items.push(Object.assign({}, ev, {
          summary: content === '\x1b' ? 'Esc' : content === '\r' ? 'Enter' : content,
          data_decoded: content,
        }));
      } else if (ev) {
        var fmt = typeof formatEventContent === 'function' ? formatEventContent(ev) : (ev.summary || '');
        items.push(Object.assign({}, ev, { summary: fmt, data_decoded: ev.data_decoded || '' }));
      }
      continue;
    }

    // Usa feedBase64 com data_b64 quando disponivel (fluxo principal)
    // Fallback para feed com data_decoded (legado)
    var dataB64 = ev.data_b64 || '';
    var rawText = ev.data_decoded || ev.summary || '';
    var tsMs = typeof eventTimestamp === 'function' ? eventTimestamp(ev) : (ev.ts_ms || 0);
    var seq = Number(ev.seq_global || 0);

    var belongsToGroup = pendingGroup &&
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
        dataB64List: [],
      };
    } else {
      pendingGroup.lastTs = tsMs;
      pendingGroup.lastSeq = seq;
    }

    // Alimenta o terminal: prefere data_b64 com decoder incremental
    if (dataB64 && typeof feedBase64 === 'function') {
      feedBase64(sessionTerminal, dataB64, encoding);
      if (pendingGroup.dataB64List) pendingGroup.dataB64List.push(dataB64);
    } else if (rawText && typeof feed === 'function') {
      // Fallback legado: alimenta com texto ja decodificado
      feed(sessionTerminal, rawText);
    }

    pendingGroup.events.push(ev);
    pendingGroup.totalBytes += Number(ev.n_bytes || ev.n || 0);
  }

  flushGroup();
  return items;
}

return { buildTimelineItems: buildTimelineItems };

}));

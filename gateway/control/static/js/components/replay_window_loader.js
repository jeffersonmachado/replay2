/**
 * replay_window_loader.js — composição de janelas do endpoint de replay (X6).
 *
 * O endpoint /api/captures/{id}/replay atende sessões enormes em fatias
 * (offset/limit) e marca window.truncated=true enquanto houver mais eventos.
 * Este módulo calcula a próxima janela e faz o merge incremental da resposta
 * no payload já carregado, sem duplicar eventos deterministic_input e
 * checkpoints (que vêm como prefixo completo em toda resposta).
 * Lógica pura, testável sem DOM.
 */

/**
 * Offset da próxima janela a buscar, ou null quando não há mais eventos.
 */
export function nextWindowOffset(windowInfo) {
  if (!windowInfo || windowInfo.truncated !== true) return null;
  const offset = Number(windowInfo.offset || 0);
  const limit = Number(windowInfo.limit || 0);
  if (!Number.isFinite(offset) || !Number.isFinite(limit) || limit <= 0) return null;
  return offset + limit;
}

function appendUnique(target, items, keyFn) {
  const seen = new Set(target.map(keyFn));
  let added = 0;
  for (const item of items || []) {
    const key = keyFn(item);
    if (key == null || key === '' || seen.has(key)) continue;
    seen.add(key);
    target.push(item);
    added += 1;
  }
  return added;
}

/**
 * Funde a resposta de uma janela seguinte no replayData carregado.
 * - events/timeline_items: dedup por event_id (deterministic_input e
 *   checkpoints repetem entre janelas — vêm como prefixo);
 * - event_refs/checkpoint_refs de timeline e playback: dedup por valor;
 * - final_snapshot/canonical_signatures: versão mais recente prevalece;
 * - window: substituído pelo da nova resposta.
 * Retorna o próprio replayData (mutado).
 */
export function mergeReplayWindow(replayData, payload) {
  if (!replayData || !payload || typeof payload !== 'object') return replayData;

  const eventKey = (ev) => String(ev?.event_id ?? ev?.seq_global ?? '');

  if (!Array.isArray(replayData.events)) replayData.events = [];
  appendUnique(replayData.events, payload.events || [], eventKey);

  if (!Array.isArray(replayData.timeline_items)) replayData.timeline_items = [];
  appendUnique(replayData.timeline_items, payload.timeline_items || [], eventKey);

  for (const viewName of ['timeline', 'playback']) {
    const view = replayData[viewName];
    const newView = payload[viewName];
    if (!view || !newView) continue;
    if (Array.isArray(view.event_refs) && Array.isArray(newView.event_refs)) {
      appendUnique(view.event_refs, newView.event_refs, (ref) => String(ref));
    }
    if (Array.isArray(view.checkpoint_refs) && Array.isArray(newView.checkpoint_refs)) {
      appendUnique(view.checkpoint_refs, newView.checkpoint_refs, (ref) => String(ref));
    }
  }

  if (!Array.isArray(replayData.checkpoints)) replayData.checkpoints = [];
  appendUnique(
    replayData.checkpoints,
    payload.checkpoints || [],
    (cp) => `${cp?.seq_global ?? ''}:${cp?.reason || ''}`,
  );

  if (payload.final_snapshot) replayData.final_snapshot = payload.final_snapshot;
  if (payload.canonical_signatures) replayData.canonical_signatures = payload.canonical_signatures;
  if (payload.checkpoints_capped !== undefined) replayData.checkpoints_capped = payload.checkpoints_capped;
  if (payload.window) replayData.window = payload.window;

  return replayData;
}

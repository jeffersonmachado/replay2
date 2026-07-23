/**
 * checkpoint_seek.js — localização de checkpoint para seek no playback.
 *
 * Usado por: templates/capture_session_replay.html (import dinâmico).
 */

/**
 * Retorna o melhor checkpoint para um seq_global alvo:
 * o de maior seq_global que ainda seja <= targetSeqGlobal.
 * @param {Array<object>} checkpoints - checkpoints com campo seq_global
 * @param {number} targetSeqGlobal - seq_global do evento alvo
 * @returns {object|null} checkpoint escolhido ou null se nenhum serve
 */
export function findBestCheckpoint(checkpoints, targetSeqGlobal) {
  let best = null;
  for (const cp of checkpoints || []) {
    const cpSeq = cp.seq_global || 0;
    if (cpSeq <= targetSeqGlobal && (!best || cpSeq > (best.seq_global || 0))) {
      best = cp;
    }
  }
  return best;
}

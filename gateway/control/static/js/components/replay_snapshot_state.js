/**
 * replay_snapshot_state.js — estado de playback baseado em snapshots canonicos
 *
 * NÃO interpreta ANSI, ESC, CSI, OSC, SGR.
 * Apenas gerencia snapshots, diffs e assinaturas do TerminalEngine Python.
 *
 * Responsabilidades:
 *   - inicializar estado a partir de initial_snapshot
 *   - aplicar diff (delega para terminal_snapshot_renderer.js)
 *   - aplicar checkpoint (snapshot completo)
 *   - validar base (seq_global, text_sig, visual_sig)
 *   - validar resultado apos aplicar diff
 *   - manter currentSeqGlobal
 *   - manter currentCheckpointSeqGlobal
 *   - lancar erro claro em inconsistencia
 */
import {
  decodeSnapshotPayload,
  validateSnapshotPayload,
  validateDiffPayload,
  applyDiff,
  cloneSnapshot,
  renderSnapshotToHtml,
  renderSnapshotToText,
} from "./terminal_snapshot_renderer.js";

// ── Constantes ──────────────────────────────────────────────────────────────

const DEFAULT_GEOMETRY = Object.freeze({ rows: 25, cols: 80 });

// ── ReplaySnapshotState ─────────────────────────────────────────────────────

export class ReplaySnapshotState {
  constructor() {
    this._snapshot = null;
    this._currentSeqGlobal = 0;
    this._currentCheckpointSeqGlobal = 0;
    this._applyCount = 0;
    this._errorCount = 0;
  }

  // ── Inicializacao ──────────────────────────────────────────────────────

  /**
   * Carrega snapshot inicial a partir do payload do backend.
   * @param {object} initialSnapshotPayload - payload compacto do backend
   */
  loadInitialSnapshot(initialSnapshotPayload) {
    if (!initialSnapshotPayload) {
      this._recordError("loadInitialSnapshot: payload nulo");
      return false;
    }
    const snap = decodeSnapshotPayload(initialSnapshotPayload);
    if (!snap || !snap.cells) {
      this._recordError("loadInitialSnapshot: decode falhou");
      return false;
    }
    if (!validateSnapshotPayload(snap)) {
      this._recordError("loadInitialSnapshot: validacao falhou");
      return false;
    }
    this._snapshot = snap;
    this._currentSeqGlobal = 0;
    this._currentCheckpointSeqGlobal = 0;
    this._applyCount = 0;
    this._errorCount = 0;
    return true;
  }

  // ── Aplicar checkpoint ─────────────────────────────────────────────────

  /**
   * Substitui estado atual por um checkpoint (snapshot completo).
   * @param {object} checkpointPayload - payload compacto do checkpoint
   * @param {number} seqGlobal - seq_global do checkpoint
   */
  applyCheckpoint(checkpointPayload, seqGlobal) {
    if (!checkpointPayload) {
      this._recordError("applyCheckpoint: payload nulo, seq=" + seqGlobal);
      return false;
    }
    const snap = decodeSnapshotPayload(checkpointPayload);
    if (!snap || !snap.cells) {
      this._recordError("applyCheckpoint: decode falhou, seq=" + seqGlobal);
      return false;
    }
    if (!validateSnapshotPayload(snap)) {
      this._recordError("applyCheckpoint: validacao falhou, seq=" + seqGlobal);
      return false;
    }
    this._snapshot = snap;
    this._currentSeqGlobal = seqGlobal;
    this._currentCheckpointSeqGlobal = seqGlobal;
    return true;
  }

  // ── Aplicar diff ───────────────────────────────────────────────────────

  /**
   * Aplica um diff ao estado atual.
   * Valida base_seq_global, base_text_sig e base_visual_sig antes de aplicar.
   * @param {object} diff - diff do backend
   * @param {number} seqGlobal - seq_global do evento
   * @returns {boolean} true se aplicado com sucesso
   */
  applyEventDiff(diff, seqGlobal) {
    if (!diff) {
      this._recordError("applyEventDiff: diff nulo, seq=" + seqGlobal);
      return false;
    }

    // Valida estrutura do diff
    if (!validateDiffPayload(diff)) {
      this._recordError("applyEventDiff: diff estruturalmente invalido, seq=" + seqGlobal);
      return false;
    }

    // Valida sequencia: diff deve vir depois do estado atual
    if (diff.base_seq_global != null && this._currentSeqGlobal !== diff.base_seq_global) {
      this._recordError(
        "applyEventDiff: base_seq_global mismatch, esperado=" + this._currentSeqGlobal +
        ", recebido=" + diff.base_seq_global + ", seq=" + seqGlobal
      );
      return false;
    }

    // Valida assinatura-base
    if (diff.base_text_sig && this._snapshot && this._snapshot.text_sig) {
      if (diff.base_text_sig !== this._snapshot.text_sig) {
        this._recordError(
          "applyEventDiff: base_text_sig mismatch, seq=" + seqGlobal
        );
        return false;
      }
    }

    if (diff.base_visual_sig && this._snapshot && this._snapshot.visual_sig) {
      if (diff.base_visual_sig !== this._snapshot.visual_sig) {
        this._recordError(
          "applyEventDiff: base_visual_sig mismatch, seq=" + seqGlobal
        );
        return false;
      }
    }

    // Aplica diff
    const newSnap = applyDiff(this._snapshot, diff);
    if (!newSnap) {
      this._recordError("applyEventDiff: applyDiff retornou null, seq=" + seqGlobal);
      return false;
    }

    // Valida resultado
    if (diff.text_sig && newSnap.text_sig !== diff.text_sig) {
      this._recordError(
        "applyEventDiff: text_sig apos diff nao confere, seq=" + seqGlobal +
        ", esperado=" + diff.text_sig + ", obtido=" + newSnap.text_sig
      );
    }

    this._snapshot = newSnap;
    this._currentSeqGlobal = seqGlobal;
    this._applyCount += 1;
    return true;
  }

  // ── Acesso ao estado ───────────────────────────────────────────────────

  get snapshot() {
    return this._snapshot;
  }

  get currentSeqGlobal() {
    return this._currentSeqGlobal;
  }

  get currentCheckpointSeqGlobal() {
    return this._currentCheckpointSeqGlobal;
  }

  get applyCount() {
    return this._applyCount;
  }

  get errorCount() {
    return this._errorCount;
  }

  get rows() {
    return this._snapshot ? this._snapshot.rows : DEFAULT_GEOMETRY.rows;
  }

  get cols() {
    return this._snapshot ? this._snapshot.cols : DEFAULT_GEOMETRY.cols;
  }

  get textSig() {
    return this._snapshot ? this._snapshot.text_sig || "" : "";
  }

  get visualSig() {
    return this._snapshot ? this._snapshot.visual_sig || "" : "";
  }

  get isInitialized() {
    return this._snapshot !== null && this._snapshot.cells && this._snapshot.cells.length > 0;
  }

  // ── Renderizacao ───────────────────────────────────────────────────────

  renderHtml() {
    if (!this._snapshot) return "";
    return renderSnapshotToHtml(this._snapshot);
  }

  renderText() {
    if (!this._snapshot) return "";
    return renderSnapshotToText(this._snapshot);
  }

  // ── Clone ──────────────────────────────────────────────────────────────

  clone() {
    const state = new ReplaySnapshotState();
    state._snapshot = this._snapshot ? cloneSnapshot(this._snapshot) : null;
    state._currentSeqGlobal = this._currentSeqGlobal;
    state._currentCheckpointSeqGlobal = this._currentCheckpointSeqGlobal;
    state._applyCount = this._applyCount;
    state._errorCount = this._errorCount;
    return state;
  }

  // ── Reset ──────────────────────────────────────────────────────────────

  reset() {
    this._snapshot = null;
    this._currentSeqGlobal = 0;
    this._currentCheckpointSeqGlobal = 0;
    this._applyCount = 0;
    this._errorCount = 0;
  }

  // ── Interno ────────────────────────────────────────────────────────────

  _recordError(msg) {
    this._errorCount += 1;
    if (typeof console !== "undefined") {
      console.error("ReplaySnapshotState: " + msg);
    }
  }
}

// ── Factory function ────────────────────────────────────────────────────────

export function createReplayState() {
  return new ReplaySnapshotState();
}

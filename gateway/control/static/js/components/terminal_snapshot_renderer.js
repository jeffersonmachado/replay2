/**
 * terminal_snapshot_renderer.js — renderizador de snapshots canonicos
 *
 * NÃO interpreta ANSI, ESC, CSI, OSC, SGR, DEC Graphics.
 * Apenas renderiza células a partir de snapshots/diffs produzidos
 * pelo TerminalEngine Python (fonte oficial).
 *
 * Responsabilidades:
 *   - validar snapshot
 *   - validar diff
 *   - aplicar diff (applyDiff)
 *   - clone snapshot
 *   - produzir DOM seguro com atributos
 *   - agrupar células consecutivas com atributos iguais
 */
import { escapeHtml } from "../core/dom.js";

// ── Constantes ──────────────────────────────────────────────────────────────

const DEFAULT_CELL = Object.freeze({
  ch: " ", fg: "default", bg: "default",
  bold: false, dim: false, underline: false,
  blink: false, reverse: false, hidden: false,
});

// ── Validação ───────────────────────────────────────────────────────────────

export function validateSnapshotPayload(payload) {
  if (!payload || typeof payload !== "object") return false;
  if (payload.version !== 1) return false;
  if (!Array.isArray(payload.cells) && !Array.isArray(payload.runs)) return false;
  if (typeof payload.rows !== "number" || typeof payload.cols !== "number") return false;
  if (payload.rows < 1 || payload.cols < 1) return false;
  return true;
}

export function validateDiffPayload(diff) {
  if (!diff || typeof diff !== "object") return false;
  if (diff.version !== 1) return false;
  if (!Array.isArray(diff.changes)) return false;
  return true;
}

// ── Clone ───────────────────────────────────────────────────────────────────

export function cloneSnapshot(snap) {
  if (!snap) return null;
  const cells = snap.cells
    ? snap.cells.map(c => Object.assign({}, c))
    : [];
  return Object.assign({}, snap, { cells });
}

// ── Decode compact ──────────────────────────────────────────────────────────

export function decodeSnapshotPayload(payload) {
  // Se já tem cells, é snapshot completo
  if (payload.cells) return payload;

  // Decodifica formato compacto (runs + attribute_table)
  if (payload.runs && payload.attribute_table) {
    const rows = payload.rows || 25;
    const cols = payload.cols || 80;
    const attrTable = payload.attribute_table || [];
    const cells = Array.from({ length: rows * cols }, () => Object.assign({}, DEFAULT_CELL));

    for (const run of (payload.runs || [])) {
      const r = run.row || 0;
      const c = run.col || 0;
      const len = run.length || 0;
      const text = run.text || "";
      const attrs = attrTable[run.attr] || DEFAULT_CELL;

      for (let offset = 0; offset < Math.min(len, text.length); offset++) {
        const idx = r * cols + c + offset;
        if (idx < cells.length) {
          cells[idx] = Object.assign({}, attrs, { ch: text[offset] || " " });
        }
      }
    }

    return Object.assign({}, payload, { cells });
  }

  return null;
}

// ── Apply diff ──────────────────────────────────────────────────────────────

export function applyDiff(snapshot, diff) {
  const result = cloneSnapshot(snapshot);
  if (!result || !diff) return result;

  const cols = diff.cols || result.cols || 80;
  const rows = diff.rows || result.rows || 25;

  if (diff.geometry_changed) {
    const expected = rows * cols;
    while (result.cells.length < expected) {
      result.cells.push(Object.assign({}, DEFAULT_CELL));
    }
    result.cells = result.cells.slice(0, expected);
    result.rows = rows;
    result.cols = cols;
  }

  for (const change of (diff.changes || [])) {
    const idx = change.row * cols + change.col;
    if (idx < result.cells.length) {
      result.cells[idx] = {
        ch: change.ch || " ",
        fg: change.fg || "default",
        bg: change.bg || "default",
        bold: !!change.bold,
        dim: !!change.dim,
        underline: !!change.underline,
        blink: !!change.blink,
        reverse: !!change.reverse,
        hidden: !!change.hidden,
      };
    }
  }

  if (diff.cursor) {
    result.cursor = diff.cursor;
  }
  result.text_sig = diff.text_sig || result.text_sig;
  result.visual_sig = diff.visual_sig || result.visual_sig;

  return result;
}

// ── Render ──────────────────────────────────────────────────────────────────

/**
 * Renderiza snapshot para HTML seguro.
 * Agrupa células consecutivas com atributos efetivos idênticos.
 * Calcula effectiveFg/Bg considerando reverse.
 */
export function renderSnapshotToHtml(snapshot) {
  if (!snapshot || !snapshot.cells) return "";

  const rows = snapshot.rows || 25;
  const cols = snapshot.cols || 80;
  const cells = snapshot.cells;
  const lines = [];

  for (let r = 0; r < rows; r++) {
    let lineHtml = "";
    let inSpan = null;

    for (let c = 0; c < cols; c++) {
      const idx = r * cols + c;
      const cell = (idx < cells.length) ? cells[idx] : DEFAULT_CELL;
      const ch = escapeHtml(cell.ch || " ");

      const effectiveFg = cell.reverse ? cell.bg : cell.fg;
      const effectiveBg = cell.reverse ? cell.fg : cell.bg;

      const classes = [];
      if (effectiveFg !== "default" && effectiveFg !== null && effectiveFg !== undefined) {
        classes.push("vt-fg-" + effectiveFg);
      }
      if (effectiveBg !== "default" && effectiveBg !== null && effectiveBg !== undefined) {
        classes.push("vt-bg-" + effectiveBg);
      }
      if (cell.bold) classes.push("vt-bold");
      if (cell.dim) classes.push("vt-dim");
      if (cell.underline) classes.push("vt-underline");
      if (cell.blink) classes.push("vt-blink");
      if (cell.reverse) classes.push("vt-reverse");
      if (cell.hidden) classes.push("vt-hidden");

      const cls = classes.join(" ") || "";

      if (cls !== inSpan) {
        if (inSpan) lineHtml += "</span>";
        if (cls) lineHtml += '<span class="' + cls + '">';
        inSpan = cls || null;
      }
      lineHtml += ch;
    }
    if (inSpan) lineHtml += "</span>";
    lines.push(lineHtml);
  }

  return lines.join("\n");
}

/**
 * Renderiza snapshot como texto puro (preserva geometria).
 */
export function renderSnapshotToText(snapshot) {
  if (!snapshot || !snapshot.cells) return "";

  const rows = snapshot.rows || 25;
  const cols = snapshot.cols || 80;
  const cells = snapshot.cells;
  const lines = [];

  for (let r = 0; r < rows; r++) {
    let line = "";
    for (let c = 0; c < cols; c++) {
      const idx = r * cols + c;
      line += (idx < cells.length) ? (cells[idx].ch || " ") : " ";
    }
    lines.push(line);
  }

  return lines.join("\n");
}

/**
 * Renderiza cursor como indicador visual.
 */
export function renderCursor(cursor, rows, cols) {
  if (!cursor || !cursor.visible) return null;
  return {
    row: Math.max(0, Math.min(rows - 1, cursor.row || 0)),
    col: Math.max(0, Math.min(cols - 1, cursor.col || 0)),
  };
}

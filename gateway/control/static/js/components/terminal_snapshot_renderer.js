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

const MAX_ROWS = 200;
const MAX_COLS = 500;
const MAX_CELLS = 100000;
const MAX_DIFF_CHANGES = 100000;
const SIG_RE = /^sha256:[0-9a-f]{64}$/i;

function isInteger(value) {
  return typeof value === "number" && Number.isFinite(value) && Number.isInteger(value);
}

function validSig(value) {
  return typeof value === "string" && SIG_RE.test(value);
}

function normalizeCellForSignature(cell) {
  return {
    ch: cell?.ch || " ",
    fg: cell?.fg || "default",
    bg: cell?.bg || "default",
    bold: !!cell?.bold,
    dim: !!cell?.dim,
    underline: !!cell?.underline,
    blink: !!cell?.blink,
    reverse: !!cell?.reverse,
    hidden: !!cell?.hidden,
  };
}

function rightRotate(value, amount) {
  return (value >>> amount) | (value << (32 - amount));
}

function sha256Hex(text) {
  const bytes = Array.from(new TextEncoder().encode(text));
  const bitLen = bytes.length * 8;
  bytes.push(0x80);
  while ((bytes.length % 64) !== 56) bytes.push(0);
  const high = Math.floor(bitLen / 0x100000000);
  const low = bitLen >>> 0;
  for (const value of [high, low]) {
    bytes.push((value >>> 24) & 0xff, (value >>> 16) & 0xff, (value >>> 8) & 0xff, value & 0xff);
  }
  const k = [
    0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5,0x3956c25b,0x59f111f1,0x923f82a4,0xab1c5ed5,
    0xd807aa98,0x12835b01,0x243185be,0x550c7dc3,0x72be5d74,0x80deb1fe,0x9bdc06a7,0xc19bf174,
    0xe49b69c1,0xefbe4786,0x0fc19dc6,0x240ca1cc,0x2de92c6f,0x4a7484aa,0x5cb0a9dc,0x76f988da,
    0x983e5152,0xa831c66d,0xb00327c8,0xbf597fc7,0xc6e00bf3,0xd5a79147,0x06ca6351,0x14292967,
    0x27b70a85,0x2e1b2138,0x4d2c6dfc,0x53380d13,0x650a7354,0x766a0abb,0x81c2c92e,0x92722c85,
    0xa2bfe8a1,0xa81a664b,0xc24b8b70,0xc76c51a3,0xd192e819,0xd6990624,0xf40e3585,0x106aa070,
    0x19a4c116,0x1e376c08,0x2748774c,0x34b0bcb5,0x391c0cb3,0x4ed8aa4a,0x5b9cca4f,0x682e6ff3,
    0x748f82ee,0x78a5636f,0x84c87814,0x8cc70208,0x90befffa,0xa4506ceb,0xbef9a3f7,0xc67178f2,
  ];
  const h = [0x6a09e667,0xbb67ae85,0x3c6ef372,0xa54ff53a,0x510e527f,0x9b05688c,0x1f83d9ab,0x5be0cd19];
  const w = new Array(64);
  for (let offset = 0; offset < bytes.length; offset += 64) {
    for (let i = 0; i < 16; i++) {
      const j = offset + i * 4;
      w[i] = ((bytes[j] << 24) | (bytes[j + 1] << 16) | (bytes[j + 2] << 8) | bytes[j + 3]) >>> 0;
    }
    for (let i = 16; i < 64; i++) {
      const s0 = (rightRotate(w[i - 15], 7) ^ rightRotate(w[i - 15], 18) ^ (w[i - 15] >>> 3)) >>> 0;
      const s1 = (rightRotate(w[i - 2], 17) ^ rightRotate(w[i - 2], 19) ^ (w[i - 2] >>> 10)) >>> 0;
      w[i] = (w[i - 16] + s0 + w[i - 7] + s1) >>> 0;
    }
    let [a,b,c,d,e,f,g,hh] = h;
    for (let i = 0; i < 64; i++) {
      const S1 = (rightRotate(e, 6) ^ rightRotate(e, 11) ^ rightRotate(e, 25)) >>> 0;
      const ch = ((e & f) ^ (~e & g)) >>> 0;
      const temp1 = (hh + S1 + ch + k[i] + w[i]) >>> 0;
      const S0 = (rightRotate(a, 2) ^ rightRotate(a, 13) ^ rightRotate(a, 22)) >>> 0;
      const maj = ((a & b) ^ (a & c) ^ (b & c)) >>> 0;
      const temp2 = (S0 + maj) >>> 0;
      hh = g; g = f; f = e; e = (d + temp1) >>> 0; d = c; c = b; b = a; a = (temp1 + temp2) >>> 0;
    }
    for (let i = 0; i < 8; i++) h[i] = (h[i] + [a,b,c,d,e,f,g,hh][i]) >>> 0;
  }
  return h.map((value) => value.toString(16).padStart(8, "0")).join("");
}

function codepoints(ch) {
  return Array.from(ch || " ").map((c) => String(c.codePointAt(0))).join("+");
}

function cellFlags(cell) {
  return (cell.bold ? 1 : 0) |
    (cell.dim ? 2 : 0) |
    (cell.underline ? 4 : 0) |
    (cell.blink ? 8 : 0) |
    (cell.reverse ? 16 : 0) |
    (cell.hidden ? 32 : 0);
}

export function computeTextSignature(snapshot) {
  const rows = snapshot?.rows || 0;
  const cols = snapshot?.cols || 0;
  const cells = snapshot?.cells || [];
  const parts = ["DKT-TEXT", "1", String(rows), String(cols), String(snapshot?.encoding || "utf-8"), String(snapshot?.term || "xterm")];
  for (const cell of cells) parts.push(codepoints(cell?.ch || " "));
  return "sha256:" + sha256Hex(parts.join("\n") + "\n");
}

export function computeVisualSignature(snapshot) {
  const rows = snapshot?.rows || 0;
  const cols = snapshot?.cols || 0;
  const cells = (snapshot?.cells || []).map(normalizeCellForSignature);
  const parts = ["DKT-VISUAL", "1", String(rows), String(cols), String(snapshot?.encoding || "utf-8"), String(snapshot?.term || "xterm")];
  for (const cell of cells) {
    parts.push(`${codepoints(cell.ch)}|${cell.fg}|${cell.bg}|${cellFlags(cell)}`);
  }
  return "sha256:" + sha256Hex(parts.join("\n") + "\n");
}

export function computeSemanticSignature(snapshot) {
  const rows = snapshot?.rows || 0;
  const cols = snapshot?.cols || 0;
  const cells = snapshot?.cells || [];
  const lines = [];
  const box = new Set(Array.from("┌┐└┘├┤┬┴┼─│"));
  for (let row = 0; row < rows; row++) {
    let line = "";
    for (let col = 0; col < cols; col++) {
      let ch = cells[row * cols + col]?.ch || " ";
      if (box.has(ch)) ch = "#";
      line += ch;
    }
    lines.push(line.replace(/\s+$/u, ""));
  }
  while (lines.length && !lines[0].trim()) lines.shift();
  while (lines.length && !lines[lines.length - 1].trim()) lines.pop();
  return "sha256:" + sha256Hex(lines.join("\n"));
}

function isCanonicalDiff(diff) {
  return diff && (
    diff.base_seq_global !== undefined ||
    diff.seq_global !== undefined ||
    diff.base_text_sig !== undefined ||
    diff.base_visual_sig !== undefined ||
    diff.base_semantic_sig !== undefined ||
    diff.semantic_sig !== undefined
  );
}

// ── Validação ───────────────────────────────────────────────────────────────

export function validateSnapshotPayload(payload) {
  if (!payload || typeof payload !== "object") return false;
  if (payload.version !== 1) return false;
  if (!Array.isArray(payload.cells) && !Array.isArray(payload.runs)) return false;
  if (!isInteger(payload.rows) || !isInteger(payload.cols)) return false;
  if (payload.rows < 1 || payload.cols < 1) return false;
  if (payload.rows > MAX_ROWS || payload.cols > MAX_COLS) return false;
  if (payload.rows * payload.cols > MAX_CELLS) return false;
  if (Array.isArray(payload.cells) && payload.cells.length !== payload.rows * payload.cols) return false;
  if (Array.isArray(payload.runs)) {
    for (const run of payload.runs) {
      if (!run || typeof run !== "object") return false;
      if (!isInteger(run.row) || !isInteger(run.col) || !isInteger(run.length)) return false;
      if (run.row < 0 || run.col < 0 || run.length < 1) return false;
      if (run.row >= payload.rows || run.col >= payload.cols || run.col + run.length > payload.cols) return false;
      if (typeof (run.text || "") !== "string") return false;
    }
  }
  return true;
}

export function validateDiffPayload(diff, snapshot = null) {
  if (!diff || typeof diff !== "object") return false;
  if (diff.version !== 1) return false;
  if (!Array.isArray(diff.changes)) return false;
  const canonical = isCanonicalDiff(diff);
  if (!canonical && !snapshot) return true;
  if (!canonical && snapshot) {
    const rows = diff.rows ?? snapshot.rows ?? 25;
    const cols = diff.cols ?? snapshot.cols ?? 80;
    if (!isInteger(rows) || !isInteger(cols) || rows < 1 || cols < 1 || rows > MAX_ROWS || cols > MAX_COLS || rows * cols > MAX_CELLS) return false;
    if (diff.changes.length > MAX_DIFF_CHANGES) return false;
    const seen = new Set();
    for (const change of diff.changes) {
      if (!change || typeof change !== "object") return false;
      if (!isInteger(change.row) || !isInteger(change.col)) return false;
      if (change.row < 0 || change.col < 0 || change.row >= rows || change.col >= cols) return false;
      const key = change.row + ":" + change.col;
      if (seen.has(key)) return false;
      seen.add(key);
    }
    return true;
  }
  for (const key of ["base_seq_global", "seq_global", "rows", "cols"]) {
    if (!isInteger(diff[key])) return false;
  }
  if (diff.seq_global <= diff.base_seq_global) return false;
  if (snapshot && isInteger(snapshot.seq_global) && snapshot.seq_global !== diff.base_seq_global) return false;
  if (diff.rows < 1 || diff.rows > MAX_ROWS || diff.cols < 1 || diff.cols > MAX_COLS) return false;
  if (diff.rows * diff.cols > MAX_CELLS) return false;
  if (typeof diff.geometry_changed !== "boolean") return false;
  const baseRows = diff.base_rows ?? (snapshot ? snapshot.rows : undefined);
  const baseCols = diff.base_cols ?? (snapshot ? snapshot.cols : undefined);
  if (!isInteger(baseRows) || !isInteger(baseCols)) return false;
  const actualGeometryChanged = diff.rows !== baseRows || diff.cols !== baseCols;
  if (actualGeometryChanged !== diff.geometry_changed) return false;
  if (diff.geometry_changed) {
    const resize = diff.resize;
    if (!resize || typeof resize !== "object") return false;
    if (resize.from_rows !== baseRows || resize.from_cols !== baseCols) return false;
    if (resize.to_rows !== diff.rows || resize.to_cols !== diff.cols) return false;
  } else if (diff.resize !== null && diff.resize !== undefined) {
    return false;
  }
  for (const key of ["base_text_sig", "base_visual_sig", "base_semantic_sig", "text_sig", "visual_sig", "semantic_sig"]) {
    if (!validSig(diff[key])) return false;
  }
  if (snapshot) {
    if (snapshot.text_sig && diff.base_text_sig !== snapshot.text_sig) return false;
    if (snapshot.visual_sig && diff.base_visual_sig !== snapshot.visual_sig) return false;
    if (snapshot.semantic_sig && diff.base_semantic_sig !== snapshot.semantic_sig) return false;
  }
  if (diff.changes.length > MAX_DIFF_CHANGES) return false;
  const seen = new Set();
  for (const change of diff.changes) {
    if (!change || typeof change !== "object") return false;
    if (!isInteger(change.row) || !isInteger(change.col)) return false;
    if (change.row < 0 || change.col < 0 || change.row >= diff.rows || change.col >= diff.cols) return false;
    const key = change.row + ":" + change.col;
    if (seen.has(key)) return false;
    seen.add(key);
  }
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
  // Valida geometria ANTES de qualquer alocação
  if (!validateSnapshotPayload(payload)) {
    console.error("decodeSnapshotPayload: payload invalido");
    return null;
  }
  var rows = payload.rows;
  var cols = payload.cols;

  // Se já tem cells, é snapshot completo
  if (payload.cells) return payload;

  // Decodifica formato compacto (runs + attribute_table)
  if (payload.runs && payload.attribute_table) {
    var attrTable = payload.attribute_table || [];
    var cells = Array.from({ length: rows * cols }, function() { return Object.assign({}, DEFAULT_CELL); });

    for (var ri = 0; ri < (payload.runs || []).length; ri++) {
      var run = payload.runs[ri];
      var r = run.row || 0;
      var c = run.col || 0;
      var len = run.length || 0;
      var text = run.text || "";
      // Usa Array.from para iterar por code points (nao quebra surrogate pairs)
      var chars = Array.from(text);
      var attrs = attrTable[run.attr] || DEFAULT_CELL;

      for (var offset = 0; offset < Math.min(len, chars.length); offset++) {
        var idx = r * cols + c + offset;
        if (idx < cells.length) {
          cells[idx] = Object.assign({}, attrs, { ch: chars[offset] || " " });
        }
      }
    }

    return Object.assign({}, payload, { cells: cells, rows: rows, cols: cols });
  }

  return null;
}

// ── Apply diff ──────────────────────────────────────────────────────────────

export function applyDiff(snapshot, diff) {
  const result = cloneSnapshot(snapshot);
  if (!result || !diff) return result;
  const canonical = isCanonicalDiff(diff);
  if (!validateDiffPayload(diff, snapshot)) {
    throw new Error("applyDiff: invalid diff");
  }
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
  if (canonical) {
    const computedText = computeTextSignature(result);
    const computedVisual = computeVisualSignature(result);
    const computedSemantic = computeSemanticSignature(result);
    if (diff.text_sig && diff.text_sig !== computedText) {
      throw new Error("applyDiff: text signature mismatch");
    }
    if (diff.visual_sig && diff.visual_sig !== computedVisual) {
      throw new Error("applyDiff: visual signature mismatch");
    }
    if (diff.semantic_sig && diff.semantic_sig !== computedSemantic) {
      throw new Error("applyDiff: semantic signature mismatch");
    }
    result.text_sig = computedText;
    result.visual_sig = computedVisual;
    result.semantic_sig = computedSemantic;
  }
  result.seq_global = diff.seq_global;

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

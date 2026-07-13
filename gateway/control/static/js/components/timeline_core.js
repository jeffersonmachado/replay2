/**
 * Timeline — funções compartilhadas de formatação de eventos.
 * Usado por: gateway.js, capture_session_replay.html
 */
import { escapeHtml } from "../core/dom.js";

export function decodeBase64(b64) {
  if (!b64) return "";
  try { return atob(b64); } catch (_) { return b64; }
}

export function typeBadge(type) {
  const map = {
    bytes: "text-cyan-300",
    checkpoint: "text-purple-300",
    deterministic_input: "text-amber-300",
    session_start: "text-emerald-300",
    session_end: "text-rose-300",
    input: "text-amber-200",
  };
  return `<span class="${map[type] || "text-stone-300"} font-semibold">${escapeHtml(type)}</span>`;
}

export function dirBadge(dir) {
  if (dir === "in") return '<span class="text-emerald-400">← in</span>';
  if (dir === "out") return '<span class="text-blue-400">out →</span>';
  return `<span class="text-stone-500">${escapeHtml(dir || "-")}</span>`;
}

export function eventDetails(event) {
  const type = event.type || "";
  const parts = [];
  if (type === "bytes") {
    const data = decodeBase64(event.data_b64 || "");
    const truncated = data.length > 80 ? data.substring(0, 80) + "\u2026" : data;
    parts.push(`<span class="font-mono text-stone-300">${escapeHtml(truncated)}</span>`);
    if (event.n) parts.push(`<span class="text-stone-500">(${event.n}B)</span>`);
  }
  if (type === "deterministic_input" || type === "input") {
    if (event.key_text) parts.push(`<span class="text-amber-200">key: ${escapeHtml(event.key_text)}</span>`);
    if (event.key_kind) parts.push(`<span class="text-stone-500">kind: ${escapeHtml(event.key_kind)}</span>`);
  }
  if (event.screen_sig) parts.push(`<span class="text-stone-500">screen: ${escapeHtml(event.screen_sig.substring(0, 12))}\u2026</span>`);
  if (event.norm_sha256) parts.push(`<span class="text-stone-500">hash: ${escapeHtml(event.norm_sha256.substring(0, 10))}\u2026</span>`);
  if (!parts.length) parts.push('<span class="text-stone-500">-</span>');
  return parts.join(" ");
}

/**
 * Formata o conteúdo de um evento para exibição compacta.
 * Usa terminal virtual se disponível, senão fallback para data_decoded.
 */
export function formatEventContent(ev, term) {
  if (ev?.content_kind === "terminal_snapshot") return ev.summary || "";
  if (ev?.type === "deterministic_input") return ev.summary || "";
  const text = ev?.data_decoded ?? ev?.summary ?? "";
  if (term) {
    // feed e renderCompactText são injetados pelo módulo de terminal
    if (typeof term.feed === "function" && typeof term.renderCompactText === "function") {
      term.feed(term, text);
      return term.renderCompactText(term);
    }
  }
  // Sanitiza ANSI para visualização detalhada (seção 27)
  return sanitizeAnsiForDisplay(text);
}

/**
 * Remove sequências ANSI/CSI para exibição segura.
 * Mantém texto visível, remove escapes não-imprimíveis.
 */
export function sanitizeAnsiForDisplay(text) {
  if (!text) return "";
  return String(text)
    .replace(/\x1b\[[0-9;]*[a-zA-Z]/g, '')  // CSI sequences
    .replace(/\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)/g, '')  // OSC sequences
    .replace(/\x1b[()][0-9A-Za-z]/g, '')   // DEC charset
    .replace(/\x1b[=>]/g, '')               // DECKPAM/DECKPNM
    .replace(/\x1b[7-8]/g, '')              // save/restore cursor
    .replace(/\x1b[MDEc]/g, '')             // RI, IND, NEL, RIS
    .replace(/\x1b/g, '')                   // any remaining bare ESC
    .replace(/[\x00-\x08\x0b\x0c\x0e-\x1f]/g, ''); // other control chars (keep \t \n \r)
}

export function resolveEventByteCount(ev) {
  const count = Number(ev?.n_bytes ?? ev?.n ?? 0);
  return Number.isFinite(count) ? count : 0;
}

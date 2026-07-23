/**
 * Timeline — funções compartilhadas de formatação de eventos.
 * Usado por: gateway.js, capture_session_replay.html
 */
import { escapeHtml } from "../core/dom.js";

export function decodeBase64(b64) {
  if (!b64) return "";
  try { return atob(b64); } catch (_) { return b64; }
}

export function decodeEventForDisplay(event, encoding = "utf-8", mode = "display") {
  const chunks = Array.isArray(event?.data_b64_chunks) ? event.data_b64_chunks : [event?.data_b64 || ""];
  const bytes = [];
  for (const chunk of chunks) {
    if (!chunk) continue;
    try {
      const raw = atob(chunk);
      for (let i = 0; i < raw.length; i += 1) bytes.push(raw.charCodeAt(i) & 0xff);
    } catch (_) {
      return "[base64 inválido]";
    }
  }
  const enc = normalizeDisplayEncoding(event?.encoding || encoding || "utf-8");
  let decoded = "";
  try {
    decoded = decodeBytesForDisplay(bytes, enc);
  } catch (_) {
    decoded = new TextDecoder("utf-8", { fatal: false }).decode(new Uint8Array(bytes));
  }
  if (mode === "raw") return decoded;
  return sanitizeAnsiForDisplay(decoded);
}

export function normalizeDisplayEncoding(encoding = "utf-8") {
  const enc = String(encoding || "utf-8").trim().toLowerCase();
  const supported = {
    "utf-8": "utf-8",
    utf8: "utf-8",
    ascii: "utf-8",
    cp850: "cp850",
    ibm850: "cp850",
    "850": "cp850",
    cp437: "cp437",
    ibm437: "cp437",
    "437": "cp437",
    "iso-8859-1": "iso-8859-1",
    "latin-1": "iso-8859-1",
    latin1: "latin1",
    cp1252: "windows-1252",
    "windows-1252": "windows-1252",
    "1252": "windows-1252",
  };
  return supported[enc] || "utf-8";
}

export function decodeBytesForDisplay(bytes, encoding = "utf-8") {
  const enc = normalizeDisplayEncoding(encoding);
  if (enc === "cp850" || enc === "cp437" || enc === "iso-8859-1" || enc === "latin1" || enc === "windows-1252") {
    return decodeSingleByte(bytes, enc);
  }
  return new TextDecoder(enc, { fatal: false }).decode(new Uint8Array(bytes));
}

let CP850_TABLE = null;
let CP437_TABLE = null;
let WIN1252_TABLE = null;

function buildTable(chars) {
  const table = {};
  for (let i = 0; i < 128; i += 1) table[0x80 + i] = chars.charAt(i);
  return table;
}

function cp850Table() {
  if (!CP850_TABLE) {
    CP850_TABLE = buildTable(
      "\u00C7\u00FC\u00E9\u00E2\u00E4\u00E0\u00E5\u00E7\u00EA\u00EB\u00E8\u00EF\u00EE\u00EC\u00C4\u00C5" +
      "\u00C9\u00E6\u00C6\u00F4\u00F6\u00F2\u00FB\u00F9\u00FF\u00D6\u00DC\u00F8\u00A3\u00D8\u00D7\u0192" +
      "\u00E1\u00ED\u00F3\u00FA\u00F1\u00D1\u00AA\u00BA\u00BF\u00AE\u00AC\u00BD\u00BC\u00A1\u00AB\u00BB" +
      "\u2591\u2592\u2593\u2502\u2524\u00C1\u00C2\u00C0\u00A9\u2563\u2551\u2557\u255D\u00A2\u00A5\u2510" +
      "\u2514\u2534\u252C\u251C\u2500\u253C\u00E3\u00C3\u255A\u2554\u2569\u2566\u2560\u2550\u256C\u00A4" +
      "\u00F0\u00D0\u00CA\u00CB\u00C8\u0131\u00CD\u00CE\u00CF\u2518\u250C\u2588\u2584\u00A6\u00CC\u2580" +
      "\u00D3\u00DF\u00D4\u00D2\u00F5\u00D5\u00B5\u00FE\u00DE\u00DA\u00DB\u00D9\u00FD\u00DD\u00AF\u00B4" +
      "\u00AD\u00B1\u2017\u00BE\u00B6\u00A7\u00F7\u00B8\u00B0\u00A8\u00B7\u00B9\u00B3\u00B2\u25A0\u00A0"
    );
  }
  return CP850_TABLE;
}

function cp437Table() {
  if (!CP437_TABLE) {
    CP437_TABLE = buildTable(
      "\u00C7\u00FC\u00E9\u00E2\u00E4\u00E0\u00E5\u00E7\u00EA\u00EB\u00E8\u00EF\u00EE\u00EC\u00C4\u00C5" +
      "\u00C9\u00E6\u00C6\u00F4\u00F6\u00F2\u00FB\u00F9\u00FF\u00D6\u00DC\u00A2\u00A3\u00A5\u20A7\u0192" +
      "\u00E1\u00ED\u00F3\u00FA\u00F1\u00D1\u00AA\u00BA\u00BF\u2310\u00AC\u00BD\u00BC\u00A1\u00AB\u00BB" +
      "\u2591\u2592\u2593\u2502\u2524\u2561\u2562\u2556\u2555\u2563\u2551\u2557\u255D\u255C\u255B\u2510" +
      "\u2514\u2534\u252C\u251C\u2500\u253C\u255E\u255F\u255A\u2554\u2569\u2566\u2560\u2550\u256C\u2567" +
      "\u2568\u2564\u2565\u2559\u2558\u2552\u2553\u256B\u256A\u2518\u250C\u2588\u2584\u258C\u2590\u2580" +
      "\u03B1\u00DF\u0393\u03C0\u03A3\u03C3\u00B5\u03C4\u03A6\u0398\u03A9\u03B4\u221E\u03C6\u03B5\u2229" +
      "\u2261\u00B1\u2265\u2264\u2320\u2321\u00F7\u2248\u00B0\u2219\u00B7\u221A\u207F\u00B2\u25A0\u00A0"
    );
  }
  return CP437_TABLE;
}

function windows1252Byte(byte) {
  if (byte < 0x80 || byte >= 0xA0) return String.fromCharCode(byte);
  if (!WIN1252_TABLE) {
    WIN1252_TABLE = buildTable("\u20AC\u0081\u201A\u0192\u201E\u2026\u2020\u2021\u02C6\u2030\u0160\u2039\u0152\u008D\u017D\u008F" +
      "\u0090\u2018\u2019\u201C\u201D\u2022\u2013\u2014\u02DC\u2122\u0161\u203A\u0153\u009D\u017E\u0178");
  }
  return WIN1252_TABLE[byte] || String.fromCharCode(byte);
}

function decodeSingleByte(bytes, encoding) {
  const table = encoding === "cp850" ? cp850Table() : encoding === "cp437" ? cp437Table() : null;
  let result = "";
  for (const byte of bytes) {
    const b = byte & 0xff;
    if (b < 0x80) result += String.fromCharCode(b);
    else if (table && table[b]) result += table[b];
    else if (encoding === "iso-8859-1" || encoding === "latin1") result += String.fromCharCode(b);
    else result += windows1252Byte(b);
  }
  return result;
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
    const data = decodeEventForDisplay(event, event.encoding || "utf-8", "display");
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

export function eventTimestamp(ev) {
  return ev?.timestamp_ms ?? ev?.ts_ms ?? 0;
}

export function resolveEventByteCount(ev) {
  const count = Number(ev?.n_bytes ?? ev?.n ?? 0);
  return Number.isFinite(count) ? count : 0;
}

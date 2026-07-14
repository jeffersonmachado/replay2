/**
 * Timeline — tabela de eventos + cards reutilizáveis.
 * Usado por: aba Eventos, aba Compliance, Replay (capture_session_replay.html)
 */
import { escapeHtml, html, text } from "../core/dom.js";
import { typeBadge, dirBadge, eventDetails, decodeBase64, resolveEventByteCount } from "./timeline_core.js";

export function renderEventsTable(events, targetId = "#gw_recent_events", countId = "#gw_events_count") {
  if (!events.length) {
    html(targetId, '<tr><td colspan="6" class="px-3 py-6 text-center text-stone-500">Nenhum evento.</td></tr>');
    text(countId, "0 eventos");
    return;
  }
  text(countId, `${events.length} eventos`);
  const rows = events.map((e) => {
    const ts = e.ts_ms ? new Date(e.ts_ms).toLocaleString("pt-BR") : "-";
    return `<tr class="border-b border-stone-700/30 hover:bg-stone-700/20">
      <td class="px-3 py-1.5 text-stone-500 font-mono">${e.seq_global ?? "-"}</td>
      <td class="px-3 py-1.5 text-stone-400 whitespace-nowrap">${escapeHtml(ts)}</td>
      <td class="px-3 py-1.5">${typeBadge(e.type)}</td>
      <td class="px-3 py-1.5 text-stone-300">${escapeHtml(e.actor || "-")}</td>
      <td class="px-3 py-1.5">${dirBadge(e.dir)}</td>
      <td class="px-3 py-1.5">${eventDetails(e)}</td>
    </tr>`;
  }).join("");
  html(targetId, rows);
}

// ── Cards (visual igual ao Replay) ────────────────────────────────────────

function dirCardClass(ev) {
  if (ev.type === "deterministic_input") return "r2ctl-event-dir-det";
  return ev.direction === "in" ? "r2ctl-event-dir-in" : "r2ctl-event-dir-out";
}

function dirCardLabel(ev) {
  if (ev.type === "deterministic_input") return "🎯 DET";
  return ev.direction === "in" ? "📥 IN" : "📤 OUT";
}

function formatCardContent(ev) {
  // terminal_snapshot: usar HTML com atributos quando disponivel
  if (ev.content_kind === "terminal_snapshot") {
    if (ev.snapshot_html) return ev.snapshot_html;
    return ev.summary || "";
  }
  if (ev.type === "deterministic_input") return ev.summary || ev.key_text || ev.key_kind || "-";
  if (ev.type === "bytes" && ev.data_b64) {
    const data = decodeBase64(ev.data_b64);
    return data.length > 600 ? data.substring(0, 600) + "\u2026" : data;
  }
  return ev.data_decoded || ev.summary || "-";
}

function cardMetaLabel(ev) {
  const seq = ev.seq_global ?? ev.seq_global_end ?? "-";
  if (ev.type === "deterministic_input") {
    return `Seq #${seq} &middot; ${escapeHtml(ev.key_kind || ev.key_text || "input")}`;
  }
  const bytes = resolveEventByteCount(ev);
  const chunks = Number(ev?.chunk_count || 1);
  const parts = [`Seq #${seq}`, `${bytes} bytes`];
  if (chunks > 1) parts.push(`${chunks} chunks`);
  return parts.join(" &middot; ");
}

function cardDetDetails(ev) {
  if (ev.type !== "deterministic_input") return "";
  return `<div class="hidden r2ctl-event-det-details" data-det-details="${escapeHtml(String(ev.seq_global || ""))}">
    screen=${escapeHtml(ev.screen_sig || "-")}
    source=${escapeHtml(ev.screen_source || "-")}
    kind=${escapeHtml(ev.key_kind || "-")}
    age_ms=${escapeHtml(ev.screen_snapshot_age_ms ?? "-")}
    flags=${(ev.is_probable_paste ? "paste " : "") + (ev.is_probable_command ? "command " : "") + (ev.contains_escape ? "escape " : "") + (ev.contains_newline ? "newline" : "")}
  </div>`;
}

export function renderEventsCards(events, targetId, countId) {
  if (!events.length) {
    html(targetId, '<div class="text-stone-400 text-sm p-3">Nenhum evento.</div>');
    if (countId) text(countId, "0 eventos");
    return;
  }
  if (countId) text(countId, `${events.length} eventos`);
  const cards = events.map((e) => {
    const ts = e.timestamp_ms || e.ts_ms;
    const displayTs = ts ? new Date(ts).toLocaleTimeString("pt-BR") : "";
    const isSnapshot = e.content_kind === "terminal_snapshot";
    // Para terminal_snapshot com snapshot_html, renderiza HTML diretamente
    // (o snapshot_html ja tem caracteres escapados via escapeHtml)
    const contentHtml = isSnapshot && e.snapshot_html
      ? e.snapshot_html
      : escapeHtml(formatCardContent(e));
    return `<div class="r2ctl-event-card">
      <div class="${dirCardClass(e)} r2ctl-event-dir">${dirCardLabel(e)}</div>
      <div>
        <div class="flex items-center justify-between gap-3 text-xs text-stone-400 mb-1">
          <div>${typeBadge(e.type)} &middot; ${cardMetaLabel(e)} &middot; ${escapeHtml(displayTs)} &middot; ${escapeHtml(e.actor || "-")}</div>
          ${e.type === "deterministic_input" ? '<button type="button" class="r2ctl-btn-soft text-xs r2ctl-det-toggle" data-det-toggle="' + escapeHtml(String(e.seq_global || "")) + '">Detalhes</button>' : ""}
        </div>
        <pre class="r2ctl-event-content${isSnapshot ? " terminal-snapshot" : ""}">${contentHtml}</pre>
        ${cardDetDetails(e)}
      </div>
    </div>`;
  }).join("");
  html(targetId, cards);

  // Bind DET detail toggles
  targetId && document.querySelectorAll(targetId + " .r2ctl-det-toggle").forEach((btn) => {
    btn.addEventListener("click", () => {
      const id = btn.dataset.detToggle;
      const detail = document.querySelector(`[data-det-details="${id}"]`);
      if (detail) detail.classList.toggle("hidden");
    });
  });
}

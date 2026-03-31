import { escapeHtml } from "../core/dom.js";

export function simpleCards(items) {
  return (items || [])
    .map(
      (item) => `
        <div class="r2ctl-detail-surface rounded-2xl p-4">
          <div class="text-xs uppercase tracking-[0.14em] text-stone-500">${escapeHtml(item.label)}</div>
          <div class="mt-2 text-2xl font-semibold text-stone-100">${escapeHtml(item.value)}</div>
          ${item.copy ? `<div class="mt-2 text-xs text-stone-400">${escapeHtml(item.copy)}</div>` : ""}
        </div>
      `,
    )
    .join("");
}

export function textListCards(items, emptyLabel, valueFormatter) {
  return (items || []).length
    ? (items || [])
        .map(
          (item) => `
            <div class="r2ctl-obs-run">
              <div class="font-mono text-sm text-stone-100">${escapeHtml(item.title || item.name || item.flow_name || item.environment || item.failure_type || "-")}</div>
              <div class="mt-1 text-xs text-stone-400">${escapeHtml(valueFormatter ? valueFormatter(item) : item.copy || "-")}</div>
            </div>
          `,
        )
        .join("")
    : `<div class="text-sm text-stone-400">${escapeHtml(emptyLabel)}</div>`;
}

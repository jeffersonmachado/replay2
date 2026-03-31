import { escapeHtml, formatAgo, formatCount } from "../core/dom.js";

export function gatewayMetricRows(items, emptyLabel) {
  return (items || []).length
    ? (items || [])
        .map(
          (item) => `
            <div class="flex items-center justify-between rounded-xl border border-stone-800 bg-stone-950/40 px-3 py-2">
              <span class="font-mono text-xs text-stone-300">${escapeHtml(item.label || item.type || "unknown")}</span>
              <span class="text-sm font-semibold text-stone-100">${formatCount(item.value ?? item.count ?? 0)}</span>
            </div>
          `,
        )
        .join("")
    : `<div class="text-sm text-stone-400">${escapeHtml(emptyLabel)}</div>`;
}

export function gatewaySignalRows(signal, keys) {
  return (keys || [])
    .map(
      (key) => `
        <div class="flex items-center justify-between rounded-xl border border-stone-800 bg-stone-950/40 px-3 py-2">
          <span class="text-xs uppercase tracking-[0.14em] text-stone-500">${escapeHtml(key)}</span>
          <span class="font-mono text-xs text-stone-200">${escapeHtml((signal || {})[key] || "-")}</span>
        </div>
      `,
    )
    .join("");
}

export function gatewaySessionCard(item) {
  return `
    <div class="r2ctl-detail-surface rounded-2xl p-4">
      <div class="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
        <div>
          <div class="font-mono text-sm text-stone-100">${escapeHtml(item.session_id || "-")}</div>
          <div class="mt-1 text-xs text-stone-400">actor=${escapeHtml(item.actor || "-")} • compliance=${escapeHtml(item.compliance_status || "-")} • ${formatAgo(item.last_ts_ms)}</div>
          <div class="mt-1 text-xs text-stone-500">checkpoint=${formatCount(item.checkpoint_count || 0)} • deterministic=${formatCount(item.deterministic_input_count || 0)} • in=${formatCount(item.bytes_in || 0)} • out=${formatCount(item.bytes_out || 0)}</div>
          <div class="mt-1 text-xs text-stone-500">${escapeHtml(item.compliance_reason || "sem motivo registrado")}</div>
        </div>
        <button class="r2ctl-btn-soft" data-session="${escapeHtml(item.session_id || "")}">Ver timeline</button>
      </div>
    </div>
  `;
}

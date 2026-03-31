import { escapeHtml } from "../core/dom.js";

export function emptyTableRow(message, colspan = 6) {
  return `
    <tr>
      <td colspan="${colspan}" class="px-6 py-14 text-center">
        <div class="r2ctl-empty-state mx-auto max-w-md rounded-3xl px-6 py-10">
          <p class="text-lg font-semibold text-stone-200">${escapeHtml(message)}</p>
        </div>
      </td>
    </tr>
  `;
}

export function statList(items, keyName = "name") {
  return (items || []).length
    ? items
        .map(
          (item) => `
            <div class="flex items-center justify-between rounded-xl border border-stone-800 bg-stone-950/40 px-3 py-2">
              <span class="font-mono text-xs text-stone-300">${escapeHtml(item[keyName] || item.type || item.status || "unknown")}</span>
              <span class="text-sm font-semibold text-stone-100">${escapeHtml(item.count || 0)}</span>
            </div>
          `,
        )
        .join("")
    : '<div class="text-sm text-stone-400">Sem dados.</div>';
}

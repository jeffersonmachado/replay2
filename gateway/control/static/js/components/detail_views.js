import { escapeHtml, formatCount, formatDate, statusLabel, statusToneClass } from "../core/dom.js";

export function runIdentityCard(run) {
  return `
    <div class="rounded-2xl border border-stone-800 bg-stone-950/40 p-4">
      <div class="flex items-center justify-between gap-3">
        <div class="font-mono text-sm text-stone-100">#${escapeHtml(run.id || "-")}</div>
        <span class="r2ctl-status-pill ${statusToneClass(run.status)}">${escapeHtml(statusLabel(run.status))}</span>
      </div>
      <div class="mt-3 text-sm text-stone-300">${escapeHtml(run.target_user || "-")}@${escapeHtml(run.target_host || "-")}</div>
      <div class="mt-2 text-xs text-stone-500">criada em ${formatDate(run.created_at_ms)} • modo ${escapeHtml(run.mode || "-")}</div>
      <div class="mt-2 text-xs text-stone-400">compliance=${escapeHtml(run.compliance_status || "-")} • entry=${escapeHtml(run.entry_mode || "-")} • gateway=${escapeHtml(run.gateway_endpoint || "-")}</div>
    </div>
  `;
}

export function comparisonSummaryCard(summary) {
  const items = [
    { label: "novas", value: summary.new_failure_groups || 0 },
    { label: "recorrentes", value: summary.recurring_failure_groups || 0 },
    { label: "resolvidas", value: summary.resolved_failure_groups || 0 },
  ];
  return `
    <div class="rounded-2xl border border-stone-800 bg-stone-950/40 p-4">
      <div class="text-xs uppercase tracking-[0.14em] text-stone-500">Comparacao</div>
      <div class="mt-3 grid gap-2 sm:grid-cols-3">
        ${items
          .map(
            (item) => `
              <div class="rounded-xl border border-stone-800 bg-stone-950/40 px-3 py-2">${escapeHtml(item.label)} <span class="float-right">${formatCount(item.value)}</span></div>
            `,
          )
          .join("")}
      </div>
    </div>
  `;
}

export function failureTypeList(entries) {
  return entries.length
    ? entries
        .map(
          ([name, count]) => `
            <div class="flex items-center justify-between rounded-xl border border-stone-800 bg-stone-950/40 px-3 py-2">
              <span class="font-mono text-xs text-stone-300">${escapeHtml(name)}</span>
              <span class="text-sm font-semibold text-stone-100">${formatCount(count)}</span>
            </div>
          `,
        )
        .join("")
    : '<div class="text-sm text-stone-400">Sem falhas registradas.</div>';
}

export function exportLinks(runId) {
  return `
    <div class="mt-3 flex flex-wrap gap-2">
      <a href="/api/runs/${escapeHtml(runId)}/report/export?format=md" class="r2ctl-btn-soft">Exportar MD</a>
      <a href="/api/runs/${escapeHtml(runId)}/report/export?format=json" class="r2ctl-btn-soft">Exportar JSON</a>
      <a href="/api/runs/${escapeHtml(runId)}/report/export?format=csv" class="r2ctl-btn-soft">Exportar CSV</a>
    </div>
  `;
}

export function reprocessFailureCard(item) {
  return `
    <div class="rounded-xl border border-stone-800 bg-stone-950/40 p-3">
      <div class="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
        <div>
          <div class="font-mono text-sm text-stone-100">${escapeHtml(item.failure_type || "falha")}</div>
          <div class="mt-1 text-xs text-stone-400">sessao=${escapeHtml(item.session_id || "-")} • seq=${escapeHtml(item.seq_global || 0)}</div>
        </div>
        <div class="flex flex-wrap gap-2">
          <button class="r2ctl-btn-soft" data-reprocess="${escapeHtml(item.id)}" data-scope="from-failure">Desta falha</button>
          <button class="r2ctl-btn-soft" data-reprocess="${escapeHtml(item.id)}" data-scope="session-from-failure">Sessao desta falha</button>
        </div>
      </div>
    </div>
  `;
}

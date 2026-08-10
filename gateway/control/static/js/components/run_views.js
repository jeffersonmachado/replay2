import { escapeHtml, formatAgo, formatCount, formatDate, statusLabel, statusToneClass } from "../core/dom.js";

// Extrai a origem sintética da run (params_json gravado pelo replay
// sintético 1-clique: synthetic=true, source_capture_id, journey_id).
export function runSyntheticOrigin(run) {
  if (!run) return null;
  let params = run.params;
  if (!params || typeof params !== "object") {
    try {
      params = JSON.parse(run.params_json || "{}");
    } catch {
      return null;
    }
  }
  if (!params || params.synthetic !== true) return null;
  const captureId = Number(params.source_capture_id || 0);
  return {
    captureId: Number.isInteger(captureId) && captureId > 0 ? captureId : null,
    journeyId: String(params.journey_id || "").trim(),
  };
}

// Badge "sintético" com link para a captura de referência (rastreabilidade).
export function runSyntheticBadgeHtml(run) {
  const origin = runSyntheticOrigin(run);
  if (!origin) return "";
  const captureLink = origin.captureId
    ? `<a href="/captures/${origin.captureId}" class="underline decoration-rose-400/60 underline-offset-2 hover:text-rose-100">captura #${origin.captureId}</a>`
    : "captura n/d";
  return `<span class="ml-2 inline-flex items-center gap-1 rounded-full border border-rose-900/60 bg-rose-950/50 px-2 py-0.5 text-xs text-rose-200" title="Run gerada pelo replay sintético (dados substituídos)">sintético • ${captureLink}</span>`;
}

export function runSummaryCards(items) {
  return (items || [])
    .map(
      (item) => `
        <div class="r2ctl-detail-surface rounded-2xl p-4">
          <div class="text-xs uppercase tracking-[0.14em] text-stone-400">${escapeHtml(item.label)}</div>
          <div class="mt-2 text-2xl font-semibold text-stone-100">${formatCount(item.value)}</div>
          ${item.copy ? `<div class="mt-2 text-xs text-stone-400">${escapeHtml(item.copy)}</div>` : ""}
        </div>
      `,
    )
    .join("");
}

export function runLinkCard(run) {
  return `
    <a href="/runs/${escapeHtml(run.id)}" class="block rounded-2xl border border-stone-800 bg-stone-950/35 p-4 transition hover:border-stone-700">
      <div class="flex items-center justify-between gap-3">
        <div class="font-mono text-sm text-stone-100">#${escapeHtml(run.id)}</div>
        <span class="r2ctl-status-pill ${statusToneClass(run.status)}">${escapeHtml(statusLabel(run.status))}</span>
      </div>
      <div class="mt-2 text-sm text-stone-300">${escapeHtml(run.target_user || "-")}@${escapeHtml(run.target_host || "-")}</div>
      <div class="mt-1 text-xs text-stone-400">compliance=${escapeHtml(run.compliance_status || "-")} • ${formatAgo(run.created_at_ms)}</div>
    </a>
  `;
}

export function runCompactCard(run) {
  return `
    <div class="r2ctl-obs-run">
      <div class="font-mono text-sm text-stone-100">#${escapeHtml(run.id)}</div>
      <div class="mt-1 text-xs text-stone-400">${escapeHtml(run.target_host || "-")} • ${escapeHtml(run.compliance_status || "-")}</div>
    </div>
  `;
}

export function runTableRow(run) {
  return `
    <tr class="r2ctl-row align-top">
      <td class="px-4 py-4">
        <a href="/runs/${escapeHtml(run.id)}" class="r2ctl-run-id">#${escapeHtml(run.id)}</a>${runSyntheticBadgeHtml(run)}
      </td>
      <td class="px-4 py-4"><span class="r2ctl-status-pill ${statusToneClass(run.status)}">${escapeHtml(statusLabel(run.status))}</span></td>
      <td class="px-4 py-4 text-stone-300">
        <div>${escapeHtml(run.target_user || "-")}@${escapeHtml(run.target_host || "-")}</div>
        <div class="mt-1 text-xs text-stone-400">${formatDate(run.created_at_ms)}</div>
      </td>
      <td class="px-4 py-4 text-stone-300">${formatCount(run.last_seq_global_applied || 0)}</td>
      <td class="px-4 py-4">
        <span class="r2ctl-status-pill ${statusToneClass(run.compliance_status)}">${escapeHtml(run.compliance_status || "-")}</span>
      </td>
      <td class="px-4 py-4">
        <div class="r2ctl-actions">
          <button class="r2ctl-btn-action r2ctl-btn-action-start" data-action="start" data-id="${escapeHtml(run.id)}">iniciar</button>
          <button class="r2ctl-btn-action r2ctl-btn-action-pause" data-action="pause" data-id="${escapeHtml(run.id)}">pausar</button>
          <button class="r2ctl-btn-action r2ctl-btn-action-resume" data-action="resume" data-id="${escapeHtml(run.id)}">retomar</button>
          <button class="r2ctl-btn-action r2ctl-btn-action-cancel" data-action="cancel" data-id="${escapeHtml(run.id)}">cancelar</button>
          <button class="r2ctl-btn-action r2ctl-btn-action-retry" data-action="retry" data-id="${escapeHtml(run.id)}">repetir</button>
        </div>
      </td>
    </tr>
  `;
}

export function regressionCard(item) {
  return `
    <div class="r2ctl-comparison-card r2ctl-comparison-card-danger">
      <div class="font-mono text-sm text-stone-100">run #${escapeHtml(item.run_id)}</div>
      <div class="mt-2 text-xs text-stone-400">baseline #${escapeHtml(item.baseline_run_id || "-")}</div>
    </div>
  `;
}

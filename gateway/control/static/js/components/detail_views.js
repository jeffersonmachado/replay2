import { escapeHtml, formatCount, formatDate, statusLabel, statusToneClass } from "../core/dom.js";
import { runSyntheticBadgeHtml } from "./run_views.js";

export function runIdentityCard(run) {
  return `
    <div class="rounded-2xl border border-stone-800 bg-stone-950/40 p-4">
      <div class="flex items-center justify-between gap-3">
        <div class="font-mono text-sm text-stone-100">#${escapeHtml(run.id || "-")}${runSyntheticBadgeHtml(run)}</div>
        <span class="r2ctl-status-pill ${statusToneClass(run.status)}">${escapeHtml(statusLabel(run.status))}</span>
      </div>
      <div class="mt-3 text-sm text-stone-300">${escapeHtml(run.target_user || "-")}@${escapeHtml(run.target_host || "-")}</div>
      <div class="mt-2 text-xs text-stone-400">criada em ${formatDate(run.created_at_ms)} • modo ${escapeHtml(run.mode || "-")}</div>
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
      <div class="text-xs uppercase tracking-[0.14em] text-stone-400">Comparacao</div>
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

const EXECUTION_POLICY_LABELS = {
  conservative: "conservadora (padrão)",
  adaptive: "adaptativa (batching seguro)",
  adaptive_shadow: "shadow (mede, não acelera)",
};

export function runAdaptiveMetrics(run) {
  if (!run) return null;
  let metrics = run.metrics;
  if (metrics == null && run.metrics_json) {
    try {
      metrics = JSON.parse(run.metrics_json);
    } catch (_) {
      return null;
    }
  }
  const adaptive = metrics && typeof metrics === "object" ? metrics.adaptive : null;
  if (!adaptive || typeof adaptive !== "object" || !adaptive.execution_policy) return null;
  return adaptive;
}

function _fmtMs(value) {
  const n = Number(value);
  return Number.isFinite(n) ? `${formatCount(Math.round(n))} ms` : "-";
}

function _fmtPct(ratio) {
  const n = Number(ratio);
  return Number.isFinite(n) ? `${(n * 100).toFixed(1).replace(".", ",")}%` : "-";
}

export function adaptiveMetricsCard(run) {
  const adaptive = runAdaptiveMetrics(run);
  if (!adaptive) return "";
  const policy = String(adaptive.execution_policy || "");
  const policyLabel = EXECUTION_POLICY_LABELS[policy] || policy;
  const rows = [
    ["jornada total", _fmtMs(adaptive.journey_total_ms)],
    ["resposta ERP", _fmtMs(adaptive.erp_response_ms)],
    ["overhead Replay2", `${_fmtMs(adaptive.replay_overhead_ms)} (${_fmtPct(adaptive.replay_overhead_ratio)})`],
    ["pacing", _fmtMs(adaptive.pacing_ms)],
    ["espera explícita", _fmtMs(adaptive.explicit_wait_ms)],
    ["espera de sincronização", _fmtMs(adaptive.sync_wait_ms)],
    ["checkpoints", _fmtMs(adaptive.checkpoint_wait_ms)],
    ["batches", `${formatCount(adaptive.batch_count || 0)} (${formatCount(adaptive.batched_action_count || 0)} ações)`],
    ["barreiras", formatCount(adaptive.barrier_count || 0)],
    ["fallbacks conservadores", formatCount(adaptive.conservative_fallback_count || 0)],
    ["economia por batching", _fmtMs(adaptive.batching_saved_ms)],
  ];
  const shadow = adaptive.shadow && typeof adaptive.shadow === "object" ? adaptive.shadow : null;
  const shadowHtml = shadow
    ? `
      <div class="mt-3 rounded-xl border border-amber-700/50 bg-amber-950/20 px-3 py-2">
        <div class="text-[11px] uppercase tracking-[0.14em] text-amber-300">Shadow — o que o modo adaptativo faria</div>
        <div class="mt-2 grid gap-1 text-xs text-stone-300 sm:grid-cols-2">
          <span>candidatos a batch: <span class="font-mono text-stone-100">${formatCount(shadow.batch_candidates || 0)}</span> (${formatCount(shadow.safe_candidates || 0)} seguros)</span>
          <span>economia potencial: <span class="font-mono text-stone-100">${_fmtMs(shadow.potential_saved_ms)}</span></span>
          <span>decisões false-safe: <span class="font-mono ${Number(shadow.false_safe_decisions) ? "text-rose-300" : "text-emerald-300"}">${formatCount(shadow.false_safe_decisions || 0)}</span></span>
          <span>fallbacks: <span class="font-mono text-stone-100">${formatCount(shadow.fallbacks || 0)}</span></span>
        </div>
      </div>`
    : "";
  return `
    <div class="rounded-2xl border border-stone-800 bg-stone-950/40 p-4">
      <div class="flex items-center justify-between gap-3">
        <div class="text-xs uppercase tracking-[0.14em] text-stone-400">Motor de execução</div>
        <span class="text-xs text-stone-300">política: <span class="font-mono text-stone-100">${escapeHtml(policyLabel)}</span></span>
      </div>
      <div class="mt-3 grid gap-1 text-xs text-stone-300 sm:grid-cols-2 lg:grid-cols-3">
        ${rows.map(([label, value]) => `<span>${escapeHtml(label)}: <span class="font-mono text-stone-100">${escapeHtml(value)}</span></span>`).join("")}
      </div>
      ${shadowHtml}
    </div>
  `;
}

export function reprocessFailureCard(item) {
  return `
    <div class="rounded-xl border border-stone-800 bg-stone-950/40 p-3">
      <div class="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
        <div>
          <div class="font-mono text-sm text-stone-100">${escapeHtml(item.failure_type || "falha")}</div>
          <div class="mt-1 text-xs text-stone-400">sessão ${escapeHtml(item.session_id || "-")} • seq ${escapeHtml(item.seq_global || 0)}</div>
        </div>
        <div class="flex flex-wrap gap-2">
          <button class="r2ctl-btn-soft" data-reprocess="${escapeHtml(item.id)}" data-scope="from-failure" title="Cria uma nova run que reexecuta a trilha a partir do ponto desta falha (todas as sessões). A run original é preservada e a nova entra na fila.">↻ A partir desta falha</button>
          <button class="r2ctl-btn-soft" data-reprocess="${escapeHtml(item.id)}" data-scope="session-from-failure" title="Cria uma nova run que reexecuta apenas a sessão desta falha, a partir do mesmo ponto. A run original é preservada e a nova entra na fila.">↻ Só esta sessão</button>
        </div>
      </div>
    </div>
  `;
}

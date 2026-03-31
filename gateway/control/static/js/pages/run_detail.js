import { apiJson, jsonRequest } from "../core/api.js";
import { html } from "../core/dom.js";
import { comparisonSummaryCard, exportLinks, failureTypeList, reprocessFailureCard, runIdentityCard } from "../components/detail_views.js";

async function reprocessFromFailure(runId, failureId, scope) {
  const result = await apiJson(`/api/runs/${runId}/reprocess-from-failure`, jsonRequest("POST", { failure_id: Number(failureId), scope }));
  if (result?.data?.id) {
    window.location = `/runs/${result.data.id}`;
  }
}

function renderDetail(run, report, comparison, failures) {
  const summary = comparison.summary || {};
  const failureRows = Object.entries((report.summary || {}).by_type || {});
  html(
    "#detail",
    `
      <div class="grid gap-4 lg:grid-cols-2">
        ${runIdentityCard(run)}
        ${comparisonSummaryCard(summary)}
      </div>
      <div class="grid gap-4 lg:grid-cols-2">
        <div class="rounded-2xl border border-stone-800 bg-stone-950/40 p-4">
          <div class="text-xs uppercase tracking-[0.14em] text-stone-500">Falhas por tipo</div>
          <div class="mt-3 space-y-2">${failureTypeList(failureRows)}</div>
        </div>
        <div class="rounded-2xl border border-stone-800 bg-stone-950/40 p-4">
          <div class="text-xs uppercase tracking-[0.14em] text-stone-500">Exportações</div>
          ${exportLinks(run.id)}
        </div>
      </div>
      <div class="rounded-2xl border border-stone-800 bg-stone-950/40 p-4">
        <div class="text-xs uppercase tracking-[0.14em] text-stone-500">Reprocessamento por falha</div>
        <div class="mt-3 space-y-3">
          ${(failures || []).slice(0, 8).map((item) => reprocessFailureCard(item)).join("") || '<div class="text-sm text-stone-400">Sem falhas estruturadas para reprocessamento guiado.</div>'}
        </div>
      </div>
    `,
  );

  document.querySelectorAll("[data-reprocess]").forEach((button) => {
    button.addEventListener("click", () => reprocessFromFailure(run.id, button.dataset.reprocess, button.dataset.scope));
  });
}

async function loadDetail(id) {
  const runId = id || (document.getElementById("detail_id")?.value || "").trim();
  if (!runId) return;
  document.getElementById("detail_id").value = runId;
  const [detail, report, comparison, events, failures] = await Promise.all([
    apiJson(`/api/runs/${runId}`),
    apiJson(`/api/runs/${runId}/report`),
    apiJson(`/api/runs/${runId}/compare`),
    apiJson(`/api/runs/${runId}/events`),
    apiJson(`/api/runs/${runId}/failures`),
  ]);
  if (!detail?.data?.run) return;
  renderDetail(detail.data.run, report?.data?.report || {}, comparison?.data?.comparison || {}, failures?.data?.failures || []);
  html("#events", JSON.stringify({ failures: failures?.data?.failures || [], events: events?.data?.events || [] }, null, 2));
}

window.addEventListener("DOMContentLoaded", () => {
  const pathParts = window.location.pathname.split("/");
  const runId = pathParts[pathParts.length - 1];
  if (/^\d+$/.test(runId)) {
    loadDetail(runId);
  }
  document.getElementById("load_detail_btn")?.addEventListener("click", () => loadDetail());
});

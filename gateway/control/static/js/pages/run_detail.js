import { apiJson, jsonRequest } from "../core/api.js";
import { escapeHtml, html, text } from "../core/dom.js";
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
          <div class="text-xs uppercase tracking-[0.14em] text-stone-400">Falhas por tipo</div>
          <div class="mt-3 space-y-2">${failureTypeList(failureRows)}</div>
        </div>
        <div class="rounded-2xl border border-stone-800 bg-stone-950/40 p-4">
          <div class="text-xs uppercase tracking-[0.14em] text-stone-400">Exportações</div>
          ${exportLinks(run.id)}
        </div>
      </div>
      <div class="rounded-2xl border border-stone-800 bg-stone-950/40 p-4">
        <div class="text-xs uppercase tracking-[0.14em] text-stone-400">Reprocessamento por falha</div>
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
  const failureList = failures?.data?.failures || [];
  const eventList = events?.data?.events || [];

  let eventsHtml = "";
  if (!failureList.length && !eventList.length) {
    eventsHtml = '<div class="text-sm text-stone-400">Nenhum evento ou falha registrado.</div>';
  } else {
    eventsHtml = '<div class="space-y-4">';

    if (failureList.length) {
      eventsHtml += '<div><div class="text-xs uppercase tracking-[0.14em] text-stone-400 mb-2">Falhas (' + failureList.length + ')</div>';
      eventsHtml += '<div class="r2ctl-table-scroll"><table class="w-full text-sm"><thead><tr class="text-left text-stone-400 border-b border-stone-700/40">'
        + '<th class="py-1 pr-2">Tipo</th><th class="py-1 pr-2">Gravidade</th><th class="py-1 pr-2">Sessão</th><th class="py-1 pr-2">Seq</th><th class="py-1">Data/Hora</th></tr></thead><tbody>';
      eventsHtml += failureList.map(f => {
        const sev = String(f.severity || "—");
        const ts = f.ts_ms ? new Date(f.ts_ms).toLocaleString("pt-BR") : "—";
        return '<tr class="border-b border-stone-800/40">'
          + '<td class="py-1 pr-2 text-stone-200">' + escapeHtml(f.failure_type || "—") + '</td>'
          + '<td class="py-1 pr-2"><span class="rounded-full bg-stone-800 px-2 py-0.5 text-xs">' + escapeHtml(sev) + '</span></td>'
          + '<td class="py-1 pr-2 font-mono text-xs text-stone-400">' + escapeHtml(String(f.session_id || "—")) + '</td>'
          + '<td class="py-1 pr-2 text-stone-400">' + escapeHtml(String(f.seq_global ?? "—")) + '</td>'
          + '<td class="py-1 text-xs text-stone-400">' + escapeHtml(ts) + '</td></tr>';
      }).join("");
      eventsHtml += '</tbody></table></div></div>';
    }

    if (eventList.length) {
      eventsHtml += '<div><div class="text-xs uppercase tracking-[0.14em] text-stone-400 mb-2">Eventos (' + eventList.length + ')</div>';
      eventsHtml += '<div class="r2ctl-table-scroll"><table class="w-full text-sm"><thead><tr class="text-left text-stone-400 border-b border-stone-700/40">'
        + '<th class="py-1 pr-2">Tipo</th><th class="py-1 pr-2">Seq Global</th><th class="py-1 pr-2">Sessão</th><th class="py-1">Data/Hora</th></tr></thead><tbody>';
      eventsHtml += eventList.map(e => {
        const ts = e.ts_ms ? new Date(e.ts_ms).toLocaleString("pt-BR") : "—";
        return '<tr class="border-b border-stone-800/40">'
          + '<td class="py-1 pr-2"><span class="rounded-full bg-stone-800 px-2 py-0.5 text-xs font-semibold text-stone-200">' + escapeHtml(e.type || e.event_type || "—") + '</span></td>'
          + '<td class="py-1 pr-2 text-stone-400">' + escapeHtml(String(e.seq_global ?? e.seq ?? "—")) + '</td>'
          + '<td class="py-1 pr-2 font-mono text-xs text-stone-400">' + escapeHtml(String(e.session_id || e.session_uuid || "—")) + '</td>'
          + '<td class="py-1 text-xs text-stone-400">' + escapeHtml(ts) + '</td></tr>';
      }).join("");
      eventsHtml += '</tbody></table></div></div>';
    }

    eventsHtml += '</div>';
  }

  html("#events", eventsHtml);
}

window.addEventListener("DOMContentLoaded", () => {
  const pathParts = window.location.pathname.split("/");
  const runId = pathParts[pathParts.length - 1];
  if (/^\d+$/.test(runId)) {
    loadDetail(runId);
  }
  document.getElementById("load_detail_btn")?.addEventListener("click", () => loadDetail());
});

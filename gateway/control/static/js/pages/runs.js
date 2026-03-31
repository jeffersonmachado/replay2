import { apiJson, jsonRequest } from "../core/api.js";
import { html, text } from "../core/dom.js";
import { runTableRow } from "../components/run_views.js";
import { emptyTableRow } from "../components/tables.js";
import { activatePageSections } from "../components/page_sections.js";

const FAILED_STATUSES = new Set(["failed", "cancelled", "canceled"]);
const COMPLIANCE_BLOCKED = new Set(["rejected", "blocked"]);

async function act(runId, action) {
  await apiJson(`/api/runs/${runId}/${action}`, jsonRequest("POST", {}));
  await loadSection();
}

function activeSection() {
  return String((window.__R2CTL_PAGE_STATE__ || {}).section || "queue");
}

function renderTable(tbodyId, runs, statusId, message) {
  text(statusId, `${runs.length} itens`);
  html(tbodyId, runs.length ? runs.map(runTableRow).join("") : emptyTableRow(message));
}

async function loadSection() {
  const section = activeSection();

  if (section === "queue") {
    const result = await apiJson("/api/runs?limit=200");
    if (!result?.data) return;
    const statusFilter = (document.getElementById("runs_filter_status")?.value || "").trim().toLowerCase();
    const complianceFilter = (document.getElementById("runs_filter_compliance")?.value || "").trim().toLowerCase();
    const runs = (result.data.runs || []).filter((run) => {
      const okStatus = !statusFilter || String(run.status || "").toLowerCase().includes(statusFilter);
      const okCompliance = !complianceFilter || String(run.compliance_status || "").toLowerCase().includes(complianceFilter);
      return okStatus && okCompliance;
    });
    text("#runs_visible_count", runs.length);
    text("#runs_failed_count", runs.filter((r) => FAILED_STATUSES.has(String(r.status || "").toLowerCase())).length);
    text("#runs_blocked_count", runs.filter((r) => COMPLIANCE_BLOCKED.has(String(r.compliance_status || "").toLowerCase())).length);
    text("#runs_refresh_status", `atualizado com ${runs.length} itens`);
    html("#runs_rows", runs.length ? runs.map(runTableRow).join("") : emptyTableRow("Nenhuma run encontrada para o filtro atual."));
    return;
  }

  if (section === "history") {
    const result = await apiJson("/api/runs?limit=500");
    if (!result?.data) return;
    const runs = (result.data.runs || []).slice().sort((a, b) => (b.created_at_ms || 0) - (a.created_at_ms || 0));
    renderTable("#history_rows", runs, "#history_refresh_status", "Nenhuma run no historico.");
    return;
  }

  if (section === "failures") {
    const result = await apiJson("/api/runs?limit=500");
    if (!result?.data) return;
    const runs = (result.data.runs || []).filter((r) => FAILED_STATUSES.has(String(r.status || "").toLowerCase()));
    renderTable("#failures_rows", runs, "#failures_refresh_status", "Nenhuma run com falha encontrada.");
    return;
  }

  if (section === "compliance") {
    const result = await apiJson("/api/runs?limit=500");
    if (!result?.data) return;
    const runs = (result.data.runs || []).filter((r) => COMPLIANCE_BLOCKED.has(String(r.compliance_status || "").toLowerCase()));
    renderTable("#compliance_rows", runs, "#compliance_refresh_status", "Nenhuma run com compliance bloqueado.");
    return;
  }
}

async function compareRuns() {
  const runA = (document.getElementById("compare_run_a")?.value || "").trim();
  const runB = (document.getElementById("compare_run_b")?.value || "").trim();
  if (!runA || !runB) return;
  const [resA, resB] = await Promise.all([
    apiJson(`/api/runs/${encodeURIComponent(runA)}`),
    apiJson(`/api/runs/${encodeURIComponent(runB)}`),
  ]);
  const a = resA?.data?.run;
  const b = resB?.data?.run;
  if (!a || !b) {
    html("#compare_result", `<p class="text-sm text-rose-300">Uma ou ambas as runs nao foram encontradas.</p>`);
    return;
  }
  html(
    "#compare_result",
    `<div class="grid gap-4 lg:grid-cols-2">
      <div class="r2ctl-detail-surface rounded-2xl p-4">
        <div class="text-xs uppercase tracking-[0.14em] text-stone-500 mb-2">Run A — #${a.id}</div>
        <div class="text-sm text-stone-200">Status: ${a.status || "-"}</div>
        <div class="mt-1 text-sm text-stone-200">Compliance: ${a.compliance_status || "-"}</div>
        <div class="mt-1 text-sm text-stone-200">Destino: ${a.target_user || "-"}@${a.target_host || "-"}</div>
        <div class="mt-1 text-xs text-stone-400">Progresso: ${a.last_seq_global_applied || 0} eventos</div>
      </div>
      <div class="r2ctl-detail-surface rounded-2xl p-4">
        <div class="text-xs uppercase tracking-[0.14em] text-stone-500 mb-2">Run B — #${b.id}</div>
        <div class="text-sm text-stone-200">Status: ${b.status || "-"}</div>
        <div class="mt-1 text-sm text-stone-200">Compliance: ${b.compliance_status || "-"}</div>
        <div class="mt-1 text-sm text-stone-200">Destino: ${b.target_user || "-"}@${b.target_host || "-"}</div>
        <div class="mt-1 text-xs text-stone-400">Progresso: ${b.last_seq_global_applied || 0} eventos</div>
      </div>
    </div>`,
  );
}

window.addEventListener("DOMContentLoaded", () => {
  activatePageSections("runs", "queue");
  loadSection();

  ["runs_filter_status", "runs_filter_compliance"].forEach((id) => {
    document.getElementById(id)?.addEventListener("input", loadSection);
  });

  document.getElementById("compare_btn")?.addEventListener("click", compareRuns);

  document.addEventListener("click", async (event) => {
    const target = event.target.closest("[data-action]");
    if (!target) return;
    await act(target.dataset.id, target.dataset.action);
  });
});


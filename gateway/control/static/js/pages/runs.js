import { apiJson, jsonRequest } from "../core/api.js";
import { escapeHtml, formatCount, html, text } from "../core/dom.js";
import { failureTableRow, runTableRow, runSyntheticOrigin } from "../components/run_views.js";
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
    const originFilter = (document.getElementById("runs_filter_origin")?.value || "").trim().toLowerCase();
    const runs = (result.data.runs || []).filter((run) => {
      const okStatus = !statusFilter || String(run.status || "").toLowerCase().includes(statusFilter);
      const okCompliance = !complianceFilter || String(run.compliance_status || "").toLowerCase().includes(complianceFilter);
      const isSynthetic = Boolean(runSyntheticOrigin(run));
      const okOrigin = !originFilter || (originFilter === "synthetic" ? isSynthetic : !isSynthetic);
      return okStatus && okCompliance && okOrigin;
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
    const runId = (document.getElementById("failures_filter_run")?.value || "").trim();
    const failureType = (document.getElementById("failures_filter_type")?.value || "").trim();
    const severity = (document.getElementById("failures_filter_severity")?.value || "").trim();
    const qs = new URLSearchParams({ limit: "200" });
    if (runId) qs.set("run_id", runId);
    if (failureType) qs.set("failure_type", failureType);
    if (severity) qs.set("severity", severity);
    const result = await apiJson(`/api/runs/failures?${qs.toString()}`);
    if (!result?.data) return;
    const payload = result.data;
    const failures = payload.failures || [];
    const typeSelect = document.getElementById("failures_filter_type");
    if (typeSelect && typeSelect.dataset.loaded !== "1") {
      typeSelect.innerHTML =
        `<option value="">Todos os tipos</option>` +
        (payload.available_types || [])
          .map((t) => `<option value="${escapeHtml(t)}">${escapeHtml(t)}</option>`)
          .join("");
      typeSelect.value = failureType;
      typeSelect.dataset.loaded = "1";
    }
    html(
      "#failures_by_type",
      (payload.by_type || [])
        .map(
          (item) =>
            `<button data-failure-type="${escapeHtml(item.failure_type)}" class="rounded-full border border-stone-700 px-3 py-1 text-xs ${
              item.failure_type === failureType
                ? "bg-rose-900/40 text-rose-200 border-rose-800"
                : "bg-stone-800/60 text-stone-300 hover:bg-stone-700/60"
            }">${escapeHtml(item.failure_type)} · ${formatCount(item.count)}</button>`,
        )
        .join("") || `<span class="text-xs text-stone-500">nenhuma falha registrada</span>`,
    );
    text(
      "#failures_refresh_status",
      `últimas ${failures.length} de ${formatCount(payload.total || 0)} falhas`,
    );
    html(
      "#failures_rows",
      failures.length
        ? failures.map(failureTableRow).join("")
        : emptyTableRow("Nenhuma falha encontrada para o filtro atual."),
    );
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
        <div class="text-xs uppercase tracking-[0.14em] text-stone-400 mb-2">Run A — #${a.id}</div>
        <div class="text-sm text-stone-200">Status: ${escapeHtml(a.status || "-")}</div>
        <div class="mt-1 text-sm text-stone-200">Compliance: ${escapeHtml(a.compliance_status || "-")}</div>
        <div class="mt-1 text-sm text-stone-200">Destino: ${escapeHtml(a.target_user || "-")}@${escapeHtml(a.target_host || "-")}</div>
        <div class="mt-1 text-xs text-stone-400">Progresso: ${a.last_seq_global_applied || 0} eventos</div>
      </div>
      <div class="r2ctl-detail-surface rounded-2xl p-4">
        <div class="text-xs uppercase tracking-[0.14em] text-stone-400 mb-2">Run B — #${b.id}</div>
        <div class="text-sm text-stone-200">Status: ${escapeHtml(b.status || "-")}</div>
        <div class="mt-1 text-sm text-stone-200">Compliance: ${escapeHtml(b.compliance_status || "-")}</div>
        <div class="mt-1 text-sm text-stone-200">Destino: ${escapeHtml(b.target_user || "-")}@${escapeHtml(b.target_host || "-")}</div>
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
  document.getElementById("runs_filter_origin")?.addEventListener("change", loadSection);

  document.getElementById("compare_btn")?.addEventListener("click", compareRuns);

  document.getElementById("failures_filter_btn")?.addEventListener("click", loadSection);
  document.getElementById("failures_filter_run")?.addEventListener("keydown", (e) => {
    if (e.key === "Enter") loadSection();
  });
  document.getElementById("failures_filter_severity")?.addEventListener("change", loadSection);
  document.getElementById("failures_by_type")?.addEventListener("click", (e) => {
    const chip = e.target.closest("[data-failure-type]");
    if (!chip) return;
    const select = document.getElementById("failures_filter_type");
    if (!select) return;
    // Clicar no chip ativo limpa o filtro; clicar em outro aplica.
    select.value = select.value === chip.dataset.failureType ? "" : chip.dataset.failureType;
    loadSection();
  });

  document.addEventListener("click", async (event) => {
    const target = event.target.closest("[data-action]");
    if (!target) return;
    await act(target.dataset.id, target.dataset.action);
  });
});


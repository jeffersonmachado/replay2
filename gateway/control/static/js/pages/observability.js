import { apiJson, jsonRequest } from "../core/api.js";
import { escapeHtml, formatCount, html, text } from "../core/dom.js";
import { regressionCard, runCompactCard } from "../components/run_views.js";
import { textListCards } from "../components/status_cards.js";
import { statList } from "../components/tables.js";
import { buildQuery } from "../components/filters.js";
import { activatePageSections } from "../components/page_sections.js";

function renderScenarios(items) {
  window.obsScenarios = items || [];
  html(
    "#obs_scenarios",
    (items || []).length
      ? (items || []).map((item, index) => `
          <div class="r2ctl-obs-run">
            <div class="flex items-center justify-between gap-3">
              <div>
                <div class="font-mono text-sm text-stone-100">${escapeHtml(item.name || "-")}</div>
                <div class="mt-1 text-[11px] text-stone-500">${escapeHtml(item.visibility || "private")} • tags=${escapeHtml((item.tags || []).join(",") || "-")}</div>
              </div>
              <div class="flex gap-2">
                <button class="r2ctl-btn-soft" data-obs-use="${index}">Aplicar</button>
                <button class="r2ctl-btn-soft" data-obs-delete="${escapeHtml(item.id)}">Excluir</button>
              </div>
            </div>
          </div>
        `).join("")
      : '<div class="text-sm text-stone-400">Nenhum cenário salvo.</div>',
  );
  document.querySelectorAll("[data-obs-use]").forEach((button) => button.addEventListener("click", () => useScenario(button.dataset.obsUse)));
  document.querySelectorAll("[data-obs-delete]").forEach((button) => button.addEventListener("click", () => deleteScenario(button.dataset.obsDelete)));
}

function currentScenarioFilters() {
  return {
    log_dir: document.getElementById("obs_log_dir")?.value || "",
    environment: document.getElementById("obs_env_filter")?.value || "",
    created_from_ms: document.getElementById("obs_created_from")?.value || "",
    created_to_ms: document.getElementById("obs_created_to")?.value || "",
    run_limit: document.getElementById("obs_run_limit")?.value || "50",
  };
}

function useScenario(index) {
  const item = (window.obsScenarios || [])[Number(index)] || {};
  const filters = item.filters || {};
  document.getElementById("obs_log_dir").value = filters.log_dir || "";
  document.getElementById("obs_env_filter").value = filters.environment || "";
  document.getElementById("obs_created_from").value = filters.created_from_ms || "";
  document.getElementById("obs_created_to").value = filters.created_to_ms || "";
  document.getElementById("obs_run_limit").value = filters.run_limit || "50";
  loadOverview();
}

async function loadScenarios() {
  const qs = buildQuery({
    visibility: document.getElementById("obs_scenario_visibility_filter")?.value || "",
    tag: document.getElementById("obs_scenario_tag_filter")?.value || "",
  });
  const result = await apiJson(`/api/observability/scenarios?${qs}`);
  if (result?.data) renderScenarios(result.data.scenarios || []);
}

async function saveScenario() {
  const payload = {
    name: document.getElementById("obs_scenario_name")?.value || "",
    visibility: document.getElementById("obs_scenario_visibility")?.value || "private",
    tags: document.getElementById("obs_scenario_tags")?.value || "",
    filters: currentScenarioFilters(),
  };
  const result = await apiJson("/api/observability/scenarios", jsonRequest("POST", payload));
  if (result?.ok) {
    document.getElementById("obs_scenario_name").value = "";
    document.getElementById("obs_scenario_tags").value = "";
    loadScenarios();
  }
}

async function deleteScenario(id) {
  await apiJson(`/api/observability/scenarios/${id}`, { method: "DELETE" });
  loadScenarios();
}

function renderList(selector, items, keyName) {
  html(selector, statList(items || [], keyName));
}

async function loadOverview() {
  const qs = buildQuery(currentScenarioFilters());
  const result = await apiJson(`/api/observability/overview?${qs}`);
  if (!result?.data) return;
  const data = result.data;
  const gw = data.gateway || {};
  const ops = data.ops || {};
  const trend = data.trend || {};
  const reprocess = ops.reprocess_analytics || {};
  text("#obs_status", data.error || `gateway=${gw.log_dir || "-"} • arquivos=${gw.files_scanned || 0}`);
  text("#obs_metric_events", (gw.summary || {}).window_events || 0);
  text("#obs_metric_sessions", (gw.summary || {}).unique_sessions || 0);
  text("#obs_metric_open_runs", (ops.summary || {}).open_runs || 0);
  text("#obs_metric_failures", (ops.summary || {}).total_failures || 0);
  renderList("#obs_gateway_types", (gw.summary || {}).top_types || [], "type");
  renderList("#obs_run_status", ops.run_status || [], "status");
  renderList("#obs_failure_types", ops.failure_types || [], "failure_type");
  html("#obs_failures", JSON.stringify(ops.recent_failures || [], null, 2));
  html("#obs_runs", (ops.recent_runs || []).length ? (ops.recent_runs || []).map(runCompactCard).join("") : '<div class="text-sm text-stone-400">Sem runs recentes.</div>');
  html("#obs_regressions", (ops.recent_regressions || []).length ? (ops.recent_regressions || []).map(regressionCard).join("") : '<div class="text-sm text-stone-400">Nenhuma regressão relevante.</div>');
  html("#obs_active_filters", Object.entries((trend.summary || {}).filters || currentScenarioFilters()).map(([key, value]) => `<div class="flex items-center justify-between rounded-xl border border-stone-800 bg-stone-950/40 px-3 py-2"><span class="text-xs uppercase tracking-[0.14em] text-stone-500">${escapeHtml(key)}</span><span class="font-mono text-xs text-stone-200">${escapeHtml(value || "-")}</span></div>`).join(""));
  ["#obs_sla_breaches", "#obs_sla_warnings", "#obs_reprocess_flows", "#obs_reprocess_signatures", "#obs_reprocess_environments", "#obs_reprocess_candidates", "#obs_reprocess_queue", "#obs_trend_environments", "#obs_trend_flows"].forEach((selector) => html(selector, ""));
  html("#obs_sla_breaches", textListCards(ops.sla_breaches || [], "Nenhum cenário com SLA estourado.", (item) => ((item.sla_summary || {}).breaches || []).join(" | ") || "sem detalhe"));
  html("#obs_sla_warnings", textListCards(ops.sla_warnings || [], "Nenhum cenário em alerta.", (item) => ((item.sla_summary || {}).warnings || []).join(" | ") || "sem detalhe"));
  html("#obs_reprocess_flows", textListCards(reprocess.by_flow || [], "Sem reprocessamentos por fluxo.", (item) => `tentativas=${formatCount(item.attempts || 0)}`));
  html("#obs_reprocess_signatures", textListCards(reprocess.repeated_signatures || [], "Sem assinaturas repetidas.", (item) => `repeticao=${escapeHtml(item.repeat_rate_pct || 0)}%`));
  html("#obs_reprocess_environments", textListCards(reprocess.by_environment || [], "Sem recuperação por ambiente.", (item) => `tentativas=${formatCount(item.attempts || 0)}`));
  html("#obs_reprocess_candidates", textListCards(reprocess.automation_candidates || [], "Sem candidatas fortes a automação.", (item) => `score=${escapeHtml(item.automation_candidate_score || 0)}`));
  html("#obs_reprocess_queue", textListCards(reprocess.pending_queue || [], "Sem fila pendente ou reincidente.", (item) => `${escapeHtml(item.failure_type || "-")} • ${escapeHtml(item.scope || "-")}`));
  html("#obs_trend_environments", textListCards(trend.environments || [], "Sem tendência por ambiente.", (item) => `runs=${formatCount(item.runs || 0)} • regressões=${formatCount(item.regressions || 0)}`));
  html("#obs_trend_flows", textListCards(trend.flows || [], "Sem tendência por fluxo.", (item) => `falhas=${formatCount(item.failures || 0)} • regressões=${formatCount(item.regressions || 0)}`));
}

window.addEventListener("DOMContentLoaded", () => {
  activatePageSections("observability", "overview");
  loadOverview();
  loadScenarios();
  document.getElementById("obs_refresh_btn")?.addEventListener("click", loadOverview);
  document.getElementById("obs_save_scenario_btn")?.addEventListener("click", saveScenario);
  ["obs_scenario_visibility_filter", "obs_scenario_tag_filter"].forEach((id) => document.getElementById(id)?.addEventListener("input", loadScenarios));
  setInterval(() => {
    if (document.getElementById("obs_auto_refresh")?.checked) {
      loadOverview();
    }
  }, 3000);
});

import { apiJson, jsonRequest } from "../core/api.js";
import { html, text } from "../core/dom.js";
import { operationalScenarioCard, policyCard, profileCard, targetCard } from "../components/catalog_views.js";
import { clearScenarioEditor, collectOperationalRoutingPayload, collectProfilePayload, collectRunLikePayload, collectScenarioMetaPayload, collectTargetPayload, fillScenarioEditor } from "../core/form_helpers.js";
import { buildQuery } from "../components/filters.js";
import { activatePageSections } from "../components/page_sections.js";

function populateSelect(id, items, mapFn, emptyLabel) {
  const sel = document.getElementById(id);
  if (!sel) return;
  const options = [`<option value="">${emptyLabel}</option>`]
    .concat((items || []).map((item) => {
      const mapped = mapFn(item) || {};
      return `<option value="${mapped.value || ""}">${mapped.label || ""}</option>`;
    }));
  sel.innerHTML = options.join("");
}

async function loadTargets() {
  const result = await apiJson("/api/targets");
  if (!result?.data) return [];
  const targets = result.data.targets || [];
  text("#targets_status", `ambientes cadastrados: ${targets.length}`);
  html("#catalog_targets_list", targets.map((item) => targetCard(item)).join("") || '<div class="text-sm text-stone-400">Nenhum ambiente cadastrado.</div>');
  html("#catalog_policies_list", targets.map((item) => policyCard(item)).join("") || '<div class="text-sm text-stone-400">Nenhuma política derivada disponível.</div>');
  populateSelect("ops_target_env_id", targets, (item) => ({ value: item.id, label: `${item.name || item.env_id} (${item.host || "-"})` }), "ambiente alvo (opcional)");
  return targets;
}

async function createTarget() {
  const result = await apiJson("/api/targets", jsonRequest("POST", collectTargetPayload()));
  if (result?.ok) await loadTargets();
}

async function loadProfiles() {
  const result = await apiJson("/api/connection-profiles");
  if (!result?.data) return;
  const profiles = result.data.connection_profiles || [];
  text("#profiles_status", `perfis cadastrados: ${profiles.length}`);
  html("#catalog_profiles_list", profiles.map((item) => profileCard(item)).join("") || '<div class="text-sm text-stone-400">Nenhum perfil cadastrado.</div>');
  populateSelect("ops_connection_profile_id", profiles, (item) => ({ value: item.id, label: `${item.name || item.profile_id} (${item.username || "-"})` }), "perfil de conexão (opcional)");
}

async function createProfile() {
  const result = await apiJson("/api/connection-profiles", jsonRequest("POST", collectProfilePayload()));
  if (result?.ok) await loadProfiles();
}

function clearOperationalScenarioEditor() {
  clearScenarioEditor();
}

async function loadOperationalScenarios() {
  const qs = buildQuery({
    environment: document.getElementById("ops_filter_environment")?.value || "",
    usage_user: document.getElementById("ops_filter_usage_user")?.value || "",
    scenario_type: document.getElementById("ops_filter_type")?.value || "",
    sort_by: document.getElementById("ops_sort_by")?.value || "updated",
  });
  const result = await apiJson(`/api/operational-scenarios?${qs}`);
  if (!result?.data) return;
  const scenarios = result.data.scenarios || [];
  window.operationalScenarios = scenarios;
  text("#ops_scenarios_status", `cenários: ${scenarios.length}`);
  html("#ops_scenarios", scenarios.length ? scenarios.map((item, index) => operationalScenarioCard(item, index)).join("") : '<div class="text-sm text-stone-400">Nenhum cenário operacional salvo.</div>');
  document.querySelectorAll("[data-edit-scenario]").forEach((button) => button.addEventListener("click", () => editScenario(button.dataset.editScenario)));
  document.querySelectorAll("[data-instantiate-scenario]").forEach((button) => button.addEventListener("click", () => instantiateScenario(button.dataset.instantiateScenario)));
}

function editScenario(index) {
  fillScenarioEditor((window.operationalScenarios || [])[Number(index)] || {});
}

async function instantiateScenario(id) {
  const result = await apiJson(`/api/operational-scenarios/${id}/instantiate-run`, jsonRequest("POST", {}));
  if (result?.data?.run_id) {
    window.location = `/runs/${result.data.run_id}`;
  }
}

async function saveOperationalScenario() {
  const payload = { ...collectRunLikePayload(), ...collectOperationalRoutingPayload(), ...collectScenarioMetaPayload() };
  const result = await apiJson("/api/operational-scenarios", jsonRequest("POST", payload));
  if (result?.ok) {
    clearOperationalScenarioEditor();
    await loadOperationalScenarios();
  }
}

window.addEventListener("DOMContentLoaded", () => {
  activatePageSections("catalog", "targets");
  loadTargets();
  loadProfiles();
  loadOperationalScenarios();
  document.getElementById("catalog_refresh_targets_btn")?.addEventListener("click", loadTargets);
  document.getElementById("catalog_refresh_profiles_btn")?.addEventListener("click", loadProfiles);
  document.getElementById("catalog_refresh_scenarios_btn")?.addEventListener("click", loadOperationalScenarios);
  document.getElementById("catalog_create_target_btn")?.addEventListener("click", createTarget);
  document.getElementById("catalog_create_profile_btn")?.addEventListener("click", createProfile);
  document.getElementById("save_operational_scenario_btn")?.addEventListener("click", saveOperationalScenario);
  document.getElementById("clear_operational_editor_btn")?.addEventListener("click", clearOperationalScenarioEditor);
  ["ops_filter_environment", "ops_filter_usage_user", "ops_filter_type", "ops_sort_by"].forEach((id) => document.getElementById(id)?.addEventListener("input", loadOperationalScenarios));
});

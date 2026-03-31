/**
 * form_helpers.js — helpers de leitura e reset de formulários compartilhados entre
 * catalog.js e run_new.js (targets, connection profiles, campos base de run).
 */

export function fieldInt(id) {
  return parseInt(document.getElementById(id)?.value || "0", 10);
}

export function fieldFloat(id) {
  return parseFloat(document.getElementById(id)?.value || "0");
}

export function collectTargetPayload() {
  return {
    env_id: document.getElementById("target_env_env_id")?.value || "",
    name: document.getElementById("target_env_name")?.value || "",
    host: document.getElementById("target_env_host")?.value || "",
    port: document.getElementById("target_env_port")?.value || "",
    platform: document.getElementById("target_env_platform")?.value || "linux",
    transport_hint: document.getElementById("target_env_transport_hint")?.value || "ssh",
    gateway_required: document.getElementById("target_env_gateway_required")?.checked || false,
    direct_ssh_policy: document.getElementById("target_env_direct_ssh_policy")?.value || "unrestricted",
    capture_start_mode: document.getElementById("target_env_capture_start_mode")?.value || "session_start_required",
    capture_compliance_mode: document.getElementById("target_env_capture_compliance_mode")?.value || "off",
    allow_admin_direct_access: document.getElementById("target_env_allow_admin_direct_access")?.checked || false,
    gateway_host: document.getElementById("target_env_gateway_host")?.value || "",
    gateway_user: document.getElementById("target_env_gateway_user")?.value || "",
    gateway_port: document.getElementById("target_env_gateway_port")?.value || "",
    description: document.getElementById("target_env_description")?.value || "",
  };
}

export function resetTargetFields() {
  [
    "target_env_env_id",
    "target_env_name",
    "target_env_host",
    "target_env_port",
    "target_env_gateway_host",
    "target_env_gateway_user",
    "target_env_gateway_port",
    "target_env_description",
  ].forEach((id) => {
    const el = document.getElementById(id);
    if (el) el.value = "";
  });
  const sel = (id, v) => { const el = document.getElementById(id); if (el) el.value = v; };
  sel("target_env_platform", "linux");
  sel("target_env_transport_hint", "ssh");
  sel("target_env_direct_ssh_policy", "unrestricted");
  sel("target_env_capture_start_mode", "session_start_required");
  sel("target_env_capture_compliance_mode", "off");
  const chk = (id, v) => { const el = document.getElementById(id); if (el) el.checked = v; };
  chk("target_env_gateway_required", false);
  chk("target_env_allow_admin_direct_access", false);
}

export function collectProfilePayload() {
  return {
    profile_id: document.getElementById("connection_profile_profile_id")?.value || "",
    name: document.getElementById("connection_profile_name")?.value || "",
    transport: document.getElementById("connection_profile_transport")?.value || "ssh",
    username: document.getElementById("connection_profile_username")?.value || "",
    port: document.getElementById("connection_profile_port")?.value || "",
    command: document.getElementById("connection_profile_command")?.value || "",
    credential_ref: document.getElementById("connection_profile_credential_ref")?.value || "",
  };
}

export function resetProfileFields() {
  [
    "connection_profile_profile_id",
    "connection_profile_name",
    "connection_profile_username",
    "connection_profile_port",
    "connection_profile_command",
    "connection_profile_credential_ref",
  ].forEach((id) => {
    const el = document.getElementById(id);
    if (el) el.value = "";
  });
  const el = document.getElementById("connection_profile_transport");
  if (el) el.value = "ssh";
}

export function collectRunMatchParams() {
  const params = {};
  params.match_mode = document.getElementById("match_mode")?.value || "strict";
  params.input_mode = document.getElementById("input_mode")?.value || "raw";
  params.on_deterministic_mismatch = document.getElementById("on_deterministic_mismatch")?.value || "fail-fast";
  const threshold = fieldFloat("match_threshold");
  if (threshold >= 0) params.match_threshold = threshold;
  return params;
}

export function collectRunBaseFields() {
  return {
    log_dir: document.getElementById("log_dir")?.value || "",
    target_host: document.getElementById("target_host")?.value || "",
    target_user: document.getElementById("target_user")?.value || "",
    target_command: document.getElementById("target_cmd")?.value || "",
    mode: document.getElementById("mode")?.value || "strict-global",
  };
}

export function collectRunLikePayload() {
  const params = { ...collectRunMatchParams() };
  const concurrency = fieldInt("concurrency");
  if (concurrency > 0) params.concurrency = concurrency;
  const speed = fieldFloat("speed");
  if (speed > 0) params.speed = speed;
  const jitter = fieldInt("jitter");
  if (jitter >= 0) params.jitter_ms = jitter;
  return { ...collectRunBaseFields(), params };
}

export function collectOperationalRoutingPayload() {
  const targetEnvId = parseInt(document.getElementById("ops_target_env_id")?.value || "0", 10) || 0;
  const connectionProfileId = parseInt(document.getElementById("ops_connection_profile_id")?.value || "0", 10) || 0;
  return {
    target_env_id: targetEnvId > 0 ? targetEnvId : null,
    connection_profile_id: connectionProfileId > 0 ? connectionProfileId : null,
  };
}

export function collectScenarioMetaPayload() {
  return {
    name: document.getElementById("ops_scenario_name")?.value || "",
    description: document.getElementById("ops_scenario_description")?.value || "",
    scenario_type: document.getElementById("ops_scenario_type")?.value || "replay",
    squad: document.getElementById("ops_scenario_squad")?.value || "",
    area: document.getElementById("ops_scenario_area")?.value || "",
    tags: document.getElementById("ops_scenario_tags")?.value || "",
    owner_name: document.getElementById("ops_scenario_owner_name")?.value || "",
    owner_contact: document.getElementById("ops_scenario_owner_contact")?.value || "",
    sla_max_failure_rate_pct: document.getElementById("ops_scenario_sla_failure_rate")?.value || "",
    sla_max_criticality_score: document.getElementById("ops_scenario_sla_criticality")?.value || "",
  };
}

export function fillScenarioEditor(item) {
  const set = (id, v) => { const el = document.getElementById(id); if (el) el.value = v ?? ""; };
  set("ops_scenario_name", item.name);
  set("ops_scenario_description", item.description);
  set("ops_scenario_type", item.scenario_type || "replay");
  set("ops_scenario_squad", item.squad);
  set("ops_scenario_area", item.area);
  set("ops_scenario_tags", (item.tags || []).join(","));
  set("ops_scenario_owner_name", item.owner_name);
  set("ops_scenario_owner_contact", item.owner_contact);
  set("ops_scenario_sla_failure_rate", (item.sla || {}).max_failure_rate_pct);
  set("ops_scenario_sla_criticality", (item.sla || {}).max_criticality_score);
  set("log_dir", item.log_dir);
  set("target_host", item.target_host);
  set("target_user", item.target_user);
  set("target_cmd", item.target_command);
  set("ops_target_env_id", item.target_env_id || "");
  set("ops_connection_profile_id", item.connection_profile_id || "");
  set("mode", item.mode || "strict-global");
  set("match_mode", (item.params || {}).match_mode || "strict");
  set("input_mode", (item.params || {}).input_mode || "raw");
  set("on_deterministic_mismatch", (item.params || {}).on_deterministic_mismatch || "fail-fast");
  set("match_threshold", (item.params || {}).match_threshold);
}

export function clearScenarioEditor() {
  [
    "ops_scenario_name",
    "ops_scenario_description",
    "ops_scenario_squad",
    "ops_scenario_area",
    "ops_scenario_tags",
    "ops_scenario_owner_name",
    "ops_scenario_owner_contact",
    "ops_scenario_sla_failure_rate",
    "ops_scenario_sla_criticality",
  ].forEach((id) => { const el = document.getElementById(id); if (el) el.value = ""; });
  const el = document.getElementById("ops_scenario_type");
  if (el) el.value = "replay";
  const targetSel = document.getElementById("ops_target_env_id");
  if (targetSel) targetSel.value = "";
  const profileSel = document.getElementById("ops_connection_profile_id");
  if (profileSel) profileSel.value = "";
}

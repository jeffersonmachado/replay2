import { apiJson, jsonRequest } from "../core/api.js";
import { escapeHtml, text } from "../core/dom.js";
import { collectProfilePayload, collectRunBaseFields, collectRunMatchParams, collectTargetPayload, fieldFloat, fieldInt, resetProfileFields, resetTargetFields } from "../core/form_helpers.js";

let _targetsById = new Map();
let _profilesById = new Map();

function collectRunFormPayload() {
  const mode = document.getElementById("mode").value;
  const params = { ...collectRunMatchParams() };
  if (mode === "parallel-sessions") {
    if (fieldInt("concurrency") > 0) params.concurrency = fieldInt("concurrency");
    const ramp = fieldFloat("ramp");
    if (ramp > 0) params.ramp_up_per_sec = ramp;
    if (fieldFloat("speed") > 0) params.speed = fieldFloat("speed");
    if (fieldInt("jitter") >= 0) params.jitter_ms = fieldInt("jitter");
    const pool = (document.getElementById("user_pool")?.value || "").split(",").map((item) => item.trim()).filter(Boolean);
    if (pool.length) params.target_user_pool = pool;
    params.on_checkpoint_mismatch = document.getElementById("on_mismatch")?.value || "continue";
  }
  if (fieldInt("replay_from_seq_global") > 0) params.replay_from_seq_global = fieldInt("replay_from_seq_global");
  if (fieldInt("replay_to_seq_global") > 0) params.replay_to_seq_global = fieldInt("replay_to_seq_global");
  if ((document.getElementById("replay_session_id")?.value || "").trim()) params.replay_session_id = document.getElementById("replay_session_id").value.trim();
  if ((document.getElementById("replay_from_checkpoint_sig")?.value || "").trim()) params.replay_from_checkpoint_sig = document.getElementById("replay_from_checkpoint_sig").value.trim();
  if (document.getElementById("match_ignore_case")?.checked) params.match_ignore_case = true;

  const payload = { ...collectRunBaseFields(), mode, params };
  if (fieldInt("target_env_id") > 0) payload.target_env_id = fieldInt("target_env_id");
  if (fieldInt("connection_profile_id") > 0) payload.connection_profile_id = fieldInt("connection_profile_id");
  return payload;
}

function parsePositiveInt(value) {
  const parsed = parseInt(String(value || "0"), 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : 0;
}

function collectQueryPrefill() {
  const params = new URLSearchParams(window.location.search);
  return {
    targetEnvId: parsePositiveInt(params.get("target_env_id")),
    connectionProfileId: parsePositiveInt(params.get("connection_profile_id")),
    replaySessionId: (params.get("replay_session_id") || "").trim(),
    replayFromCheckpointSig: (params.get("replay_from_checkpoint_sig") || "").trim(),
    inputMode: (params.get("input_mode") || "").trim(),
    onDeterministicMismatch: (params.get("on_deterministic_mismatch") || "").trim(),
  };
}

function syncManualTargetFields() {
  const targetId = parsePositiveInt(document.getElementById("target_env_id")?.value || "0");
  const profileId = parsePositiveInt(document.getElementById("connection_profile_id")?.value || "0");
  const hostInput = document.getElementById("target_host");
  const userInput = document.getElementById("target_user");
  const cmdInput = document.getElementById("target_cmd");
  const status = document.getElementById("run_form_status");
  const target = _targetsById.get(targetId);
  const profile = _profilesById.get(profileId);

  if (hostInput) {
    if (target && (target.host || "").trim()) hostInput.value = String(target.host || "").trim();
    hostInput.disabled = Boolean(targetId);
  }
  if (userInput) {
    if (profile && (profile.username || "").trim()) userInput.value = String(profile.username || "").trim();
    userInput.disabled = Boolean(profileId);
  }
  if (cmdInput) {
    if (profile && profile.command != null) cmdInput.value = String(profile.command || "").trim();
    cmdInput.disabled = Boolean(profileId);
  }
  if (status) {
    if (targetId || profileId) {
      status.textContent = "resolução via catálogo ativa";
    } else {
      status.textContent = "pronto";
    }
  }
}

function applyQueryPrefill() {
  const prefill = collectQueryPrefill();
  const targetSelect = document.getElementById("target_env_id");
  const profileSelect = document.getElementById("connection_profile_id");
  if (targetSelect && prefill.targetEnvId > 0 && _targetsById.has(prefill.targetEnvId)) {
    targetSelect.value = String(prefill.targetEnvId);
  }
  if (profileSelect && prefill.connectionProfileId > 0 && _profilesById.has(prefill.connectionProfileId)) {
    profileSelect.value = String(prefill.connectionProfileId);
  }
  if (prefill.replaySessionId) {
    const el = document.getElementById("replay_session_id");
    if (el) el.value = prefill.replaySessionId;
  }
  if (prefill.replayFromCheckpointSig) {
    const el = document.getElementById("replay_from_checkpoint_sig");
    if (el) el.value = prefill.replayFromCheckpointSig;
  }
  if (prefill.inputMode) {
    const el = document.getElementById("input_mode");
    if (el) el.value = prefill.inputMode;
  }
  if (prefill.onDeterministicMismatch) {
    const el = document.getElementById("on_deterministic_mismatch");
    if (el) el.value = prefill.onDeterministicMismatch;
  }
  syncManualTargetFields();
}

function resetTargetForm() {
  resetTargetFields();
}

async function loadTargets() {
  const result = await apiJson("/api/targets");
  if (!result?.data) return;
  const targets = result.data.targets || [];
  _targetsById = new Map(targets.map((item) => [parsePositiveInt(item.id), item]));
  const select = document.getElementById("target_env_id");
  if (select) {
    select.innerHTML = '<option value="">ambiente alvo manual</option>' + targets.map((item) => `<option value="${escapeHtml(item.id)}">${escapeHtml(item.name)} • ${escapeHtml(item.host)}</option>`).join("");
  }
  text("#targets_status", `ambientes cadastrados: ${targets.length}`);
  syncManualTargetFields();
}

async function loadProfiles() {
  const result = await apiJson("/api/connection-profiles");
  if (!result?.data) return;
  const profiles = result.data.connection_profiles || [];
  _profilesById = new Map(profiles.map((item) => [parsePositiveInt(item.id), item]));
  const select = document.getElementById("connection_profile_id");
  if (select) {
    select.innerHTML = '<option value="">perfil de conexão manual</option>' + profiles.map((item) => `<option value="${escapeHtml(item.id)}">${escapeHtml(item.name)} • ${escapeHtml(item.transport || "ssh")}</option>`).join("");
  }
  text("#profiles_status", `perfis cadastrados: ${profiles.length}`);
  syncManualTargetFields();
}

async function createTarget() {
  const result = await apiJson("/api/targets", jsonRequest("POST", collectTargetPayload()));
  if (!result?.ok) return;
  resetTargetForm();
  await loadTargets();
}

async function createProfile() {
  const result = await apiJson("/api/connection-profiles", jsonRequest("POST", collectProfilePayload()));
  if (!result?.ok) return;
  resetProfileFields();
  await loadProfiles();
}

async function createRun() {
  const payload = collectRunFormPayload();
  text("#run_form_status", "criando run...");
  const result = await apiJson("/api/runs", jsonRequest("POST", payload));
  if (!result?.data?.id) {
    text("#run_form_status", "falha ao criar run");
    return;
  }
  localStorage.setItem("gatewayMonitorLogDir", payload.log_dir || "");
  window.location = `/runs/${result.data.id}`;
}

// Pré-preenche log_dir vindo de uma captura (/runs/new?log_dir=...&source_capture=...)
function preselectFromCapture() {
  const params = new URLSearchParams(window.location.search);
  const logDir = params.get("log_dir") || "";
  const sourceCaptureId = params.get("source_capture") || "";
  if (!logDir) return;
  const logDirInput = document.getElementById("log_dir");
  if (logDirInput) {
    logDirInput.value = logDir;
  }
  if (sourceCaptureId) {
    const note = document.createElement("div");
    note.className = "text-xs text-stone-400 mt-1";
    note.textContent = `Replay originado da captura #${sourceCaptureId}`;
    logDirInput?.parentElement?.appendChild(note);
  }
}

window.addEventListener("DOMContentLoaded", () => {
  Promise.all([loadTargets(), loadProfiles()]).then(() => {
    preselectFromCapture();
    applyQueryPrefill();
  });
  document.getElementById("target_env_id")?.addEventListener("change", syncManualTargetFields);
  document.getElementById("connection_profile_id")?.addEventListener("change", syncManualTargetFields);
  document.getElementById("create_run_btn")?.addEventListener("click", createRun);
  document.getElementById("refresh_targets_btn")?.addEventListener("click", loadTargets);
  document.getElementById("refresh_profiles_btn")?.addEventListener("click", loadProfiles);
  document.getElementById("create_target_btn")?.addEventListener("click", createTarget);
  document.getElementById("create_profile_btn")?.addEventListener("click", createProfile);
});

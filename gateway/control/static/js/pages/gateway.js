import { apiJson, jsonRequest } from "../core/api.js";
import { escapeHtml, formatAgo, formatCount, html, text } from "../core/dom.js";
import { gatewayMetricRows, gatewaySessionCard, gatewaySignalRows } from "../components/gateway_views.js";
import { buildQuery } from "../components/filters.js";
import { activatePageSections } from "../components/page_sections.js";

// ── Feedback ───────────────────────────────────────────────────────────────

function setFeedback(message, tone = "neutral") {
  const el = document.getElementById("gateway_feedback");
  if (!el) return;
  const tones = { neutral: "text-stone-300", success: "text-emerald-300", warning: "text-amber-300", danger: "text-rose-300" };
  el.className = `text-sm ${tones[tone] || tones.neutral}`;
  el.textContent = String(message || "");
}

// ── Renderizar estado do gateway ───────────────────────────────────────────

function formatTs(ms) {
  if (!ms) return "-";
  try {
    return new Date(ms).toLocaleString("pt-BR");
  } catch (_) {
    return String(ms);
  }
}

function renderGatewayState(state) {
  const active = Boolean(state.active);
  const env = state.environment || {};
  const policy = state.policy || {};

  text("#gw_state_active", active ? "ATIVO" : "INATIVO");
  const activeEl = document.getElementById("gw_state_active");
  if (activeEl) {
    activeEl.className = `mt-2 text-xl font-semibold ${active ? "text-emerald-300" : "text-stone-400"}`;
  }
  const stateCardEl = document.getElementById("gw_state_card");
  if (stateCardEl) {
    stateCardEl.className = active
      ? "r2ctl-metric-success rounded-2xl px-4 py-4"
      : "r2ctl-detail-surface rounded-2xl px-4 py-4";
  }

  const envName = env.env_name || env.hostname || "-";
  text("#gw_state_env", envName);
  text("#gw_state_by", state.activated_by_username || "-");
  text("#gw_state_at", formatTs(state.activated_at_ms));
  text("#gw_state_hostname", env.hostname || env.fqdn || "-");
  text("#gw_state_profile", state.connection_profile_id ? `perfil #${state.connection_profile_id}` : "nenhum");
  text("#gw_state_capture", policy.capture_available ? "disponível" : "indisponível");
  text("#gw_state_policy", policy.policy_ok === false ? "inconsistente" : (policy.policy_ok === true ? "ok" : "pendente"));
  text("#gw_state_ssh", policy.desired_ssh_route || "-");

  // Botões
  const activateBtn = document.getElementById("gateway_activate_btn");
  const deactivateBtn = document.getElementById("gateway_deactivate_btn");
  const capturePanel = document.getElementById("gw_capture_link_panel");
  if (activateBtn) { activateBtn.classList.toggle("hidden", active); }
  if (deactivateBtn) { deactivateBtn.classList.toggle("hidden", !active); }
  if (capturePanel) { capturePanel.classList.toggle("hidden", !active); }

  const statusMsg = policy.reason && policy.reason !== "ok"
    ? `${active ? "Gateway ativo" : "Gateway inativo"}: ${policy.reason}`
    : (active ? `Gateway ativo desde ${formatTs(state.activated_at_ms)}.` : "Gateway inativo.");
  setFeedback(
    state.error || statusMsg,
    active ? "success" : "neutral",
  );

  window.gatewayState = state;
}

// ── Carregar estado do gateway ─────────────────────────────────────────────

async function loadGatewayState() {
  const result = await apiJson("/api/gateway/state");
  if (result?.data) renderGatewayState(result.data);
}

// ── Ativar ─────────────────────────────────────────────────────────────────

async function activateGateway() {
  const profileId = document.getElementById("gw_profile_select")?.value || "";
  const opUserId = document.getElementById("gw_opuser_select")?.value || "";
  setFeedback("Ativando gateway...", "neutral");
  const result = await apiJson(
    "/api/gateway/activate",
    jsonRequest("POST", {
      connection_profile_id: profileId ? parseInt(profileId, 10) : null,
      operational_user_id: opUserId ? parseInt(opUserId, 10) : null,
    }),
  );
  if (!result) return;
  if (result.ok) {
    renderGatewayState(result.data);
    const autoCapture = result.data?.auto_capture || null;
    if (autoCapture && autoCapture.id) {
      setFeedback(`Gateway ativado e captura #${autoCapture.id} iniciada automaticamente.`, "success");
    }
  } else {
    setFeedback(result.data?.error || "Falha ao ativar gateway.", "danger");
  }
}

// ── Desativar ──────────────────────────────────────────────────────────────

async function deactivateGateway(force = false) {
  setFeedback("Desativando gateway...", "neutral");
  const result = await apiJson("/api/gateway/deactivate", jsonRequest("POST", { force }));
  if (!result) return;
  if (result.ok) {
    renderGatewayState(result.data);
  } else if (result.status === 409 && !force) {
    const err = result.data?.error || "";
    const confirmMsg = `${err}\n\nDeseja forçar a desativação?`;
    if (window.confirm(confirmMsg)) {
      await deactivateGateway(true);
    } else {
      setFeedback(err, "warning");
    }
  } else {
    setFeedback(result.data?.error || "Falha ao desativar gateway.", "danger");
  }
}

// ── Carregar seleções pro advanced panel ───────────────────────────────────

async function loadAdvancedSelections() {
  const [profilesRes, usersRes] = await Promise.all([
    apiJson("/api/connection-profiles"),
    apiJson("/api/users"),
  ]);
  const profileSel = document.getElementById("gw_profile_select");
  if (profileSel && (profilesRes?.data?.connection_profiles || profilesRes?.data?.profiles)) {
    (profilesRes.data.connection_profiles || profilesRes.data.profiles).forEach((p) => {
      const opt = document.createElement("option");
      opt.value = p.id;
      opt.textContent = p.name || p.profile_id;
      profileSel.appendChild(opt);
    });
  }
  const opUserSel = document.getElementById("gw_opuser_select");
  if (opUserSel && usersRes?.data?.users) {
    usersRes.data.users
      .filter((u) => u.role === "operator" || u.role === "admin")
      .forEach((u) => {
        const opt = document.createElement("option");
        opt.value = u.id;
        opt.textContent = u.username;
        opUserSel.appendChild(opt);
      });
  }
}

// ── Monitor / Sessões (mantidos do original) ───────────────────────────────

function currentLogDir() {
  return (document.getElementById("monitor_log_dir")?.value || localStorage.getItem("gatewayMonitorLogDir") || "").trim();
}

function renderGatewayMonitor(data) {
  const summary = data.summary || {};
  text("#gw_metric_events", formatCount(summary.window_events || 0));
  text("#gw_metric_sessions", formatCount(summary.unique_sessions || 0));
  text("#gw_metric_checkpoints", formatCount(summary.checkpoints || 0));
  text("#gw_metric_attention", formatCount(summary.attention_events || 0));
  text("#gw_monitor_status", data.error || `fonte: ${data.log_dir || "-"} • deterministic=${formatCount(summary.deterministic_inputs || 0)}`);
  text("#gw_last_seen", formatAgo(summary.last_ts_ms));
  html("#gw_top_types", gatewayMetricRows((summary.top_types || []).map((item) => ({ label: item.type, value: item.count })), "Sem eventos para resumir."));
  const lastSignal = summary.last_event || {};
  html("#gw_last_signal", gatewaySignalRows(lastSignal, ["type", "actor", "session_id", "dir", "n", "screen_sig", "key_kind"]));
  html("#gw_recent_events", JSON.stringify(data.events || [], null, 2));
}

async function loadGatewayMonitor() {
  const logDir = currentLogDir();
  if (!logDir) {
    text("#gw_monitor_status", "informe um log_dir para monitorar");
    return;
  }
  localStorage.setItem("gatewayMonitorLogDir", logDir);
  const result = await apiJson(`/api/gateway/monitor?log_dir=${encodeURIComponent(logDir)}&limit=40`);
  if (result?.data) renderGatewayMonitor(result.data);
}

function renderGatewaySessionDetail(data) {
  html("#gw_session_detail", JSON.stringify(data, null, 2));
}

async function loadGatewaySessionDetail(sessionId) {
  if (!sessionId) return;
  window.currentGatewaySessionId = sessionId;
  const logDir = currentLogDir();
  if (!logDir) return;
  const qs = buildQuery({
    log_dir: logDir,
    limit: document.getElementById("gw_detail_limit")?.value || "200",
    seq_global_from: document.getElementById("gw_detail_seq_from")?.value || "",
    seq_global_to: document.getElementById("gw_detail_seq_to")?.value || "",
    ts_from: document.getElementById("gw_detail_ts_from")?.value || "",
    ts_to: document.getElementById("gw_detail_ts_to")?.value || "",
  });
  const result = await apiJson(`/api/gateway/sessions/${encodeURIComponent(sessionId)}?${qs}`);
  if (result?.data) renderGatewaySessionDetail(result.data);
}

function renderGatewaySessions(data) {
  text("#gw_sessions_status", data.error || `sessões ${formatCount((data.summary || {}).returned_sessions || 0)} • fonte ${data.log_dir || "-"}`);
  html(
    "#gw_sessions",
    (data.sessions || []).length
      ? (data.sessions || []).map((item) => gatewaySessionCard(item)).join("")
      : '<div class="text-sm text-stone-400">Nenhuma sessão encontrada.</div>',
  );
  document.querySelectorAll("[data-session]").forEach((button) => {
    button.addEventListener("click", () => loadGatewaySessionDetail(button.dataset.session));
  });
}

async function loadGatewaySessions() {
  const logDir = currentLogDir();
  if (!logDir) {
    text("#gw_sessions_status", "informe um log_dir para monitorar");
    return;
  }
  const qs = buildQuery({
    log_dir: logDir,
    limit: "60",
    actor: document.getElementById("gw_session_actor")?.value || "",
    session_id: document.getElementById("gw_session_id_filter")?.value || "",
    event_type: document.getElementById("gw_session_event_type")?.value || "",
    q: document.getElementById("gw_session_q")?.value || "",
  });
  const result = await apiJson(`/api/gateway/sessions?${qs}`);
  if (result?.data) renderGatewaySessions(result.data);
}

// ── Init ───────────────────────────────────────────────────────────────────

window.addEventListener("DOMContentLoaded", () => {
  activatePageSections("gateway", "status");
  const saved = localStorage.getItem("gatewayMonitorLogDir") || "";
  if (saved) { const el = document.getElementById("monitor_log_dir"); if (el) el.value = saved; }

  loadGatewayState();
  loadAdvancedSelections();
  loadGatewayMonitor();
  loadGatewaySessions();

  document.getElementById("gateway_activate_btn")?.addEventListener("click", activateGateway);
  document.getElementById("gateway_deactivate_btn")?.addEventListener("click", () => deactivateGateway(false));
  document.getElementById("load_gateway_monitor_btn")?.addEventListener("click", loadGatewayMonitor);
  document.getElementById("load_gateway_sessions_btn")?.addEventListener("click", loadGatewaySessions);

  ["gw_session_actor", "gw_session_id_filter", "gw_session_event_type", "gw_session_q"].forEach((id) =>
    document.getElementById(id)?.addEventListener("input", loadGatewaySessions),
  );
  ["gw_detail_seq_from", "gw_detail_seq_to", "gw_detail_ts_from", "gw_detail_ts_to", "gw_detail_limit"].forEach((id) =>
    document.getElementById(id)?.addEventListener("input", () => loadGatewaySessionDetail(window.currentGatewaySessionId)),
  );
});

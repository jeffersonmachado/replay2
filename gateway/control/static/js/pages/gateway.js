import { apiJson, jsonRequest } from "../core/api.js";
import { escapeHtml, formatAgo, formatCount, html, text } from "../core/dom.js";
import { gatewayMetricRows, gatewaySessionCard, gatewaySignalRows } from "../components/gateway_views.js";
import { buildQuery } from "../components/filters.js";
import { activatePageSections } from "../components/page_sections.js";
import { connectWs } from "../core/ws.js";
import { initCombobox } from "../components/combobox.js";
import { renderEventsTable, renderEventsCards } from "../components/timeline_table.js";

// ── Feedback ───────────────────────────────────────────────────────────────

function setFeedback(message, tone = "neutral") {
  const el = document.getElementById("gateway_feedback");
  if (!el) return;
  const tones = { neutral: "text-stone-300", success: "text-emerald-300", warning: "text-amber-300", danger: "text-rose-300" };
  el.className = `text-sm ${tones[tone] || tones.neutral}`;
  el.textContent = String(message || "");
}

// ── Escopo de captura ─────────────────────────────────────────────────────

let _systemUsers = [];
let _systemGroups = [];
let _currentScope = { users: "*", groups: "*" };

async function loadCaptureScopeOptions() {
  try {
    const result = await apiJson("/api/gateway/system-users");
    _systemUsers = result.data?.users || [];
    _systemGroups = result.data?.groups || [];
    renderScopeCheckboxes();
  } catch (_) {
    _systemUsers = [];
    _systemGroups = [];
  }
}

function renderScopeCheckboxes() {
  // Usuários
  const usersList = document.getElementById("gw_scope_users_list");
  const usersCount = document.getElementById("gw_scope_users_count");
  if (usersList && usersCount) {
    const selectedUsers = _currentScope.users === "*" ? _systemUsers.map(u => u.name) : (_currentScope.users || "*").split(",").map(s => s.trim()).filter(Boolean);
    const isAllUsers = _currentScope.users === "*";
    usersCount.textContent = isAllUsers ? "todos" : `${selectedUsers.length} selecionados`;
    const userChecks = _systemUsers.map(u => `
      <label class="flex items-center gap-2 text-xs text-stone-300 hover:text-stone-100 cursor-pointer py-0.5">
        <input type="checkbox" class="gw-scope-user" value="${escapeHtml(u.name)}" ${isAllUsers || selectedUsers.includes(u.name) ? "checked" : ""} />
        <span class="font-mono">${escapeHtml(u.name)}</span>
        <span class="text-stone-500">(${u.uid})</span>
      </label>
    `).join("");
    usersList.innerHTML = `
      <label class="flex items-center gap-2 text-xs text-stone-100 hover:text-white cursor-pointer py-0.5 border-b border-stone-700/50 pb-1 mb-1 font-semibold">
        <input type="checkbox" class="gw-scope-toggle-all" data-target="gw-scope-user" ${isAllUsers ? "checked" : ""} />
        <span>Todos</span>
      </label>
      ${userChecks}
    `;
  }

  // Grupos
  const groupsList = document.getElementById("gw_scope_groups_list");
  const groupsCount = document.getElementById("gw_scope_groups_count");
  if (groupsList && groupsCount) {
    const selectedGroups = _currentScope.groups === "*" ? _systemGroups.map(g => g.name) : (_currentScope.groups || "*").split(",").map(s => s.trim()).filter(Boolean);
    const isAllGroups = _currentScope.groups === "*";
    groupsCount.textContent = isAllGroups ? "todos" : `${selectedGroups.length} selecionados`;
    const groupChecks = _systemGroups.map(g => `
      <label class="flex items-center gap-2 text-xs text-stone-300 hover:text-stone-100 cursor-pointer py-0.5">
        <input type="checkbox" class="gw-scope-group" value="${escapeHtml(g.name)}" ${isAllGroups || selectedGroups.includes(g.name) ? "checked" : ""} />
        <span class="font-mono">${escapeHtml(g.name)}</span>
        <span class="text-stone-500">(${g.gid})</span>
      </label>
    `).join("");
    groupsList.innerHTML = `
      <label class="flex items-center gap-2 text-xs text-stone-100 hover:text-white cursor-pointer py-0.5 border-b border-stone-700/50 pb-1 mb-1 font-semibold">
        <input type="checkbox" class="gw-scope-toggle-all" data-target="gw-scope-group" ${isAllGroups ? "checked" : ""} />
        <span>Todos</span>
      </label>
      ${groupChecks}
    `;
  }

  // Event listeners para "Todos"
  document.querySelectorAll(".gw-scope-toggle-all").forEach((toggle) => {
    toggle.onchange = () => {
      const target = toggle.dataset.target;
      document.querySelectorAll(`.${target}`).forEach((cb) => { cb.checked = toggle.checked; });
      _updateScopeCounts();
    };
  });

  // Event listeners para checkboxes individuais (sincroniza "Todos")
  document.querySelectorAll(".gw-scope-user, .gw-scope-group").forEach((cb) => {
    cb.onchange = () => {
      const all = cb.className.startsWith("gw-scope-user") ? "gw-scope-user" : "gw-scope-group";
      const total = document.querySelectorAll(`.${all}`).length;
      const checked = document.querySelectorAll(`.${all}:checked`).length;
      const toggleAll = document.querySelector(`.gw-scope-toggle-all[data-target="${all}"]`);
      if (toggleAll) { toggleAll.checked = checked === total; }
      _updateScopeCounts();
    };
  });
}

function _updateScopeCounts() {
  const usersTotal = document.querySelectorAll(".gw-scope-user").length;
  const usersChecked = document.querySelectorAll(".gw-scope-user:checked").length;
  const usersCount = document.getElementById("gw_scope_users_count");
  if (usersCount) { usersCount.textContent = usersChecked === usersTotal ? "todos" : `${usersChecked} selecionados`; }

  const groupsTotal = document.querySelectorAll(".gw-scope-group").length;
  const groupsChecked = document.querySelectorAll(".gw-scope-group:checked").length;
  const groupsCount = document.getElementById("gw_scope_groups_count");
  if (groupsCount) { groupsCount.textContent = groupsChecked === groupsTotal ? "todos" : `${groupsChecked} selecionados`; }
}

function getSelectedScopeValues(className) {
  const all = document.querySelectorAll(`.${className}`);
  const checks = document.querySelectorAll(`.${className}:checked`);
  if (checks.length === all.length && all.length > 0) return "*";
  if (checks.length === 0) return "";
  return Array.from(checks).map(c => c.value).join(",");
}

async function saveCaptureScope() {
  const users = getSelectedScopeValues("gw-scope-user");
  const groups = getSelectedScopeValues("gw-scope-group");
  const fb = document.getElementById("gw_scope_feedback");
  const btn = document.getElementById("gw_scope_save_btn");

  if (btn) { btn.disabled = true; btn.textContent = "Salvando..."; }
  try {
    const result = await apiJson("/api/gateway/capture-scope", jsonRequest("POST", { users: users || "*", groups: groups || "*" }));
    _currentScope = result.data.capture_scope || result.data;
    if (fb) {
      fb.classList.remove("hidden");
      fb.className = "text-xs text-emerald-300 mt-2";
      fb.textContent = "Escopo salvo.";
    }
    _updateScopeCounts();
  } catch (err) {
    if (fb) {
      fb.classList.remove("hidden");
      fb.className = "text-xs text-rose-300 mt-2";
      fb.textContent = `Erro ao salvar: ${err.message}`;
    }
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = "Salvar"; }
    setTimeout(() => { if (fb) fb.classList.add("hidden"); }, 3000);
  }
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

  // Escopo de captura
  const scopeSection = document.getElementById("gw_capture_scope_section");
  if (scopeSection) {
    scopeSection.classList.toggle("hidden", !active);
    if (active) {
      _currentScope = state.capture_scope || { users: "*", groups: "*" };
      if (_systemUsers.length === 0) {
        loadCaptureScopeOptions();
      } else {
        renderScopeCheckboxes();
      }
    }
  }

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

// ── Monitor / Sessões ─────────────────────────────────────────────────────

function currentLogDir() {
  return (document.getElementById("monitor_log_dir")?.value || localStorage.getItem("gatewayMonitorLogDir") || "").trim();
}

async function resolveLogDir() {
  let logDir = currentLogDir();
  if (logDir) return logDir;
  try {
    const status = await apiJson("/api/gateway/status");
    if (status?.data?.capture_log_dir) {
      logDir = status.data.capture_log_dir;
      localStorage.setItem("gatewayMonitorLogDir", logDir);
      const input = document.getElementById("monitor_log_dir");
      if (input && !input.value) input.value = logDir;
    }
  } catch (_) { /* ignora */ }
  return logDir;
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
  renderEventsTable(data.events || []);
  // também popula a visão de cards (oculta por padrão)
  renderEventsCards(data.events || [], "#gw_events_cards_view");
}

// ── Toggle tabela / cards ──────────────────────────────────────────────────

const VIEW_PREF_KEY = "dakota_gateway_timeline_view";

function getViewPref() {
  return localStorage.getItem(VIEW_PREF_KEY) || "table";
}

function setViewPref(view) {
  localStorage.setItem(VIEW_PREF_KEY, view);
}

// Botões já com listener registrado (evita acúmulo de listeners em chamadas recursivas)
const _viewToggleWired = new WeakSet();

function applyViewToggle(tableContainerId, cardsContainerId, toggleBtnId, countId) {
  const view = getViewPref();
  const tableEl = document.getElementById(tableContainerId);
  const cardsEl = document.getElementById(cardsContainerId);
  const btn = document.getElementById(toggleBtnId);
  if (!tableEl || !cardsEl) return;
  if (view === "cards") {
    tableEl.classList.add("hidden");
    cardsEl.classList.remove("hidden");
    if (btn) btn.textContent = "📊 Tabela";
  } else {
    tableEl.classList.remove("hidden");
    cardsEl.classList.add("hidden");
    if (btn) btn.textContent = "🃏 Cards";
  }
  if (btn && !_viewToggleWired.has(btn)) {
    _viewToggleWired.add(btn);
    btn.addEventListener("click", () => {
      const current = getViewPref();
      const next = current === "cards" ? "table" : "cards";
      setViewPref(next);
      applyViewToggle(tableContainerId, cardsContainerId, toggleBtnId, countId);
    });
  }
}

async function loadGatewayMonitor() {
  const logDir = await resolveLogDir();
  if (!logDir) {
    text("#gw_monitor_status", "informe um log_dir para monitorar");
    return;
  }
  const result = await apiJson(`/api/gateway/monitor?log_dir=${encodeURIComponent(logDir)}&limit=40`);
  if (result?.data) renderGatewayMonitor(result.data);
}

function renderGatewaySessionDetail(data) {
  const events = data.events || [];
  if (!events.length) {
    html("#gw_session_detail", '<tr><td colspan="6" class="px-3 py-6 text-center text-stone-500">Nenhum evento nesta sessão.</td></tr>');
    html("#gw_session_detail_cards", '<div class="text-stone-400 text-sm p-3">Nenhum evento nesta sessão.</div>');
    text("#gw_session_detail_status", "0 eventos");
    return;
  }
  text("#gw_session_detail_status", `${events.length} eventos`);
  renderEventsTable(events, "#gw_session_detail", "#gw_session_detail_status");
  renderEventsCards(events, "#gw_detail_cards_view", "#gw_session_detail_status");
}

function uniqueSorted(items) {
  return Array.from(new Set((items || []).filter((item) => item !== null && item !== undefined && String(item).trim() !== ""))).sort((a, b) => String(a).localeCompare(String(b), "pt-BR"));
}

function fillSimpleSelect(selectId, values, allLabel) {
  const selectEl = document.getElementById(selectId);
  if (!selectEl) return;
  const current = selectEl.value || "";
  selectEl.innerHTML = "";

  const allOption = document.createElement("option");
  allOption.value = "";
  allOption.textContent = allLabel;
  selectEl.appendChild(allOption);

  uniqueSorted(values || []).forEach((value) => {
    const option = document.createElement("option");
    option.value = String(value);
    option.textContent = String(value);
    selectEl.appendChild(option);
  });
  selectEl.value = current;
}

// ── População de filtros de sessão ────────────────────────────────────────

function fillComboBoxOptions(outputSelectId, values, emptyLabel) {
  const selectEl = document.getElementById(outputSelectId);
  if (!selectEl) return;
  const current = selectEl.value || "";
  selectEl.innerHTML = "";
  const emptyOpt = document.createElement("option");
  emptyOpt.value = "";
  emptyOpt.textContent = emptyLabel;
  selectEl.appendChild(emptyOpt);
  uniqueSorted(values || []).forEach((value) => {
    const opt = document.createElement("option");
    opt.value = String(value);
    opt.textContent = String(value);
    selectEl.appendChild(opt);
  });
  selectEl.value = current;
  // Reinicializa o combobox com as novas opções
  const wrapper = selectEl.closest("[data-combobox]");
  if (wrapper) {
    initCombobox(wrapper);
  }
}

function updateSessionFilterCombos(data) {
  const sessions = data?.sessions || [];
  fillComboBoxOptions("gw_session_actor_output", sessions.map((item) => item.actor || ""), "todos os atores");
  fillComboBoxOptions("gw_session_logname_output", sessions.map((item) => item.logname || ""), "todos os lognames");
  fillComboBoxOptions("gw_session_id_filter_output", sessions.map((item) => item.session_id || ""), "todas as sessões");
  fillSimpleSelect("gw_session_uid_select", sessions.map((item) => item.uid), "todos os uid");
  fillSimpleSelect("gw_session_gid_select", sessions.map((item) => item.gid), "todos os gid");
  fillEventTypeSelect(
    "gw_session_event_type",
    sessions.flatMap((item) => (item.event_types || []).map((entry) => String(entry || "").trim()).filter(Boolean)),
  );
}

function fillEventTypeSelect(selectId, values) {
  const selectEl = document.getElementById(selectId);
  if (!selectEl) return;
  const current = selectEl.value || "";
  const defaultTypes = ["session_start", "session_end", "bytes", "checkpoint", "deterministic_input"];
  const extraTypes = uniqueSorted(values || []).filter((value) => !defaultTypes.includes(value));
  const allTypes = ["", ...defaultTypes, ...extraTypes];
  selectEl.innerHTML = "";
  allTypes.forEach((value) => {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = value ? value : "todos os tipos";
    selectEl.appendChild(option);
  });
  selectEl.value = current;
}

async function loadGatewaySessionDetail(sessionId) {
  if (!sessionId) return;
  window.currentGatewaySessionId = sessionId;
  const logDir = await resolveLogDir();
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
  updateSessionFilterCombos(data);
  text("#gw_sessions_status", data.error || `sessões ${formatCount((data.summary || {}).returned_sessions || 0)} • fonte ${data.log_dir || "-"}`);
  html(
    "#gw_sessions",
    (data.sessions || []).length
      ? (data.sessions || []).map((item) => gatewaySessionCard(item)).join("")
      : '<div class="text-sm text-stone-400">Nenhuma sessão encontrada.</div>',
  );
  document.querySelectorAll("[data-session]").forEach((button) => {
    button.addEventListener("click", () => {
      const sessionId = button.dataset.session;
      window.currentGatewaySessionId = sessionId;
      window.location.href = '/gateway/compliance?session_id=' + encodeURIComponent(sessionId);
    });
  });
}

async function loadGatewaySessions() {
  const logDir = await resolveLogDir();
  if (!logDir) {
    text("#gw_sessions_status", "informe um log_dir para monitorar");
    return;
  }
  const qs = buildQuery({
    log_dir: logDir,
    limit: "60",
    actor: document.getElementById("gw_session_actor_output")?.value || "",
    logname: document.getElementById("gw_session_logname_output")?.value || "",
    session_id: document.getElementById("gw_session_id_filter_output")?.value || "",
    event_type: document.getElementById("gw_session_event_type")?.value || "",
    uid: document.getElementById("gw_session_uid_select")?.value || "",
    gid: document.getElementById("gw_session_gid_select")?.value || "",
    ts_from: document.getElementById("gw_session_ts_from")?.value || "",
    ts_to: document.getElementById("gw_session_ts_to")?.value || "",
    q: document.getElementById("gw_session_q")?.value || "",
  });
  const result = await apiJson(`/api/gateway/sessions?${qs}`);
  if (result?.data) renderGatewaySessions(result.data);
}

// ── WebSocket — status do serviço em tempo real ────────────────────────────

let _wsGateway = null;
let _wsLastUpdate = null;

function renderServiceStatus(data) {
  const yes = (v) => v ? '<span class="text-emerald-300">sim</span>' : '<span class="text-rose-300">não</span>';
  const run = (v) => v ? '<span class="text-emerald-300">rodando</span>' : '<span class="text-rose-300">parado</span>';
  const svc = data.service || "?";
  const ss = data.service_running ? "running" : (data.socket_running ? "socket" : "dead");
  html("#gw_svc_sshd", `${run(data.running)} <span class="text-xs text-stone-500">(${svc} ${ss})</span>`);
  html("#gw_svc_wrapper", data.capture_installed ? yes(true) : '<span class="text-rose-300">não instalado</span>');
  const cfgs = data.capture_configs || [];
  html("#gw_svc_ssh_int", cfgs.length
    ? `<span class="text-emerald-300">sim</span> <span class="text-xs text-stone-500">(${cfgs.join(", ")})</span>`
    : '<span class="text-rose-300">não configurado</span>');
  html("#gw_svc_active", data.capture_active
    ? '<span class="text-emerald-300">pronta</span>'
    : '<span class="text-amber-300">incompleta</span>');
  const errEl = document.getElementById("gw_svc_error");
  if (data.error) {
    errEl.classList.remove("hidden");
    errEl.textContent = data.error;
  } else {
    errEl.classList.add("hidden");
  }
  _wsLastUpdate = Date.now();
  updateWsAge();
}

function updateWsAge() {
  const el = document.getElementById("gw_ws_age");
  if (!el || !_wsLastUpdate) return;
  const s = Math.round((Date.now() - _wsLastUpdate) / 1000);
  el.textContent = s < 60 ? `atualizado há ${s}s` : `atualizado há ${Math.floor(s/60)}min`;
}

function connectGatewayWs() {
  if (_wsGateway) return;
  _wsGateway = connectWs("/ws/gateway-status", {
    onMessage: (data) => renderServiceStatus(data),
    onOpen: () => {
      const ind = document.getElementById("gw_ws_indicator");
      if (ind) { ind.className = "ml-2 inline-block w-2 h-2 rounded-full bg-emerald-400"; ind.title = "websocket conectado"; }
    },
    onClose: () => {
      const ind = document.getElementById("gw_ws_indicator");
      if (ind) { ind.className = "ml-2 inline-block w-2 h-2 rounded-full bg-rose-400"; ind.title = "websocket desconectado"; }
    },
  });
}

// Atualiza o age a cada 10s
setInterval(updateWsAge, 10000);

// ── Init ───────────────────────────────────────────────────────────────────

window.addEventListener("DOMContentLoaded", () => {
  activatePageSections("gateway", "status");
  const saved = localStorage.getItem("gatewayMonitorLogDir") || "";
  if (saved) { const el = document.getElementById("monitor_log_dir"); if (el) el.value = saved; }

  loadGatewayState();
  loadAdvancedSelections();
  loadGatewayMonitor();
  loadGatewaySessions();
  connectGatewayWs();

  // Auto-load session detail from URL param
  const urlParams = new URLSearchParams(window.location.search);
  const sidParam = urlParams.get('session_id');
  if (sidParam) {
    activatePageSections("gateway", "compliance");
    window.currentGatewaySessionId = sidParam;
    // Wait for logDir to be available
    setTimeout(() => loadGatewaySessionDetail(sidParam), 500);
  }

  document.getElementById("gateway_activate_btn")?.addEventListener("click", activateGateway);
  document.getElementById("gateway_deactivate_btn")?.addEventListener("click", () => deactivateGateway(false));
  document.getElementById("load_gateway_monitor_btn")?.addEventListener("click", loadGatewayMonitor);
  document.getElementById("load_gateway_sessions_btn")?.addEventListener("click", loadGatewaySessions);
  document.getElementById("gw_scope_save_btn")?.addEventListener("click", saveCaptureScope);

  ["gw_session_event_type", "gw_session_actor_output", "gw_session_logname_output", "gw_session_id_filter_output", "gw_session_uid_select", "gw_session_gid_select"].forEach((id) => {
    document.getElementById(id)?.addEventListener("change", loadGatewaySessions);
  });
  // Campos de texto livre: input event para filtro em tempo real
  ["gw_session_ts_from", "gw_session_ts_to", "gw_session_q"].forEach((id) => {
    document.getElementById(id)?.addEventListener("input", loadGatewaySessions);
  });
  ["gw_detail_seq_from", "gw_detail_seq_to", "gw_detail_ts_from", "gw_detail_ts_to", "gw_detail_limit"].forEach((id) =>
    document.getElementById(id)?.addEventListener("input", () => loadGatewaySessionDetail(window.currentGatewaySessionId)),
  );

  // Inicializa toggles de visualização tabela/cards
  applyViewToggle("gw_events_table_view", "gw_events_cards_view", "gw_events_view_toggle", "gw_events_count");
  applyViewToggle("gw_detail_table_view", "gw_detail_cards_view", "gw_detail_view_toggle", "gw_session_detail_status");
});

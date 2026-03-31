import { apiJson, jsonRequest } from "../core/api.js";
import { escapeHtml, formatCount, html, text } from "../core/dom.js";
import { activatePageSections } from "../components/page_sections.js";

// ── Helpers ────────────────────────────────────────────────────────────────

function fmt(ms) {
  if (!ms) return "-";
  try { return new Date(ms).toLocaleString("pt-BR"); } catch (_) { return String(ms); }
}

function statusBadge(status) {
  const map = {
    active: "text-emerald-300 font-semibold",
    finished: "text-stone-400",
    interrupted: "text-amber-300",
    failed: "text-rose-300 font-semibold",
  };
  return `<span class="${map[status] || "text-stone-400"}">${status || "-"}</span>`;
}

function pickBestReplaySession(sessions, capture) {
  const items = Array.isArray(sessions) ? sessions : [];
  const captureSessionId = String(capture?.session_uuid || "").trim();
  if (!items.length) return null;

  const scored = items.map((session, idx) => {
    const sid = String(session?.session_id || "").trim();
    const actor = String(session?.actor || "").trim().toLowerCase();
    const bytesIn = Number(session?.bytes_in || 0);
    const bytesOut = Number(session?.bytes_out || 0);
    const detCount = Number(session?.deterministic_input_count || 0);
    const eventCount = Number(session?.event_count || 0);
    const isCaptureEnvelope = Boolean(captureSessionId) && sid === captureSessionId;
    const isGatewayActor = actor === "gateway";
    const score =
      (detCount > 0 ? 1000 : 0) +
      ((bytesIn + bytesOut) > 0 ? 400 : 0) +
      (bytesIn > 0 ? 200 : 0) +
      (eventCount > 0 ? 50 : 0) +
      (!isCaptureEnvelope ? 25 : 0) +
      (!isGatewayActor ? 10 : 0) -
      idx;
    return { session, score, isCaptureEnvelope, isGatewayActor };
  });

  scored.sort((a, b) => b.score - a.score);
  return scored[0]?.session || items[0] || null;
}

function buildReplayViewHref(captureId, sessionId) {
  const params = new URLSearchParams({
    capture_id: String(captureId || ""),
    session_id: String(sessionId || ""),
  });
  return `/captures/${captureId}/replay?${params.toString()}`;
}

function renderCaptureSessionCard(captureId, session, preferredSessionId) {
  const sessionId = String(session?.session_id || "").trim();
  const actor = String(session?.actor || "-").trim() || "-";
  const isPreferred = preferredSessionId && sessionId === preferredSessionId;
  const bytesIn = Number(session?.bytes_in || 0);
  const bytesOut = Number(session?.bytes_out || 0);
  const detCount = Number(session?.deterministic_input_count || 0);
  const eventCount = Number(session?.event_count || 0);
  const status = String(session?.status || "open");
  const isInteractive = bytesIn > 0 || detCount > 0;
  const chips = [];
  if (isPreferred) chips.push('<span class="rounded-full bg-sky-900/40 px-2 py-1 text-[10px] uppercase tracking-[0.18em] text-sky-200">View padrão</span>');
  if (isInteractive) chips.push('<span class="rounded-full bg-emerald-900/40 px-2 py-1 text-[10px] uppercase tracking-[0.18em] text-emerald-200">Interativa</span>');
  if (actor.toLowerCase() === "gateway") chips.push('<span class="rounded-full bg-stone-800 px-2 py-1 text-[10px] uppercase tracking-[0.18em] text-stone-300">Técnica</span>');

  return `
    <div class="r2ctl-detail-surface rounded-2xl px-4 py-3">
      <div class="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
        <div class="min-w-0 flex-1">
          <div class="mb-2 flex flex-wrap items-center gap-2">
            <span class="text-sm font-semibold text-stone-100">${escapeHtml(actor)}</span>
            <span class="text-xs text-stone-400 font-mono break-all">${escapeHtml(sessionId)}</span>
            ${chips.join("")}
          </div>
          <div class="grid gap-2 text-xs text-stone-300 sm:grid-cols-2 xl:grid-cols-5">
            <span>status: <span class="text-stone-100">${escapeHtml(status)}</span></span>
            <span>eventos: <span class="text-stone-100">${formatCount(eventCount)}</span></span>
            <span>entrada: <span class="text-stone-100">${formatCount(bytesIn)} bytes</span></span>
            <span>saída: <span class="text-stone-100">${formatCount(bytesOut)} bytes</span></span>
            <span>det: <span class="text-stone-100">${formatCount(detCount)}</span></span>
          </div>
        </div>
        <div class="flex shrink-0 items-center gap-2">
          <a href="${buildReplayViewHref(captureId, sessionId)}" class="r2ctl-btn-soft text-xs">View</a>
          <a href="/runs/new?${new URLSearchParams({
            log_dir: String(window._currentCaptureLogDir || ""),
            source_capture: String(captureId || ""),
            replay_session_id: sessionId,
            input_mode: detCount > 0 ? "deterministic" : "raw",
            on_deterministic_mismatch: "fail-fast",
          }).toString()}" class="r2ctl-btn-soft text-xs">runs/new</a>
        </div>
      </div>
    </div>
  `;
}

// ── Renderizar lista ───────────────────────────────────────────────────────

function renderCaptureCard(cap) {
  const env = cap.environment || {};
  const envName = env.env_name || env.hostname || "-";
  return `
    <div class="r2ctl-detail-surface rounded-2xl px-4 py-3 flex flex-col sm:flex-row sm:items-center gap-3 justify-between">
      <div class="flex-1 min-w-0">
        <div class="flex items-center gap-2 mb-1">
          ${statusBadge(cap.status)}
          <span class="text-xs text-stone-400">#${cap.id} · ${fmt(cap.started_at_ms)}</span>
        </div>
        <div class="text-xs text-stone-400 space-x-2">
          <span>por <span class="text-stone-300">${cap.created_by_username || "-"}</span></span>
          <span>·</span>
          <span>env: <span class="text-stone-300 font-mono">${envName}</span></span>
          ${cap.connection_profile_name ? `<span>·</span><span>perfil: <span class="text-stone-300">${cap.connection_profile_name}</span></span>` : ""}
          ${cap.ended_at_ms ? `<span>·</span><span>encerrou ${fmt(cap.ended_at_ms)}</span>` : ""}
        </div>
      </div>
      <a href="/captures/${cap.id}" class="r2ctl-btn-soft text-xs shrink-0">Ver detalhe</a>
    </div>`;
}

async function loadCaptures() {
  const result = await apiJson("/api/captures?limit=60");
  if (!result) return;
  const items = (result.data?.captures || []);
  html(
    "#captures_list_content",
    items.length
      ? items.map(renderCaptureCard).join("")
      : '<div class="text-sm text-stone-400">Nenhuma captura registrada ainda.</div>',
  );
}

// ── Estado do gateway para nova captura ───────────────────────────────────

let _gatewayActive = false;

async function loadGatewayState() {
  const result = await apiJson("/api/gateway/state");
  if (!result?.data) return;
  const state = result.data;
  const env = state.environment || {};
  _gatewayActive = Boolean(state.active);

  text("#cap_env_name", env.env_name || env.hostname || "não identificado");
  text("#cap_env_hostname", env.hostname || env.fqdn || "-");
  const gwEl = document.getElementById("cap_gw_status");
  if (gwEl) {
    gwEl.textContent = _gatewayActive ? "ATIVO" : "INATIVO";
    gwEl.className = `text-sm font-semibold ${_gatewayActive ? "text-emerald-300" : "text-amber-300"}`;
  }

  const startBtn = document.getElementById("cap_start_btn");
  const inactiveMsg = document.getElementById("cap_gw_inactive_msg");
  const alertBanner = document.getElementById("captures_gateway_alert");

  if (startBtn) startBtn.disabled = !_gatewayActive;
  if (inactiveMsg) inactiveMsg.classList.toggle("hidden", _gatewayActive);
  if (alertBanner) alertBanner.classList.toggle("hidden", _gatewayActive);
}

// ── Seleções avancadas ─────────────────────────────────────────────────────

async function loadSelections() {
  const [profilesRes, usersRes, envsRes] = await Promise.all([
    apiJson("/api/connection-profiles"),
    apiJson("/api/users"),
    apiJson("/api/targets"),
  ]);
  const profileSel = document.getElementById("cap_profile_select");
  if (profileSel && (profilesRes?.data?.connection_profiles || profilesRes?.data?.profiles)) {
    (profilesRes.data.connection_profiles || profilesRes.data.profiles).forEach((p) => {
      const opt = document.createElement("option"); opt.value = p.id; opt.textContent = p.name || p.profile_id; profileSel.appendChild(opt);
    });
  }
  const opUserSel = document.getElementById("cap_opuser_select");
  if (opUserSel && usersRes?.data?.users) {
    usersRes.data.users.filter((u) => ["operator", "admin"].includes(u.role)).forEach((u) => {
      const opt = document.createElement("option"); opt.value = u.id; opt.textContent = u.username; opUserSel.appendChild(opt);
    });
  }
  const envSel = document.getElementById("cap_target_env_select");
  if (envSel && (envsRes?.data?.targets || envsRes?.data?.environments)) {
    (envsRes.data.targets || envsRes.data.environments).forEach((e) => {
      const opt = document.createElement("option"); opt.value = e.id; opt.textContent = e.name || e.env_id; envSel.appendChild(opt);
    });
  }
}

// ── Iniciar captura ────────────────────────────────────────────────────────

async function startCapture() {
  if (!_gatewayActive) {
    text("#cap_start_feedback", "Ative o gateway para iniciar captura.", "text-amber-300");
    return;
  }
  const btn = document.getElementById("cap_start_btn");
  if (btn) { btn.disabled = true; btn.textContent = "Iniciando..."; }
  const profileId = document.getElementById("cap_profile_select")?.value || "";
  const opUserId = document.getElementById("cap_opuser_select")?.value || "";
  const targetEnvId = document.getElementById("cap_target_env_select")?.value || "";
  const notes = document.getElementById("cap_notes")?.value || "";
  const body = {
    connection_profile_id: profileId ? parseInt(profileId, 10) : null,
    operational_user_id: opUserId ? parseInt(opUserId, 10) : null,
    target_env_id: targetEnvId ? parseInt(targetEnvId, 10) : null,
    notes: notes || null,
  };
  const result = await apiJson("/api/captures/start", jsonRequest("POST", body));
  if (btn) { btn.disabled = !_gatewayActive; btn.textContent = "Iniciar Captura"; }
  if (!result) return;
  if (result.ok) {
    const capture = result.data;
    const feedbackEl = document.getElementById("cap_start_feedback");
    if (feedbackEl) { feedbackEl.className = "text-sm text-emerald-300 mb-3"; feedbackEl.textContent = `Captura #${capture.id} iniciada.`; }
    setTimeout(() => { window.location.href = `/captures/${capture.id}`; }, 600);
  } else {
    const feedbackEl = document.getElementById("cap_start_feedback");
    if (feedbackEl) { feedbackEl.className = "text-sm text-rose-300 mb-3"; feedbackEl.textContent = result.data?.error || "Falha ao iniciar captura."; }
  }
}

// ── Detalhe de captura ─────────────────────────────────────────────────────

async function loadCaptureDetail(captureId) {
  const result = await apiJson(`/api/captures/${captureId}`);
  if (!result?.data || result.status === 404) {
    html("#captures-detail", '<div class="text-sm text-rose-300">Captura não encontrada.</div>');
    return;
  }
  const cap = result.data;
  const env = cap.environment || {};

  // Mostrar secao de detalhe
  const detailSection = document.getElementById("captures-detail");
  if (detailSection) detailSection.classList.remove("hidden");

  text("#cap_detail_id", `sessão #${cap.id} · uuid: ${cap.session_uuid || "-"}`);
  const statusEl = document.getElementById("cap_detail_status");
  if (statusEl) {
    const colors = { active: "text-emerald-300", finished: "text-stone-300", interrupted: "text-amber-300", failed: "text-rose-300" };
    statusEl.textContent = cap.status || "-";
    statusEl.className = `mt-2 text-lg font-semibold ${colors[cap.status] || "text-stone-50"}`;
  }
  text("#cap_detail_by", cap.created_by_username || "-");
  text("#cap_detail_started", fmt(cap.started_at_ms));
  text("#cap_detail_ended", cap.ended_at_ms ? fmt(cap.ended_at_ms) : "em andamento");
  text("#cap_detail_env", env.env_name || env.hostname || "-");
  text("#cap_detail_profile", cap.connection_profile_name || "nenhum");
  text("#cap_detail_logdir", cap.log_dir || "-");
  window._currentCaptureLogDir = cap.log_dir || "";

  // Botao de encerrar
  const stopBtn = document.getElementById("cap_stop_btn");
  if (stopBtn) {
    stopBtn.classList.toggle("hidden", cap.status !== "active");
    stopBtn.onclick = () => stopCapture(captureId);
  }

  // Links de View e Replay
  const replayPanel = document.getElementById("cap_detail_replay_panel");
  const replayCopy = document.getElementById("cap_detail_replay_copy");
  const viewLink = document.getElementById("cap_detail_view_link");
  const replayLink = document.getElementById("cap_detail_replay_link");
  if (replayPanel && replayCopy && viewLink && replayLink) {
    replayPanel.classList.add("hidden");
    viewLink.classList.add("hidden");
    replayLink.classList.add("hidden");

    const sessionsResult = await apiJson(`/api/captures/${captureId}/sessions?limit=10`);
    const sessions = sessionsResult?.data?.sessions || [];
    const latestSession = pickBestReplaySession(sessions, cap);
    const latestSessionId = String(latestSession?.session_id || "").trim();

    if (latestSessionId) {
      const viewParams = new URLSearchParams({
        capture_id: String(cap.id || captureId),
        session_id: latestSessionId,
      });
      viewLink.href = `/captures/${captureId}/replay?${viewParams.toString()}`;
      viewLink.classList.remove("hidden");
    }

    if (cap.status === "finished") {
      replayCopy.textContent = latestSessionId
        ? "Visualize a sessão capturada ou crie um replay operacional."
        : "Criar replay a partir desta captura.";

      const params = new URLSearchParams({
        log_dir: cap.log_dir || "",
        source_capture: String(cap.id || ""),
        replay_session_id: latestSessionId,
        input_mode: latestSession?.deterministic_input_count ? "deterministic" : "raw",
        on_deterministic_mismatch: "fail-fast",
      });
      if (cap.target_env_id) params.set("target_env_id", String(cap.target_env_id));
      if (cap.connection_profile_id) params.set("connection_profile_id", String(cap.connection_profile_id));
      replayLink.href = `/runs/new?${params.toString()}`;
      replayLink.classList.remove("hidden");
    } else if (latestSessionId) {
      replayCopy.textContent = "Visualize a sessão capturada desta execução.";
    }

    if (!viewLink.classList.contains("hidden") || !replayLink.classList.contains("hidden")) {
      replayPanel.classList.remove("hidden");
    }

    renderCaptureSessions(captureId, cap, sessions, latestSessionId);
  }

  window._currentCaptureId = captureId;
}

function renderCaptureSessions(captureId, capture, sessions, preferredSessionId) {
  const listEl = document.getElementById("cap_sessions_list");
  const summaryEl = document.getElementById("cap_sessions_summary");
  if (!listEl || !summaryEl) return;

  const items = Array.isArray(sessions) ? sessions : [];
  const interactiveCount = items.filter((session) => Number(session?.bytes_in || 0) > 0 || Number(session?.deterministic_input_count || 0) > 0).length;
  summaryEl.textContent = `${formatCount(items.length)} sessão(ões) • ${formatCount(interactiveCount)} interativa(s)`;

  if (!items.length) {
    listEl.innerHTML = '<div class="r2ctl-detail-surface rounded-2xl px-4 py-3 text-sm text-stone-400">Nenhuma sessão encontrada nesta captura.</div>';
    return;
  }

  listEl.innerHTML = items
    .map((session) => renderCaptureSessionCard(captureId, session, preferredSessionId || ""))
    .join("");
}

async function loadCaptureEvents(captureId) {
  const result = await apiJson(`/api/captures/${captureId}/events?limit=300`);
  if (!result?.data) return;
  html("#cap_events_content", JSON.stringify(result.data, null, 2));
}

async function stopCapture(captureId) {
  const btn = document.getElementById("cap_stop_btn");
  if (btn) { btn.disabled = true; btn.textContent = "Encerrando..."; }
  const result = await apiJson(`/api/captures/${captureId}/stop`, jsonRequest("POST", {}));
  if (btn) { btn.disabled = false; btn.textContent = "Encerrar Captura"; }
  if (result?.ok) {
    await loadCaptureDetail(captureId);
  }
}

// ── Init ───────────────────────────────────────────────────────────────────

window.addEventListener("DOMContentLoaded", async () => {
  // Detectar rota: /captures e /captures/:id ("new" redireciona para lista lógica)
  const segments = window.location.pathname.split("/").filter(Boolean);
  const captureSegment = segments[1] || "";
  const captureId = /^\d+$/.test(captureSegment) ? parseInt(captureSegment, 10) : null;

  if (captureId) {
    // Pagina de detalhe
    activatePageSections("captures", "detail");
    await loadCaptureDetail(captureId);
    document.getElementById("cap_load_events_btn")?.addEventListener("click", () => loadCaptureEvents(captureId));
  } else {
    activatePageSections("captures", "list");
    await loadCaptures();
    await loadGatewayState();
    document.getElementById("reload_captures_btn")?.addEventListener("click", loadCaptures);
    document.getElementById("cap_start_btn")?.addEventListener("click", startCapture);
  }
});

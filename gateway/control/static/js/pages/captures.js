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

function normalizeNumber(value, fallback, min, max) {
  const num = Number.parseInt(String(value || ""), 10);
  if (!Number.isFinite(num)) return fallback;
  return Math.max(min, Math.min(num, max));
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
          <div class="mt-1 text-xs text-stone-400">
            <span>início: <span class="text-stone-300">${fmt(session.started_at_ms)}</span></span>
            ${session.ended_at_ms ? `<span class="ml-3">fim: <span class="text-stone-300">${fmt(session.ended_at_ms)}</span></span>` : ""}
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
  const sessionCount = cap.session_count ?? "-";
  const eventCount = cap.event_count ?? "-";
  const reason = cap.stop_reason || cap.interrupted_reason || cap.notes || "";
  return `
    <div class="r2ctl-detail-surface rounded-2xl px-4 py-3 flex flex-col sm:flex-row sm:items-center gap-3 justify-between">
      <div class="flex-1 min-w-0">
        <div class="flex items-center gap-2 mb-1">
          ${statusBadge(cap.status)}
          <span class="text-xs text-stone-400">#${cap.id} · ${fmt(cap.started_at_ms)}</span>
        </div>
        <div class="text-xs text-stone-400 space-x-2">
          <span>criada por <span class="text-stone-300">${escapeHtml(cap.created_by_username || "-")}</span></span>
          <span>·</span>
          <span>env: <span class="text-stone-300 font-mono">${escapeHtml(envName)}</span></span>
          ${cap.connection_profile_name ? `<span>·</span><span>perfil: <span class="text-stone-300">${escapeHtml(cap.connection_profile_name)}</span></span>` : ""}
          <span>·</span>
          <span>sessões: <span class="text-stone-300">${sessionCount}</span></span>
          ${eventCount !== "-" ? `<span>·</span><span>eventos: <span class="text-stone-300">${eventCount}</span></span>` : ""}
          ${cap.ended_at_ms ? `<span>·</span><span>encerrou ${fmt(cap.ended_at_ms)}</span>` : ""}
        </div>
        ${reason ? `<div class="text-xs text-amber-300/80 italic mt-1">Motivo: ${escapeHtml(reason)}</div>` : ""}
      </div>
      <a href="/captures/${cap.id}" class="r2ctl-btn-soft text-xs shrink-0">Ver detalhe</a>
    </div>`;
}

// ── Paginação ──────────────────────────────────────────────────────────────

let _capturesPage = 0;
let _capturesTotal = 0;

function capturesPageSize() {
  return parseInt(document.getElementById("captures_page_size")?.value || "20", 10);
}

function capturesSearch() {
  return document.getElementById("captures_search")?.value?.trim() || "";
}

function goToPage(page) {
  if (page < 0) page = 0;
  const totalPages = Math.max(1, Math.ceil(_capturesTotal / capturesPageSize()));
  if (page >= totalPages) page = totalPages - 1;
  _capturesPage = page;
  loadCaptures();
}

async function loadCaptures() {
  const limit = capturesPageSize();
  const offset = _capturesPage * limit;
  const search = capturesSearch();
  const params = new URLSearchParams({ limit: String(limit), offset: String(offset) });
  if (search) params.set("search", search);

  const author = (document.getElementById("captures_author")?.value || "").trim();
  const tsFrom = document.getElementById("captures_ts_from")?.value || "";
  const tsTo = document.getElementById("captures_ts_to")?.value || "";
  const status = document.getElementById("captures_status")?.value || "";
  if (author) params.set("created_by", author);
  // Converte data YYYY-MM-DD para ms (início/fim do dia UTC)
  if (tsFrom) params.set("ts_from", String(new Date(tsFrom + "T00:00:00Z").getTime()));
  if (tsTo) params.set("ts_to", String(new Date(tsTo + "T23:59:59.999Z").getTime()));
  if (status) params.set("status", status);

  const result = await apiJson(`/api/captures?${params.toString()}`);
  if (!result) return;
  const items = (result.data?.captures || []);
  _capturesTotal = result.data?.total || items.length;

  html(
    "#captures_list_content",
    items.length
      ? items.map(renderCaptureCard).join("")
      : '<div class="text-sm text-stone-400">Nenhuma captura ' + (search ? 'para "' + escapeHtml(search) + '"' : 'registrada ainda.') + '</div>',
  );

  // Atualiza controles de paginação
  const totalPages = Math.max(1, Math.ceil(_capturesTotal / capturesPageSize()));
  const pagDiv = document.getElementById("captures_pagination");
  if (pagDiv) pagDiv.classList.toggle("hidden", _capturesTotal <= capturesPageSize());

  const info = document.getElementById("captures_page_info");
  if (info) {
    const from = _capturesTotal > 0 ? offset + 1 : 0;
    const to = Math.min(offset + items.length, _capturesTotal);
    info.textContent = `${from}-${to} de ${_capturesTotal}`;
  }

  document.getElementById("captures_page_first")?.classList.toggle("opacity-40", _capturesPage === 0);
  document.getElementById("captures_page_prev")?.classList.toggle("opacity-40", _capturesPage === 0);
  document.getElementById("captures_page_next")?.classList.toggle("opacity-40", _capturesPage >= totalPages - 1);
  document.getElementById("captures_page_last")?.classList.toggle("opacity-40", _capturesPage >= totalPages - 1);

  // Banner de info
  const infoBanner = document.getElementById("captures_gateway_info");
  if (infoBanner) {
    infoBanner.classList.toggle("hidden", items.length > 0 || !_gatewayActive);
  }
}

// ── Estado do gateway (banners da lista de capturas) ───────────────────────

let _gatewayActive = false;

async function loadGatewayState() {
  const result = await apiJson("/api/gateway/state");
  if (!result?.data) return;
  _gatewayActive = Boolean(result.data.active);

  const alertBanner = document.getElementById("captures_gateway_alert");
  if (alertBanner) alertBanner.classList.toggle("hidden", _gatewayActive);
  // Info banner controlado pelo loadCaptures (mostra só se não houver capturas)
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
  setupSynthesisPanel(captureId, cap);

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

function setupSynthesisPanel(captureId, capture) {
  const panel = document.getElementById("cap_synthesis_panel");
  if (!panel) return;
  const isFinished = String(capture?.status || "") === "finished";
  panel.classList.toggle("hidden", !isFinished);
  if (!isFinished) return;

  const sourceInput = document.getElementById("cap_synth_source_dir");
  const samplesInput = document.getElementById("cap_synth_samples");
  const seedInput = document.getElementById("cap_synth_seed");
  if (sourceInput && !sourceInput.value) sourceInput.value = localStorage.getItem("replay2.captureSynth.sourceDir") || "";
  if (samplesInput && !samplesInput.value) samplesInput.value = localStorage.getItem("replay2.captureSynth.samples") || "10";
  if (seedInput && !seedInput.value) seedInput.value = localStorage.getItem("replay2.captureSynth.seed") || "42";

  const btn = document.getElementById("cap_synthesize_btn");
  if (btn) btn.onclick = () => synthesizeCapture(captureId);
}

async function synthesizeCapture(captureId) {
  const btn = document.getElementById("cap_synthesize_btn");
  const sourceInput = document.getElementById("cap_synth_source_dir");
  const samplesInput = document.getElementById("cap_synth_samples");
  const seedInput = document.getElementById("cap_synth_seed");
  const feedback = document.getElementById("cap_synthesis_feedback");
  const resultEl = document.getElementById("cap_synthesis_result");

  const sourceDir = String(sourceInput?.value || "").trim();
  const samples = normalizeNumber(samplesInput?.value, 10, 1, 10000);
  const seed = normalizeNumber(seedInput?.value, 42, -2147483648, 2147483647);

  if (!sourceDir) {
    if (feedback) {
      feedback.className = "mt-3 text-sm text-amber-300";
      feedback.textContent = "Informe o source_dir.";
    }
    return;
  }

  localStorage.setItem("replay2.captureSynth.sourceDir", sourceDir);
  localStorage.setItem("replay2.captureSynth.samples", String(samples));
  localStorage.setItem("replay2.captureSynth.seed", String(seed));

  if (btn) { btn.disabled = true; btn.textContent = "Gerando..."; }
  if (feedback) {
    feedback.className = "mt-3 text-sm text-stone-300";
    feedback.textContent = "Gerando template, dataset e sessões sintéticas...";
  }
  if (resultEl) {
    resultEl.classList.add("hidden");
    resultEl.innerHTML = "";
  }

  const response = await apiJson(`/api/captures/${captureId}/synthesize`, jsonRequest("POST", {
    source_dir: sourceDir,
    samples,
    seed,
    name: `capture_${captureId}_seed_${seed}`,
    validate: true,
  }));

  if (btn) { btn.disabled = false; btn.textContent = "Gerar"; }

  if (!response?.ok) {
    if (feedback) {
      feedback.className = "mt-3 text-sm text-rose-300";
      feedback.textContent = response?.data?.error || "Falha ao gerar dados sintéticos.";
    }
    return;
  }

  const data = response.data || {};
  const artifacts = data.artifacts || {};
  const validation = data.validation || {};
  if (feedback) {
    feedback.className = "mt-3 text-sm text-emerald-300";
    feedback.textContent = `${formatCount(data.generated_sessions || 0)} sessão(ões) gerada(s).`;
  }
  if (resultEl) {
    resultEl.classList.remove("hidden");
    resultEl.innerHTML = `
      <div class="grid gap-2 text-xs md:grid-cols-2">
        <span>jornada: <span class="font-mono text-emerald-50">${escapeHtml(data.journey_id || "-")}</span></span>
        <span>validas: <span class="font-mono text-emerald-50">${formatCount(validation.valid_sessions || 0)}/${formatCount(validation.total_sessions || data.generated_sessions || 0)}</span></span>
        <span class="md:col-span-2">template: <span class="font-mono text-emerald-50 break-all">${escapeHtml(artifacts.template || "-")}</span></span>
        <span class="md:col-span-2">dataset: <span class="font-mono text-emerald-50 break-all">${escapeHtml(artifacts.dataset || "-")}</span></span>
        <span class="md:col-span-2">sessões: <span class="font-mono text-emerald-50 break-all">${escapeHtml(artifacts.sessions_dir || "-")}</span></span>
        <span class="md:col-span-2">relatório: <span class="font-mono text-emerald-50 break-all">${escapeHtml(artifacts.report || "-")}</span></span>
      </div>
    `;
  }
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
  text("#cap_events_content", JSON.stringify(result.data, null, 2));
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
    // Paginação
    document.getElementById("captures_page_first")?.addEventListener("click", () => goToPage(0));
    document.getElementById("captures_page_prev")?.addEventListener("click", () => goToPage(_capturesPage - 1));
    document.getElementById("captures_page_next")?.addEventListener("click", () => goToPage(_capturesPage + 1));
    document.getElementById("captures_page_last")?.addEventListener("click", () => goToPage(Math.ceil(_capturesTotal / capturesPageSize()) - 1));
    document.getElementById("captures_page_size")?.addEventListener("change", () => { _capturesPage = 0; loadCaptures(); });
    // Filtro com debounce para campos de texto
    let searchTimer = null;
    const debouncedReload = () => {
      clearTimeout(searchTimer);
      searchTimer = setTimeout(() => { _capturesPage = 0; loadCaptures(); }, 300);
    };
    document.getElementById("captures_search")?.addEventListener("input", debouncedReload);
    document.getElementById("captures_author")?.addEventListener("input", debouncedReload);
    // Filtros sem debounce (select e number)
    document.getElementById("captures_status")?.addEventListener("change", () => { _capturesPage = 0; loadCaptures(); });
    document.getElementById("captures_ts_from")?.addEventListener("change", () => { _capturesPage = 0; loadCaptures(); });
    document.getElementById("captures_ts_to")?.addEventListener("change", () => { _capturesPage = 0; loadCaptures(); });
  }
});

import { apiJson, jsonRequest } from "../core/api.js";
import { escapeHtml, html } from "../core/dom.js";
import { comparisonSummaryCard, exportLinks, failureTypeList, reprocessFailureCard, runIdentityCard } from "../components/detail_views.js";
import { failureHasScreenDiff, renderFailureInlinePlayerHtml } from "../components/failure_diff.js";
import { runSyntheticOrigin, runSyntheticSubstitutions } from "../components/run_views.js";
import { renderRunDeparaHtml } from "../components/synthetic_depara.js";

let currentFailures = [];
let currentRun = null;
// Falhas navegáveis do player inline, em ordem cronológica (seq crescente) —
// o usuário acompanha onde a divergência começou e como evoluiu.
let playerFailures = [];
let playerIdx = 0;
let playerTimer = null;
// Pares original → sintético da run (marcação âmbar das trocas no player).
let currentSubstitutions = [];

const PLAYER_INTERVAL_MS = 1500;

function stopFailurePlayer() {
  if (playerTimer) {
    clearInterval(playerTimer);
    playerTimer = null;
  }
  const button = document.getElementById("fp_play");
  if (button) button.textContent = "▶ Reproduzir";
}

function renderPlayerFrame() {
  const failure = playerFailures[playerIdx];
  if (!failure) return;
  html("#failure_player", renderFailureInlinePlayerHtml(failure, playerIdx + 1, playerFailures.length, currentSubstitutions, currentRun?.id));
}
function selectFailurePlayer(idx, { scroll = false } = {}) {
  if (idx < 0 || idx >= playerFailures.length) return;
  stopFailurePlayer();
  playerIdx = idx;
  renderPlayerFrame();
  const container = document.getElementById("failure_player");
  container?.classList.remove("hidden");
  if (scroll) container?.scrollIntoView({ behavior: "smooth", block: "start" });
}

function toggleFailurePlayer() {
  if (playerTimer) {
    stopFailurePlayer();
    return;
  }
  const button = document.getElementById("fp_play");
  if (button) button.textContent = "⏸ Pausar";
  playerTimer = setInterval(() => {
    if (playerIdx >= playerFailures.length - 1) {
      stopFailurePlayer();
      return;
    }
    playerIdx += 1;
    renderPlayerFrame();
  }, PLAYER_INTERVAL_MS);
}

function closeRunDeparaModal() {
  document.getElementById("run_depara_modal")?.classList.add("hidden");
}

async function openRunDeparaModal() {
  const origin = runSyntheticOrigin(currentRun);
  if (!origin || !origin.captureId || !currentRun?.log_dir) return;
  const modal = document.getElementById("run_depara_modal");
  modal?.classList.remove("hidden");
  html("#run_depara_content", '<div class="text-sm text-stone-400">Carregando de→para...</div>');
  const result = await apiJson(
    `/api/captures/${origin.captureId}/synthetic-substitutions?log_dir=${encodeURIComponent(currentRun.log_dir)}`,
  );
  if (result?.ok && result?.data) {
    html("#run_depara_content", renderRunDeparaHtml(result.data));
    return;
  }
  const detail = result?.data?.error || (result ? `HTTP ${result.status}` : "sem resposta do servidor");
  html(
    "#run_depara_content",
    `<div class="rounded-2xl border border-red-700/40 bg-red-900/20 p-3 text-sm text-red-300">Falha ao carregar o de→para: ${escapeHtml(detail)}</div>`,
  );
}

async function reprocessFromFailure(runId, failureId, scope, button) {
  const originalLabel = button ? button.textContent : "";
  if (button) {
    button.disabled = true;
    button.textContent = "criando…";
  }
  const result = await apiJson(`/api/runs/${runId}/reprocess-from-failure`, jsonRequest("POST", { failure_id: Number(failureId), scope }));
  if (result?.ok && result?.data?.id) {
    window.location = `/runs/${result.data.id}`;
    return;
  }
  const detail = result?.data?.error || result?.data?.message || (result ? `HTTP ${result.status}` : "sem resposta do servidor");
  const extra = result?.data?.detail && result.data.detail !== detail ? ` (${result.data.detail})` : "";
  alert(`Não foi possível criar o reprocessamento: ${detail}${extra}`);
  if (button) {
    button.disabled = false;
    button.textContent = originalLabel;
  }
}

function renderDetail(run, report, comparison, failures) {
  const summary = comparison.summary || {};
  const failureRows = Object.entries((report.summary || {}).by_type || {});
  const syntheticOrigin = runSyntheticOrigin(run);
  const deparaCard = syntheticOrigin
    ? `
      <div class="rounded-2xl border border-rose-900/50 bg-rose-950/20 p-4">
        <div class="text-xs uppercase tracking-[0.14em] text-rose-300">Dados sintéticos</div>
        <p class="mt-1 text-xs text-stone-400">Esta run usou dados sintetizados a partir da
          <a href="/captures/${syntheticOrigin.captureId}" class="underline decoration-rose-400/60 underline-offset-2 hover:text-rose-100">captura #${syntheticOrigin.captureId}</a>.
          Veja o que foi substituído em cada tela.</p>
        <div class="mt-3"><button id="run_depara_btn" type="button" class="r2ctl-btn-soft text-xs">⇄ Ver dados substituídos</button></div>
      </div>`
    : "";
  html(
    "#detail",
    `
      <div class="grid gap-4 lg:grid-cols-2">
        ${runIdentityCard(run)}
        ${comparisonSummaryCard(summary)}
      </div>
      ${deparaCard}
      <div class="grid gap-4 lg:grid-cols-2">
        <div class="rounded-2xl border border-stone-800 bg-stone-950/40 p-4">
          <div class="text-xs uppercase tracking-[0.14em] text-stone-400">Falhas por tipo</div>
          <div class="mt-3 space-y-2">${failureTypeList(failureRows)}</div>
        </div>
        <div class="rounded-2xl border border-stone-800 bg-stone-950/40 p-4">
          <div class="text-xs uppercase tracking-[0.14em] text-stone-400">Exportações</div>
          ${exportLinks(run.id)}
        </div>
      </div>
      <div class="rounded-2xl border border-stone-800 bg-stone-950/40 p-4">
        <div class="text-xs uppercase tracking-[0.14em] text-stone-400">Reprocessamento por falha</div>
        <p class="mt-1 text-xs text-stone-400">Cria uma <strong>nova run</strong> que reexecuta a trilha a partir do ponto da falha escolhida — a run original é preservada e a nova entra na fila (inicie quando quiser).${(failures || []).length > 8 ? ` Exibindo as 8 primeiras de ${(failures || []).length} falhas.` : ""}</p>
        <div class="mt-3 space-y-3">
          ${(failures || []).slice(0, 8).map((item) => reprocessFailureCard(item)).join("") || '<div class="text-sm text-stone-400">Sem falhas estruturadas para reprocessamento guiado.</div>'}
        </div>
      </div>
    `,
  );

  document.querySelectorAll("[data-reprocess]").forEach((button) => {
    button.addEventListener("click", () => reprocessFromFailure(run.id, button.dataset.reprocess, button.dataset.scope, button));
  });
  document.getElementById("run_depara_btn")?.addEventListener("click", () => openRunDeparaModal());
}

async function loadDetail(id) {
  const runId = id || (document.getElementById("detail_id")?.value || "").trim();
  if (!runId) return;
  document.getElementById("detail_id").value = runId;
  const [detail, report, comparison, events, failures] = await Promise.all([
    apiJson(`/api/runs/${runId}`),
    apiJson(`/api/runs/${runId}/report`),
    apiJson(`/api/runs/${runId}/compare`),
    apiJson(`/api/runs/${runId}/events?limit=1000`),
    apiJson(`/api/runs/${runId}/failures?limit=1000`),
  ]);
  if (!detail?.data?.run) return;
  renderDetail(detail.data.run, report?.data?.report || {}, comparison?.data?.comparison || {}, failures?.data?.failures || []);
  const failureList = failures?.data?.failures || [];
  const eventList = events?.data?.events || [];
  currentFailures = failureList;
  currentRun = detail.data.run;
  currentSubstitutions = runSyntheticSubstitutions(detail.data.run);
  stopFailurePlayer();
  document.getElementById("failure_player")?.classList.add("hidden");
  // Ordem cronológica para o player acompanhar o início da divergência.
  playerFailures = failureList
    .slice()
    .sort((a, b) => (Number(a.seq_global) || 0) - (Number(b.seq_global) || 0));
  playerIdx = 0;

  let eventsHtml = "";
  if (!failureList.length && !eventList.length) {
    eventsHtml = '<div class="text-sm text-stone-400">Nenhum evento ou falha registrado.</div>';
  } else {
    eventsHtml = '<div class="space-y-4">';

    if (failureList.length) {
      eventsHtml += '<div><div class="text-xs uppercase tracking-[0.14em] text-stone-400 mb-2">Falhas (' + failureList.length + ')</div>';
      eventsHtml += '<div class="r2ctl-table-scroll"><table class="w-full text-sm"><thead><tr class="text-left text-stone-400 border-b border-stone-700/40">'
        + '<th class="py-1 pr-2">Tipo</th><th class="py-1 pr-2">Gravidade</th><th class="py-1 pr-2">Sessão</th><th class="py-1 pr-2">Seq</th><th class="py-1 pr-2">Data/Hora</th><th class="py-1">Divergência</th></tr></thead><tbody>';
      eventsHtml += failureList.map((f, idx) => {
        const sev = String(f.severity || "—");
        const ts = f.ts_ms ? new Date(f.ts_ms).toLocaleString("pt-BR") : "—";
        const diffLabel = failureHasScreenDiff(f) ? "Ver telas" : "Detalhes";
        return '<tr class="border-b border-stone-800/40">'
          + '<td class="py-1 pr-2 text-stone-200">' + escapeHtml(f.failure_type || "—") + '</td>'
          + '<td class="py-1 pr-2"><span class="rounded-full bg-stone-800 px-2 py-0.5 text-xs">' + escapeHtml(sev) + '</span></td>'
          + '<td class="py-1 pr-2 font-mono text-xs text-stone-400">' + escapeHtml(String(f.session_id || "—")) + '</td>'
          + '<td class="py-1 pr-2 text-stone-400">' + escapeHtml(String(f.seq_global ?? "—")) + '</td>'
          + '<td class="py-1 pr-2 text-xs text-stone-400">' + escapeHtml(ts) + '</td>'
          + '<td class="py-1"><button type="button" class="r2ctl-btn-soft text-xs" data-failure-idx="' + idx + '">' + diffLabel + '</button></td></tr>';
      }).join("");
      eventsHtml += '</tbody></table></div></div>';
    }

    if (eventList.length) {
      eventsHtml += '<div><div class="text-xs uppercase tracking-[0.14em] text-stone-400 mb-2">Eventos (' + eventList.length + ')</div>';
      eventsHtml += '<div class="r2ctl-table-scroll"><table class="w-full text-sm"><thead><tr class="text-left text-stone-400 border-b border-stone-700/40">'
        + '<th class="py-1 pr-2">Tipo</th><th class="py-1 pr-2">Seq Global</th><th class="py-1 pr-2">Sessão</th><th class="py-1">Data/Hora</th></tr></thead><tbody>';
      eventsHtml += eventList.map(e => {
        const ts = e.ts_ms ? new Date(e.ts_ms).toLocaleString("pt-BR") : "—";
        // A API devolve {kind, message, data_json}: seq/sessão ficam no payload.
        let data = {};
        try { data = e.data_json ? JSON.parse(e.data_json) : {}; } catch { data = {}; }
        return '<tr class="border-b border-stone-800/40">'
          + '<td class="py-1 pr-2"><span class="rounded-full bg-stone-800 px-2 py-0.5 text-xs font-semibold text-stone-200">' + escapeHtml(e.kind || e.type || e.event_type || "—") + '</span></td>'
          + '<td class="py-1 pr-2 text-stone-400">' + escapeHtml(String(e.seq_global ?? data.seq_global ?? e.seq ?? "—")) + '</td>'
          + '<td class="py-1 pr-2 font-mono text-xs text-stone-400">' + escapeHtml(String(e.session_id || data.session_id || e.session_uuid || "—")) + '</td>'
          + '<td class="py-1 text-xs text-stone-400">' + escapeHtml(ts) + '</td></tr>';
      }).join("");
      eventsHtml += '</tbody></table></div></div>';
    }

    eventsHtml += '</div>';
  }

  html("#events", eventsHtml);
}

window.addEventListener("DOMContentLoaded", () => {
  const pathParts = window.location.pathname.split("/");
  const runId = pathParts[pathParts.length - 1];
  if (/^\d+$/.test(runId)) {
    loadDetail(runId);
  }
  document.getElementById("load_detail_btn")?.addEventListener("click", () => loadDetail());
  document.getElementById("run_depara_close_btn")?.addEventListener("click", () => closeRunDeparaModal());
  document.getElementById("run_depara_modal")?.addEventListener("click", (ev) => {
    if (ev.target === ev.currentTarget) closeRunDeparaModal();
  });
  document.getElementById("events")?.addEventListener("click", (ev) => {
    const button = ev.target.closest("[data-failure-idx]");
    if (!button) return;
    const failure = currentFailures[Number(button.dataset.failureIdx)];
    const playerPos = playerFailures.findIndex((entry) => entry === failure);
    selectFailurePlayer(playerPos >= 0 ? playerPos : 0, { scroll: true });
  });
  document.getElementById("failure_player")?.addEventListener("click", (ev) => {
    if (ev.target.closest("#fp_first")) {
      selectFailurePlayer(0);
      return;
    }
    if (ev.target.closest("#fp_prev")) {
      selectFailurePlayer(playerIdx - 1);
      return;
    }
    if (ev.target.closest("#fp_next")) {
      selectFailurePlayer(playerIdx + 1);
      return;
    }
    if (ev.target.closest("#fp_last")) {
      selectFailurePlayer(playerFailures.length - 1);
      return;
    }
    if (ev.target.closest("#fp_play")) toggleFailurePlayer();
  });
  // Salto direto: Enter no campo "ir para" (delegação — o player é re-renderizado a cada frame).
  document.getElementById("failure_player")?.addEventListener("keydown", (ev) => {
    const input = ev.target.closest("#fp_goto");
    if (!input || ev.key !== "Enter") return;
    ev.preventDefault();
    const target = Number(input.value);
    if (Number.isInteger(target) && target >= 1 && target <= playerFailures.length) {
      selectFailurePlayer(target - 1);
    }
  });
});

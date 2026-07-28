/**
 * host_metrics_panel.js — painel de recursos do host (/observability/resources).
 *
 * Mostra CPU/memória/load/disco do servidor durante uma run (ou janela
 * manual), exporta a série em JSON e sobrepõe uma série importada de outro
 * ambiente para comparação de estresse. Helpers puros (computeStats,
 * buildPolylinePoints, rebaseSamples) são testáveis sem DOM.
 */
import { apiJson } from "../core/api.js";
import { escapeHtml, text } from "../core/dom.js";

export const EXPORT_FORMAT = "dakota-host-metrics/v1";

const CHART_W = 600;
const CHART_H = 160;
const CHART_PAD = 8;

const CHARTS = [
  { key: "cpu_pct", label: "CPU (%)", fixedMax: 100 },
  { key: "mem_pct", label: "Memória (%)", fixedMax: 100 },
  { key: "load1", label: "Load (1 min)", fixedMax: null },
  { key: "disk_read_kbs", label: "Disco — leitura (kB/s)", fixedMax: null, altKey: "disk_write_kbs", altLabel: "escrita" },
];

const SERIES_COLORS = ["#34d399", "#38bdf8"];

// ── Helpers puros ────────────────────────────────────────────────────────────

/** Rebaixa os timestamps para segundos relativos ao início da série. */
export function rebaseSamples(samples) {
  if (!samples.length) return [];
  const t0 = samples[0].ts_ms;
  return samples.map((s) => ({ ...s, rel_s: (s.ts_ms - t0) / 1000 }));
}

/** Média/máx por campo numérico da série (ignora null). */
export function computeStats(samples, fields) {
  const stats = {};
  for (const field of fields) {
    const values = samples.map((s) => s[field]).filter((v) => typeof v === "number");
    stats[field] = values.length
      ? { avg: Math.round((values.reduce((a, b) => a + b, 0) / values.length) * 10) / 10, max: Math.max(...values) }
      : { avg: null, max: null };
  }
  return stats;
}

/** Pontos "x,y" do polyline SVG para a série (usa rel_s e escala vmax). */
export function buildPolylinePoints(samples, key, vmax, width = CHART_W, height = CHART_H, pad = CHART_PAD) {
  const usableW = width - pad * 2;
  const usableH = height - pad * 2;
  const tMax = samples.length ? samples[samples.length - 1].rel_s : 0;
  const spanT = tMax > 0 ? tMax : 1;
  const spanV = vmax > 0 ? vmax : 1;
  return samples
    .filter((s) => typeof s[key] === "number")
    .map((s) => {
      const x = pad + (s.rel_s / spanT) * usableW;
      const y = pad + usableH - (s[key] / spanV) * usableH;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
}

// ── Estado do painel ─────────────────────────────────────────────────────────

const state = {
  localSamples: [],
  compareSamples: [],
  compareLabel: "",
  window: null,
};

function seriesList() {
  const list = [{ name: "este ambiente", samples: rebaseSamples(state.localSamples), color: SERIES_COLORS[0] }];
  if (state.compareSamples.length) {
    list.push({ name: state.compareLabel || "outro ambiente", samples: rebaseSamples(state.compareSamples), color: SERIES_COLORS[1] });
  }
  return list;
}

// ── Render ───────────────────────────────────────────────────────────────────

function renderChart(container, chart, series) {
  const allValues = series.flatMap((s) => s.samples.map((p) => p[chart.key]).filter((v) => typeof v === "number"));
  if (!allValues.length) {
    return "";
  }
  const vmax = chart.fixedMax || Math.max(...allValues) * 1.1 || 1;
  const lines = series
    .filter((s) => s.samples.length)
    .map((s) => {
      const points = buildPolylinePoints(s.samples, chart.key, vmax);
      return points ? `<polyline points="${points}" fill="none" stroke="${s.color}" stroke-width="1.5" />` : "";
    })
    .join("");
  const tMax = Math.max(...series.map((s) => (s.samples.length ? s.samples[s.samples.length - 1].rel_s : 0)), 0);
  return `
    <div class="r2ctl-detail-surface rounded-2xl p-4">
      <h3 class="text-sm font-semibold uppercase tracking-[0.18em] text-stone-300">${escapeHtml(chart.label)}</h3>
      <svg viewBox="0 0 ${CHART_W} ${CHART_H}" class="mt-2 w-full" role="img" aria-label="${escapeHtml(chart.label)}">
        <rect x="0" y="0" width="${CHART_W}" height="${CHART_H}" fill="rgba(0,0,0,0.3)" rx="8" />
        ${lines}
        <text x="${CHART_PAD}" y="${CHART_PAD + 10}" fill="#a8a29e" font-size="10">máx ${Math.round(vmax * 10) / 10}</text>
        <text x="${CHART_W - CHART_PAD - 70}" y="${CHART_H - 6}" fill="#a8a29e" font-size="10">${Math.round(tMax)}s</text>
      </svg>
      <div class="mt-1 flex flex-wrap gap-3 text-[11px] text-stone-400">
        ${series.map((s) => `<span><span style="color:${s.color}">●</span> ${escapeHtml(s.name)}</span>`).join("")}
      </div>
    </div>`;
}

function renderStats(series) {
  const el = document.getElementById("obs_res_stats");
  if (!el) return;
  const fields = ["cpu_pct", "mem_pct", "load1", "disk_read_kbs", "disk_write_kbs"];
  const rows = series.map((s) => {
    const stats = computeStats(s.samples, fields);
    const cell = (f) => (stats[f].avg === null ? "-" : `${stats[f].avg} / ${stats[f].max}`);
    return `<tr class="border-b border-stone-700/30">
      <td class="px-3 py-1.5"><span style="color:${s.color}">●</span> ${escapeHtml(s.name)}</td>
      <td class="px-3 py-1.5 font-mono">${cell("cpu_pct")}</td>
      <td class="px-3 py-1.5 font-mono">${cell("mem_pct")}</td>
      <td class="px-3 py-1.5 font-mono">${cell("load1")}</td>
      <td class="px-3 py-1.5 font-mono">${cell("disk_read_kbs")}</td>
      <td class="px-3 py-1.5 font-mono">${cell("disk_write_kbs")}</td>
    </tr>`;
  }).join("");
  el.innerHTML = `
    <h3 class="text-sm font-semibold uppercase tracking-[0.18em] text-stone-300">Resumo (média / máx)</h3>
    <table class="mt-2 w-full text-xs text-stone-200">
      <thead><tr class="text-stone-400">
        <th class="px-3 py-1 text-left">série</th><th class="px-3 py-1 text-left">CPU%</th>
        <th class="px-3 py-1 text-left">Mem%</th><th class="px-3 py-1 text-left">Load1</th>
        <th class="px-3 py-1 text-left">Disco leitura kB/s</th><th class="px-3 py-1 text-left">Disco escrita kB/s</th>
      </tr></thead>
      <tbody>${rows}</tbody>
    </table>`;
  el.classList.remove("hidden");
}

function renderAll() {
  const series = seriesList();
  const container = document.getElementById("obs_res_charts");
  if (!container) return;
  const chartsHtml = CHARTS.map((chart) => renderChart(container, chart, series)).filter(Boolean).join("");
  container.innerHTML = chartsHtml || '<div class="text-sm text-stone-400">Sem amostras na janela selecionada.</div>';
  renderStats(series);
}

// ── Ações ────────────────────────────────────────────────────────────────────

async function loadRunsIntoSelect() {
  const select = document.getElementById("obs_res_run");
  if (!select) return;
  const result = await apiJson("/api/runs?limit=50");
  const runs = result?.data?.runs || [];
  select.innerHTML = '<option value="">— janela manual —</option>' + runs.map((run) => {
    const when = run.created_at_ms ? new Date(run.created_at_ms).toLocaleString("pt-BR") : "-";
    return `<option value="${run.id}">#${run.id} ${escapeHtml(run.status || "-")} • ${escapeHtml(run.mode || "-")} • ${escapeHtml(when)}</option>`;
  }).join("");
}

async function loadMetrics() {
  const runId = document.getElementById("obs_res_run")?.value || "";
  let url;
  if (runId) {
    url = `/api/observability/host-metrics?run_id=${encodeURIComponent(runId)}`;
  } else {
    const fromVal = document.getElementById("obs_res_from")?.value || "";
    const toVal = document.getElementById("obs_res_to")?.value || "";
    const fromMs = fromVal ? new Date(fromVal).getTime() : 0;
    const toMs = toVal ? new Date(toVal).getTime() : 0;
    if (!fromMs || !toMs) {
      text("#obs_res_status", "Informe uma run ou a janela de/até.");
      return;
    }
    url = `/api/observability/host-metrics?from_ms=${fromMs}&to_ms=${toMs}`;
  }
  text("#obs_res_status", "Carregando amostras...");
  const result = await apiJson(url);
  if (!result?.ok) {
    text("#obs_res_status", `Erro: ${result?.data?.error || `HTTP ${result?.status || "?"}`}`);
    return;
  }
  state.localSamples = result.data.samples || [];
  state.window = result.data.window || null;
  text(
    "#obs_res_status",
    `${result.data.total_samples || 0} amostras na janela (mostrando ${state.localSamples.length}). ` +
    "Coleta local a cada poucos segundos pelo próprio replay2.",
  );
  renderAll();
}

async function exportRun() {
  const runId = document.getElementById("obs_res_run")?.value || "";
  if (!runId) {
    text("#obs_res_status", "Selecione uma run para exportar.");
    return;
  }
  const result = await apiJson(`/api/observability/host-metrics/export?run_id=${encodeURIComponent(runId)}`);
  if (!result?.ok) {
    text("#obs_res_status", `Erro ao exportar: ${result?.data?.error || "falha"}`);
    return;
  }
  const blob = new Blob([JSON.stringify(result.data, null, 2)], { type: "application/json" });
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = `host-metrics-${result.data.env || "env"}-${result.data.host || "host"}-run${runId}.json`;
  link.click();
  URL.revokeObjectURL(link.href);
  text("#obs_res_status", `Exportado: ${link.download} — leve este arquivo ao outro ambiente e use "Importar comparação".`);
}

function importCompare(file) {
  const reader = new FileReader();
  reader.onload = () => {
    try {
      const payload = JSON.parse(String(reader.result || "{}"));
      if (payload.format !== EXPORT_FORMAT || !Array.isArray(payload.samples)) {
        text("#obs_res_status", "Arquivo inválido: use um export gerado por este painel.");
        return;
      }
      state.compareSamples = payload.samples;
      state.compareLabel = `${payload.env || "?"}@${payload.host || "?"} run#${payload.run?.id || "?"}`;
      document.getElementById("obs_res_compare_clear")?.classList.remove("hidden");
      text("#obs_res_status", `Comparação carregada: ${state.compareLabel} (${payload.samples.length} amostras, eixo rebasado em t+0).`);
      renderAll();
    } catch (_err) {
      text("#obs_res_status", "Falha ao ler o arquivo de comparação.");
    }
  };
  reader.readAsText(file);
}

function clearCompare() {
  state.compareSamples = [];
  state.compareLabel = "";
  document.getElementById("obs_res_compare_clear")?.classList.add("hidden");
  const input = document.getElementById("obs_res_compare");
  if (input) input.value = "";
  renderAll();
}

export function initHostMetricsPanel() {
  if (!document.getElementById("obs-resources")) return;
  loadRunsIntoSelect();
  document.getElementById("obs_res_apply")?.addEventListener("click", loadMetrics);
  document.getElementById("obs_res_run")?.addEventListener("change", loadMetrics);
  document.getElementById("obs_res_export")?.addEventListener("click", exportRun);
  document.getElementById("obs_res_compare")?.addEventListener("change", (ev) => {
    const file = ev.target.files?.[0];
    if (file) importCompare(file);
  });
  document.getElementById("obs_res_compare_clear")?.addEventListener("click", clearCompare);
}

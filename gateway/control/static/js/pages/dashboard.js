import { apiJson } from "../core/api.js";
import { escapeHtml, formatAgo, formatCount, html, statusLabel, statusToneClass, text } from "../core/dom.js";
import { buildQuery } from "../components/filters.js";
import { runLinkCard, runSummaryCards } from "../components/run_views.js";
import { statList } from "../components/tables.js";

async function loadCaptures() {
  const result = await apiJson("/api/captures?limit=50");
  if (!result?.data) return;
  const captures = result.data.captures || [];
  text("#dashboard_captures_total", captures.length);
  text("#dashboard_captures_active", captures.filter((c) => c.status === "active").length);
  text("#dashboard_captures_finished", captures.filter((c) => c.status === "finished").length);
  text("#dashboard_captures_interrupted", captures.filter((c) => c.status === "interrupted").length);
  html(
    "#dashboard_recent_captures",
    captures.length
      ? captures.slice(0, 5).map((cap) => {
          const dateStr = cap.started_at_ms ? formatAgo(cap.started_at_ms) : "-";
          const statusCls = cap.status === "active" ? "text-emerald-300" : cap.status === "finished" ? "text-stone-300" : "text-amber-300";
          return `<div class="flex items-center justify-between gap-3 rounded-xl bg-black/20 px-3 py-2">
            <div class="min-w-0">
              <div class="truncate text-sm font-medium text-stone-200">#${cap.id} ${escapeHtml(cap.created_by_username || "-")}</div>
              <div class="text-xs text-stone-400">${escapeHtml(cap.notes || "sem notas")}</div>
            </div>
            <div class="shrink-0 text-right">
              <div class="text-xs ${statusCls} font-semibold">${escapeHtml(cap.status)}</div>
              <div class="text-xs text-stone-500">${dateStr}</div>
            </div>
          </div>`;
        }).join("")
      : '<div class="text-sm text-stone-400">Nenhuma captura registrada.</div>',
  );
  text("#dashboard_captures_status", `${captures.length} capturas carregadas`);
}

async function loadRuns() {
  const result = await apiJson("/api/runs?limit=20");
  if (!result?.data) return [];
  const runs = result.data.runs || [];
  text("#metric_total_runs", runs.length);
  text("#metric_running_runs", runs.filter((run) => ["running", "resuming"].includes(String(run.status || "").toLowerCase())).length);
  text("#metric_queued_runs", runs.filter((run) => ["queued", "pending"].includes(String(run.status || "").toLowerCase())).length);
  text("#metric_failed_runs", runs.filter((run) => ["failed", "cancelled", "canceled"].includes(String(run.status || "").toLowerCase())).length);
  html(
    "#dashboard_runs_summary",
    runSummaryCards([
      { label: "Sucesso", value: runs.filter((run) => ["success", "completed", "done"].includes(String(run.status || "").toLowerCase())).length },
      { label: "Compliance bloqueado", value: runs.filter((run) => ["rejected", "blocked"].includes(String(run.compliance_status || "").toLowerCase())).length },
      { label: "Em observação", value: runs.filter((run) => ["warn", "warning"].includes(String(run.compliance_status || "").toLowerCase())).length },
      { label: "Com gateway", value: runs.filter((run) => Boolean(run.via_gateway)).length },
    ]),
  );
  html(
    "#dashboard_recent_runs",
    runs.length
      ? runs.slice(0, 6).map(runLinkCard).join("")
      : '<div class="text-sm text-stone-400">Nenhuma run recente.</div>',
  );
  text("#dashboard_runs_status", `${runs.length} runs carregadas`);
  return runs;
}

async function loadAlerts() {
  const result = await apiJson("/api/observability/overview?run_limit=12");
  if (!result?.data) return;
  const ops = result.data.ops || {};
  const alerts = [...(ops.recent_regressions || []), ...(ops.recent_failures || []).slice(0, 4)];
  html(
    "#dashboard_alerts",
    alerts.length
      ? alerts.slice(0, 6).map((item) => `
          <div class="r2ctl-comparison-card ${item.run_id ? "r2ctl-comparison-card-danger" : ""}">
            <div class="flex items-center justify-between gap-3">
              <div class="font-mono text-sm text-stone-100">${escapeHtml(item.run_id ? `run #${item.run_id}` : item.failure_type || "falha")}</div>
              <div class="text-xs text-stone-400">${escapeHtml(item.environment || item.session_id || "-")}</div>
            </div>
            <div class="mt-2 text-sm text-stone-300">${escapeHtml(item.message || item.failure_type || "alerta operacional")}</div>
          </div>
        `).join("")
      : '<div class="text-sm text-stone-400">Sem alertas recentes.</div>',
  );
}

async function loadGateway() {
  const status = await apiJson("/api/gateway/status");
  let defaultLogDir = "";
  if (status?.data) {
    text("#dashboard_gateway_service", status.data.running ? "ativo" : "inativo");
    defaultLogDir = status.data.capture_log_dir || "";
  }
  const input = document.getElementById("dashboard_log_dir");
  const logDir = (input?.value || localStorage.getItem("gatewayMonitorLogDir") || defaultLogDir || "").trim();
  if (input && logDir && !input.value) input.value = logDir;
  if (!logDir) {
    text("#dashboard_gateway_status", "aguardando log_dir");
    return;
  }
  localStorage.setItem("gatewayMonitorLogDir", logDir);
  const query = buildQuery({ log_dir: logDir, limit: 40 });
  const monitor = await apiJson(`/api/gateway/monitor?${query}`);
  if (!monitor?.data) return;
  const summary = monitor.data.summary || {};
  text("#dashboard_gateway_events", formatCount(summary.window_events || 0));
  text("#dashboard_gateway_sessions", formatCount(summary.unique_sessions || 0));
  text("#dashboard_gateway_status", monitor.data.error || `fonte: ${monitor.data.log_dir || "-"}`);
  html("#dashboard_gateway_types", statList(summary.top_types || [], "type"));
}

window.addEventListener("DOMContentLoaded", () => {
  loadCaptures();
  loadRuns();
  loadAlerts();
  loadGateway();
  document.getElementById("dashboard_refresh_gateway")?.addEventListener("click", loadGateway);
});

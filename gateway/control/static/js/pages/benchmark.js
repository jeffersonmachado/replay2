// benchmark.js — página do benchmark real AIX vs Linux (contrato §21).
// Consome /api/benchmarks*; nunca inventa números: estados vazios explícitos
// e selo REAL / SIMULATION / INCONCLUSIVE sempre visível no topo.
import { apiJson, jsonRequest } from "../core/api.js";
import { escapeHtml, html, qs, text } from "../core/dom.js";

let selectedExperimentId = "";
let pollTimer = null;

// ── helpers de apresentação ────────────────────────────────────────────────

function fmt(value, casas = 2) {
  if (value === null || value === undefined || value === "") return "-";
  const num = Number(value);
  if (Number.isNaN(num)) return escapeHtml(String(value));
  return num.toFixed(casas);
}

function seal(kind, note) {
  const badge = qs("#bench_seal_badge");
  const card = qs("#bench_seal");
  const translate = { REAL: "REAL", SIMULATION: "SIMULAÇÃO", INCONCLUSIVE: "INCONCLUSIVO" };
  const label = translate[kind] || kind || "INCONCLUSIVO";
  const styles = {
    REAL: "bg-emerald-600/30 text-emerald-200",
    SIMULATION: "bg-amber-600/30 text-amber-200",
    INCONCLUSIVE: "bg-stone-700/60 text-stone-300",
  };
  const borders = {
    REAL: "r2ctl-card mb-4 p-4 border-emerald-600/40",
    SIMULATION: "r2ctl-card mb-4 p-4 border-amber-600/50",
    INCONCLUSIVE: "r2ctl-card mb-4 p-4 border-stone-600/40",
  };
  badge.className = `inline-flex items-center rounded-full px-3 py-1 text-sm font-bold ${styles[kind] || styles.INCONCLUSIVE}`;
  badge.textContent = label;
  card.className = borders[kind] || borders.INCONCLUSIVE;
  text("#bench_seal_text", note);
}

function verdictBadge(verdict) {
  const translate = { PASS: "APROVADO", WARN: "ALERTA", FAIL: "REPROVADO", INCONCLUSIVE: "INCONCLUSIVO" };
  const styles = {
    PASS: "bg-emerald-600/30 text-emerald-200",
    WARN: "bg-amber-600/30 text-amber-200",
    FAIL: "bg-red-600/30 text-red-200",
    INCONCLUSIVE: "bg-stone-700/60 text-stone-300",
  };
  const v = String(verdict || "INCONCLUSIVE").toUpperCase();
  const label = translate[v] || v;
  return `<span class="inline-flex items-center rounded-full px-3 py-1 text-sm font-bold ${styles[v] || styles.INCONCLUSIVE}">${escapeHtml(label)}</span>`;
}

function statusBadge(status) {
  const s = String(status || "").toUpperCase();
  const styles = {
    RUNNING: "bg-emerald-600/30 text-emerald-200",
    COMPLETED: "bg-emerald-600/30 text-emerald-200",
    CREATED: "bg-stone-700/60 text-stone-300",
    FAILED: "bg-red-600/30 text-red-200",
    CANCELLED: "bg-amber-600/30 text-amber-200",
  };
  return `<span class="inline-flex items-center rounded-full px-2 py-0.5 text-xs ${styles[s] || "bg-stone-700/60 text-stone-300"}">${escapeHtml(s || "-")}</span>`;
}

function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
}

function startPolling() {
  stopPolling();
  pollTimer = setInterval(() => { loadDetail(selectedExperimentId, { silent: true }); }, 5000);
}

// ── lista de experimentos ──────────────────────────────────────────────────

async function loadExperiments() {
  const result = await apiJson("/api/benchmarks");
  if (!result?.data?.ok) return;
  const experiments = result.data.experiments || [];
  qs("#bexp_list_empty").classList.toggle("hidden", experiments.length > 0);
  html("#bexp_rows", experiments.map((exp) => `
    <tr class="border-b border-stone-800/40 cursor-pointer hover:bg-stone-800/30" data-exp-id="${escapeHtml(exp.experiment_id)}">
      <td class="py-1 pr-2 text-rose-300">${escapeHtml(exp.experiment_id)}</td>
      <td class="py-1 pr-2">${statusBadge(exp.status)}</td>
      <td class="py-1 pr-2">${verdictBadge(exp.verdict)}</td>
      <td class="py-1 text-stone-400">${exp.created_at_ms ? new Date(Number(exp.created_at_ms)).toLocaleString("pt-BR") : "-"}</td>
    </tr>`).join(""));
  document.querySelectorAll("#bexp_rows tr").forEach((row) => {
    row.addEventListener("click", () => selectExperiment(row.dataset.expId));
  });
}

// ── criação de experimento ─────────────────────────────────────────────────

function parseJsonField(selector, label) {
  const raw = (qs(selector)?.value || "").trim();
  if (!raw) return null;
  try {
    return JSON.parse(raw);
  } catch (_err) {
    throw new Error(`${label}: JSON inválido`);
  }
}

async function createExperiment() {
  const status = qs("#bexp_create_status");
  status.textContent = "";
  let body;
  try {
    const environments = parseJsonField("#bexp_envs", "Ambientes");
    if (!Array.isArray(environments) || !environments.length) {
      throw new Error("Ambientes: informe ao menos um ambiente (JSON)");
    }
    const concurrency = (qs("#bexp_concurrency").value || "")
      .split(",").map((v) => parseInt(v.trim(), 10)).filter((n) => Number.isInteger(n) && n > 0);
    if (!concurrency.length) throw new Error("Concorrência: informe níveis válidos (ex.: 1,5,10)");
    const journeys = parseJsonField("#bexp_journeys", "Jornadas") || [];
    body = {
      experiment_id: (qs("#bexp_id").value || "").trim(),
      journey_set_sha256: (qs("#bexp_journey_hash").value || "").trim(),
      dataset_sha256: (qs("#bexp_dataset_hash").value || "").trim(),
      seed: parseInt(qs("#bexp_seed").value, 10) || 0,
      terminal_geometry: (qs("#bexp_geometry").value || "80x24").trim(),
      concurrency_levels: concurrency,
      iterations: parseInt(qs("#bexp_iterations").value, 10) || 1,
      warmup_seconds: parseInt(qs("#bexp_warmup").value, 10) || 0,
      measurement_seconds: parseInt(qs("#bexp_measurement").value, 10) || 0,
      cooldown_seconds: parseInt(qs("#bexp_cooldown").value, 10) || 0,
      think_time_profile: {
        type: (qs("#bexp_think_time").value || "none").trim(),
        sha256: "",
        params: {},
      },
      stop_conditions: {
        error_rate_pct: parseFloat(qs("#bexp_stop_error").value) || 5.0,
        p99_limit_ms: parseFloat(qs("#bexp_stop_p99").value) || 5000.0,
        host_cpu_pct: 95.0,
        swap_growth_mb: 512.0,
      },
      environments,
      journeys,
    };
  } catch (err) {
    status.textContent = err.message;
    return;
  }
  qs("#bexp_create_btn").disabled = true;
  status.textContent = "Criando...";
  const result = await apiJson("/api/benchmarks", jsonRequest("POST", body));
  qs("#bexp_create_btn").disabled = false;
  if (result?.data?.ok) {
    status.textContent = `Criado: ${result.data.experiment_id}`;
    await loadExperiments();
    selectExperiment(result.data.experiment_id);
  } else {
    status.textContent = `Erro: ${result?.data?.error || "falha ao criar"}`;
  }
}

// ── detalhe do experimento ─────────────────────────────────────────────────

function selectExperiment(experimentId) {
  selectedExperimentId = experimentId;
  qs("#bench_detail").classList.remove("hidden");
  loadDetail(experimentId);
}

function renderEnvironments(environments) {
  const ids = Object.keys(environments || {});
  if (!ids.length) {
    html("#bdetail_envs", '<div class="text-sm text-stone-400">Sem modelos de ambiente registrados.</div>');
    return;
  }
  html("#bdetail_envs", ids.map((id) => {
    const env = environments[id];
    const cpu = env.cpu || {};
    return `<div class="r2ctl-detail-surface rounded-xl p-3 text-sm">
      <div class="font-semibold text-stone-200">${escapeHtml(id)} <span class="text-xs text-stone-400">${escapeHtml(env.platform || "?")} / ${escapeHtml(env.architecture || "?")}</span></div>
      <div class="text-stone-300 mt-1">host: ${escapeHtml(env.host || "-")}:${escapeHtml(String(env.port || 22))}</div>
      <div class="text-stone-400 text-xs mt-1">
        CPU: ${escapeHtml(cpu.model || "-")} · vCPU: ${fmt(cpu.virtual_processors, 0)} · físicos: ${fmt(cpu.physical_processors, 0)} · entitled: ${fmt(cpu.entitled_capacity)} · SMT: ${fmt(cpu.smt_mode, 0)}<br>
        ${escapeHtml(cpu.shared_or_dedicated || "")} ${escapeHtml(cpu.capped_or_uncapped || "")} ${cpu.sockets ? `· sockets: ${fmt(cpu.sockets, 0)} · cores/socket: ${fmt(cpu.cores_per_socket, 0)} · threads/core: ${fmt(cpu.threads_per_core, 0)}` : ""}<br>
        Memória: ${fmt(env.memory_mb, 0)} MB
      </div>
    </div>`;
  }).join(""));
}

function renderStats(comparison) {
  const statsByEnv = comparison?.stats_by_env || {};
  const tpsByEnv = comparison?.tps_by_env || {};
  const counts = comparison?.counts || {};
  const ids = Object.keys(statsByEnv);
  qs("#bdetail_stats_empty").classList.toggle("hidden", ids.length > 0);
  if (!ids.length) {
    html("#bdetail_stats", "");
    return;
  }
  html("#bdetail_stats", ids.map((id) => {
    const s = statsByEnv[id] || {};
    const role = id === comparison.baseline_env ? "baseline" : (id === comparison.target_env ? "alvo" : "");
    const cnt = role === "baseline" ? (counts.baseline || {}) : (role === "alvo" ? (counts.target || {}) : {});
    return `<div class="r2ctl-detail-surface rounded-xl p-3 text-sm">
      <div class="font-semibold text-stone-200">${escapeHtml(id)} ${role ? `<span class="text-xs text-stone-400">(${role})</span>` : ""}</div>
      <div class="text-stone-300 mt-1">
        TPS: <strong class="text-emerald-300">${fmt(tpsByEnv[id])}</strong> · n=${fmt(s.n, 0)}<br>
        P50: ${fmt(s.p50)}ms · P90: ${fmt(s.p90)}ms · <strong>P95: ${fmt(s.p95)}ms</strong> · <strong>P99: ${fmt(s.p99)}ms</strong> · max: ${fmt(s.max)}ms<br>
        mean: ${fmt(s.mean)}ms · cv: ${fmt(s.cv, 4)}<br>
        <span class="text-xs text-stone-400">IC95: [${fmt(s.ci95_low)}, ${fmt(s.ci95_high)}] ms</span><br>
        ${cnt.total !== undefined ? `<span class="text-xs">erros: <span class="text-red-300">${fmt(cnt.errors, 0)}</span> · timeouts: ${fmt(cnt.timeouts, 0)} · divergências: <span class="text-red-300">${fmt(cnt.divergences, 0)}</span> / total: ${fmt(cnt.total, 0)}</span>` : ""}
      </div>
    </div>`;
  }).join(""));
}

function renderDegradation(comparison) {
  const byEnv = comparison?.degradation_by_env || {};
  const ids = Object.keys(byEnv);
  if (!ids.length) {
    html("#bdetail_degradation", '<div class="text-sm text-stone-400">Sem escada de carga executada.</div>');
    return;
  }
  html("#bdetail_degradation", ids.map((id) => {
    const d = byEnv[id] || {};
    return `<div class="r2ctl-detail-surface rounded-xl p-3 text-sm">
      <div class="font-semibold text-stone-200">${escapeHtml(id)}</div>
      <div class="text-stone-300 mt-1">
        ponto de degradação: <strong class="text-amber-300">${fmt(d.degradation_point, 0)}</strong><br>
        limite operacional seguro: ${fmt(d.safe_operational_limit, 0)} · máximo observado: ${fmt(d.maximum_observed_limit, 0)}<br>
        gargalo dominante: <strong>${escapeHtml(d.dominant_bottleneck || "unknown")}</strong><br>
        recuperação: ${d.recovery_seconds !== null && d.recovery_seconds !== undefined ? `${fmt(d.recovery_seconds)}s` : "não medida"}
      </div>
    </div>`;
  }).join(""));
}

function renderCapacityAndNormalization(capacity, comparison) {
  const cap = capacity || {};
  const ids = Object.keys(cap);
  html("#bdetail_capacity", ids.length ? ids.map((id) => {
    const c = cap[id] || {};
    return `<div class="r2ctl-detail-surface rounded-xl p-3 text-sm">
      <div class="font-semibold text-stone-200">${escapeHtml(id)}</div>
      <div class="text-stone-300 mt-1">TPS máximo observado: <strong>${fmt(c.max_tps_observed)}</strong><br>
      maior nível testado: ${fmt(c.max_concurrency_tested, 0)}</div>
    </div>`;
  }).join("") : '<div class="text-sm text-stone-400">Sem capacidade medida.</div>');

  const norm = comparison?.normalization;
  if (!norm) {
    html("#bdetail_normalization", '<div class="text-sm text-stone-400">Normalização não calculada (sem modelos de ambiente).</div>');
    return;
  }
  const perEnv = norm.per_environment || {};
  const formulas = norm.formulas || {};
  const statusClass = norm.status === "OK" ? "text-emerald-300" : "text-amber-300";
  html("#bdetail_normalization", `
    <div class="text-sm mb-2">Status da normalização: <strong class="${statusClass}">${escapeHtml(norm.status || "-")}</strong></div>
    <div class="grid grid-cols-1 gap-3 sm:grid-cols-2 mb-2">
      ${Object.keys(perEnv).map((id) => {
        const n = perEnv[id] || {};
        return `<div class="r2ctl-detail-surface rounded-xl p-3 text-sm">
          <div class="font-semibold text-stone-200">${escapeHtml(id)}</div>
          <div class="text-stone-300 mt-1">
            tps/vCPU: ${fmt(n.tps_per_vcpu)} · tps/core físico: ${fmt(n.tps_per_physical_core)}<br>
            tps/entitled: ${fmt(n.tps_per_entitled_capacity)} · tps/CPU consumida: ${fmt(n.tps_per_consumed_cpu)}<br>
            tps/GB: ${fmt(n.tps_per_gb)}${n.cost_per_1k_transactions != null ? ` · custo/1k tx: ${fmt(n.cost_per_1k_transactions)}` : ""}
          </div>
        </div>`;
      }).join("")}
    </div>
    ${Object.keys(formulas).length ? `<div class="text-xs text-stone-400">Fórmulas: ${Object.entries(formulas).map(([k, v]) => `<code>${escapeHtml(k)} = ${escapeHtml(v)}</code>`).join(" · ")}</div>` : ""}`);
}

function renderHost(hostAggregates) {
  const list = hostAggregates || [];
  qs("#bdetail_host_empty").classList.toggle("hidden", list.length > 0);
  html("#bdetail_host", list.map((h) => `<div class="r2ctl-detail-surface rounded-xl p-3 text-sm">
    <div class="font-semibold text-stone-200">${escapeHtml(h.environment_id)}</div>
    <div class="text-stone-300 mt-1">
      CPU ocupada: média ${fmt(h.cpu_busy_avg)}% · pico ${fmt(h.cpu_busy_max)}%<br>
      Memória: média ${fmt(h.mem_used_mb_avg, 0)} MB · pico ${fmt(h.mem_used_mb_max, 0)} MB · swap máx: ${fmt(h.swap_pct_max)}%<br>
      Disco: leitura ${fmt(h.disk_read_kbs_avg, 0)} KB/s · escrita ${fmt(h.disk_write_kbs_avg, 0)} KB/s · iops ${fmt(h.iops_avg, 0)}<br>
      Rede: rx ${fmt(h.net_rx_kbs_avg, 0)} KB/s · tx ${fmt(h.net_tx_kbs_avg, 0)} KB/s<br>
      <span class="text-xs text-stone-400">${fmt(h.samples, 0)} amostras</span>
    </div>
  </div>`).join(""));
}

function renderRuns(runs) {
  const list = runs || [];
  qs("#bdetail_runs_empty").classList.toggle("hidden", list.length > 0);
  html("#bdetail_runs_rows", list.map((run) => `
    <tr class="border-b border-stone-800/40">
      <td class="py-1 pr-2 text-stone-300">${escapeHtml(run.run_id)}</td>
      <td class="py-1 pr-2 text-stone-300">${escapeHtml(run.environment_id)}</td>
      <td class="py-1 pr-2 text-stone-300">${fmt(run.iteration, 0)}</td>
      <td class="py-1 pr-2 text-stone-300">${fmt(run.concurrency, 0)}</td>
      <td class="py-1 pr-2">${statusBadge(run.status)}</td>
      <td class="py-1 text-stone-400 text-xs">${escapeHtml(run.error_reason || "")}</td>
    </tr>`).join(""));
}

async function loadDetail(experimentId, { silent = false } = {}) {
  if (!experimentId) return;
  const [detail, comparisonRes, metricsRes, runsRes] = await Promise.all([
    apiJson(`/api/benchmarks/${encodeURIComponent(experimentId)}`),
    apiJson(`/api/benchmarks/${encodeURIComponent(experimentId)}/comparison`),
    apiJson(`/api/benchmarks/${encodeURIComponent(experimentId)}/metrics`),
    apiJson(`/api/benchmarks/${encodeURIComponent(experimentId)}/runs`),
  ]);
  const exp = detail?.data?.experiment;
  if (!exp) {
    if (!silent) seal("INCONCLUSIVE", "Experimento não encontrado.");
    return;
  }

  text("#bdetail_title", exp.experiment_id);
  html("#bdetail_status", statusBadge(exp.status));
  text("#bdetail_reason", exp.reason || "");
  qs("#bdetail_report_json").href = `/api/benchmarks/${encodeURIComponent(experimentId)}/report`;
  qs("#bdetail_report_md").href = `/api/benchmarks/${encodeURIComponent(experimentId)}/report?format=md`;
  qs("#bdetail_start_btn").disabled = exp.status === "RUNNING";
  qs("#bdetail_cancel_btn").disabled = exp.status !== "RUNNING";

  const contract = exp.contract || {};
  text("#bdetail_contract_sha", exp.contract_sha256 || "-");
  text("#bdetail_journey_sha", contract.journey_set_sha256 || "-");
  text("#bdetail_dataset_sha", contract.dataset_sha256 || "-");
  text("#bdetail_concurrency", (contract.concurrency_levels || []).join(", ") || "-");
  text("#bdetail_iterations", contract.iterations ?? "-");
  text("#bdetail_phases", `${contract.warmup_seconds ?? 0}s / ${contract.measurement_seconds ?? 0}s / ${contract.cooldown_seconds ?? 0}s`);

  const cmp = comparisonRes?.data || {};
  const comparison = cmp.comparison || null;
  const verdict = cmp.verdict || exp.verdict || "INCONCLUSIVE";
  html("#bdetail_verdict", verdictBadge(verdict));
  html("#bdetail_reasons", (cmp.reasons || []).map((r) => `<li>${escapeHtml(r)}</li>`).join(""));
  const rec = cmp.recommendation;
  qs("#bdetail_recommendation").classList.toggle("hidden", !rec);
  if (rec) text("#bdetail_recommendation", `Recomendação: ${rec}`);

  renderEnvironments(cmp.environments || exp.environments || {});
  renderStats(comparison);
  renderDegradation(comparison);
  renderCapacityAndNormalization(cmp.capacity, comparison);
  renderHost(metricsRes?.data?.host_aggregates || []);
  renderRuns(runsRes?.data?.runs || []);

  // Selo do topo: REAL quando há comparação de amostras reais.
  if (cmp.result_type === "REAL") {
    seal("REAL", `Experimento ${exp.experiment_id}: resultado medido com amostras reais de replay pareado (contrato ${String(exp.contract_sha256 || "").slice(0, 12)}…).`);
  } else {
    seal("INCONCLUSIVE", `Experimento ${exp.experiment_id}: ainda sem amostras reais — execute para produzir números válidos.`);
  }

  if (exp.status === "RUNNING") {
    startPolling();
  } else {
    stopPolling();
  }
}

async function experimentAction(action) {
  if (!selectedExperimentId) return;
  const result = await apiJson(
    `/api/benchmarks/${encodeURIComponent(selectedExperimentId)}/${action}`,
    jsonRequest("POST", {}));
  if (result?.data?.ok) {
    loadDetail(selectedExperimentId);
    loadExperiments();
  } else {
    text("#bdetail_reason", result?.data?.error || `falha ao ${action}`);
  }
}

// ── benchmark sintético legado (SIMULAÇÃO) ─────────────────────────────────

async function runSimulation() {
  const btn = qs("#bsim_run_btn");
  const status = qs("#bsim_status");
  const result = qs("#bsim_result");
  btn.disabled = true;
  status.classList.remove("hidden");
  status.textContent = "Executando simulação...";
  result.classList.add("hidden");

  let envs;
  try {
    envs = JSON.parse(qs("#bsim_envs").value);
  } catch (_err) {
    status.textContent = "JSON de ambientes inválido";
    btn.disabled = false;
    return;
  }

  try {
    const resp = await apiJson("/api/synthetic/benchmark", jsonRequest("POST", {
      name: qs("#bsim_name").value,
      journey_id: qs("#bsim_journey").value,
      environments: envs,
      concurrency: parseInt(qs("#bsim_concurrency").value, 10) || 5,
      iterations: parseInt(qs("#bsim_iterations").value, 10) || 3,
      seed: parseInt(qs("#bsim_seed").value, 10) || 0,
    }));
    const data = resp?.data || {};
    if (resp?.ok) {
      html("#bsim_summary", `<strong>${escapeHtml(data.summary || "").replace(/\n/g, "<br>")}</strong>`);
      html("#bsim_envs_result", (data.environments || []).map((e) =>
        `<div class="r2ctl-card p-3"><span class="text-sm text-stone-300">${escapeHtml(e.name)} (${escapeHtml(e.host)})</span><br>
         TPS: ${fmt(e.tps)} | Lat: ${fmt(e.avg_latency_ms)}ms | Erros: ${fmt(e.errors, 0)} | Diverg: ${fmt(e.divergences, 0)}</div>`
      ).join(""));
      html("#bsim_comparisons", (data.comparisons || []).map((c) =>
        `<div class="r2ctl-card p-3 border-amber-700/40">
          <span class="text-sm font-medium text-amber-300">${escapeHtml(c.verdict)} (SIMULAÇÃO)</span>
          <span class="text-sm text-stone-300 ml-2">${escapeHtml(c.baseline)} vs ${escapeHtml(c.target)}</span>
          <div class="text-xs text-amber-200/70 mt-1">Sem recomendação de migração: simulação não sustenta decisão.</div>
        </div>`
      ).join(""));
      status.textContent = "Simulação concluída";
      result.classList.remove("hidden");
      seal("SIMULATION", "Resultado exibido é SIMULAÇÃO (benchmark sintético legado): números NÃO são medição real e NÃO sustentam decisão de migração.");
    } else {
      status.textContent = `Erro: ${data.error || "desconhecido"}`;
    }
  } catch (err) {
    status.textContent = `Erro: ${err.message}`;
  }
  btn.disabled = false;
}

// ── boot ───────────────────────────────────────────────────────────────────

function boot() {
  qs("#bexp_create_btn")?.addEventListener("click", createExperiment);
  qs("#bexp_refresh_list_btn")?.addEventListener("click", loadExperiments);
  qs("#bdetail_start_btn")?.addEventListener("click", () => experimentAction("start"));
  qs("#bdetail_cancel_btn")?.addEventListener("click", () => experimentAction("cancel"));
  qs("#bdetail_refresh_btn")?.addEventListener("click", () => loadDetail(selectedExperimentId));
  qs("#bsim_run_btn")?.addEventListener("click", runSimulation);
  seal("INCONCLUSIVE", "Nenhum experimento selecionado. Crie um experimento real ou selecione um existente — números só aparecem após execução com amostras reais.");
  loadExperiments();
}

boot();

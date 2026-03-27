#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import secrets
import sqlite3
import time
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from dakota_gateway import auth
from dakota_gateway.replay_control import (
    Runner,
    add_run_event,
    cancel_run,
    create_run,
    get_run,
    pause_run,
    query_all,
    query_one,
    resume_run,
    retry_run,
)
from dakota_gateway.state_db import connect, init_db, now_ms
from dakota_gateway.state_db import ConnectionPool


INDEX_HTML = """<!doctype html>
<html><head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Dakota Calçados | Replay Control</title>
<link rel="icon" type="image/svg+xml" href="https://dakota.vtexassets.com/assets/vtex/assets-builder/dakota.dakota-theme/6.0.129/svg/logo-dakota___9e5024e768762611d1260e2e2d5e1aa5.svg" />
<script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="min-h-screen bg-[radial-gradient(circle_at_top,_rgba(251,191,36,0.12),_transparent_28%),linear-gradient(135deg,_#1c1917_0%,_#292524_46%,_#111827_100%)] text-stone-100">
<div class="fixed inset-0 pointer-events-none overflow-hidden">
  <div class="absolute -top-24 -left-16 h-72 w-72 rounded-full bg-amber-300/10 blur-3xl"></div>
  <div class="absolute bottom-0 right-0 h-80 w-80 rounded-full bg-orange-400/10 blur-3xl"></div>
</div>

<main class="relative mx-auto max-w-7xl px-4 py-6 sm:px-6 lg:px-8">
  <section class="rounded-[28px] border border-stone-700/50 bg-stone-950/65 shadow-[0_30px_80px_rgba(0,0,0,0.45)] backdrop-blur-md overflow-hidden">
    <div class="border-b border-stone-800/80 px-6 py-5 sm:px-8">
      <div class="flex flex-col gap-5 lg:flex-row lg:items-center lg:justify-between">
        <div class="flex items-center gap-4">
          <div class="rounded-2xl bg-white px-5 py-4 ring-1 ring-stone-200/80 shadow-[0_12px_40px_rgba(0,0,0,0.25)]">
            <img src="https://dakota.vtexassets.com/assets/vtex/assets-builder/dakota.dakota-theme/6.0.129/svg/logo-dakota___9e5024e768762611d1260e2e2d5e1aa5.svg" alt="Dakota" class="h-7 w-auto" loading="eager" referrerpolicy="no-referrer" />
          </div>
          <div>
            <p class="text-xs font-medium uppercase tracking-[0.24em] text-amber-200/80">Replay Control</p>
            <h1 class="mt-1 text-2xl font-semibold tracking-[0.18em] text-stone-50 sm:text-3xl">Painel Operacional</h1>
            <p class="mt-1 text-sm text-stone-300">Automação e governança de execuções para Dakota Calçados.</p>
          </div>
        </div>
        <div class="flex flex-col items-start gap-3 sm:flex-row sm:items-center">
          <div class="rounded-full border border-stone-700 bg-stone-900/70 px-4 py-2 text-sm text-stone-300" id="me"></div>
          <button onclick="logout()" class="rounded-full border border-amber-300/30 bg-amber-300/10 px-4 py-2 text-sm font-medium text-amber-100 transition hover:bg-amber-300/20">Logout</button>
        </div>
      </div>

      <div class="mt-6 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <div class="rounded-2xl border border-stone-800 bg-stone-900/55 px-4 py-4">
          <p class="text-xs uppercase tracking-[0.2em] text-stone-500">Total de runs</p>
          <p id="metric_total" class="mt-2 text-3xl font-semibold text-stone-50">0</p>
          <p class="mt-1 text-sm text-stone-400">Volume atual monitorado</p>
        </div>
        <div class="rounded-2xl border border-emerald-500/20 bg-emerald-500/8 px-4 py-4">
          <p class="text-xs uppercase tracking-[0.2em] text-emerald-200/80">Em execução</p>
          <p id="metric_running" class="mt-2 text-3xl font-semibold text-emerald-100">0</p>
          <p class="mt-1 text-sm text-emerald-100/70">Runs ativas agora</p>
        </div>
        <div class="rounded-2xl border border-amber-500/20 bg-amber-500/8 px-4 py-4">
          <p class="text-xs uppercase tracking-[0.2em] text-amber-200/80">Na fila</p>
          <p id="metric_queued" class="mt-2 text-3xl font-semibold text-amber-100">0</p>
          <p class="mt-1 text-sm text-amber-100/70">Aguardando processamento</p>
        </div>
        <div class="rounded-2xl border border-rose-500/20 bg-rose-500/8 px-4 py-4">
          <p class="text-xs uppercase tracking-[0.2em] text-rose-200/80">Com falha</p>
          <p id="metric_failed" class="mt-2 text-3xl font-semibold text-rose-100">0</p>
          <p class="mt-1 text-sm text-rose-100/70">Exigem atenção operacional</p>
        </div>
      </div>
    </div>

    <div class="grid gap-6 px-6 py-6 sm:px-8 lg:grid-cols-[1.2fr_0.8fr]">
      <section class="rounded-3xl border border-stone-800 bg-stone-900/60 p-5 shadow-inner shadow-black/20">
        <div class="mb-4 flex flex-col gap-2 sm:flex-row sm:items-end sm:justify-between">
          <div>
            <h2 class="text-lg font-semibold text-stone-100">Criar nova run</h2>
            <p class="text-sm text-stone-400">Configure a execução e envie para a fila operacional.</p>
          </div>
          <div class="text-sm text-stone-400" id="status"></div>
        </div>

        <div class="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
          <input id="log_dir" placeholder="log_dir (ex: /var/log/dakota-gateway)" class="rounded-xl border border-stone-700 bg-stone-950/80 px-4 py-3 text-sm text-stone-100 placeholder-stone-500 focus:border-amber-300 focus:outline-none focus:ring-2 focus:ring-amber-300/40 xl:col-span-2"/>
          <input id="target_host" placeholder="target_host" class="rounded-xl border border-stone-700 bg-stone-950/80 px-4 py-3 text-sm text-stone-100 placeholder-stone-500 focus:border-amber-300 focus:outline-none focus:ring-2 focus:ring-amber-300/40"/>
          <input id="target_user" placeholder="target_user" class="rounded-xl border border-stone-700 bg-stone-950/80 px-4 py-3 text-sm text-stone-100 placeholder-stone-500 focus:border-amber-300 focus:outline-none focus:ring-2 focus:ring-amber-300/40"/>
          <input id="target_cmd" placeholder="target_command (opcional)" class="rounded-xl border border-stone-700 bg-stone-950/80 px-4 py-3 text-sm text-stone-100 placeholder-stone-500 focus:border-amber-300 focus:outline-none focus:ring-2 focus:ring-amber-300/40 md:col-span-2 xl:col-span-2"/>
          <select id="mode" class="rounded-xl border border-stone-700 bg-stone-950/80 px-4 py-3 text-sm text-stone-100 focus:border-amber-300 focus:outline-none focus:ring-2 focus:ring-amber-300/40">
            <option value="strict-global">strict-global</option>
            <option value="parallel-sessions">parallel-sessions</option>
          </select>
          <select id="on_mismatch" class="rounded-xl border border-stone-700 bg-stone-950/80 px-4 py-3 text-sm text-stone-100 focus:border-amber-300 focus:outline-none focus:ring-2 focus:ring-amber-300/40">
            <option value="continue">on mismatch: continue</option>
            <option value="fail-fast">on mismatch: fail-fast</option>
          </select>
          <input id="concurrency" placeholder="concurrency (ex: 20)" class="rounded-xl border border-stone-700 bg-stone-950/80 px-4 py-3 text-sm text-stone-100 placeholder-stone-500 focus:border-amber-300 focus:outline-none focus:ring-2 focus:ring-amber-300/40"/>
          <input id="ramp" placeholder="ramp-up/s (ex: 2)" class="rounded-xl border border-stone-700 bg-stone-950/80 px-4 py-3 text-sm text-stone-100 placeholder-stone-500 focus:border-amber-300 focus:outline-none focus:ring-2 focus:ring-amber-300/40"/>
          <input id="speed" placeholder="speed (ex: 4)" class="rounded-xl border border-stone-700 bg-stone-950/80 px-4 py-3 text-sm text-stone-100 placeholder-stone-500 focus:border-amber-300 focus:outline-none focus:ring-2 focus:ring-amber-300/40"/>
          <input id="jitter" placeholder="jitter ms" class="rounded-xl border border-stone-700 bg-stone-950/80 px-4 py-3 text-sm text-stone-100 placeholder-stone-500 focus:border-amber-300 focus:outline-none focus:ring-2 focus:ring-amber-300/40"/>
          <input id="user_pool" placeholder="user pool csv (replay01,replay02,...)" class="rounded-xl border border-stone-700 bg-stone-950/80 px-4 py-3 text-sm text-stone-100 placeholder-stone-500 focus:border-amber-300 focus:outline-none focus:ring-2 focus:ring-amber-300/40 md:col-span-2 xl:col-span-2"/>
        </div>

        <div class="mt-4 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div class="flex flex-wrap items-center gap-3 text-sm text-stone-300">
            <input id="filter_status" placeholder="filtrar status (running, failed...)" class="min-w-[240px] rounded-xl border border-stone-700 bg-stone-950/80 px-4 py-3 text-sm text-stone-100 placeholder-stone-500 focus:border-amber-300 focus:outline-none focus:ring-2 focus:ring-amber-300/40"/>
            <label class="inline-flex items-center gap-2 rounded-full border border-stone-700 bg-stone-950/70 px-4 py-2">
              <input id="auto_refresh" type="checkbox" checked class="h-4 w-4 rounded border-stone-600 bg-stone-900 text-amber-300 focus:ring-amber-300/40"/>
              auto-refresh
            </label>
          </div>
          <button onclick="createRun()" class="rounded-xl bg-gradient-to-r from-amber-400 via-orange-400 to-rose-400 px-5 py-3 text-sm font-semibold text-stone-950 shadow-lg shadow-orange-950/30 transition hover:brightness-105">Criar run</button>
        </div>
      </section>

      <section class="rounded-3xl border border-stone-800 bg-stone-900/60 p-5 shadow-inner shadow-black/20">
        <div class="mb-4">
          <h2 class="text-lg font-semibold text-stone-100">Detalhe da run</h2>
          <p class="text-sm text-stone-400">Carregue o contexto e os últimos eventos de uma execução específica.</p>
        </div>
        <div class="flex gap-3">
          <input id="detail_id" placeholder="run id" class="w-28 rounded-xl border border-stone-700 bg-stone-950/80 px-4 py-3 text-sm text-stone-100 placeholder-stone-500 focus:border-amber-300 focus:outline-none focus:ring-2 focus:ring-amber-300/40"/>
          <button onclick="loadDetail()" class="rounded-xl border border-amber-300/30 bg-amber-300/10 px-4 py-3 text-sm font-medium text-amber-100 transition hover:bg-amber-300/20">Carregar</button>
        </div>
        <div class="mt-4 space-y-4">
          <pre id="detail" class="max-h-64 overflow-auto rounded-2xl border border-stone-800 bg-stone-950/90 p-4 text-xs leading-6 text-stone-300 whitespace-pre-wrap"></pre>
          <pre id="events" class="max-h-64 overflow-auto rounded-2xl border border-stone-800 bg-stone-950/90 p-4 text-xs leading-6 text-stone-300 whitespace-pre-wrap"></pre>
        </div>
      </section>
    </div>

    <div class="border-t border-stone-800/80 px-6 py-6 sm:px-8">
      <div class="mb-4 flex items-center justify-between">
        <div>
          <h2 class="text-lg font-semibold text-stone-100">Fila de execuções</h2>
          <p class="text-sm text-stone-400">Visão consolidada dos runs criados, andamento, progresso e ações operacionais.</p>
        </div>
      </div>

      <div class="overflow-hidden rounded-3xl border border-stone-800 bg-stone-950/70">
        <div class="overflow-x-auto">
          <table class="min-w-full divide-y divide-stone-800 text-sm">
            <thead class="bg-stone-900/95 text-left text-xs uppercase tracking-[0.18em] text-stone-400">
              <tr>
                <th class="px-4 py-4">id</th>
                <th class="px-4 py-4">status</th>
                <th class="px-4 py-4">created_at</th>
                <th class="px-4 py-4">progresso</th>
                <th class="px-4 py-4">params</th>
                <th class="px-4 py-4">ações</th>
              </tr>
            </thead>
            <tbody id="rows" class="divide-y divide-stone-800"></tbody>
          </table>
        </div>
      </div>
    </div>
  </section>
</main>

<script>
async function api(path, opts={}) {
  const resp = await fetch(path, {credentials:'include', ...opts});
  if (resp.status === 401) { window.location = '/login'; return null; }
  return resp;
}

async function loadMe() {
  const r = await api('/api/me');
  if (!r) return;
  const d = await r.json();
  document.getElementById('me').textContent = `user=${d.username} role=${d.role}`;
}

async function loadRuns() {
  const r = await api('/api/runs?limit=200');
  if (!r) return;
  const d = await r.json();
  const rows = document.getElementById('rows');
  rows.innerHTML='';
    const allRuns = d.runs || [];
    const statusFilter = (document.getElementById('filter_status').value || '').trim().toLowerCase();
    const filteredRuns = statusFilter
        ? allRuns.filter(r => String(r.status || '').toLowerCase().includes(statusFilter))
        : allRuns;

    const runningCount = allRuns.filter(r => ['running', 'resuming'].includes(String(r.status || '').toLowerCase())).length;
    const queuedCount = allRuns.filter(r => ['queued', 'pending'].includes(String(r.status || '').toLowerCase())).length;
    const failedCount = allRuns.filter(r => ['failed', 'canceled', 'cancelled'].includes(String(r.status || '').toLowerCase())).length;
    document.getElementById('metric_total').textContent = String(allRuns.length);
    document.getElementById('metric_running').textContent = String(runningCount);
    document.getElementById('metric_queued').textContent = String(queuedCount);
    document.getElementById('metric_failed').textContent = String(failedCount);

    if (!filteredRuns.length) {
      rows.innerHTML = `
        <tr>
          <td colspan="6" class="px-6 py-14 text-center">
            <div class="mx-auto max-w-md rounded-3xl border border-dashed border-stone-700 bg-stone-900/40 px-6 py-10">
              <p class="text-sm font-medium uppercase tracking-[0.2em] text-stone-500">Sem resultados</p>
              <p class="mt-2 text-lg font-semibold text-stone-200">Nenhuma run encontrada para o filtro atual.</p>
              <p class="mt-2 text-sm text-stone-400">Ajuste o filtro ou crie uma nova execução para visualizar dados operacionais.</p>
            </div>
          </td>
        </tr>`;
      document.getElementById('status').textContent = `runs: 0/${allRuns.length}`;
      return;
    }

    for (const run of filteredRuns) {
    const tr = document.createElement('tr');
    tr.className = 'align-top text-stone-200 hover:bg-white/[0.02]';
    const created = new Date(run.created_at_ms).toLocaleString();
    let extra = '';
    let progressValue = 0;
    let sessionsOk = 0;
    let sessionsFailed = 0;
    try {
      if (run.params_json) {
        const pj = JSON.parse(run.params_json);
        if (pj.concurrency) extra += `<br/>concurrency=${escapeHtml(pj.concurrency)}`;
        if (pj.ramp_up_per_sec) extra += `<br/>ramp=${escapeHtml(pj.ramp_up_per_sec)}/s`;
        if (pj.speed) extra += `<br/>speed=${escapeHtml(pj.speed)}`;
      }
      if (run.metrics_json) {
        const mj = JSON.parse(run.metrics_json);
        progressValue = Number(mj.last_seq_global_applied || 0);
        sessionsOk = Number(mj.sessions_success || 0);
        sessionsFailed = Number(mj.sessions_failed || 0);
        extra += `<br/>progress=${escapeHtml(progressValue)}`;
        extra += `<br/>sess_ok=${escapeHtml(sessionsOk)} fail=${escapeHtml(sessionsFailed)}`;
      }
    } catch(e) {}
    const params = `<div class="font-mono text-xs leading-6 text-stone-400">log_dir=${escapeHtml(run.log_dir)}<br/>target=${escapeHtml(run.target_user)}@${escapeHtml(run.target_host)}<br/>mode=${escapeHtml(run.mode)}${extra}</div>`;
    const statusValue = String(run.status || '').toLowerCase();
    const statusClass = statusValue === 'running' || statusValue === 'resuming'
      ? 'border-emerald-300/20 bg-emerald-300/10 text-emerald-100'
      : statusValue === 'queued' || statusValue === 'pending'
      ? 'border-amber-300/20 bg-amber-300/10 text-amber-100'
      : statusValue === 'failed' || statusValue === 'canceled' || statusValue === 'cancelled'
      ? 'border-rose-300/20 bg-rose-300/10 text-rose-100'
      : 'border-sky-300/20 bg-sky-300/10 text-sky-100';
    const progressPercent = Math.max(0, Math.min(100, progressValue > 0 ? progressValue % 101 : 0));
    const progress = `
      <div class="min-w-[170px]">
        <div class="flex items-center justify-between text-xs text-stone-400">
          <span>seq ${escapeHtml(progressValue)}</span>
          <span>${escapeHtml(progressPercent)}%</span>
        </div>
        <div class="mt-2 h-2 overflow-hidden rounded-full bg-stone-800">
          <div class="h-full rounded-full bg-gradient-to-r from-amber-400 via-orange-400 to-rose-400" style="width:${progressPercent}%"></div>
        </div>
        <div class="mt-2 flex gap-3 text-[11px] uppercase tracking-[0.14em] text-stone-500">
          <span>ok ${escapeHtml(sessionsOk)}</span>
          <span>fail ${escapeHtml(sessionsFailed)}</span>
        </div>
      </div>`;
    const actions = `
      <div class="flex flex-wrap gap-2">
        <button class="rounded-lg border border-emerald-400/20 bg-emerald-400/10 px-3 py-1.5 text-xs font-medium text-emerald-200 transition hover:bg-emerald-400/20" onclick="startRun(${run.id})">start</button>
        <button class="rounded-lg border border-amber-400/20 bg-amber-400/10 px-3 py-1.5 text-xs font-medium text-amber-200 transition hover:bg-amber-400/20" onclick="pauseRun(${run.id})">pause</button>
        <button class="rounded-lg border border-sky-400/20 bg-sky-400/10 px-3 py-1.5 text-xs font-medium text-sky-200 transition hover:bg-sky-400/20" onclick="resumeRun(${run.id})">resume</button>
        <button class="rounded-lg border border-rose-400/20 bg-rose-400/10 px-3 py-1.5 text-xs font-medium text-rose-200 transition hover:bg-rose-400/20" onclick="cancelRun(${run.id})">cancel</button>
        <button class="rounded-lg border border-violet-400/20 bg-violet-400/10 px-3 py-1.5 text-xs font-medium text-violet-200 transition hover:bg-violet-400/20" onclick="retryRun(${run.id})">retry</button>
      </div>
    `;
    tr.innerHTML = `
      <td class="px-4 py-4"><code class="rounded-lg bg-stone-800 px-2 py-1 text-amber-200">${run.id}</code></td>
      <td class="px-4 py-4"><span class="inline-flex rounded-full border px-3 py-1 text-xs font-medium ${statusClass}">${escapeHtml(run.status)}</span></td>
      <td class="px-4 py-4 text-stone-300">${created}</td>
      <td class="px-4 py-4">${progress}</td>
      <td class="px-4 py-4">${params}</td>
      <td class="px-4 py-4">${actions}</td>
    `;
    rows.appendChild(tr);
  }
    document.getElementById('status').textContent = `runs: ${filteredRuns.length}/${allRuns.length}`;
}

function escapeHtml(s) {
  return String(s).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;');
}

async function createRun() {
  const mode = document.getElementById('mode').value;
  const params = {};
  if (mode === 'parallel-sessions') {
    const c = parseInt(document.getElementById('concurrency').value || '0', 10);
    const ramp = parseFloat(document.getElementById('ramp').value || '1');
    const speed = parseFloat(document.getElementById('speed').value || '1');
    const jitter = parseInt(document.getElementById('jitter').value || '0', 10);
    const pool = (document.getElementById('user_pool').value || '').split(',').map(s=>s.trim()).filter(Boolean);
    const onm = document.getElementById('on_mismatch').value;
    if (isFinite(c) && c > 0) params.concurrency = c;
    if (isFinite(ramp) && ramp > 0) params.ramp_up_per_sec = ramp;
    if (isFinite(speed) && speed > 0) params.speed = speed;
    if (isFinite(jitter) && jitter >= 0) params.jitter_ms = jitter;
    if (pool.length) params.target_user_pool = pool;
    params.on_checkpoint_mismatch = onm;
  }
  const payload = {
    log_dir: document.getElementById('log_dir').value,
    target_host: document.getElementById('target_host').value,
    target_user: document.getElementById('target_user').value,
    target_command: document.getElementById('target_cmd').value,
    mode,
    params
  };
  const r = await api('/api/runs', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(payload)});
  if (!r) return;
  await loadRuns();
}

async function startRun(id){ const r=await api(`/api/runs/${id}/start`, {method:'POST'}); if(!r)return; await loadRuns(); }
async function pauseRun(id){ const r=await api(`/api/runs/${id}/pause`, {method:'POST'}); if(!r)return; await loadRuns(); }
async function resumeRun(id){ const r=await api(`/api/runs/${id}/resume`, {method:'POST'}); if(!r)return; await loadRuns(); }
async function cancelRun(id){ const r=await api(`/api/runs/${id}/cancel`, {method:'POST'}); if(!r)return; await loadRuns(); }
async function retryRun(id){ const r=await api(`/api/runs/${id}/retry`, {method:'POST'}); if(!r)return; await loadRuns(); }

async function loadDetail(){
  const id = document.getElementById('detail_id').value.trim();
  if (!id) return;
  const rr = await api('/api/runs?limit=200');
  if (!rr) return;
  const dd = await rr.json();
  const run = (dd.runs || []).find(x => String(x.id) === String(id));
  document.getElementById('detail').textContent = JSON.stringify(run || {}, null, 2);
  const r2 = await api(`/api/runs/${id}/events`);
  if (!r2) return;
  const evs = await r2.json();
  document.getElementById('events').textContent = JSON.stringify(evs.events || [], null, 2);
}

async function logout(){
  await fetch('/api/logout', {method:'POST', credentials:'include'});
  window.location='/login';
}

loadMe();
loadRuns();
setInterval(() => {
    const ar = document.getElementById('auto_refresh');
    if (ar && ar.checked) loadRuns();
}, 1000);
document.getElementById('filter_status').addEventListener('input', loadRuns);
</script>
</body></html>
"""


LOGIN_HTML = """<!doctype html>
<html><head><meta charset="utf-8"/><meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Dakota Calçados | Replay Control</title>
<link rel="icon" type="image/svg+xml" href="https://dakota.vtexassets.com/assets/vtex/assets-builder/dakota.dakota-theme/6.0.129/svg/logo-dakota___9e5024e768762611d1260e2e2d5e1aa5.svg" />
<script src="https://cdn.tailwindcss.com"></script>
<style>
  @keyframes fadeIn { from { opacity: 0; } to { opacity: 1; } }
  .fade-in { animation: fadeIn 0.5s ease-in; }
</style>
</head>
<body class="bg-[radial-gradient(circle_at_top,_rgba(251,191,36,0.14),_transparent_32%),linear-gradient(135deg,_#1c1917_0%,_#292524_42%,_#111827_100%)] flex items-center justify-center min-h-screen">
  <!-- Decorative elements -->
  <div class="fixed top-0 left-0 w-96 h-96 bg-amber-300/10 rounded-full blur-3xl"></div>
  <div class="fixed bottom-0 right-0 w-96 h-96 bg-orange-400/10 rounded-full blur-3xl"></div>
  
  <div class="relative w-full max-w-md mx-auto px-6 fade-in">
    <!-- Card Container -->
    <div class="bg-stone-950/65 backdrop-blur-md border border-stone-700/50 rounded-3xl shadow-[0_30px_80px_rgba(0,0,0,0.45)] p-8 space-y-8">
      <!-- Header -->
      <div class="text-center space-y-4">
        <div class="flex justify-center mb-2">
          <div class="bg-white rounded-2xl px-5 py-4 shadow-[0_12px_40px_rgba(0,0,0,0.28)] ring-1 ring-stone-200/80">
            <img
              src="https://dakota.vtexassets.com/assets/vtex/assets-builder/dakota.dakota-theme/6.0.129/svg/logo-dakota___9e5024e768762611d1260e2e2d5e1aa5.svg"
              alt="Dakota"
              class="h-7 w-auto"
              loading="eager"
              referrerpolicy="no-referrer"
            />
          </div>
        </div>
        <h1 class="text-3xl font-semibold tracking-[0.18em] uppercase text-stone-50">Replay Control</h1>
        <p class="text-stone-300 text-sm">Sistema interno de automação para Dakota Calçados</p>
      </div>
      
      <!-- Form -->
      <form id="loginForm" class="space-y-4">
        <!-- Username -->
        <div>
          <label class="block text-sm font-medium text-stone-200 mb-2">Usuário</label>
          <input id="u" type="text" placeholder="seu usuário" class="w-full px-4 py-3 bg-stone-900/70 border border-stone-700 rounded-xl text-stone-50 placeholder-stone-500 focus:outline-none focus:ring-2 focus:ring-amber-400/70 focus:border-amber-300 transition" required/>
        </div>
        
        <!-- Password -->
        <div>
          <label class="block text-sm font-medium text-stone-200 mb-2">Senha</label>
          <input id="p" type="password" placeholder="sua senha" class="w-full px-4 py-3 bg-stone-900/70 border border-stone-700 rounded-xl text-stone-50 placeholder-stone-500 focus:outline-none focus:ring-2 focus:ring-amber-400/70 focus:border-amber-300 transition" required/>
        </div>
        
        <!-- Error Message -->
        <div id="msg" class="hidden bg-red-500/10 border border-red-400/40 text-red-300 text-sm px-4 py-3 rounded-xl"></div>
        
        <!-- Submit Button -->
        <button type="button" onclick="go()" class="w-full mt-6 bg-gradient-to-r from-amber-400 via-orange-400 to-rose-400 hover:from-amber-300 hover:via-orange-300 hover:to-rose-300 text-stone-950 font-semibold py-3 px-4 rounded-xl transition transform hover:scale-[1.01] active:scale-[0.99] focus:outline-none focus:ring-2 focus:ring-amber-300/60 shadow-lg shadow-orange-950/30">Entrar</button>
      </form>
      
      <!-- Footer -->
      <div class="pt-6 border-t border-stone-700/50 space-y-2 text-center">
        <p class="text-stone-500 text-xs uppercase tracking-[0.18em]">Desenvolvido por</p>
        <div class="flex justify-center text-sm">
          <a href="https://www.results.com.br/" target="_blank" class="text-amber-200 hover:text-amber-100 transition font-medium">Results</a>
        </div>
      </div>
    </div>
    
    <!-- Footer text -->
    <p class="text-center text-stone-500 text-xs mt-8">Sistema seguro • Acesso restrito</p>
  </div>
  
  <script>
    async function go(){
      const u = document.getElementById('u').value.trim();
      const p = document.getElementById('p').value;
      const msg = document.getElementById('msg');
      
      if (!u || !p) {
        msg.classList.remove('hidden');
        msg.textContent = 'Preencha todos os campos';
        return;
      }
      
      const r = await fetch('/api/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username: u, password: p }),
        credentials: 'include'
      });
      
      if (r.status === 200) {
        window.location = '/';
        return;
      }
      
      msg.classList.remove('hidden');
      msg.textContent = 'Usuário ou senha inválidos';
    }
    
    // Allow Enter key to submit
    document.getElementById('loginForm').addEventListener('keypress', (e) => {
      if (e.key === 'Enter') go();
    });
  </script>
</body></html>
"""


def _read_json(req: BaseHTTPRequestHandler) -> dict:
    ln = int(req.headers.get("Content-Length") or "0")
    data = req.rfile.read(ln) if ln else b"{}"
    try:
        d = json.loads(data.decode("utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


class ControlServer(ThreadingHTTPServer):
    def __init__(self, addr, handler, *, db_path: str, cookie_secret: bytes, hmac_key: bytes):
        super().__init__(addr, handler)
        self.db_path = db_path
        self.cookie_secret = cookie_secret
        self.hmac_key = hmac_key
        self.db_pool = ConnectionPool(db_path, min_size=1, max_size=16)
        con = self.db_pool.acquire()
        try:
            init_db(con)
        finally:
            self.db_pool.release(con)
        self.runner = Runner(db_path, hmac_key)


class Handler(BaseHTTPRequestHandler):
    def _db(self):
        return self.server.db_pool.acquire()

    def _db_release(self, con):
        self.server.db_pool.release(con)

    def _set_cookie(self, name: str, value: str, max_age: int = 3600 * 12):
        c = SimpleCookie()
        c[name] = value
        c[name]["path"] = "/"
        c[name]["max-age"] = str(max_age)
        # internal HTTP (no TLS) by default; don't set secure automatically
        self.send_header("Set-Cookie", c.output(header="").strip())

    def _clear_cookie(self, name: str):
        c = SimpleCookie()
        c[name] = ""
        c[name]["path"] = "/"
        c[name]["max-age"] = "0"
        self.send_header("Set-Cookie", c.output(header="").strip())

    def _get_cookie(self, name: str) -> str | None:
        raw = self.headers.get("Cookie") or ""
        c = SimpleCookie()
        c.load(raw)
        if name not in c:
            return None
        return c[name].value

    def _auth(self):
      cv = self._get_cookie("dakota_session")
      if not cv:
        return None
      parsed = auth.verify_cookie(self.server.cookie_secret, cv)
      if not parsed:
        return None

      username, token, _exp = parsed
      token_hash = auth.sha256_hex(token.encode("utf-8"))
      con = self._db()
      try:
        row = query_one(
          con,
          "SELECT u.id,u.username,u.role,s.expires_at_ms "
          "FROM users u JOIN sessions s ON s.user_id=u.id "
          "WHERE u.username=? AND s.token_hash=? "
          "ORDER BY s.id DESC LIMIT 1",
          (username, token_hash),
        )
        if not row:
          return None
        if int(row["expires_at_ms"]) < int(time.time() * 1000):
          return None
        return {"id": int(row["id"]), "username": row["username"], "role": row["role"]}
      finally:
        self._db_release(con)

    def _require(self, roles: set[str] | None = None):
        u = self._auth()
        if not u:
            self.send_response(HTTPStatus.UNAUTHORIZED)
            self.end_headers()
            return None
        if roles and u["role"] not in roles:
            self.send_response(HTTPStatus.FORBIDDEN)
            self.end_headers()
            return None
        return u

    def do_GET(self):
        p = urlparse(self.path)
        if p.path == "/login":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(LOGIN_HTML.encode("utf-8"))
            return
        if p.path == "/":
            u = self._auth()
            if not u:
                # Redirect to login instead of 401
                self.send_response(302)
                self.send_header("Location", "/login")
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(INDEX_HTML.encode("utf-8"))
            return
        if p.path == "/api/me":
            u = self._require()
            if not u:
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps(u).encode("utf-8"))
            return
        if p.path == "/api/runs":
            u = self._require()
            if not u:
                return
            qs = parse_qs(p.query or "")
            limit = int((qs.get("limit") or ["200"])[0])
            limit = max(1, min(limit, 2000))
            con = self._db()
            try:
                rows = query_all(con, "SELECT * FROM replay_runs ORDER BY id DESC LIMIT ?", (limit,))
                runs = [dict(r) for r in rows]
            finally:
                self._db_release(con)
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps({"runs": runs}, ensure_ascii=False).encode("utf-8"))
            return

        if p.path.startswith("/api/runs/") and p.path.endswith("/events"):
            u = self._require()
            if not u:
                return
            parts = p.path.split("/")
            if len(parts) < 5:
                self.send_response(404)
                self.end_headers()
                return
            run_id = int(parts[3])
            con = self._db()
            try:
                rows = query_all(con, "SELECT * FROM replay_run_events WHERE run_id=? ORDER BY id DESC LIMIT 200", (run_id,))
                evs = [dict(r) for r in rows]
            finally:
                self._db_release(con)
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps({"events": evs}, ensure_ascii=False).encode("utf-8"))
            return

        if p.path == "/api/users":
            u = self._require(roles={"admin"})
            if not u:
                return
            con = self._db()
            try:
                rows = query_all(con, "SELECT id,username,role,created_at_ms FROM users ORDER BY id", ())
                users = [dict(r) for r in rows]
            finally:
                self._db_release(con)
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps({"users": users}, ensure_ascii=False).encode("utf-8"))
            return

        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        p = urlparse(self.path)
        if p.path == "/api/login":
            body = _read_json(self)
            username = str(body.get("username") or "")
            password = str(body.get("password") or "")
            con = self._db()
            try:
                row = query_one(con, "SELECT id,username,role,password_hash FROM users WHERE username=?", (username,))
                if not row or not auth.verify_password(password, row["password_hash"]):
                    self.send_response(401)
                    self.end_headers()
                    return
                token = auth.new_session_token()
                token_hash = auth.sha256_hex(token.encode("utf-8"))
                exp = int(time.time() * 1000) + 12 * 3600 * 1000
                con.execute("INSERT INTO sessions(user_id, token_hash, created_at_ms, expires_at_ms) VALUES(?,?,?,?)",
                            (int(row["id"]), token_hash, now_ms(), exp))
                cookie = auth.sign_cookie(self.server.cookie_secret, username, token, exp)
                self.send_response(200)
                self._set_cookie("dakota_session", cookie)
                self.end_headers()
                return
            finally:
                self._db_release(con)

        if p.path == "/api/logout":
            u = self._auth()
            self.send_response(200)
            self._clear_cookie("dakota_session")
            self.end_headers()
            return

        if p.path == "/api/runs":
            u = self._require(roles={"admin", "operator"})
            if not u:
                return
            body = _read_json(self)
            log_dir = str(body.get("log_dir") or "")
            target_host = str(body.get("target_host") or "")
            target_user = str(body.get("target_user") or "")
            target_command = str(body.get("target_command") or "")
            mode = str(body.get("mode") or "strict-global")
            params = body.get("params") if isinstance(body.get("params"), dict) else {}
            con = self._db()
            try:
                rid = create_run(con, u["id"], log_dir, target_host, target_user, target_command, mode)
                if params:
                    con.execute("UPDATE replay_runs SET params_json=? WHERE id=?", (json.dumps(params, ensure_ascii=False), rid))
            finally:
                self._db_release(con)
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps({"id": rid}).encode("utf-8"))
            return

        if p.path == "/api/users":
            u = self._require(roles={"admin"})
            if not u:
                return
            body = _read_json(self)
            username = str(body.get("username") or "")
            password = str(body.get("password") or "")
            role = str(body.get("role") or "")
            if role not in ("admin", "operator", "viewer"):
                self.send_response(400)
                self.end_headers()
                return
            ph = auth.pbkdf2_hash_password(password)
            con = self._db()
            try:
                con.execute(
                    "INSERT INTO users(username,password_hash,role,created_at_ms) VALUES(?,?,?,?)",
                    (username, ph, role, now_ms()),
                )
            finally:
                self._db_release(con)
            self.send_response(200)
            self.end_headers()
            return

        # run actions
        if p.path.startswith("/api/runs/"):
            u = self._require(roles={"admin", "operator"})
            if not u:
                return
            parts = p.path.split("/")
            if len(parts) < 5:
                self.send_response(404)
                self.end_headers()
                return
            run_id = int(parts[3])
            action = parts[4]
            con = self._db()
            try:
                if action == "start":
                    # mark running and start thread
                    con.execute("UPDATE replay_runs SET status='running' WHERE id=? AND status='queued'", (run_id,))
                    add_run_event(con, run_id, "api", "start solicitado", {"by": u["username"]})
                    self.server.runner.start_run_async(run_id)
                elif action == "pause":
                    pause_run(con, run_id)
                elif action == "resume":
                    resume_run(con, run_id)
                    self.server.runner.start_run_async(run_id)
                elif action == "cancel":
                    cancel_run(con, run_id)
                elif action == "retry":
                    nid = retry_run(con, run_id, created_by=u["id"])
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.end_headers()
                    self.wfile.write(json.dumps({"id": nid}).encode("utf-8"))
                    return
                else:
                    self.send_response(404)
                    self.end_headers()
                    return
            finally:
                self._db_release(con)
            self.send_response(200)
            self.end_headers()
            return

        self.send_response(404)
        self.end_headers()

    def log_message(self, fmt, *args):
        return


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--listen", default="127.0.0.1:8090")
    ap.add_argument("--db", default="")
    ap.add_argument("--cookie-secret-file", required=True)
    ap.add_argument("--hmac-key-file", required=True)
    ap.add_argument("--bootstrap-admin", default="")  # username:password
    args = ap.parse_args()

    host, port_s = args.listen.rsplit(":", 1)
    port = int(port_s)
    db_path = args.db or (Path(__file__).resolve().parents[1] / "state" / "replay.db")
    db_path = str(db_path)

    cookie_secret = Path(args.cookie_secret_file).read_bytes().strip()
    hmac_key = Path(args.hmac_key_file).read_bytes().strip()
    if not cookie_secret:
        raise SystemExit("cookie secret vazio")
    if not hmac_key:
        raise SystemExit("hmac key vazio")

    con = connect(db_path)
    init_db(con)
    if args.bootstrap_admin:
        if ":" not in args.bootstrap_admin:
            raise SystemExit("--bootstrap-admin deve ser username:password")
        u, p = args.bootstrap_admin.split(":", 1)
        ph = auth.pbkdf2_hash_password(p)
        try:
            con.execute(
                "INSERT INTO users(username,password_hash,role,created_at_ms) VALUES(?,?, 'admin', ?)",
                (u, ph, now_ms()),
            )
            print(f"Admin criado: {u}")
        except sqlite3.IntegrityError:
            print("Admin já existe")
    con.close()

    srv = ControlServer((host, port), Handler, db_path=db_path, cookie_secret=cookie_secret, hmac_key=hmac_key)
    print(f"listening on http://{host}:{port}")
    srv.serve_forever()


if __name__ == "__main__":
    main()


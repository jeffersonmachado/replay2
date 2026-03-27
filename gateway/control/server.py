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
<title>Replay Control</title>
<style>
body{font-family:system-ui,-apple-system,Segoe UI,Roboto,Ubuntu; margin:16px;}
header{display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap;}
table{width:100%;border-collapse:collapse;margin-top:12px;}
th,td{border-bottom:1px solid #e5e7eb;padding:8px;text-align:left;vertical-align:top;}
th{position:sticky;top:0;background:#fff;}
code{background:#f3f4f6;padding:2px 4px;border-radius:4px;}
.row{display:flex;gap:8px;flex-wrap:wrap;align-items:center;}
input,select{padding:6px 8px;}
button{padding:6px 10px;}
.pill{display:inline-block;padding:2px 8px;border-radius:999px;background:#eef2ff;}
.muted{color:#6b7280;}
</style>
</head>
<body>
<header>
  <h2 style="margin:0">Replay Control</h2>
  <div class="row">
    <span class="muted" id="me"></span>
    <button onclick="logout()">Logout</button>
  </div>
</header>

<div class="row" style="margin-top:12px">
  <input id="log_dir" placeholder="log_dir (ex: /var/log/dakota-gateway)" size="40"/>
  <input id="target_host" placeholder="target_host" size="18"/>
  <input id="target_user" placeholder="target_user" size="12"/>
  <input id="target_cmd" placeholder="target_command (opcional)" size="18"/>
  <select id="mode">
    <option value="strict-global">strict-global</option>
    <option value="parallel-sessions">parallel-sessions</option>
  </select>
  <input id="concurrency" placeholder="concurrency (ex: 20)" size="14"/>
  <input id="ramp" placeholder="ramp-up/s (ex: 2)" size="12"/>
  <input id="speed" placeholder="speed (ex: 4)" size="10"/>
  <input id="jitter" placeholder="jitter ms" size="10"/>
  <input id="user_pool" placeholder="user pool csv (replay01,replay02,...)" size="28"/>
  <select id="on_mismatch">
    <option value="continue">on mismatch: continue</option>
    <option value="fail-fast">on mismatch: fail-fast</option>
  </select>
  <button onclick="createRun()">Criar run</button>
  <span class="muted" id="status"></span>
</div>

<div class="row" style="margin-top:8px">
    <input id="filter_status" placeholder="filtrar status (running, failed...)" size="30"/>
    <label><input id="auto_refresh" type="checkbox" checked/> auto-refresh</label>
</div>

<table>
  <thead><tr>
    <th style="width:80px">id</th>
    <th style="width:130px">status</th>
    <th style="width:170px">created_at</th>
    <th>params</th>
    <th style="width:240px">ações</th>
  </tr></thead>
  <tbody id="rows"></tbody>
</table>

<h3>Detalhe</h3>
<div class="row">
  <input id="detail_id" placeholder="run id" size="8"/>
  <button onclick="loadDetail()">Carregar</button>
</div>
<pre id="detail" class="muted" style="white-space:pre-wrap"></pre>
<pre id="events" class="muted" style="white-space:pre-wrap"></pre>

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
    const statusFilter = (document.getElementById('filter_status').value || '').trim().toLowerCase();
    const filteredRuns = statusFilter
        ? d.runs.filter(r => String(r.status || '').toLowerCase().includes(statusFilter))
        : d.runs;

    for (const run of filteredRuns) {
    const tr = document.createElement('tr');
    const created = new Date(run.created_at_ms).toLocaleString();
    let extra = '';
    try {
      if (run.params_json) {
        const pj = JSON.parse(run.params_json);
        if (pj.concurrency) extra += `<br/>concurrency=${escapeHtml(pj.concurrency)}`;
        if (pj.ramp_up_per_sec) extra += `<br/>ramp=${escapeHtml(pj.ramp_up_per_sec)}/s`;
        if (pj.speed) extra += `<br/>speed=${escapeHtml(pj.speed)}`;
      }
      if (run.metrics_json) {
        const mj = JSON.parse(run.metrics_json);
        extra += `<br/>progress=${escapeHtml(mj.last_seq_global_applied||0)}`;
        extra += `<br/>sess_ok=${escapeHtml(mj.sessions_success||0)} fail=${escapeHtml(mj.sessions_failed||0)}`;
      }
    } catch(e) {}
    const params = `<div class="muted mono">log_dir=${escapeHtml(run.log_dir)}<br/>target=${escapeHtml(run.target_user)}@${escapeHtml(run.target_host)}<br/>mode=${escapeHtml(run.mode)}${extra}</div>`;
    const actions = `
      <button onclick="startRun(${run.id})">start</button>
      <button onclick="pauseRun(${run.id})">pause</button>
      <button onclick="resumeRun(${run.id})">resume</button>
      <button onclick="cancelRun(${run.id})">cancel</button>
      <button onclick="retryRun(${run.id})">retry</button>
    `;
    tr.innerHTML = `
      <td><code>${run.id}</code></td>
      <td><span class="pill">${escapeHtml(run.status)}</span></td>
      <td>${created}</td>
      <td>${params}</td>
      <td>${actions}</td>
    `;
    rows.appendChild(tr);
  }
    document.getElementById('status').textContent = `runs: ${filteredRuns.length}/${d.runs.length}`;
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
<title>Dakota Replay - Login</title>
<script src="https://cdn.tailwindcss.com"></script>
<style>
  @keyframes fadeIn { from { opacity: 0; } to { opacity: 1; } }
  .fade-in { animation: fadeIn 0.5s ease-in; }
</style>
</head>
<body class="bg-gradient-to-br from-slate-900 via-slate-800 to-slate-900 flex items-center justify-center min-h-screen">
  <!-- Decorative elements -->
  <div class="fixed top-0 left-0 w-96 h-96 bg-blue-500/10 rounded-full blur-3xl"></div>
  <div class="fixed bottom-0 right-0 w-96 h-96 bg-blue-400/10 rounded-full blur-3xl"></div>
  
  <div class="relative w-full max-w-md mx-auto px-6 fade-in">
    <!-- Card Container -->
    <div class="bg-slate-800/50 backdrop-blur-sm border border-slate-700/50 rounded-2xl shadow-2xl p-8 space-y-8">
      <!-- Header -->
      <div class="text-center space-y-4">
        <div class="flex justify-center mb-2">
          <div class="w-12 h-12 bg-gradient-to-br from-blue-400 to-blue-600 rounded-lg flex items-center justify-center">
            <svg class="w-6 h-6 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 10V3L4 14h7v7l9-11h-7z"></path>
            </svg>
          </div>
        </div>
        <h1 class="text-3xl font-bold text-white">Dakota Replay</h1>
        <p class="text-slate-400 text-sm">Sistema de Automação de Testes</p>
      </div>
      
      <!-- Form -->
      <form id="loginForm" class="space-y-4">
        <!-- Username -->
        <div>
          <label class="block text-sm font-medium text-slate-300 mb-2">Usuário</label>
          <input id="u" type="text" placeholder="seu usuário" class="w-full px-4 py-2 bg-slate-700/50 border border-slate-600 rounded-lg text-white placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-blue-500 transition" required/>
        </div>
        
        <!-- Password -->
        <div>
          <label class="block text-sm font-medium text-slate-300 mb-2">Senha</label>
          <input id="p" type="password" placeholder="sua senha" class="w-full px-4 py-2 bg-slate-700/50 border border-slate-600 rounded-lg text-white placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-blue-500 transition" required/>
        </div>
        
        <!-- Error Message -->
        <div id="msg" class="hidden bg-red-500/10 border border-red-500/50 text-red-400 text-sm px-4 py-2 rounded-lg"></div>
        
        <!-- Submit Button -->
        <button type="button" onclick="go()" class="w-full mt-6 bg-gradient-to-r from-blue-500 to-blue-600 hover:from-blue-600 hover:to-blue-700 text-white font-semibold py-2 px-4 rounded-lg transition transform hover:scale-105 active:scale-95 focus:outline-none focus:ring-2 focus:ring-blue-500/50">Entrar</button>
      </form>
      
      <!-- Footer -->
      <div class="pt-6 border-t border-slate-700/50 space-y-2 text-center">
        <p class="text-slate-500 text-xs">Desenvolvido por</p>
        <div class="flex justify-center gap-2 text-xs">
          <a href="https://www.dakota.com.br/" target="_blank" class="text-blue-400 hover:text-blue-300 transition">Dakota Calçados</a>
          <span class="text-slate-600">×</span>
          <a href="https://www.results.com.br/" target="_blank" class="text-blue-400 hover:text-blue-300 transition">Results</a>
        </div>
      </div>
    </div>
    
    <!-- Footer text -->
    <p class="text-center text-slate-500 text-xs mt-8">Sistema seguro • Acesso restrito</p>
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
        username, token, exp = parsed
        con = self._db()
        try:
            row = query_one(con, "SELECT u.id,u.username,u.role,s.token_hash,s.expires_at_ms FROM users u JOIN sessions s ON s.user_id=u.id WHERE u.username=?",
                            (username,))
            if not row:
                return None
            if int(row["expires_at_ms"]) < int(time.time() * 1000):
                return None
            if row["token_hash"] != auth.sha256_hex(token.encode("utf-8")):
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


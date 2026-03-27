#!/usr/bin/env python3
import argparse
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs


INDEX_HTML = """<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>dakota-replay2 dashboard</title>
  <style>
    body { font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Ubuntu, Cantarell, Noto Sans, Arial; margin: 16px; }
    header { display: flex; gap: 12px; align-items: baseline; flex-wrap: wrap; }
    code { background: #f3f4f6; padding: 2px 4px; border-radius: 4px; }
    table { width: 100%; border-collapse: collapse; margin-top: 12px; }
    th, td { border-bottom: 1px solid #e5e7eb; padding: 8px; text-align: left; vertical-align: top; }
    th { position: sticky; top: 0; background: white; }
    .muted { color: #6b7280; }
    .pill { display: inline-block; padding: 2px 8px; border-radius: 999px; background: #eef2ff; }
    .row { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
    input { padding: 6px 8px; }
    button { padding: 6px 10px; }
    .mono { font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', 'Courier New', monospace; }
    details pre { white-space: pre-wrap; word-break: break-word; }
  </style>
</head>
<body>
  <header>
    <h2 style="margin:0">dakota-replay2 dashboard</h2>
    <span class="muted">fonte:</span> <code id="src"></code>
  </header>

  <div class="row" style="margin-top: 10px">
    <label>Filtro type:</label>
    <input id="filterType" placeholder="ex: unknown_screen" />
    <label>Limite:</label>
    <input id="limit" value="200" size="6" />
    <button id="refresh">Atualizar</button>
    <span class="muted" id="status"></span>
  </div>

  <table>
    <thead>
      <tr>
        <th style="width: 170px">ts_ms</th>
        <th style="width: 110px">level</th>
        <th style="width: 220px">type</th>
        <th>dados</th>
      </tr>
    </thead>
    <tbody id="rows"></tbody>
  </table>

<script>
const srcEl = document.getElementById('src');
const statusEl = document.getElementById('status');
const rowsEl = document.getElementById('rows');
const filterTypeEl = document.getElementById('filterType');
const limitEl = document.getElementById('limit');
let liveSource = null;

async function load() {
  const type = filterTypeEl.value.trim();
  const limit = parseInt(limitEl.value || '200', 10);
  const qs = new URLSearchParams();
  qs.set('limit', String(isFinite(limit) ? limit : 200));
  if (type) qs.set('type', type);
  statusEl.textContent = 'carregando...';
  const resp = await fetch('/api/events?' + qs.toString());
  const data = await resp.json();
  srcEl.textContent = data.source;
  statusEl.textContent = `eventos: ${data.events.length} (total_buffer=${data.buffer_size})`;

  rowsEl.innerHTML = '';
  for (const ev of data.events) {
    const tr = document.createElement('tr');
    const ts = ev.ts_ms ?? '';
    const lvl = ev.level ?? '';
    const type = ev.type ?? '';

    tr.innerHTML = `
      <td class="mono">${escapeHtml(String(ts))}</td>
      <td><span class="pill">${escapeHtml(String(lvl))}</span></td>
      <td class="mono">${escapeHtml(String(type))}</td>
      <td>
        <details>
          <summary class="muted">ver</summary>
          <pre class="mono">${escapeHtml(JSON.stringify(ev, null, 2))}</pre>
        </details>
      </td>
    `;
    rowsEl.appendChild(tr);
  }
}

function escapeHtml(s) {
  return s.replaceAll('&', '&amp;')
          .replaceAll('<', '&lt;')
          .replaceAll('>', '&gt;')
          .replaceAll('"', '&quot;')
          .replaceAll("'", '&#39;');
}

document.getElementById('refresh').addEventListener('click', load);
function connectLive() {
  if (liveSource) liveSource.close();
  const type = filterTypeEl.value.trim();
  const limit = parseInt(limitEl.value || '200', 10);
  const qs = new URLSearchParams();
  qs.set('limit', String(isFinite(limit) ? limit : 200));
  if (type) qs.set('type', type);
  liveSource = new EventSource('/api/events/stream?' + qs.toString());
  liveSource.onmessage = (evt) => {
    const data = JSON.parse(evt.data);
    srcEl.textContent = data.source;
    statusEl.textContent = `eventos: ${data.events.length} (total_buffer=${data.buffer_size})`;
    rowsEl.innerHTML = '';
    for (const ev of data.events) {
      const tr = document.createElement('tr');
      const ts = ev.ts_ms ?? '';
      const lvl = ev.level ?? '';
      const type = ev.type ?? '';
      tr.innerHTML = `
        <td class="mono">${escapeHtml(String(ts))}</td>
        <td><span class="pill">${escapeHtml(String(lvl))}</span></td>
        <td class="mono">${escapeHtml(String(type))}</td>
        <td>
          <details>
            <summary class="muted">ver</summary>
            <pre class="mono">${escapeHtml(JSON.stringify(ev, null, 2))}</pre>
          </details>
        </td>
      `;
      rowsEl.appendChild(tr);
    }
  };
}

filterTypeEl.addEventListener('change', connectLive);
limitEl.addEventListener('change', connectLive);
document.getElementById('refresh').addEventListener('click', () => {
  load();
  connectLive();
});
load();
connectLive();
</script>
</body>
</html>
"""


class EventBuffer:
    def __init__(self, max_events: int = 5000):
        self.max_events = max_events
        self._lock = threading.Lock()
        self._events = []

    def add(self, ev: dict):
        with self._lock:
            self._events.append(ev)
            if len(self._events) > self.max_events:
                self._events = self._events[-self.max_events :]

    def snapshot(self):
        with self._lock:
            return list(self._events)

    def size(self):
        with self._lock:
            return len(self._events)


def tail_jsonl(path: str, buf: EventBuffer, stop_evt: threading.Event):
    # Tailing simples: reabre se o arquivo for rotacionado.
    last_inode = None
    f = None
    while not stop_evt.is_set():
        try:
            st = os.stat(path)
            inode = (st.st_ino, st.st_dev)
            if inode != last_inode:
                if f:
                    f.close()
                f = open(path, "r", encoding="utf-8", errors="replace")
                f.seek(0, os.SEEK_END)
                last_inode = inode
        except FileNotFoundError:
            time.sleep(0.25)
            continue

        line = f.readline()
        if not line:
            time.sleep(0.1)
            continue
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
            if isinstance(ev, dict):
                buf.add(ev)
        except Exception:
            # Ignora linhas inválidas
            pass


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        p = urlparse(self.path)
        if p.path == "/":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(INDEX_HTML.encode("utf-8"))
            return

        if p.path == "/api/events":
            qs = parse_qs(p.query or "")
            limit = int((qs.get("limit") or ["200"])[0])
            limit = max(1, min(limit, 2000))
            typ = (qs.get("type") or [""])[0].strip()

            events = self.server.buf.snapshot()
            if typ:
                events = [e for e in events if e.get("type") == typ]
            events = events[-limit:]

            payload = {
                "source": self.server.source,
                "buffer_size": self.server.buf.size(),
                "events": events,
            }
            out = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(out)
            return

        if p.path == "/api/events/stream":
            qs = parse_qs(p.query or "")
            limit = int((qs.get("limit") or ["200"])[0])
            limit = max(1, min(limit, 2000))
            typ = (qs.get("type") or [""])[0].strip()

            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.end_headers()

            last_payload = ""
            while True:
                events = self.server.buf.snapshot()
                if typ:
                    events = [e for e in events if e.get("type") == typ]
                events = events[-limit:]
                payload = json.dumps(
                    {
                        "source": self.server.source,
                        "buffer_size": self.server.buf.size(),
                        "events": events,
                    },
                    ensure_ascii=False,
                )
                if payload != last_payload:
                    try:
                        self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
                        self.wfile.flush()
                        last_payload = payload
                    except Exception:
                        break
                time.sleep(0.5)
            return

        self.send_response(404)
        self.end_headers()

    def log_message(self, fmt, *args):
        # Silencia logs HTTP padrão
        return


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--events-file", required=True, help="Caminho do JSONL produzido pela engine")
    ap.add_argument("--listen", default="127.0.0.1:8080", help="host:port (default: 127.0.0.1:8080)")
    ap.add_argument("--max-events", type=int, default=5000)
    args = ap.parse_args()

    host, port_s = args.listen.rsplit(":", 1)
    port = int(port_s)

    buf = EventBuffer(max_events=args.max_events)
    stop_evt = threading.Event()
    t = threading.Thread(target=tail_jsonl, args=(args.events_file, buf, stop_evt), daemon=True)
    t.start()

    srv = ThreadingHTTPServer((host, port), Handler)
    srv.buf = buf
    srv.source = args.events_file

    try:
        srv.serve_forever()
    finally:
        stop_evt.set()


if __name__ == "__main__":
    main()


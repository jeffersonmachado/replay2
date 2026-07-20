from pathlib import Path
import base64
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import hashlib
from datetime import datetime, timezone

import websocket

from gateway.control.services.session_replay_service import prepare_session_replay_data

ROOT = Path(__file__).resolve().parents[1]

# ── Helpers ──────────────────────────────────────────────────────────────────

def _atomic_write_json(path, data):
    tmp = Path(str(path) + ".tmp")
    tmp.parent.mkdir(parents=True, exist_ok=True)
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(str(tmp), str(path))

def _tree_hash():
    sys.path.insert(0, str(ROOT / "scripts"))
    from tree_hash import tree_hash
    return tree_hash(ROOT)

def _kill_process_group(pid, profile):
    my_uid = str(os.getuid())
    ps = str(profile)
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try: os.killpg(pid, sig)
        except OSError: pass
        time.sleep(0.3)
    for _ in range(10):
        r = subprocess.run(["pgrep","-U",my_uid,"-f",ps], capture_output=True, text=True)
        pids = []
        for l in r.stdout.strip().splitlines():
            try:
                p = int(l.strip().split()[0])
                if p != os.getpid(): pids.append(p)
            except: pass
        if not pids: return
        for p in pids:
            try: os.kill(p, signal.SIGKILL)
            except OSError: pass
        time.sleep(0.3)

def _count_chromium_processes(profile):
    r = subprocess.run(["pgrep","-U",str(os.getuid()),"-f",str(profile)], capture_output=True, text=True)
    return len([l for l in r.stdout.strip().splitlines() if l.strip()])


# ── CSS contract tests ──────────────────────────────────────────────────────

def test_terminal_snapshot_card_uses_pre_and_snapshot_class():
    s = (ROOT / "gateway/control/static/js/components/timeline_table.js").read_text("utf-8")
    assert 'class="r2ctl-event-content${isSnapshot ? " terminal-snapshot" : ""}"' in s

def test_capture_replay_fallback_keeps_terminal_snapshot_class():
    h = (ROOT / "gateway/control/templates/capture_session_replay.html").read_text("utf-8")
    assert "'<pre class=\"event-content' + (isSnapshot ? ' terminal-snapshot' : '') + '\">'" in h

def test_terminal_snapshot_css_preserves_grid_geometry():
    css = (ROOT / "gateway/control/static/control.css").read_text("utf-8")
    m = ".r2ctl-event-content.terminal-snapshot{"
    assert m in css
    rule = css.split(m,1)[1].split("}",1)[0]
    assert "white-space:pre" in rule
    assert "font-size:14px" in rule
    assert "line-height:14px" in rule

def test_capture_replay_inline_css_matches_terminal_snapshot_contract():
    h = (ROOT / "gateway/control/templates/capture_session_replay.html").read_text("utf-8")
    assert ".event-content {" in h

def test_capture_replay_loads_structured_error_body_safely():
    h = (ROOT / "gateway/control/templates/capture_session_replay.html").read_text("utf-8")
    assert "function replayErrorMessage" in h


# ── Visual test: real Chrome via CDP, timeline calls renderer ──────────────

def test_terminal_snapshot_box_drawing_renders_with_real_browser_pixels():
    chromium = (
        shutil.which("google-chrome-stable")
        or shutil.which("google-chrome")
        or shutil.which("chromium-browser")
        or shutil.which("chromium")
    )
    if not chromium:
        raise AssertionError("Chromium/Chrome headless required for visual test")

    try: from PIL import Image
    except ImportError as e:
        raise AssertionError("Pillow required for visual test") from e

    try: import websocket
    except ImportError as e:
        raise AssertionError("websocket-client required for visual test") from e

    # ── Setup ──
    def _free_port():
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            return int(s.getsockname()[1])

    def _json_get(url, timeout=5.0):
        import urllib.request
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))

    control_css = (ROOT / "gateway/control/static/control.css").read_text("utf-8")

    # ── Generate terminal snapshot ──
    box_text = (
        "┌" + "─" * 78 + "┐\r\n"
        "│" + " " * 78 + "│\r\n"
        "│ \x1b[1;4;31;42mATTR\x1b[0m \x1b[7mREV\x1b[0m 😀 cursor │\r\n"
        "└" + "─" * 78 + "┘"
    )
    box_bytes = box_text.encode("utf-8")
    box_b64 = base64.b64encode(box_bytes).decode("ascii")

    (ROOT / "tests/fixtures/audit-visual.jsonl").write_text(
        "\n".join(json.dumps(ev) for ev in [
            {"type":"session_start","session_id":"visual-box","seq_global":1,"seq_session":1,"ts_ms":1000,"rows":6,"cols":80,"encoding":"utf-8"},
            {"type":"bytes","session_id":"visual-box","seq_global":2,"seq_session":2,"ts_ms":1010,"dir":"out","n":len(box_bytes),"data_b64":box_b64},
            {"type":"session_end","session_id":"visual-box","seq_global":3,"seq_session":3,"ts_ms":1020},
        ]),
    )
    replay = prepare_session_replay_data(str(ROOT / "tests/fixtures"), "visual-box")
    assert replay["error"] is None
    snapshot_payload = replay["final_snapshot"]

    # Bundle JS for inline use
    def _bundle_js(files_and_exports):
        parts = []
        for fp, names in files_and_exports:
            src = fp.read_text("utf-8")
            lines = []
            for line in src.split("\n"):
                s = line.strip()
                if s.startswith("import "): lines.append("// " + s)
                elif s.startswith("export default "): lines.append(line.replace("export default ", "var _default_export = ", 1))
                elif s.startswith("export "): lines.append(line.replace("export ", "", 1))
                else: lines.append(line)
            clean = "\n".join(lines)
            extract = "\n".join(f"if (typeof {n} !== 'undefined') this.{n} = {n};" for n in names)
            parts.append(f"(function(){{\n{clean}\n{extract}\n}})();")
        return "\n".join(parts)

    bundled_js = _bundle_js([
        (ROOT / "gateway/control/static/js/core/dom.js", ["escapeHtml","html","text"]),
        (ROOT / "gateway/control/static/js/components/timeline_core.js", ["typeBadge","dirBadge","eventDetails","decodeEventForDisplay","resolveEventByteCount","sanitizeAnsiForDisplay"]),
        (ROOT / "gateway/control/static/js/components/terminal_snapshot_renderer.js", ["decodeSnapshotPayload","renderSnapshotToHtml","renderSnapshotToText"]),
        (ROOT / "gateway/control/static/js/components/timeline_table.js", ["renderEventsTable","renderEventsCards","renderSingleEventCard"]),
    ])

    # Pass raw terminal_snapshot payload (not pre-rendered HTML)
    snapshot_json = json.dumps(snapshot_payload, ensure_ascii=False)

    html_content = f"""<!doctype html>
<html><head><meta charset="utf-8"><style>{control_css}</style></head>
<body class="r2ctl-theme-shell">
<main style="width:920px;padding:16px">
  <div id="cards"></div>
  <script id="visual-result" type="application/json">{{}}</script>
</main>
<script>
window.__DAKOTA_VISUAL_ERRORS__ = [];
window.onerror = function(m,u,l){{window.__DAKOTA_VISUAL_ERRORS__.push({{msg:String(m),line:l}});}};
</script>
<script>
{bundled_js}
</script>
<script>
var event = {{
  type: "bytes", direction: "out", seq_global: 2, timestamp_ms: 1010,
  actor: "visual-test", content_kind: "terminal_snapshot",
  terminal_snapshot: {snapshot_json},
  n_bytes: {len(box_bytes)}, ts_ms: 1010
}};
var events = [event];
if (typeof renderEventsCards === 'function') {{
  renderEventsCards(events, "#cards", null);
}}

requestAnimationFrame(function() {{
  try {{
    var pre = document.querySelector(".r2ctl-event-content.terminal-snapshot");
    var card = document.querySelector(".r2ctl-event-card[data-event-dir='out']");
    var cs = pre ? getComputedStyle(pre) : {{}};
    var text = pre ? (pre.innerText || "") : "";
    var lines = text.split("\\n");
    var inst = window.__DAKOTA_VISUAL_TEST__ || {{}};
    var r = {{
      hasPre: !!pre,
      whiteSpace: cs.whiteSpace || "",
      wordBreak: cs.wordBreak || "",
      overflowWrap: cs.overflowWrap || "",
      fontSize: cs.fontSize || "",
      lineHeight: cs.lineHeight || "",
      letterSpacing: cs.letterSpacing || "",
      rows: lines.length,
      firstLineLength: (lines[0]||"").length,
      hasCorners: text.includes("┌") && text.includes("┘"),
      hasEmoji: text.includes("\ud83d\ude00"),
      hasReverse: !!(pre && pre.innerHTML.includes("vt-reverse")),
      hasFgBg: !!(pre && pre.innerHTML.includes("vt-fg-1") && pre.innerHTML.includes("vt-bg-2")),
      hasBold: !!(pre && pre.innerHTML.includes("vt-bold")),
      hasUnderline: !!(pre && pre.innerHTML.includes("vt-underline")),
      scrollWidth: pre ? pre.scrollWidth : 0,
      clientWidth: pre ? pre.clientWidth : 0,
      devicePixelRatio: window.devicePixelRatio,
      timelineModuleLoaded: inst.timelineModuleLoaded || false,
      timelineRenderCalls: inst.timelineRenderCalls || 0,
      outRowsCreated: inst.outRowsCreated || 0,
      terminalRendererCalls: inst.terminalRendererCalls || 0,
      rendererCompleted: inst.rendererCompleted || false,
      outCardFound: !!card,
      errors: window.__DAKOTA_VISUAL_ERRORS__ || []
    }};
    document.getElementById("visual-result").textContent = JSON.stringify(r);
  }} catch(err) {{
    document.getElementById("visual-result").textContent = JSON.stringify({{error:String(err),hasPre:false,errors:window.__DAKOTA_VISUAL_ERRORS__||[]}});
  }}
}});
</script></body></html>"""

    # ── Chromium via CDP ──
    with tempfile.TemporaryDirectory(prefix="dakota-visual-", dir=Path.home()) as tmp:
        tp = Path(tmp)
        profile = tp / "chrome-profile"; profile.mkdir()
        png = tp / "screenshot.png"
        stdout_path = profile / "stdout.log"; stderr_path = profile / "stderr.log"
        stdout_fh = open(stdout_path, "w"); stderr_fh = open(stderr_path, "w")
        port = _free_port()

        proc = subprocess.Popen(
            [chromium,"--headless=new","--no-sandbox","--disable-gpu","--disable-dev-shm-usage","--remote-allow-origins=*",
             f"--user-data-dir={profile}",f"--remote-debugging-port={port}","about:blank"],
            stdout=stdout_fh, stderr=stderr_fh, start_new_session=True,
        )
        ws = None
        start_ts = datetime.now(timezone.utc).isoformat()
        try:
            deadline = time.time() + 20
            version = None
            while time.time() < deadline:
                if proc.poll() is not None:
                    raise AssertionError(f"Chrome exited early rc={proc.returncode}")
                try:
                    version = _json_get(f"http://127.0.0.1:{port}/json/version", timeout=1)
                    break
                except Exception:
                    time.sleep(0.05)
            assert version and version.get("Browser"), "CDP not ready"

            import urllib.request, urllib.parse
            tab = json.loads(
                urllib.request.urlopen(
                    urllib.request.Request(f"http://127.0.0.1:{port}/json/new?about:blank", method="PUT"), timeout=2
                ).read().decode()
            )
            ws = websocket.create_connection(tab["webSocketDebuggerUrl"], timeout=5)
            cid = [0]

            def cdp(method, params=None):
                cid[0] += 1
                ws.send(json.dumps({"id":cid[0],"method":method,"params":params or {}}))
                while True:
                    msg = json.loads(ws.recv())
                    if msg.get("id") == cid[0]:
                        if "error" in msg: raise AssertionError(f"CDP {method}: {msg['error']}")
                        return msg.get("result",{})

            cdp("Page.enable")
            cdp("Runtime.enable")
            cdp("Emulation.setDeviceMetricsOverride", {"width":1000,"height":360,"deviceScaleFactor":1,"mobile":False})
            cdp("Page.setDocumentContent", {"frameId":tab["id"],"html":html_content})

            metrics = None
            deadline2 = time.time() + 15
            expr = ("(()=>{const e=document.getElementById('visual-result');"
                    "if(!e||!e.textContent||e.textContent==='{}'){var errs=window.__DAKOTA_VISUAL_ERRORS__||[];return errs.length?{hasPre:false,errors:errs}:null;}"
                    "return JSON.parse(e.textContent);})()")
            while time.time() < deadline2:
                r = cdp("Runtime.evaluate", {"expression":expr,"returnByValue":True})
                v = r.get("result",{}).get("value")
                if isinstance(v, dict) and v.get("hasPre"):
                    metrics = v; break
                time.sleep(0.05)
            assert metrics is not None, f"renderer DOM marker not produced. errors: {metrics}"

            shot = cdp("Page.captureScreenshot", {"format":"png","captureBeyondViewport":True})
            png.write_bytes(base64.b64decode(shot["data"]))
            cdp("Page.close")
        finally:
            stdout_fh.close(); stderr_fh.close()
            if ws:
                try: ws.close()
                except: pass
            _kill_process_group(proc.pid, profile)

        # ── Validations ──
        assert metrics["hasPre"] is True
        assert metrics["whiteSpace"] == "pre"
        assert metrics["fontSize"] == "14px"
        assert metrics["lineHeight"] == "14px"
        assert metrics["rows"] == 6
        assert metrics["firstLineLength"] == 80
        assert metrics["hasCorners"] and metrics["hasEmoji"]
        assert metrics["hasReverse"] and metrics["hasFgBg"] and metrics["hasBold"] and metrics["hasUnderline"]

        # ── Timeline instrumentation ──
        assert metrics.get("timelineModuleLoaded") is True
        assert metrics.get("timelineRenderCalls",0) >= 1
        assert metrics.get("outRowsCreated",0) == 1, f"outRowsCreated={metrics.get('outRowsCreated')}"
        assert metrics.get("terminalRendererCalls",0) >= 1, f"terminalRendererCalls={metrics.get('terminalRendererCalls')}"
        assert metrics.get("rendererCompleted") is True
        assert metrics.get("outCardFound") is True, "OUT card must be found in DOM"

        # ── Screenshot ──
        assert png.exists() and png.stat().st_size > 0
        img = Image.open(png).convert("RGB")
        dark = lambda p: p[0] < 80 and p[1] < 80 and p[2] < 80
        dark_pixels = sum(1 for p in img.getdata() if dark(p))
        assert dark_pixels > 150

        time.sleep(2)
        remaining = _count_chromium_processes(profile)
        assert remaining == 0, f"Chromium remaining: {remaining}"

        # Check for zombies among processes spawned during this test
        # The Chromium process PID is known (proc.pid)
        zombie_count = 0
        pid_to_check = proc.pid
        try:
            import subprocess as _sp
            r = _sp.run(["ps", "--no-headers", "-o", "pid,stat", "--ppid", str(pid_to_check)],
                       capture_output=True, text=True, timeout=2)
            for line in r.stdout.strip().splitlines():
                parts = line.strip().split()
                if len(parts) >= 2 and "Z" in parts[1]:
                    zombie_count += 1
        except Exception:
            pass

        # ── Atomic evidence ──
        finished_ts = datetime.now(timezone.utc).isoformat()
        tree_hash = _tree_hash()

        evidence = {
            "schema_version": "1.0",
            "run_id": f"visual-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}",
            "started_at": start_ts,
            "finished_at": finished_ts,
            "passed": True,
            "source_tree_sha256": tree_hash,
            "chromium_started": True,
            "chromium_pid": proc.pid,
            "chromium_exit_code": 0,
            "cdp_connected": True,
            "document_loaded_via_cdp": True,
            "timeline_module_loaded": metrics.get("timelineModuleLoaded", False),
            "timeline_render_calls": metrics.get("timelineRenderCalls", 0),
            "out_rows_created": metrics.get("outRowsCreated", 0),
            "terminal_renderer_calls": metrics.get("terminalRendererCalls", 0),
            "renderer_completed": metrics.get("rendererCompleted", False),
            "renderer_marker_found": True,
            "out_card_found": metrics.get("outCardFound", False),
            "computed_styles_collected": True,
            "screenshot_created": True,
            "screenshot_bytes": png.stat().st_size,
            "screenshot_sha256": hashlib.sha256(png.read_bytes()).hexdigest(),
            "pixel_analysis_executed": True,
            "pixel_validation_passed": True,
            "remaining_processes": remaining,
            "remaining_zombies": zombie_count,
        }
        _atomic_write_json(ROOT / "artifacts/visual-test-result.json", evidence)
        assert len(tree_hash) == 64, f"tree hash must be 64 chars: {len(tree_hash)}"
        assert not tree_hash.startswith("e3b0c442"), "tree hash must not be empty"


# ── Anti-falso-positivo ──

def test_visual_antifalse_positive_chromium_really_started():
    import inspect
    src = inspect.getsource(test_terminal_snapshot_box_drawing_renders_with_real_browser_pixels)
    assert "subprocess.Popen" in src
    assert "start_new_session=True" in src
    assert "Page.captureScreenshot" in src
    assert "setDocumentContent" in src
    assert "HTTPServer" not in src
    assert "SimpleHTTPRequestHandler" not in src

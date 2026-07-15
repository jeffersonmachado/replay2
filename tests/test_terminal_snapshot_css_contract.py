from pathlib import Path
import base64
import json
import os
import shutil
import signal
import subprocess
import tempfile
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

from gateway.control.services.session_replay_service import prepare_session_replay_data


ROOT = Path(__file__).resolve().parents[1]


def test_terminal_snapshot_card_uses_pre_and_snapshot_class():
    source = (ROOT / "gateway/control/static/js/components/timeline_table.js").read_text(encoding="utf-8")

    assert '<pre class="r2ctl-event-content${isSnapshot ? " terminal-snapshot" : ""}">' in source
    assert "snapshot_html" in source


def test_capture_replay_fallback_keeps_terminal_snapshot_class():
    html = (ROOT / "gateway/control/templates/capture_session_replay.html").read_text(encoding="utf-8")

    assert "'<pre class=\"event-content' + (isSnapshot ? ' terminal-snapshot' : '') + '\">'" in html


def test_terminal_snapshot_css_preserves_grid_geometry():
    css = (ROOT / "gateway/control/static/control.css").read_text(encoding="utf-8")
    marker = ".r2ctl-event-content.terminal-snapshot{"
    assert marker in css
    rule = css.split(marker, 1)[1].split("}", 1)[0]

    assert "white-space:pre" in rule
    assert "word-break:normal" in rule
    assert "overflow-wrap:normal" in rule
    assert "overflow-x:auto" in rule
    assert "font-size:14px" in rule
    assert "line-height:14px" in rule
    assert "letter-spacing:0" in rule
    assert "word-spacing:0" in rule
    assert "transform:" not in rule
    assert "zoom:" not in rule
    assert "pre-wrap" not in rule
    assert "break-word" not in rule


def test_capture_replay_inline_css_matches_terminal_snapshot_contract():
    html = (ROOT / "gateway/control/templates/capture_session_replay.html").read_text(encoding="utf-8")
    marker = ".event-content {"
    assert marker in html
    rule = html.split(marker, 1)[1].split("}", 1)[0]

    assert "white-space: pre;" in rule
    assert "word-break: normal;" in rule
    assert "overflow-wrap: normal;" in rule
    assert "overflow-x: auto;" in rule
    assert "font-size: 14px;" in rule
    assert "line-height: 14px;" in rule
    assert "letter-spacing: 0;" in rule
    assert "word-spacing: 0;" in rule
    assert "transform:" not in rule
    assert "zoom:" not in rule
    assert "pre-wrap" not in rule
    assert "break-word" not in rule


def test_capture_replay_loads_structured_error_body_safely():
    html = (ROOT / "gateway/control/templates/capture_session_replay.html").read_text(encoding="utf-8")

    assert "function replayErrorMessage(payload, fallback)" in html
    assert "const payload = await response.json().catch(() => ({}));" in html
    assert "throw new Error(replayErrorMessage(payload, `HTTP ${response.status}`));" in html
    assert "Erro ao carregar dados: ${escapeHtml(err.message)}" in html


def test_terminal_snapshot_box_drawing_renders_with_real_browser_pixels():
    chromium = (
        shutil.which("chromium")
        or shutil.which("chromium-browser")
        or shutil.which("google-chrome")
        or shutil.which("google-chrome-stable")
    )
    assert chromium, "Chromium/Chrome headless is required for the visual acceptance contract"

    try:
        from PIL import Image
    except Exception as exc:
        raise AssertionError("Pillow is required for the visual pixel acceptance contract") from exc

    def run_chromium(args, *, timeout=20):
        proc = subprocess.Popen(
            [chromium, *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
        )
        try:
            stdout, _ = proc.communicate(timeout=timeout)
            return proc.returncode, stdout
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
                proc.wait(timeout=2)
            except Exception:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except Exception:
                    pass
            stdout, _ = proc.communicate(timeout=2)
            raise AssertionError(f"Chromium timed out; output:\n{stdout}")

    with tempfile.TemporaryDirectory(prefix="dakota-replay2-visual-", dir=Path.home()) as tmp:
        tmp_path = Path(tmp)
        os.symlink(ROOT / "gateway", tmp_path / "gateway")
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        session_id = "visual-box"
        box_text = (
            "┌" + "─" * 78 + "┐\r\n"
            "│" + " " * 78 + "│\r\n"
            "│ \x1b[1;4;31;42mATTR\x1b[0m \x1b[7mREV\x1b[0m 😀 cursor │\r\n"
            "└" + "─" * 78 + "┘"
        )
        events = [
            {"type": "session_start", "session_id": session_id, "seq_global": 1, "seq_session": 1, "ts_ms": 1000, "rows": 6, "cols": 80, "encoding": "utf-8"},
            {"type": "bytes", "session_id": session_id, "seq_global": 2, "seq_session": 2, "ts_ms": 1010, "dir": "out", "n": len(box_text.encode("utf-8")), "data_b64": base64.b64encode(box_text.encode("utf-8")).decode("ascii")},
            {"type": "session_end", "session_id": session_id, "seq_global": 3, "seq_session": 3, "ts_ms": 1020},
        ]
        (log_dir / "audit-visual.part001.jsonl").write_text(
            "\n".join(json.dumps(ev, ensure_ascii=False) for ev in events),
            encoding="utf-8",
        )
        replay = prepare_session_replay_data(str(log_dir), session_id)
        assert replay["error"] is None
        snapshot_payload = replay["final_snapshot"]

        renderer_uri = (ROOT / "gateway/control/static/js/components/terminal_snapshot_renderer.js").as_uri()
        render_result = subprocess.run(
            [
                "node",
                "--input-type=module",
                "-e",
                (
                    f"import {{ decodeSnapshotPayload, renderSnapshotToHtml }} from {json.dumps(renderer_uri)};\n"
                    f"const payload = {json.dumps(snapshot_payload, ensure_ascii=False)};\n"
                    "const snapshot = decodeSnapshotPayload(payload);\n"
                    "process.stdout.write(renderSnapshotToHtml(snapshot));\n"
                ),
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=20,
            check=False,
        )
        assert render_result.returncode == 0, render_result.stdout
        snapshot_html = render_result.stdout
        assert "┌" in snapshot_html and "┘" in snapshot_html

        page = tmp_path / "box.html"
        png = tmp_path / "box.png"
        profile = tmp_path / "chromium-profile"
        event = {
            "type": "bytes",
            "direction": "out",
            "seq_global": 2,
            "timestamp_ms": 1010,
            "actor": "visual-test",
            "content_kind": "terminal_snapshot",
            "snapshot_html": snapshot_html,
            "n_bytes": len(box_text.encode("utf-8")),
        }
        handler = partial(SimpleHTTPRequestHandler, directory=str(tmp_path))
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        base_url = f"http://127.0.0.1:{server.server_port}"
        timeline_uri = f"{base_url}/gateway/control/static/js/components/timeline_table.js"
        css_uri = f"{base_url}/gateway/control/static/control.css"
        html = f"""<!doctype html>
<html><head><meta charset="utf-8">
<link rel="stylesheet" href="{css_uri}">
</head><body class="r2ctl-theme-shell">
<main style="width:920px;padding:16px">
  <div id="count"></div>
  <div id="cards"></div>
  <script id="visual-result" type="application/json">{{}}</script>
</main>
<script type="module">
import {{ renderEventsCards }} from {json.dumps(timeline_uri)};
const events = [{json.dumps(event, ensure_ascii=False)}];
renderEventsCards(events, "#cards", "#count");
requestAnimationFrame(() => {{
  const pre = document.querySelector(".r2ctl-event-content.terminal-snapshot");
  const cs = getComputedStyle(pre);
  const text = pre.innerText || "";
  const firstLine = text.split("\\n")[0] || "";
  const result = {{
    hasPre: !!pre,
    whiteSpace: cs.whiteSpace,
    wordBreak: cs.wordBreak,
    overflowWrap: cs.overflowWrap,
    fontSize: cs.fontSize,
    lineHeight: cs.lineHeight,
    letterSpacing: cs.letterSpacing,
    rows: text.split("\\n").length,
    firstLineLength: firstLine.length,
    hasCorners: text.includes("┌") && text.includes("┘"),
    hasEmoji: text.includes("😀"),
    hasReverse: !!document.querySelector(".vt-reverse"),
    hasFgBg: !!document.querySelector(".vt-fg-1.vt-bg-2"),
    hasBold: !!document.querySelector(".vt-bold"),
    hasUnderline: !!document.querySelector(".vt-underline"),
    scrollWidth: pre.scrollWidth,
    clientWidth: pre.clientWidth,
    devicePixelRatio: window.devicePixelRatio
  }};
  document.getElementById("visual-result").textContent = JSON.stringify(result);
}});
</script></body></html>"""
        page.write_text(html, encoding="utf-8")
        dump_rc, dump_out = run_chromium(
            [
                "--headless",
                "--no-sandbox",
                "--disable-gpu",
                "--allow-file-access-from-files",
                f"--user-data-dir={profile}",
                "--virtual-time-budget=3000",
                "--window-size=1000,360",
                "--dump-dom",
                f"{base_url}/{page.name}",
            ],
            timeout=20,
        )
        assert dump_rc == 0, dump_out
        marker = '<script id="visual-result" type="application/json">'
        metrics = json.loads(dump_out.split(marker, 1)[1].split("</script>", 1)[0])
        assert metrics["hasPre"] is True
        assert metrics["whiteSpace"] == "pre"
        assert metrics["wordBreak"] == "normal"
        assert metrics["overflowWrap"] == "normal"
        assert metrics["fontSize"] == "14px"
        assert metrics["lineHeight"] == "14px"
        assert metrics["letterSpacing"] == "normal" or metrics["letterSpacing"] == "0px"
        assert metrics["rows"] == 6
        assert metrics["firstLineLength"] == 80
        assert metrics["hasCorners"] and metrics["hasEmoji"]
        assert metrics["hasReverse"] and metrics["hasFgBg"] and metrics["hasBold"] and metrics["hasUnderline"]
        assert metrics["devicePixelRatio"] >= 1

        shot_rc, shot_out = run_chromium(
            [
                "--headless",
                "--no-sandbox",
                "--disable-gpu",
                "--allow-file-access-from-files",
                f"--user-data-dir={profile}",
                "--virtual-time-budget=1000",
                "--window-size=1000,360",
                f"--screenshot={png}",
                f"{base_url}/{page.name}",
            ],
            timeout=20,
        )
        assert shot_rc == 0, shot_out
        assert png.exists() and png.stat().st_size > 0
        img = Image.open(png).convert("RGB")
        dark = lambda pixel: pixel[0] < 80 and pixel[1] < 80 and pixel[2] < 80
        dark_pixels = sum(1 for pixel in img.getdata() if dark(pixel))
        assert dark_pixels > 150
        horizontal_counts = [
            sum(1 for x in range(0, 120) if dark(img.getpixel((x, y))))
            for y in range(0, 45)
        ]
        vertical_counts = [
            sum(1 for y in range(0, 95) if dark(img.getpixel((x, y))))
            for x in range(0, 95)
        ]
        assert max(horizontal_counts) >= 20
        assert max(vertical_counts) >= 20
        server.shutdown()
        server.server_close()

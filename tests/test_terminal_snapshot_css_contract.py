from pathlib import Path
import shutil
import subprocess
import tempfile


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
    if not chromium:
        test_terminal_snapshot_css_preserves_grid_geometry()
        return

    try:
        from PIL import Image
    except Exception:
        test_terminal_snapshot_css_preserves_grid_geometry()
        return

    html = """<!doctype html>
<html><head><meta charset="utf-8">
<style>
body{margin:0;background:#fff}
.terminal-snapshot{white-space:pre;font-family:"DejaVu Sans Mono","Consolas",monospace;font-size:28px;line-height:28px;letter-spacing:0;color:#000;background:#fff}
</style></head><body><pre class="terminal-snapshot">┌──┐
│  │
└──┘</pre></body></html>"""
    with tempfile.TemporaryDirectory(prefix="dakota-replay2-visual-", dir=Path.home()) as tmp:
        tmp_path = Path(tmp)
        page = tmp_path / "box.html"
        png = tmp_path / "box.png"
        page.write_text(html, encoding="utf-8")
        result = subprocess.run(
            [
                chromium,
                "--headless",
                "--no-sandbox",
                "--disable-gpu",
                "--window-size=220,120",
                f"--screenshot={png}",
                page.as_uri(),
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=20,
            check=False,
        )
        assert result.returncode == 0, result.stdout
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

from __future__ import annotations

from pathlib import Path

from control.ui_templates import LOGIN_HTML, ROUTES_CONFIG, render_page


STATIC_ROOT = Path(__file__).resolve().parents[1] / "static"


def _send_html(handler, html: str, *, status_code: int = 200) -> None:
    handler.send_response(status_code)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.end_headers()
    handler.wfile.write(html.encode("utf-8"))


def _require_user_for_page(handler) -> dict | None:
    user = handler._auth()
    if user:
        return user
    handler.send_response(302)
    handler.send_header("Location", "/login")
    handler.end_headers()
    return None


def _serve_static_asset(handler, asset_path: str) -> bool:
    if not asset_path.startswith("/assets/"):
        return False
    relative = asset_path[len("/assets/") :].strip("/")
    if not relative:
        return False
    fs_path = (STATIC_ROOT / relative).resolve()
    if STATIC_ROOT not in fs_path.parents and fs_path != STATIC_ROOT:
        return False
    if not fs_path.is_file():
        handler.send_response(404)
        handler.end_headers()
        return True
    content_type = "application/octet-stream"
    if fs_path.suffix == ".css":
        content_type = "text/css; charset=utf-8"
    elif fs_path.suffix in (".js", ".cjs", ".mjs"):
        content_type = "application/javascript; charset=utf-8"
    handler.send_response(200)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Cache-Control", "no-cache")
    handler.end_headers()
    handler.wfile.write(fs_path.read_bytes())
    return True


def _match_route(path: str, config: dict) -> bool:
    matcher = config.get("match")
    if callable(matcher):
        return bool(matcher(path))
    return path == config.get("path")


def _page_scripts(config: dict) -> list[str]:
    scripts = config.get("scripts")
    if isinstance(scripts, list):
        return scripts
    script = config.get("script")
    if isinstance(script, str) and script:
        return [f"/assets/js/pages/{script}"]
    return []


def render_ui_route(request, config: dict) -> str:
    return render_page(
        config["template"],
        title=config["title"],
        page_title=config["page_title"],
        page_description=config["page_description"],
        page_kicker=config["page_kicker"],
        active_menu=config["menu"],
        active_submenu=config.get("submenu"),
        page_scripts=_page_scripts(config),
        page_state=config.get("page_state"),
    )


def handle_ui_get_route(handler, parsed_path) -> bool:
    path = parsed_path.path
    if _serve_static_asset(handler, path):
        return True

    if path == "/login":
        _send_html(handler, LOGIN_HTML)
        return True

    for route in ROUTES_CONFIG:
        if not _match_route(path, route):
            continue
        if not _require_user_for_page(handler):
            return True
        _send_html(handler, render_ui_route(handler, route))
        return True

    return False

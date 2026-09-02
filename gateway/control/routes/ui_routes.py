from __future__ import annotations

import gzip
import os
import threading
from pathlib import Path
from urllib.parse import parse_qs

from control.ui_templates import LOGIN_HTML, ROUTES_CONFIG, render_page


STATIC_ROOT = Path(__file__).resolve().parents[1] / "static"

# ── Cache em memória de assets estáticos ──
# mermaid.min.js tem ~3,3 MB: reler do disco a cada requisição drenava CPU/IO
# do control plane. O cache é invalidado por (mtime_ns, size) — um deploy que
# troca o arquivo atualiza a entrada na próxima requisição, sem restart.
_ASSET_CACHE: dict[str, dict] = {}
_ASSET_CACHE_LOCK = threading.Lock()
_ASSET_CACHE_CONTROL = "public, max-age=3600"
_ASSET_GZIP_MIN_BYTES = 1024
# Tipos que compensam comprimir (texto); binários já comprimidos ficam crus.
_ASSET_GZIP_TYPES = frozenset({
    "text/css; charset=utf-8",
    "application/javascript; charset=utf-8",
    "image/svg+xml",
})


def _reset_asset_cache() -> None:
    """Esvazia o cache de assets (uso em testes; a invalidação normal é por mtime)."""
    with _ASSET_CACHE_LOCK:
        _ASSET_CACHE.clear()


def _load_asset_entry(fs_path: Path, content_type: str) -> dict | None:
    """Retorna a entrada cacheada do asset, relendo do disco só se mudou."""
    try:
        st = fs_path.stat()
    except OSError:
        return None
    key = str(fs_path)
    with _ASSET_CACHE_LOCK:
        entry = _ASSET_CACHE.get(key)
        if entry and entry["mtime_ns"] == st.st_mtime_ns and entry["size"] == st.st_size:
            return entry
    # Leitura/compressão fora do lock (arquivo grande não bloqueia os demais).
    raw = fs_path.read_bytes()
    gz = (
        gzip.compress(raw)
        if st.st_size >= _ASSET_GZIP_MIN_BYTES and content_type in _ASSET_GZIP_TYPES
        else None
    )
    entry = {
        "mtime_ns": st.st_mtime_ns,
        "size": st.st_size,
        "etag": '"%x-%x"' % (st.st_mtime_ns, st.st_size),
        "raw": raw,
        "gzip": gz,
    }
    with _ASSET_CACHE_LOCK:
        # Recheca: se o arquivo mudou durante a leitura, não cacheia a entrada
        # (responde com ela nesta requisição e a próxima relê).
        try:
            st2 = fs_path.stat()
        except OSError:
            return entry
        if st2.st_mtime_ns == st.st_mtime_ns and st2.st_size == st.st_size:
            _ASSET_CACHE[key] = entry
    return entry


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
    elif fs_path.suffix == ".svg":
        content_type = "image/svg+xml"
    entry = _load_asset_entry(fs_path, content_type)
    if entry is None:
        handler.send_response(404)
        handler.end_headers()
        return True
    etag = entry["etag"]
    if_none_match = handler.headers.get("If-None-Match") or ""
    if etag in [tag.strip() for tag in if_none_match.split(",")]:
        handler.send_response(304)
        handler.send_header("ETag", etag)
        handler.send_header("Cache-Control", _ASSET_CACHE_CONTROL)
        handler.end_headers()
        return True
    accept_encoding = (handler.headers.get("Accept-Encoding") or "").lower()
    use_gzip = entry["gzip"] is not None and "gzip" in accept_encoding
    body = entry["gzip"] if use_gzip else entry["raw"]
    handler.send_response(200)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Cache-Control", _ASSET_CACHE_CONTROL)
    handler.send_header("ETag", etag)
    handler.send_header("Vary", "Accept-Encoding")
    if use_gzip:
        handler.send_header("Content-Encoding", "gzip")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)
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


def render_ui_route(request, config: dict, *, embed: bool = False) -> str:
    page_state = config.get("page_state")
    if config.get("template") == "captures.html":
        # Expõe o padrão do servidor para a UI pré-preencher o source_dir da
        # síntese de dados (usuário leigo não precisa conhecer o caminho).
        page_state = {
            **(page_state or {}),
            "default_source_dir": os.environ.get("DAKOTA_SOURCE_ROOT", "").strip(),
        }
    return render_page(
        config["template"],
        title=config["title"],
        page_title=config["page_title"],
        page_description=config["page_description"],
        page_kicker=config["page_kicker"],
        active_menu=config["menu"],
        active_submenu=config.get("submenu"),
        page_scripts=_page_scripts(config),
        page_state=page_state,
        embed=embed,
    )


def handle_ui_get_route(handler, parsed_path) -> bool:
    path = parsed_path.path
    if _serve_static_asset(handler, path):
        return True

    if path == "/login":
        _send_html(handler, LOGIN_HTML)
        return True

    # embed=1: renderiza a página sem chrome (sidebar/topbar/statusbar) para
    # uso em iframes — ex.: painéis lado a lado de /runs/{id}/compare.
    qs = parse_qs(parsed_path.query or "")
    embed = str((qs.get("embed") or [""])[0] or "").strip() == "1"

    for route in ROUTES_CONFIG:
        if not _match_route(path, route):
            continue
        if not _require_user_for_page(handler):
            return True
        _send_html(handler, render_ui_route(handler, route, embed=embed))
        return True

    return False

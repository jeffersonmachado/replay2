from __future__ import annotations

from pathlib import Path

from control.ui_templates import LOGIN_HTML, render_page


STATIC_ROOT = Path(__file__).resolve().parents[1] / "static"


ROUTES_CONFIG = [
    {
        "path": "/",
        "template": "dashboard.html",
        "title": "Dakota Calcados | Replay Control",
        "page_title": "Painel Operacional",
        "page_description": "Dashboard executivo com foco em filas, alertas e status resumido do gateway.",
        "page_kicker": "Controle de Replay",
        "menu": "dashboard",
        "script": "dashboard.js",
    },
    {
        "path": "/runs",
        "template": "runs.html",
        "title": "Dakota Calcados | Execucoes",
        "page_title": "Execucoes",
        "page_description": "Fila, historico, falhas, comparacao e compliance em uma lista previsivel.",
        "page_kicker": "Operacao diaria",
        "menu": "runs",
        "submenu": "runs",
        "script": "runs.js",
    },
    {
        "path": "/runs/new",
        "template": "run_new.html",
        "title": "Dakota Calcados | Nova Run",
        "page_title": "Nova Run",
        "page_description": "Criacao dedicada de runs com reaproveitamento de ambientes e perfis.",
        "page_kicker": "Execucoes",
        "menu": "runs",
        "submenu": "runs_new",
        "script": "run_new.js",
    },
    {
        "path": "/runs/history",
        "template": "runs.html",
        "title": "Dakota Calcados | Historico de Runs",
        "page_title": "Execucoes",
        "page_description": "Historico consolidado de runs e status operacionais.",
        "page_kicker": "Operacao diaria",
        "menu": "runs",
        "submenu": "runs_history",
        "script": "runs.js",
        "page_state": {"section": "history"},
    },
    {
        "path": "/runs/failures",
        "template": "runs.html",
        "title": "Dakota Calcados | Falhas",
        "page_title": "Execucoes",
        "page_description": "Falhas e diagnosticos das execucoes recentes.",
        "page_kicker": "Operacao diaria",
        "menu": "runs",
        "submenu": "runs_failures",
        "script": "runs.js",
        "page_state": {"section": "failures"},
    },
    {
        "path": "/runs/comparison",
        "template": "runs.html",
        "title": "Dakota Calcados | Comparacao",
        "page_title": "Execucoes",
        "page_description": "Comparacao de runs para apoiar investigacao de regressao.",
        "page_kicker": "Operacao diaria",
        "menu": "runs",
        "submenu": "runs_comparison",
        "script": "runs.js",
        "page_state": {"section": "comparison"},
    },
    {
        "path": "/runs/compliance",
        "template": "runs.html",
        "title": "Dakota Calcados | Compliance",
        "page_title": "Execucoes",
        "page_description": "Visao de compliance por run e por sessao.",
        "page_kicker": "Operacao diaria",
        "menu": "runs",
        "submenu": "runs_compliance",
        "script": "runs.js",
        "page_state": {"section": "compliance"},
    },
    {
        "path": "/gateway",
        "template": "gateway.html",
        "title": "Dakota Calcados | Gateway",
        "page_title": "Gateway",
        "page_description": "Status, monitor, sessoes, eventos e compliance por sessao.",
        "page_kicker": "Infraestrutura operacional",
        "menu": "gateway",
        "script": "gateway.js",
    },
    {
        "path": "/gateway/status",
        "template": "gateway.html",
        "title": "Dakota Calcados | Gateway",
        "page_title": "Gateway",
        "page_description": "Status, monitor, sessoes, eventos e compliance por sessao.",
        "page_kicker": "Infraestrutura operacional",
        "menu": "gateway",
        "submenu": "gateway_status",
        "script": "gateway.js",
        "page_state": {"section": "status"},
    },
    {
        "path": "/gateway/monitor",
        "template": "gateway.html",
        "title": "Dakota Calcados | Gateway",
        "page_title": "Gateway",
        "page_description": "Status, monitor, sessoes, eventos e compliance por sessao.",
        "page_kicker": "Infraestrutura operacional",
        "menu": "gateway",
        "submenu": "gateway_monitor",
        "script": "gateway.js",
        "page_state": {"section": "monitor"},
    },
    {
        "path": "/gateway/sessions",
        "template": "gateway.html",
        "title": "Dakota Calcados | Gateway",
        "page_title": "Gateway",
        "page_description": "Status, monitor, sessoes, eventos e compliance por sessao.",
        "page_kicker": "Infraestrutura operacional",
        "menu": "gateway",
        "submenu": "gateway_sessions",
        "script": "gateway.js",
        "page_state": {"section": "sessions"},
    },
    {
        "path": "/gateway/events",
        "template": "gateway.html",
        "title": "Dakota Calcados | Gateway",
        "page_title": "Gateway",
        "page_description": "Status, monitor, sessoes, eventos e compliance por sessao.",
        "page_kicker": "Infraestrutura operacional",
        "menu": "gateway",
        "submenu": "gateway_events",
        "script": "gateway.js",
        "page_state": {"section": "events"},
    },
    {
        "path": "/gateway/compliance",
        "template": "gateway.html",
        "title": "Dakota Calcados | Gateway",
        "page_title": "Gateway",
        "page_description": "Status, monitor, sessoes, eventos e compliance por sessao.",
        "page_kicker": "Infraestrutura operacional",
        "menu": "gateway",
        "submenu": "gateway_compliance",
        "script": "gateway.js",
        "page_state": {"section": "compliance"},
    },
    {
        "path": "/catalog",
        "template": "catalog.html",
        "title": "Dakota Calcados | Catalogo",
        "page_title": "Catalogo",
        "page_description": "Ambientes, perfis, politicas e cenarios operacionais organizados por dominio.",
        "page_kicker": "Configuracao reutilizavel",
        "menu": "catalog",
        "script": "catalog.js",
    },
    {
        "path": "/catalog/targets",
        "template": "catalog.html",
        "title": "Dakota Calcados | Catalogo",
        "page_title": "Catalogo",
        "page_description": "Ambientes, perfis, politicas e cenarios operacionais organizados por dominio.",
        "page_kicker": "Configuracao reutilizavel",
        "menu": "catalog",
        "submenu": "catalog_targets",
        "script": "catalog.js",
        "page_state": {"section": "targets"},
    },
    {
        "path": "/catalog/profiles",
        "template": "catalog.html",
        "title": "Dakota Calcados | Catalogo",
        "page_title": "Catalogo",
        "page_description": "Ambientes, perfis, politicas e cenarios operacionais organizados por dominio.",
        "page_kicker": "Configuracao reutilizavel",
        "menu": "catalog",
        "submenu": "catalog_profiles",
        "script": "catalog.js",
        "page_state": {"section": "profiles"},
    },
    {
        "path": "/catalog/policies",
        "template": "catalog.html",
        "title": "Dakota Calcados | Catalogo",
        "page_title": "Catalogo",
        "page_description": "Ambientes, perfis, politicas e cenarios operacionais organizados por dominio.",
        "page_kicker": "Configuracao reutilizavel",
        "menu": "catalog",
        "submenu": "catalog_policies",
        "script": "catalog.js",
        "page_state": {"section": "policies"},
    },
    {
        "path": "/catalog/scenarios",
        "template": "catalog.html",
        "title": "Dakota Calcados | Catalogo",
        "page_title": "Catalogo",
        "page_description": "Ambientes, perfis, politicas e cenarios operacionais organizados por dominio.",
        "page_kicker": "Configuracao reutilizavel",
        "menu": "catalog",
        "submenu": "catalog_scenarios",
        "script": "catalog.js",
        "page_state": {"section": "scenarios"},
    },
    {
        "path": "/observability",
        "template": "observability.html",
        "title": "Dakota Calcados | Observability",
        "page_title": "Observabilidade",
        "page_description": "Diagnostico analitico, SLA, reprocessamentos e tendencias da operacao.",
        "page_kicker": "Replay Suite",
        "menu": "observability",
        "submenu": "observability",
        "script": "observability.js",
    },
    {
        "path": "/observability/overview",
        "template": "observability.html",
        "title": "Dakota Calcados | Observability",
        "page_title": "Observabilidade",
        "page_description": "Diagnostico analitico, SLA, reprocessamentos e tendencias da operacao.",
        "page_kicker": "Replay Suite",
        "menu": "observability",
        "submenu": "observability",
        "script": "observability.js",
        "page_state": {"section": "overview"},
    },
    {
        "path": "/observability/sla",
        "template": "observability.html",
        "title": "Dakota Calcados | Observability",
        "page_title": "Observabilidade",
        "page_description": "Diagnostico analitico, SLA, reprocessamentos e tendencias da operacao.",
        "page_kicker": "Replay Suite",
        "menu": "observability",
        "submenu": "observability_sla",
        "script": "observability.js",
        "page_state": {"section": "sla"},
    },
    {
        "path": "/observability/reprocess",
        "template": "observability.html",
        "title": "Dakota Calcados | Observability",
        "page_title": "Observabilidade",
        "page_description": "Diagnostico analitico, SLA, reprocessamentos e tendencias da operacao.",
        "page_kicker": "Replay Suite",
        "menu": "observability",
        "submenu": "observability_reprocess",
        "script": "observability.js",
        "page_state": {"section": "reprocess"},
    },
    {
        "path": "/observability/regressions",
        "template": "observability.html",
        "title": "Dakota Calcados | Observability",
        "page_title": "Observabilidade",
        "page_description": "Diagnostico analitico, SLA, reprocessamentos e tendencias da operacao.",
        "page_kicker": "Replay Suite",
        "menu": "observability",
        "submenu": "observability_regressions",
        "script": "observability.js",
        "page_state": {"section": "regressions"},
    },
    {
        "path": "/observability/trends",
        "template": "observability.html",
        "title": "Dakota Calcados | Observability",
        "page_title": "Observabilidade",
        "page_description": "Diagnostico analitico, SLA, reprocessamentos e tendencias da operacao.",
        "page_kicker": "Replay Suite",
        "menu": "observability",
        "submenu": "observability_trends",
        "script": "observability.js",
        "page_state": {"section": "trends"},
    },
    {
        "path": "/observability/flows",
        "template": "observability.html",
        "title": "Dakota Calcados | Observability",
        "page_title": "Observabilidade",
        "page_description": "Diagnostico analitico, SLA, reprocessamentos e tendencias da operacao.",
        "page_kicker": "Replay Suite",
        "menu": "observability",
        "submenu": "observability_flows",
        "script": "observability.js",
        "page_state": {"section": "flows"},
    },
    {
        "path": "/observability/signatures",
        "template": "observability.html",
        "title": "Dakota Calcados | Observability",
        "page_title": "Observabilidade",
        "page_description": "Diagnostico analitico, SLA, reprocessamentos e tendencias da operacao.",
        "page_kicker": "Replay Suite",
        "menu": "observability",
        "submenu": "observability_signatures",
        "script": "observability.js",
        "page_state": {"section": "signatures"},
    },
    {
        "path": "/observability/automation",
        "template": "observability.html",
        "title": "Dakota Calcados | Observability",
        "page_title": "Observabilidade",
        "page_description": "Diagnostico analitico, SLA, reprocessamentos e tendencias da operacao.",
        "page_kicker": "Replay Suite",
        "menu": "observability",
        "submenu": "observability_automation",
        "script": "observability.js",
        "page_state": {"section": "automation"},
    },
    {
        "path": "/admin",
        "template": "admin.html",
        "title": "Dakota Calcados | Administracao",
        "page_title": "Administracao",
        "page_description": "Usuarios, sessao atual e parametros globais da instancia.",
        "page_kicker": "Gestao",
        "menu": "admin",
        "submenu": "admin_users",
        "script": "admin.js",
        "page_state": {"section": "users"},
    },
    {
        "path": "/admin/users",
        "template": "admin.html",
        "title": "Dakota Calcados | Administracao",
        "page_title": "Administracao",
        "page_description": "Usuarios, sessao atual e parametros globais da instancia.",
        "page_kicker": "Gestao",
        "menu": "admin",
        "submenu": "admin_users",
        "script": "admin.js",
        "page_state": {"section": "users"},
    },
    {
        "path": "/admin/session",
        "template": "admin.html",
        "title": "Dakota Calcados | Administracao",
        "page_title": "Administracao",
        "page_description": "Usuarios, sessao atual e parametros globais da instancia.",
        "page_kicker": "Gestao",
        "menu": "admin",
        "submenu": "admin_session",
        "script": "admin.js",
        "page_state": {"section": "session"},
    },
    {
        "path": "/admin/settings",
        "template": "admin.html",
        "title": "Dakota Calcados | Administracao",
        "page_title": "Administracao",
        "page_description": "Usuarios, sessao atual e parametros globais da instancia.",
        "page_kicker": "Gestao",
        "menu": "admin",
        "submenu": "admin_settings",
        "script": "admin.js",
        "page_state": {"section": "settings"},
    },
    {
        "match": lambda path: path.startswith("/runs/") and path.count("/") == 2,
        "template": "run_detail.html",
        "title": "Dakota Calcados | Detalhe da Run",
        "page_title": "Detalhe da Run",
        "page_description": "Inspecao de eventos, falhas, comparacao e exportacoes da execucao selecionada.",
        "page_kicker": "Execucoes",
        "menu": "runs",
        "submenu": "runs_history",
        "script": "run_detail.js",
    },
    # ── Capturas (UI-first) ──────────────────────────────────────────────
    {
        "path": "/captures",
        "template": "captures.html",
        "title": "Dakota Calcados | Capturas",
        "page_title": "Capturas",
        "page_description": "Sessoes de captura iniciadas e gerenciadas pela UI.",
        "page_kicker": "Gateway operacional",
        "menu": "captures",
        "script": "captures.js",
        "page_state": {"section": "list"},
    },
    {
        "path": "/captures/new",
        "template": "captures.html",
        "title": "Dakota Calcados | Capturas",
        "page_title": "Capturas",
        "page_description": "Sessoes de captura iniciadas automaticamente na ativacao do gateway.",
        "page_kicker": "Gateway operacional",
        "menu": "captures",
        "submenu": "captures",
        "script": "captures.js",
        "page_state": {"section": "list"},
    },
    {
        "match": lambda path: path.startswith("/captures/") and path.count("/") == 2 and path.split("/")[2] not in ("new",),
        "template": "captures.html",
        "title": "Dakota Calcados | Detalhe da Captura",
        "page_title": "Capturas",
        "page_description": "Detalhe e timeline de eventos da sessao de captura.",
        "page_kicker": "Gateway operacional",
        "menu": "captures",
        "submenu": "captures",
        "script": "captures.js",
        "page_state": {"section": "detail"},
    },
    {
        "match": lambda path: path.startswith("/captures/") and "/replay" in path,
        "template": "capture_session_replay.html",
        "title": "Dakota Calcados | Replay de Sessao",
        "page_title": "Visualização & Replay",
        "page_description": "Reproduca e analise a captura de sessao com entrada/saida do usuario.",
        "page_kicker": "Auditoria de sessao",
        "menu": "captures",
        "submenu": "captures",
        "script": None,  # Script integrado no template
    },
]


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
    elif fs_path.suffix == ".js":
        content_type = "application/javascript; charset=utf-8"
    handler.send_response(200)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Cache-Control", "no-store, max-age=0")
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

from __future__ import annotations

import json
from pathlib import Path


_TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
_TEMPLATE_CACHE: dict[str, tuple[int, str]] = {}


_MENU_CONFIG = [
    {
        "label": "Dashboard",
        "href": "/",
        "icon": "DB",
        "key": "dashboard",
    },
    {
        "label": "Execuções",
        "href": "/runs",
        "icon": "EX",
        "key": "runs",
        "children": [
            {"label": "Nova execução", "href": "/runs/new", "key": "runs_new"},
            {"label": "Fila", "href": "/runs", "key": "runs"},
            {"label": "Histórico", "href": "/runs/history", "key": "runs_history"},
            {"label": "Falhas", "href": "/runs/failures", "key": "runs_failures"},
            {"label": "Comparação", "href": "/runs/comparison", "key": "runs_comparison"},
            {"label": "Conformidade", "href": "/runs/compliance", "key": "runs_compliance"},
        ],
    },
    {
        "label": "Gateway",
        "href": "/gateway",
        "icon": "GW",
        "key": "gateway",
        "children": [
            {"label": "Status", "href": "/gateway/status", "key": "gateway_status"},
            {"label": "Monitor", "href": "/gateway/monitor", "key": "gateway_monitor"},
            {"label": "Eventos", "href": "/gateway/events", "key": "gateway_events"},
            {"label": "Sessões", "href": "/gateway/sessions", "key": "gateway_sessions"},
            {"label": "Conformidade", "href": "/gateway/compliance", "key": "gateway_compliance"},
        ],
    },
    {
        "label": "Capturas",
        "href": "/captures",
        "icon": "CP",
        "key": "captures",
        "children": [
            {"label": "Lista", "href": "/captures", "key": "captures"},
        ],
    },
    {
        "label": "Catálogo",
        "href": "/catalog",
        "icon": "CT",
        "key": "catalog",
        "children": [
            {"label": "Ambientes", "href": "/catalog/targets", "key": "catalog_targets"},
            {"label": "Perfis", "href": "/catalog/profiles", "key": "catalog_profiles"},
            {"label": "Políticas", "href": "/catalog/policies", "key": "catalog_policies"},
            {"label": "Cenários", "href": "/catalog/scenarios", "key": "catalog_scenarios"},
        ],
    },
    {
        "label": "Observabilidade",
        "href": "/observability",
        "icon": "OB",
        "key": "observability",
        "children": [
            {"label": "Visão Geral", "href": "/observability/overview", "key": "observability"},
            {"label": "SLA", "href": "/observability/sla", "key": "observability_sla"},
            {"label": "Reprocessamentos", "href": "/observability/reprocess", "key": "observability_reprocess"},
            {"label": "Regressões", "href": "/observability/regressions", "key": "observability_regressions"},
            {"label": "Tendências", "href": "/observability/trends", "key": "observability_trends"},
            {"label": "Fluxos sensíveis", "href": "/observability/flows", "key": "observability_flows"},
            {"label": "Assinaturas", "href": "/observability/signatures", "key": "observability_signatures"},
            {"label": "Automação", "href": "/observability/automation", "key": "observability_automation"},
            {"label": "Recursos", "href": "/observability/resources", "key": "observability_resources"},
        ],
    },
    {
        "label": "Engenharia",
        "href": "/pipeline",
        "icon": "EG",
        "key": "engineering",
        "children": [
            {"label": "Pipeline", "href": "/pipeline", "key": "engineering_pipeline"},
            {"label": "Sintéticos", "href": "/synthetic", "key": "engineering_synthetic"},
            {"label": "Benchmark", "href": "/benchmark", "key": "engineering_benchmark"},
            {"label": "Avaliação IA", "href": "/assess", "key": "engineering_assess"},
            {"label": "Auditoria IA", "href": "/audit", "key": "engineering_audit"},
            {"label": "Relatório de jornadas", "href": "/journeys-report", "key": "engineering_journeys_report"},
            {"label": "Regras de negócio", "href": "/business-rules", "key": "engineering_business_rules"},
        ],
    },
    {
        "label": "Administração",
        "href": "/admin",
        "icon": "AD",
        "key": "admin",
        "children": [
            {"label": "Usuários", "href": "/admin/users", "key": "admin_users"},
            {"label": "Sessão atual", "href": "/admin/session", "key": "admin_session"},
            {"label": "Parâmetros", "href": "/admin/settings", "key": "admin_settings"},
        ],
    },
]


def get_menu_config() -> list[dict]:
    return _MENU_CONFIG


def _load_template(filename: str, *, use_cache: bool = True) -> str:
    path = _TEMPLATES_DIR / filename
    try:
        mtime_ns = path.stat().st_mtime_ns
    except OSError:
        mtime_ns = 0
    if use_cache and filename in _TEMPLATE_CACHE:
        cached_mtime, cached_content = _TEMPLATE_CACHE[filename]
        if cached_mtime == mtime_ns:
            return cached_content
    content = path.read_text(encoding="utf-8")
    if use_cache:
        _TEMPLATE_CACHE[filename] = (mtime_ns, content)
    return content


def render_template(template_name: str, context: dict[str, str]) -> str:
    rendered = _load_template(template_name)
    for key, value in context.items():
        rendered = rendered.replace(f"{{{{{key}}}}}", str(value))
    return rendered


def _render_sidebar(*, active_menu: str, active_submenu: str) -> str:
    template = _load_template("partials/sidebar.html")
    items_html = []
    for item in get_menu_config():
        active = item["key"] == active_menu
        children = []
        for child in item.get("children", []):
            child_active = child["key"] == active_submenu or child["key"] == active_menu
            children.append(
                (
                    '<a href="{href}" class="r2ctl-sidebar-sublink {klass}">{label}</a>'
                ).format(
                    href=child["href"],
                    label=child["label"],
                    klass="is-active" if child_active else "",
                )
            )
        items_html.append(
            (
                '<div class="r2ctl-sidebar-group {group_klass}">'
                '<a href="{href}" class="r2ctl-sidebar-link {klass}">'
                '<span class="r2ctl-sidebar-icon">{icon}</span>'
                '<span>{label}</span>'
                "</a>"
                + ('<div class="r2ctl-sidebar-subnav">{children}</div>' if children else "")
                + "</div>"
            ).format(
                href=item["href"],
                label=item["label"],
                icon=item["icon"],
                klass="is-active" if active else "",
                group_klass="is-open" if active else "",
                children="".join(children),
            )
        )
    return template.replace("{{nav_items}}", "".join(items_html))


def build_layout_context(
    *,
    title: str,
    page_title: str,
    page_description: str,
    page_kicker: str,
    active_menu: str,
    active_submenu: str | None = None,
    page_scripts: list[str] | None = None,
    page_state: dict | None = None,
    embed: bool = False,
) -> dict[str, str]:
    if embed:
        # Modo embed (iframe, ex.: /runs/{id}/compare): sem chrome da UI.
        sidebar = ""
        topbar = ""
        statusbar = ""
    else:
        sidebar = _render_sidebar(active_menu=active_menu, active_submenu=active_submenu or active_menu)
        topbar = render_template(
            "partials/topbar.html",
            {
                "page_kicker": page_kicker,
                "page_title": page_title,
                "page_description": page_description,
            },
        )
        statusbar = _load_template("partials/statusbar.html")
    scripts = []
    if page_state:
        # Neutraliza "</" para evitar fechamento prematuro de <script> (ex.: "</script>" nos dados)
        safe_state = json.dumps(page_state, ensure_ascii=True, separators=(',', ':')).replace('</', '<\\/')
        scripts.append(
            f"<script>window.__R2CTL_PAGE_STATE__ = {safe_state};</script>"
        )
    scripts.extend(
        f'<script type="module" src="{path}?v={_VERSION}"></script>'
        for path in (page_scripts or []))
    return {
        "title": title,
        "sidebar": sidebar,
        "topbar": topbar,
        "statusbar": statusbar,
        "scripts": "\n".join(scripts),
        # cache-buster: a cada deploy a URL dos assets muda, furando cache
        # de browser com CSS/JS de versão anterior
        "asset_v": _VERSION,
    }


def render_page(
    template_name: str,
    *,
    title: str,
    page_title: str,
    page_description: str,
    page_kicker: str,
    active_menu: str,
    active_submenu: str | None = None,
    page_scripts: list[str] | None = None,
    page_state: dict | None = None,
    embed: bool = False,
) -> str:
    content = _load_template(template_name)
    context = build_layout_context(
        title=title,
        page_title=page_title,
        page_description=page_description,
        page_kicker=page_kicker,
        active_menu=active_menu,
        active_submenu=active_submenu,
        page_scripts=page_scripts,
        page_state=page_state,
        embed=embed,
    )
    context["content"] = content
    return render_template("base_embed.html" if embed else "base.html", context)


_VERSION = (Path(__file__).resolve().parent.parent.parent / "VERSION").read_text(encoding="utf-8").strip()
LOGIN_HTML = _load_template("login.html").replace("{{version}}", _VERSION)


ROUTES_CONFIG = [
    {
        "path": "/",
        "template": "dashboard.html",
        "title": "Dakota Calçados | Replay Control",
        "page_title": "Painel Operacional",
        "page_description": "Dashboard executivo com foco em filas, alertas e status resumido do gateway.",
        "page_kicker": "Controle de Replay",
        "menu": "dashboard",
        "script": "dashboard.js",
    },
    {
        "path": "/runs",
        "template": "runs.html",
        "title": "Dakota Calçados | Execuções",
        "page_title": "Execuções",
        "page_description": "Fila, histórico, falhas, comparação e conformidade em uma lista previsível.",
        "page_kicker": "Operação diária",
        "menu": "runs",
        "submenu": "runs",
        "script": "runs.js",
    },
    {
        "path": "/runs/new",
        "template": "run_new.html",
        "title": "Dakota Calçados | Nova Execução",
        "page_title": "Nova Execução",
        "page_description": "Criação dedicada de execuções com reaproveitamento de ambientes e perfis.",
        "page_kicker": "Execuções",
        "menu": "runs",
        "submenu": "runs_new",
        "script": "run_new.js",
    },
    {
        "path": "/runs/history",
        "template": "runs.html",
        "title": "Dakota Calçados | Histórico de Execuções",
        "page_title": "Execuções",
        "page_description": "Histórico consolidado de execuções e status operacionais.",
        "page_kicker": "Operação diária",
        "menu": "runs",
        "submenu": "runs_history",
        "script": "runs.js",
        "page_state": {"section": "history"},
    },
    {
        "path": "/runs/failures",
        "template": "runs.html",
        "title": "Dakota Calçados | Falhas",
        "page_title": "Execuções",
        "page_description": "Falhas e diagnósticos das execuções recentes.",
        "page_kicker": "Operação diária",
        "menu": "runs",
        "submenu": "runs_failures",
        "script": "runs.js",
        "page_state": {"section": "failures"},
    },
    {
        "path": "/runs/comparison",
        "template": "runs.html",
        "title": "Dakota Calçados | Comparação",
        "page_title": "Execuções",
        "page_description": "Comparação de execuções para apoiar investigação de regressão.",
        "page_kicker": "Operação diária",
        "menu": "runs",
        "submenu": "runs_comparison",
        "script": "runs.js",
        "page_state": {"section": "comparison"},
    },
    {
        "path": "/runs/compliance",
        "template": "runs.html",
        "title": "Dakota Calçados | Conformidade",
        "page_title": "Execuções",
        "page_description": "Visão de conformidade por execução e por sessão.",
        "page_kicker": "Operação diária",
        "menu": "runs",
        "submenu": "runs_compliance",
        "script": "runs.js",
        "page_state": {"section": "compliance"},
    },
    {
        "path": "/gateway",
        "template": "gateway.html",
        "title": "Dakota Calçados | Gateway",
        "page_title": "Gateway",
        "page_description": "Status, monitor, sessões, eventos e conformidade por sessão.",
        "page_kicker": "Infraestrutura operacional",
        "menu": "gateway",
        "script": "gateway.js",
    },
    {
        "path": "/gateway/status",
        "template": "gateway.html",
        "title": "Dakota Calçados | Gateway",
        "page_title": "Gateway",
        "page_description": "Status, monitor, sessões, eventos e conformidade por sessão.",
        "page_kicker": "Infraestrutura operacional",
        "menu": "gateway",
        "submenu": "gateway_status",
        "script": "gateway.js",
        "page_state": {"section": "status"},
    },
    {
        "path": "/gateway/monitor",
        "template": "gateway.html",
        "title": "Dakota Calçados | Gateway",
        "page_title": "Gateway",
        "page_description": "Status, monitor, sessões, eventos e conformidade por sessão.",
        "page_kicker": "Infraestrutura operacional",
        "menu": "gateway",
        "submenu": "gateway_monitor",
        "script": "gateway.js",
        "page_state": {"section": "monitor"},
    },
    {
        "path": "/gateway/sessions",
        "template": "gateway.html",
        "title": "Dakota Calçados | Gateway",
        "page_title": "Gateway",
        "page_description": "Status, monitor, sessões, eventos e conformidade por sessão.",
        "page_kicker": "Infraestrutura operacional",
        "menu": "gateway",
        "submenu": "gateway_sessions",
        "script": "gateway.js",
        "page_state": {"section": "sessions"},
    },
    {
        "path": "/gateway/events",
        "template": "gateway.html",
        "title": "Dakota Calçados | Gateway",
        "page_title": "Gateway",
        "page_description": "Status, monitor, sessões, eventos e conformidade por sessão.",
        "page_kicker": "Infraestrutura operacional",
        "menu": "gateway",
        "submenu": "gateway_events",
        "script": "gateway.js",
        "page_state": {"section": "events"},
    },
    {
        "path": "/gateway/compliance",
        "template": "gateway.html",
        "title": "Dakota Calçados | Gateway",
        "page_title": "Gateway",
        "page_description": "Status, monitor, sessões, eventos e conformidade por sessão.",
        "page_kicker": "Infraestrutura operacional",
        "menu": "gateway",
        "submenu": "gateway_compliance",
        "script": "gateway.js",
        "page_state": {"section": "compliance"},
    },
    {
        "path": "/catalog",
        "template": "catalog.html",
        "title": "Dakota Calçados | Catálogo",
        "page_title": "Catálogo",
        "page_description": "Ambientes, perfis, políticas e cenários operacionais organizados por domínio.",
        "page_kicker": "Configuração reutilizável",
        "menu": "catalog",
        "script": "catalog.js",
    },
    {
        "path": "/catalog/targets",
        "template": "catalog.html",
        "title": "Dakota Calçados | Catálogo",
        "page_title": "Catálogo",
        "page_description": "Ambientes, perfis, políticas e cenários operacionais organizados por domínio.",
        "page_kicker": "Configuração reutilizável",
        "menu": "catalog",
        "submenu": "catalog_targets",
        "script": "catalog.js",
        "page_state": {"section": "targets"},
    },
    {
        "path": "/catalog/profiles",
        "template": "catalog.html",
        "title": "Dakota Calçados | Catálogo",
        "page_title": "Catálogo",
        "page_description": "Ambientes, perfis, políticas e cenários operacionais organizados por domínio.",
        "page_kicker": "Configuração reutilizável",
        "menu": "catalog",
        "submenu": "catalog_profiles",
        "script": "catalog.js",
        "page_state": {"section": "profiles"},
    },
    {
        "path": "/catalog/policies",
        "template": "catalog.html",
        "title": "Dakota Calçados | Catálogo",
        "page_title": "Catálogo",
        "page_description": "Ambientes, perfis, políticas e cenários operacionais organizados por domínio.",
        "page_kicker": "Configuração reutilizável",
        "menu": "catalog",
        "submenu": "catalog_policies",
        "script": "catalog.js",
        "page_state": {"section": "policies"},
    },
    {
        "path": "/catalog/scenarios",
        "template": "catalog.html",
        "title": "Dakota Calçados | Catálogo",
        "page_title": "Catálogo",
        "page_description": "Ambientes, perfis, políticas e cenários operacionais organizados por domínio.",
        "page_kicker": "Configuração reutilizável",
        "menu": "catalog",
        "submenu": "catalog_scenarios",
        "script": "catalog.js",
        "page_state": {"section": "scenarios"},
    },
    {
        "path": "/observability",
        "template": "observability.html",
        "title": "Dakota Calçados | Observabilidade",
        "page_title": "Observabilidade",
        "page_description": "Diagnóstico analítico, SLA, reprocessamentos e tendências da operação.",
        "page_kicker": "Replay Suite",
        "menu": "observability",
        "submenu": "observability",
        "script": "observability.js",
    },
    {
        "path": "/observability/overview",
        "template": "observability.html",
        "title": "Dakota Calçados | Observabilidade",
        "page_title": "Observabilidade",
        "page_description": "Diagnóstico analítico, SLA, reprocessamentos e tendências da operação.",
        "page_kicker": "Replay Suite",
        "menu": "observability",
        "submenu": "observability",
        "script": "observability.js",
        "page_state": {"section": "overview"},
    },
    {
        "path": "/observability/sla",
        "template": "observability.html",
        "title": "Dakota Calçados | Observabilidade",
        "page_title": "Observabilidade",
        "page_description": "Diagnóstico analítico, SLA, reprocessamentos e tendências da operação.",
        "page_kicker": "Replay Suite",
        "menu": "observability",
        "submenu": "observability_sla",
        "script": "observability.js",
        "page_state": {"section": "sla"},
    },
    {
        "path": "/observability/reprocess",
        "template": "observability.html",
        "title": "Dakota Calçados | Observabilidade",
        "page_title": "Observabilidade",
        "page_description": "Diagnóstico analítico, SLA, reprocessamentos e tendências da operação.",
        "page_kicker": "Replay Suite",
        "menu": "observability",
        "submenu": "observability_reprocess",
        "script": "observability.js",
        "page_state": {"section": "reprocess"},
    },
    {
        "path": "/observability/regressions",
        "template": "observability.html",
        "title": "Dakota Calçados | Observabilidade",
        "page_title": "Observabilidade",
        "page_description": "Diagnóstico analítico, SLA, reprocessamentos e tendências da operação.",
        "page_kicker": "Replay Suite",
        "menu": "observability",
        "submenu": "observability_regressions",
        "script": "observability.js",
        "page_state": {"section": "regressions"},
    },
    {
        "path": "/observability/trends",
        "template": "observability.html",
        "title": "Dakota Calçados | Observabilidade",
        "page_title": "Observabilidade",
        "page_description": "Diagnóstico analítico, SLA, reprocessamentos e tendências da operação.",
        "page_kicker": "Replay Suite",
        "menu": "observability",
        "submenu": "observability_trends",
        "script": "observability.js",
        "page_state": {"section": "trends"},
    },
    {
        "path": "/observability/flows",
        "template": "observability.html",
        "title": "Dakota Calçados | Observabilidade",
        "page_title": "Observabilidade",
        "page_description": "Diagnóstico analítico, SLA, reprocessamentos e tendências da operação.",
        "page_kicker": "Replay Suite",
        "menu": "observability",
        "submenu": "observability_flows",
        "script": "observability.js",
        "page_state": {"section": "flows"},
    },
    {
        "path": "/observability/signatures",
        "template": "observability.html",
        "title": "Dakota Calçados | Observabilidade",
        "page_title": "Observabilidade",
        "page_description": "Diagnóstico analítico, SLA, reprocessamentos e tendências da operação.",
        "page_kicker": "Replay Suite",
        "menu": "observability",
        "submenu": "observability_signatures",
        "script": "observability.js",
        "page_state": {"section": "signatures"},
    },
    {
        "path": "/observability/automation",
        "template": "observability.html",
        "title": "Dakota Calçados | Observabilidade",
        "page_title": "Observabilidade",
        "page_description": "Diagnóstico analítico, SLA, reprocessamentos e tendências da operação.",
        "page_kicker": "Replay Suite",
        "menu": "observability",
        "submenu": "observability_automation",
        "script": "observability.js",
        "page_state": {"section": "automation"},
    },
    {
        "path": "/observability/resources",
        "template": "observability.html",
        "title": "Dakota Calçados | Observabilidade",
        "page_title": "Observabilidade - Recursos do Host",
        "page_description": "CPU, memória, fila de tarefas e disco do servidor durante a execução, com comparação entre ambientes.",
        "page_kicker": "Replay Suite",
        "menu": "observability",
        "submenu": "observability_resources",
        "script": "observability.js",
        "page_state": {"section": "resources"},
    },
    {
        "path": "/admin",
        "template": "admin.html",
        "title": "Dakota Calçados | Administração",
        "page_title": "Administração",
        "page_description": "Usuários, sessão atual e parâmetros globais da instância.",
        "page_kicker": "Gestão",
        "menu": "admin",
        "submenu": "admin_users",
        "script": "admin.js",
        "page_state": {"section": "users"},
    },
    {
        "path": "/admin/users",
        "template": "admin.html",
        "title": "Dakota Calçados | Administração",
        "page_title": "Administração",
        "page_description": "Usuários, sessão atual e parâmetros globais da instância.",
        "page_kicker": "Gestão",
        "menu": "admin",
        "submenu": "admin_users",
        "script": "admin.js",
        "page_state": {"section": "users"},
    },
    {
        "path": "/admin/session",
        "template": "admin.html",
        "title": "Dakota Calçados | Administração",
        "page_title": "Administração",
        "page_description": "Usuários, sessão atual e parâmetros globais da instância.",
        "page_kicker": "Gestão",
        "menu": "admin",
        "submenu": "admin_session",
        "script": "admin.js",
        "page_state": {"section": "session"},
    },
    {
        "path": "/admin/settings",
        "template": "admin.html",
        "title": "Dakota Calçados | Administração",
        "page_title": "Administração",
        "page_description": "Usuários, sessão atual e parâmetros globais da instância.",
        "page_kicker": "Gestão",
        "menu": "admin",
        "submenu": "admin_settings",
        "script": "admin.js",
        "page_state": {"section": "settings"},
    },
    {
        "match": lambda path: path.startswith("/runs/") and path.endswith("/compare"),
        "template": "run_compare.html",
        "title": "Dakota Calçados | Comparar Sessões",
        "page_title": "Comparar Sessões",
        "page_description": "Sessão capturada (esperada) e sessão observada na run, lado a lado, com seek no ponto da falha.",
        "page_kicker": "Execuções",
        "menu": "runs",
        "submenu": "runs_history",
        "script": None,  # Script integrado no template
    },
    {
        "match": lambda path: path.startswith("/runs/") and path.endswith("/replay"),
        "template": "capture_session_replay.html",
        "title": "Dakota Calçados | Replay da Sessão Observada",
        "page_title": "Sessão Observada",
        "page_description": "Reprodução da sessão observada durante a run (saída real do destino).",
        "page_kicker": "Execuções",
        "menu": "runs",
        "submenu": "runs_history",
        "script": None,  # Script integrado no template
    },
    {
        "match": lambda path: path.startswith("/runs/") and path.count("/") == 2,
        "template": "run_detail.html",
        "title": "Dakota Calçados | Detalhe da Execução",
        "page_title": "Detalhe da Execução",
        "page_description": "Inspeção de eventos, falhas, comparação e exportações da execução selecionada.",
        "page_kicker": "Execuções",
        "menu": "runs",
        "submenu": "runs_history",
        "script": "run_detail.js",
    },
    # ── Capturas (UI-first) ──────────────────────────────────────────────
    {
        "path": "/captures",
        "template": "captures.html",
        "title": "Dakota Calçados | Capturas",
        "page_title": "Capturas",
        "page_description": "Monitoramento e replay de sessões capturadas.",
        "page_kicker": "Capturas de sessão",
        "menu": "captures",
        "script": "captures.js",
        "page_state": {"section": "list"},
    },
    {
        "path": "/captures/new",
        "template": "captures.html",
        "title": "Dakota Calçados | Capturas",
        "page_title": "Capturas",
        "page_description": "Sessões de captura iniciadas automaticamente na ativação do gateway.",
        "page_kicker": "Capturas de sessão",
        "menu": "captures",
        "submenu": "captures",
        "script": "captures.js",
        "page_state": {"section": "list"},
    },
    {
        "match": lambda path: path.startswith("/captures/") and path.count("/") == 2 and path.split("/")[2] not in ("new",),
        "template": "captures.html",
        "title": "Dakota Calçados | Detalhe da Captura",
        "page_title": "Capturas",
        "page_description": "Detalhe e linha do tempo de eventos da sessão de captura.",
        "page_kicker": "Capturas de sessão",
        "menu": "captures",
        "submenu": "captures",
        "script": "captures.js",
        "page_state": {"section": "detail"},
    },
    {
        "match": lambda path: path.startswith("/captures/") and "/replay" in path,
        "template": "capture_session_replay.html",
        "title": "Dakota Calçados | Replay de Sessão",
        "page_title": "Visualização & Replay",
        "page_description": "Reproduza e analise a captura de sessão com entrada/saída do usuário.",
        "page_kicker": "Auditoria de sessão",
        "menu": "captures",
        "submenu": "captures",
        "script": None,  # Script integrado no template
    },
    {
        "path": "/synthetic",
        "template": "synthetic.html",
        "title": "Dakota Calçados | Controle Sintético",
        "page_title": "Controle Sintético",
        "page_description": "Geração de massa sintética, jornadas, stress e homologação.",
        "page_kicker": "Qualidade & Homologação",
        "menu": "engineering",
        "submenu": "engineering_synthetic",
        "script": None,  # Script integrado no template
    },
    {
        "path": "/pipeline",
        "template": "pipeline.html",
        "title": "Dakota Calçados | Pipeline",
        "page_title": "Pipeline — Descoberta → Jornadas → Síntese",
        "page_description": "Preparação integrada: análise do código-fonte, geração de jornadas e dados sintéticos.",
        "page_kicker": "Engenharia de Validação",
        "menu": "engineering",
        "submenu": "engineering_pipeline",
    },
    {
        "path": "/benchmark",
        "template": "benchmark.html",
        "title": "Dakota Calçados | Benchmark",
        "page_title": "Benchmark — AIX vs Linux",
        "page_description": "Comparação de desempenho entre ambientes com as mesmas jornadas e massa de dados.",
        "page_kicker": "Engenharia de Validação",
        "menu": "engineering",
        "submenu": "engineering_benchmark",
        "script": "benchmark.js",
    },
    {
        "path": "/assess",
        "template": "assess.html",
        "title": "Dakota Calçados | Avaliação IA",
        "page_title": "Avaliação IA",
        "page_description": "Análise inteligente do código-fonte legado: código sem uso, gargalos, riscos e recomendações.",
        "page_kicker": "Engenharia de Validação",
        "menu": "engineering",
        "submenu": "engineering_assess",
    },
    {
        "path": "/audit",
        "template": "audit.html",
        "title": "Dakota Calçados | Auditoria IA",
        "page_title": "Auditoria de Inferência IA",
        "page_description": "Trilha de auditoria: como e por que cada entidade foi inferida pela IA.",
        "page_kicker": "Engenharia de Validação",
        "menu": "engineering",
        "submenu": "engineering_audit",
    },
    {
        "path": "/journeys-report",
        "template": "journeys-report.html",
        "title": "Dakota Calçados | Relatório de Jornadas",
        "page_title": "Relatório de Decisões — Jornadas",
        "page_description": "Justificativas detalhadas para cada decisão na geração de jornadas CRUD.",
        "page_kicker": "Engenharia de Validação",
        "menu": "engineering",
        "submenu": "engineering_journeys_report",
    },
    {
        "path": "/business-rules",
        "template": "business-rules.html",
        "title": "Dakota Calçados | Regras de Negócio",
        "page_title": "Regras de Negócio — Visão de Processos",
        "page_description": "Validação orientada a fluxos de negócio: lacunas, dependências e cobertura.",
        "page_kicker": "Engenharia de Validação",
        "menu": "engineering",
        "submenu": "engineering_business_rules",
    },
]

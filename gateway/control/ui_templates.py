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
            {"label": "Nova run", "href": "/runs/new", "key": "runs_new"},
            {"label": "Fila", "href": "/runs", "key": "runs"},
            {"label": "Historico", "href": "/runs/history", "key": "runs_history"},
            {"label": "Falhas", "href": "/runs/failures", "key": "runs_failures"},
            {"label": "Comparacao", "href": "/runs/comparison", "key": "runs_comparison"},
            {"label": "Compliance", "href": "/runs/compliance", "key": "runs_compliance"},
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
            {"label": "Sessoes", "href": "/gateway/sessions", "key": "gateway_sessions"},
            {"label": "Compliance", "href": "/gateway/compliance", "key": "gateway_compliance"},
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
            {"label": "Politicas", "href": "/catalog/policies", "key": "catalog_policies"},
            {"label": "Cenarios", "href": "/catalog/scenarios", "key": "catalog_scenarios"},
        ],
    },
    {
        "label": "Observabilidade",
        "href": "/observability",
        "icon": "OB",
        "key": "observability",
        "children": [
            {"label": "Overview", "href": "/observability/overview", "key": "observability"},
            {"label": "SLA", "href": "/observability/sla", "key": "observability_sla"},
            {"label": "Reprocessamentos", "href": "/observability/reprocess", "key": "observability_reprocess"},
            {"label": "Regressoes", "href": "/observability/regressions", "key": "observability_regressions"},
            {"label": "Tendencias", "href": "/observability/trends", "key": "observability_trends"},
            {"label": "Fluxos sensiveis", "href": "/observability/flows", "key": "observability_flows"},
            {"label": "Assinaturas", "href": "/observability/signatures", "key": "observability_signatures"},
            {"label": "Automacao", "href": "/observability/automation", "key": "observability_automation"},
        ],
    },
    {
        "label": "Engenharia",
        "href": "/pipeline",
        "icon": "EG",
        "key": "engineering",
        "children": [
            {"label": "Pipeline", "href": "/pipeline", "key": "engineering_pipeline"},
            {"label": "Benchmark", "href": "/benchmark", "key": "engineering_benchmark"},
            {"label": "AI Assessment", "href": "/assess", "key": "engineering_assess"},
            {"label": "Auditoria IA", "href": "/audit", "key": "engineering_audit"},
            {"label": "Relatorio Jornadas", "href": "/journeys-report", "key": "engineering_journeys_report"},
            {"label": "Regras de Negocio", "href": "/business-rules", "key": "engineering_business_rules"},
        ],
    },
    {
        "label": "Administração",
        "href": "/admin",
        "icon": "AD",
        "key": "admin",
        "children": [
            {"label": "Usuarios", "href": "/admin/users", "key": "admin_users"},
            {"label": "Sessao atual", "href": "/admin/session", "key": "admin_session"},
            {"label": "Parametros", "href": "/admin/settings", "key": "admin_settings"},
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
) -> dict[str, str]:
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
        scripts.append(
            f"<script>window.__R2CTL_PAGE_STATE__ = {json.dumps(page_state, ensure_ascii=True, separators=(',', ':'))};</script>"
        )
    scripts.extend(f'<script type="module" src="{path}"></script>' for path in (page_scripts or []))
    return {
        "title": title,
        "sidebar": sidebar,
        "topbar": topbar,
        "statusbar": statusbar,
        "scripts": "\n".join(scripts),
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
    )
    context["content"] = content
    return render_template("base.html", context)


LOGIN_HTML = _load_template("login.html")


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
        "page_description": "Monitoramento e replay de sessões capturadas.",
        "page_kicker": "Capturas de sessão",
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
        "page_kicker": "Capturas de sessão",
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
        "page_kicker": "Capturas de sessão",
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
    {
        "path": "/synthetic",
        "template": "synthetic.html",
        "title": "Dakota Calcados | Synthetic Control",
        "page_title": "Synthetic Control",
        "page_description": "Geração de massa sintética, jornadas, stress e homologação.",
        "page_kicker": "Qualidade & Homologação",
        "menu": "synthetic",
        "script": None,  # Script integrado no template
    },
    {
        "path": "/pipeline",
        "template": "pipeline.html",
        "title": "Dakota Calcados | Pipeline",
        "page_title": "Pipeline — Discovery → Journey → Synthetic",
        "page_description": "Pipeline integrado de analise de codigo-fonte, geracao de jornadas e dados sinteticos.",
        "page_kicker": "Engenharia de Validacao",
        "menu": "engineering",
        "submenu": "engineering_pipeline",
    },
    {
        "path": "/benchmark",
        "template": "benchmark.html",
        "title": "Dakota Calcados | Benchmark",
        "page_title": "Benchmark — AIX vs Linux",
        "page_description": "Comparacao de performance entre ambientes com mesmas jornadas e massa.",
        "page_kicker": "Engenharia de Validacao",
        "menu": "engineering",
        "submenu": "engineering_benchmark",
    },
    {
        "path": "/assess",
        "template": "assess.html",
        "title": "Dakota Calcados | AI Assessment",
        "page_title": "AI Assessment",
        "page_description": "Analise inteligente: garbage collector, gargalos, riscos e recomendacoes.",
        "page_kicker": "Engenharia de Validacao",
        "menu": "engineering",
        "submenu": "engineering_assess",
    },
    {
        "path": "/audit",
        "template": "audit.html",
        "title": "Dakota Calcados | Auditoria IA",
        "page_title": "Auditoria de Inferencia IA",
        "page_description": "Trilha de auditoria: como e por que cada entidade foi inferida pela IA.",
        "page_kicker": "Engenharia de Validacao",
        "menu": "engineering",
        "submenu": "engineering_audit",
    },
    {
        "path": "/journeys-report",
        "template": "journeys-report.html",
        "title": "Dakota Calcados | Relatorio de Jornadas",
        "page_title": "Relatorio de Decisoes — Jornadas",
        "page_description": "Justificativas detalhadas para cada decisao na geracao de jornadas CRUD.",
        "page_kicker": "Engenharia de Validacao",
        "menu": "engineering",
        "submenu": "engineering_journeys_report",
    },
    {
        "path": "/business-rules",
        "template": "business-rules.html",
        "title": "Dakota Calcados | Regras de Negocio",
        "page_title": "Regras de Negocio — Visao de Processos",
        "page_description": "Validacao orientada a fluxos de negocio: gaps, dependencias e cobertura.",
        "page_kicker": "Engenharia de Validacao",
        "menu": "engineering",
        "submenu": "engineering_business_rules",
    },
]

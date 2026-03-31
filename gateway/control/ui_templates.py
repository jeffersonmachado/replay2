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

from __future__ import annotations

import json
from urllib.parse import parse_qs

from dakota_gateway.replay_control import query_one
from control.routes.route_helpers import parse_int, write_json
from control.routes.gateway_routes import _validated_log_dir
from control.services.host_metrics_service import build_export, query_host_metrics, run_window
from control.services.report_overview_service import build_observability_overview, build_runs_trend_report
from control.services.analytics_scenario_service import (
    delete_analytics_scenario,
    list_analytics_scenarios,
    save_analytics_scenario,
    set_analytics_scenario_favorite,
)

def handle_observability_get_route(handler, parsed_path) -> bool:
    path = parsed_path.path
    if path == "/api/observability/host-metrics":
        user = handler._require()
        if not user:
            return True
        qs = parse_qs(parsed_path.query or "")
        run_id = parse_int((qs.get("run_id") or ["0"])[0] or 0, 0, min_value=0)
        max_points = parse_int((qs.get("max_points") or ["360"])[0] or 360, 360, min_value=10, max_value=2000)
        con = handler._db()
        try:
            if run_id:
                window = run_window(con, run_id)
                if not window:
                    write_json(handler, 404, {"error": f"run {run_id} nao encontrada"})
                    return True
                from_ms, to_ms = window["from_ms"], window["to_ms"]
            else:
                from_ms = parse_int((qs.get("from_ms") or ["0"])[0] or 0, 0, min_value=0)
                to_ms = parse_int((qs.get("to_ms") or ["0"])[0] or 0, 0, min_value=0)
            if not from_ms or not to_ms or to_ms < from_ms:
                write_json(handler, 400, {"error": "informe run_id ou from_ms/to_ms validos"})
                return True
            payload = query_host_metrics(con, from_ms, to_ms, max_points=max_points)
        finally:
            handler._db_release(con)
        write_json(handler, 200, payload)
        return True

    if path == "/api/observability/host-metrics/export":
        user = handler._require()
        if not user:
            return True
        qs = parse_qs(parsed_path.query or "")
        run_id = parse_int((qs.get("run_id") or ["0"])[0] or 0, 0, min_value=0)
        if not run_id:
            write_json(handler, 400, {"error": "run_id obrigatorio"})
            return True
        con = handler._db()
        try:
            payload = build_export(con, run_id)
        finally:
            handler._db_release(con)
        if not payload:
            write_json(handler, 404, {"error": f"run {run_id} nao encontrada"})
            return True
        write_json(handler, 200, payload)
        return True

    if path == "/api/observability/overview":
        user = handler._require()
        if not user:
            return True
        qs = parse_qs(parsed_path.query or "")
        log_dir = _validated_log_dir(handler, (qs.get("log_dir") or [""])[0])
        if log_dir is None:
            write_json(handler, 400, {"error": "log_dir fora do diretorio de capturas configurado"})
            return True
        limit = parse_int((qs.get("limit") or ["40"])[0] or 40, 40, min_value=1, max_value=200)
        environment = str((qs.get("environment") or [""])[0])
        created_from_ms = parse_int((qs.get("created_from_ms") or ["0"])[0] or 0, 0, min_value=0)
        created_to_ms = parse_int((qs.get("created_to_ms") or ["0"])[0] or 0, 0, min_value=0)
        run_limit = parse_int((qs.get("run_limit") or ["50"])[0] or 50, 50, min_value=1)
        con = handler._db()
        try:
            payload = build_observability_overview(
                con,
                log_dir=log_dir,
                limit=limit,
                user_id=int(user["id"]),
                environment=environment,
                created_from_ms=created_from_ms,
                created_to_ms=created_to_ms,
                run_limit=run_limit,
            )
        finally:
            handler._db_release(con)
        write_json(handler, 200, payload)
        return True

    if path == "/api/observability/scenarios":
        user = handler._require()
        if not user:
            return True
        qs = parse_qs(parsed_path.query or "")
        visibility = str((qs.get("visibility") or [""])[0])
        tag = str((qs.get("tag") or [""])[0])
        con = handler._db()
        try:
            payload = {
                "scenarios": list_analytics_scenarios(
                    con,
                    scope="observability",
                    user_id=int(user["id"]),
                    visibility=visibility,
                    tag=tag,
                )
            }
        finally:
            handler._db_release(con)
        write_json(handler, 200, payload)
        return True

    if path == "/api/reports/runs/trend":
        user = handler._require()
        if not user:
            return True
        qs = parse_qs(parsed_path.query or "")
        run_limit = parse_int((qs.get("run_limit") or ["50"])[0] or 50, 50, min_value=1)
        environment = str((qs.get("environment") or [""])[0])
        created_from_ms = parse_int((qs.get("created_from_ms") or ["0"])[0] or 0, 0, min_value=0)
        created_to_ms = parse_int((qs.get("created_to_ms") or ["0"])[0] or 0, 0, min_value=0)
        con = handler._db()
        try:
            payload = build_runs_trend_report(
                con,
                run_limit=run_limit,
                environment=environment,
                created_from_ms=created_from_ms,
                created_to_ms=created_to_ms,
            )
        finally:
            handler._db_release(con)
        write_json(handler, 200, payload)
        return True

    return False

def handle_observability_post_route(handler, parsed_path, body: dict) -> bool:
    path = parsed_path.path
    if path == "/api/observability/scenarios":
        user = handler._require()
        if not user:
            return True
        try:
            con = handler._db()
            try:
                scenario_id = save_analytics_scenario(
                    con,
                    name=str(body.get("name") or ""),
                    description=str(body.get("description") or ""),
                    scope="observability",
                    visibility=str(body.get("visibility") or "private"),
                    tags=body.get("tags"),
                    filters=body.get("filters") if isinstance(body.get("filters"), dict) else {},
                    created_by=int(user["id"]),
                )
                payload = {
                    "ok": True,
                    "scenario_id": scenario_id,
                    "scenarios": list_analytics_scenarios(con, scope="observability", user_id=int(user["id"])),
                }
            finally:
                handler._db_release(con)
        except ValueError as exc:
            write_json(handler, 400, {"ok": False, "error": str(exc)})
            return True
        write_json(handler, 200, payload)
        return True

    if path.startswith("/api/observability/scenarios/") and path.endswith("/favorite"):
        user = handler._require()
        if not user:
            return True
        parts = path.split("/")
        if len(parts) < 6:
            handler.send_response(404)
            handler.end_headers()
            return True
        scenario_id = int(parts[4])
        favorite = bool(body.get("favorite"))
        con = handler._db()
        try:
            row = query_one(con, "SELECT id FROM analytics_scenarios WHERE id=? AND scope=?", (scenario_id, "observability"))
            if not row:
                handler.send_response(404)
                handler.end_headers()
                return True
            set_analytics_scenario_favorite(con, scenario_id, int(user["id"]), favorite)
            payload = {"ok": True, "scenarios": list_analytics_scenarios(con, scope="observability", user_id=int(user["id"]))}
        finally:
            handler._db_release(con)
        write_json(handler, 200, payload)
        return True

    return False

def handle_observability_delete_route(handler, parsed_path) -> bool:
    path = parsed_path.path
    if not path.startswith("/api/observability/scenarios/"):
        return False
    user = handler._require()
    if not user:
        return True
    parts = path.split("/")
    if len(parts) < 5:
        handler.send_response(404)
        handler.end_headers()
        return True
    scenario_id = int(parts[4])
    con = handler._db()
    try:
        row = query_one(con, "SELECT created_by FROM analytics_scenarios WHERE id=? AND scope=?", (scenario_id, "observability"))
        if not row:
            deleted = False
        elif user["role"] != "admin" and int(row["created_by"] or 0) != int(user["id"]):
            write_json(handler, 403, {"ok": False, "error": "sem permissão para excluir este cenário"})
            return True
        else:
            deleted = delete_analytics_scenario(con, scenario_id, scope="observability")
        payload = {"ok": deleted, "scenarios": list_analytics_scenarios(con, scope="observability", user_id=int(user["id"]))}
    finally:
        handler._db_release(con)
    write_json(handler, 200 if deleted else 404, payload)
    return True

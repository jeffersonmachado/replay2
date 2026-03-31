from __future__ import annotations

import json

from dakota_gateway.replay_control import query_one
from control.services.scenario_service import (
    delete_operational_scenario,
    instantiate_run_from_scenario,
    list_operational_scenarios,
    save_operational_scenario,
    set_operational_scenario_favorite,
)


def _write_json(handler, status_code: int, payload: dict) -> None:
    handler.send_response(status_code)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.end_headers()
    handler.wfile.write(json.dumps(payload, ensure_ascii=False).encode("utf-8"))


def handle_operational_get_route(handler, parsed_path, parse_qs_fn) -> bool:
    if parsed_path.path != "/api/operational-scenarios":
        return False
    user = handler._require()
    if not user:
        return True
    qs = parse_qs_fn(parsed_path.query or "")
    scenario_type = str((qs.get("scenario_type") or [""])[0])
    environment = str((qs.get("environment") or [""])[0])
    squad = str((qs.get("squad") or [""])[0])
    area = str((qs.get("area") or [""])[0])
    tag = str((qs.get("tag") or [""])[0])
    owner = str((qs.get("owner") or [""])[0])
    sla_status = str((qs.get("sla_status") or [""])[0])
    favorite_only = str((qs.get("favorite_only") or [""])[0]).strip().lower() in {"1", "true", "yes", "sim"}
    usage_user = str((qs.get("usage_user") or [""])[0])
    used_from_ms = int((qs.get("used_from_ms") or ["0"])[0] or 0)
    used_to_ms = int((qs.get("used_to_ms") or ["0"])[0] or 0)
    sort_by = str((qs.get("sort_by") or ["updated"])[0] or "updated")
    con = handler._db()
    try:
        payload = {
            "scenarios": list_operational_scenarios(
                con,
                user_id=int(user["id"]),
                scenario_type=scenario_type,
                environment=environment,
                squad=squad,
                area=area,
                tag=tag,
                owner=owner,
                sla_status=sla_status,
                favorite_only=favorite_only,
                usage_user=usage_user,
                used_from_ms=used_from_ms,
                used_to_ms=used_to_ms,
                sort_by=sort_by,
            )
        }
    finally:
        handler._db_release(con)
    _write_json(handler, 200, payload)
    return True


def handle_operational_post_route(handler, parsed_path, body: dict) -> bool:
    path = parsed_path.path
    if path == "/api/operational-scenarios":
        user = handler._require(roles={"admin", "operator"})
        if not user:
            return True
        try:
            con = handler._db()
            try:
                scenario_id = save_operational_scenario(con, payload=body, created_by=int(user["id"]))
                payload = {"ok": True, "scenario_id": scenario_id, "scenarios": list_operational_scenarios(con, user_id=int(user["id"]))}
            finally:
                handler._db_release(con)
        except ValueError as exc:
            _write_json(handler, 400, {"ok": False, "error": str(exc)})
            return True
        _write_json(handler, 200, payload)
        return True

    if path.startswith("/api/operational-scenarios/") and path.endswith("/favorite"):
        user = handler._require()
        if not user:
            return True
        parts = path.split("/")
        if len(parts) < 5:
            handler.send_response(404)
            handler.end_headers()
            return True
        scenario_id = int(parts[3])
        favorite = bool(body.get("favorite"))
        con = handler._db()
        try:
            row = query_one(con, "SELECT id FROM operational_scenarios WHERE id=?", (scenario_id,))
            if not row:
                handler.send_response(404)
                handler.end_headers()
                return True
            set_operational_scenario_favorite(con, scenario_id, int(user["id"]), favorite)
            payload = {"ok": True, "scenarios": list_operational_scenarios(con, user_id=int(user["id"]))}
        finally:
            handler._db_release(con)
        _write_json(handler, 200, payload)
        return True

    if path.startswith("/api/operational-scenarios/") and path.endswith("/instantiate-run"):
        user = handler._require(roles={"admin", "operator"})
        if not user:
            return True
        parts = path.split("/")
        if len(parts) < 5:
            handler.send_response(404)
            handler.end_headers()
            return True
        scenario_id = int(parts[3])
        con = handler._db()
        try:
            run_id = instantiate_run_from_scenario(con, scenario_id, int(user["id"]))
        except ValueError as exc:
            _write_json(handler, 404, {"ok": False, "error": str(exc)})
            return True
        finally:
            handler._db_release(con)
        _write_json(handler, 200, {"ok": True, "run_id": run_id})
        return True

    return False


def handle_operational_delete_route(handler, parsed_path) -> bool:
    if not parsed_path.path.startswith("/api/operational-scenarios/"):
        return False
    user = handler._require(roles={"admin", "operator"})
    if not user:
        return True
    parts = parsed_path.path.split("/")
    if len(parts) < 4:
        handler.send_response(404)
        handler.end_headers()
        return True
    scenario_id = int(parts[3])
    con = handler._db()
    try:
        deleted = delete_operational_scenario(con, scenario_id)
        payload = {"ok": deleted, "scenarios": list_operational_scenarios(con)}
    finally:
        handler._db_release(con)
    _write_json(handler, 200 if deleted else 404, payload)
    return True

from __future__ import annotations

import json
import sqlite3
from urllib.parse import parse_qs

from dakota_gateway.compliance import normalize_direct_ssh_policy_payload
from dakota_gateway.replay_control import query_one
from dakota_gateway.state_db import exec1, now_ms
from control.services.environment_service import (
    list_connection_profiles,
    list_target_environments,
    normalize_connection_profile_payload,
    normalize_target_environment_payload,
)


def _write_json(handler, status_code: int, payload: dict) -> None:
    handler.send_response(status_code)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.end_headers()
    handler.wfile.write(json.dumps(payload, ensure_ascii=False).encode("utf-8"))


def handle_catalog_get_route(handler, parsed_path) -> bool:
    path = parsed_path.path
    if path == "/api/targets":
        user = handler._require()
        if not user:
            return True
        qs = parse_qs(parsed_path.query or "")
        gateway_required_filter = str((qs.get("gateway_required") or [""])[0]).strip().lower()
        con = handler._db()
        try:
            targets = list_target_environments(con)
            if gateway_required_filter in {"0", "1", "true", "false", "yes", "no", "sim", "nao", "não"}:
                wanted = gateway_required_filter in {"1", "true", "yes", "sim"}
                targets = [item for item in targets if bool(item.get("gateway_required")) is wanted]
            payload = {"targets": targets}
        finally:
            handler._db_release(con)
        _write_json(handler, 200, payload)
        return True

    if path.startswith("/api/targets/") and path.count("/") == 3:
        user = handler._require()
        if not user:
            return True
        target_id = int(path.split("/")[3])
        con = handler._db()
        try:
            payload = next((item for item in list_target_environments(con) if int(item["id"]) == target_id), None)
        finally:
            handler._db_release(con)
        if not payload:
            handler.send_response(404)
            handler.end_headers()
            return True
        _write_json(handler, 200, {"target": payload})
        return True

    if path == "/api/connection-profiles":
        user = handler._require()
        if not user:
            return True
        con = handler._db()
        try:
            payload = {"connection_profiles": list_connection_profiles(con)}
        finally:
            handler._db_release(con)
        _write_json(handler, 200, payload)
        return True

    return False


def handle_catalog_post_route(handler, parsed_path, body: dict) -> bool:
    path = parsed_path.path
    if path == "/api/targets":
        user = handler._require(roles={"admin", "operator"})
        if not user:
            return True
        con = handler._db()
        try:
            clean = normalize_target_environment_payload(body)
            now = now_ms()
            target_id = exec1(
                con,
                """
                INSERT INTO target_environments(
                    env_id, name, host, port, platform, transport_hint,
                    gateway_required, direct_ssh_policy, capture_start_mode,
                    capture_compliance_mode, allow_admin_direct_access,
                    description, metadata_json, created_at_ms, updated_at_ms
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    clean["env_id"],
                    clean["name"],
                    clean["host"],
                    clean["port"],
                    clean["platform"],
                    clean["transport_hint"],
                    1 if clean["gateway_required"] else 0,
                    clean["direct_ssh_policy"],
                    clean["capture_start_mode"],
                    clean["capture_compliance_mode"],
                    1 if clean["allow_admin_direct_access"] else 0,
                    clean["description"] or None,
                    json.dumps(clean["metadata"], ensure_ascii=False),
                    now,
                    now,
                ),
            )
        except (ValueError, sqlite3.IntegrityError) as exc:
            _write_json(handler, 400, {"ok": False, "error": str(exc)})
            return True
        finally:
            handler._db_release(con)
        _write_json(handler, 200, {"ok": True, "id": target_id})
        return True

    if path.startswith("/api/targets/") and path.endswith("/policy"):
        user = handler._require(roles={"admin", "operator"})
        if not user:
            return True
        parts = path.split("/")
        if len(parts) < 5:
            handler.send_response(404)
            handler.end_headers()
            return True
        target_id = int(parts[3])
        con = handler._db()
        try:
            existing = query_one(con, "SELECT id FROM target_environments WHERE id=?", (target_id,))
            if not existing:
                handler.send_response(404)
                handler.end_headers()
                return True
            policy = normalize_direct_ssh_policy_payload(body)
            con.execute(
                """
                UPDATE target_environments
                SET gateway_required=?, direct_ssh_policy=?, capture_start_mode=?,
                    capture_compliance_mode=?, allow_admin_direct_access=?, updated_at_ms=?
                WHERE id=?
                """,
                (
                    1 if policy["gateway_required"] else 0,
                    policy["direct_ssh_policy"],
                    policy["capture_start_mode"],
                    policy["capture_compliance_mode"],
                    1 if policy["allow_admin_direct_access"] else 0,
                    now_ms(),
                    target_id,
                ),
            )
            payload = next((item for item in list_target_environments(con) if int(item["id"]) == target_id), None)
        finally:
            handler._db_release(con)
        _write_json(handler, 200, {"ok": True, "target": payload})
        return True

    if path == "/api/connection-profiles":
        user = handler._require(roles={"admin", "operator"})
        if not user:
            return True
        con = handler._db()
        try:
            clean = normalize_connection_profile_payload(body)
            now = now_ms()
            profile_id = exec1(
                con,
                """
                INSERT INTO connection_profiles(
                    profile_id, name, transport, username, port, command, credential_ref, auth_mode, options_json, created_at_ms, updated_at_ms
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    clean["profile_id"],
                    clean["name"],
                    clean["transport"],
                    clean["username"] or None,
                    clean["port"],
                    clean["command"] or None,
                    clean["credential_ref"] or None,
                    clean["auth_mode"],
                    json.dumps(clean["options"], ensure_ascii=False),
                    now,
                    now,
                ),
            )
        except (ValueError, sqlite3.IntegrityError) as exc:
            _write_json(handler, 400, {"ok": False, "error": str(exc)})
            return True
        finally:
            handler._db_release(con)
        _write_json(handler, 200, {"ok": True, "id": profile_id})
        return True

    return False

from __future__ import annotations

import json
import re
import secrets

from dakota_gateway.compliance import normalize_direct_ssh_policy_payload, normalize_target_policy, policy_to_params, target_policy_reason
from dakota_gateway.state_db import query_all, query_one


def resource_slug(value: str, fallback_prefix: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", str(value or "").strip().lower()).strip("-")
    if cleaned:
        return cleaned[:48]
    return f"{fallback_prefix}-{secrets.token_hex(4)}"


def normalize_target_environment_payload(payload: dict | None) -> dict:
    raw = payload or {}
    env_id = str(raw.get("env_id") or "").strip()
    name = str(raw.get("name") or "").strip()
    host = str(raw.get("host") or "").strip()
    if not name:
        raise ValueError("name obrigatório")
    if not host:
        raise ValueError("host obrigatório")
    transport_hint = str(raw.get("transport_hint") or "ssh").strip().lower()
    if transport_hint not in {"ssh", "telnet"}:
        raise ValueError("transport_hint inválido")
    platform_name = str(raw.get("platform") or "linux").strip().lower()
    port_raw = raw.get("port")
    port = None
    if port_raw not in (None, ""):
        port = int(port_raw)
        if port <= 0:
            raise ValueError("port inválida")
    metadata = dict(raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {})
    gateway_host = str(raw.get("gateway_host") or metadata.get("gateway_host") or "").strip()
    gateway_user = str(raw.get("gateway_user") or metadata.get("gateway_user") or "").strip()
    gateway_port_raw = raw.get("gateway_port", metadata.get("gateway_port"))
    gateway_port = 0
    if gateway_port_raw not in (None, ""):
        gateway_port = int(gateway_port_raw)
        if gateway_port <= 0:
            raise ValueError("gateway_port inválida")
    if gateway_host:
        metadata["gateway_host"] = gateway_host
    if gateway_user:
        metadata["gateway_user"] = gateway_user
    if gateway_port:
        metadata["gateway_port"] = gateway_port
    policy = normalize_direct_ssh_policy_payload(raw)
    return {
        "env_id": env_id or resource_slug(name, "env"),
        "name": name,
        "host": host,
        "port": port,
        "platform": platform_name,
        "transport_hint": transport_hint,
        "gateway_required": policy["gateway_required"],
        "direct_ssh_policy": policy["direct_ssh_policy"],
        "capture_start_mode": policy["capture_start_mode"],
        "capture_compliance_mode": policy["capture_compliance_mode"],
        "allow_admin_direct_access": policy["allow_admin_direct_access"],
        "description": str(raw.get("description") or "").strip(),
        "metadata": metadata,
    }


def normalize_connection_profile_payload(payload: dict | None) -> dict:
    raw = payload or {}
    profile_id = str(raw.get("profile_id") or "").strip()
    name = str(raw.get("name") or "").strip()
    if not name:
        raise ValueError("name obrigatório")
    transport = str(raw.get("transport") or "ssh").strip().lower()
    if transport not in {"ssh", "telnet"}:
        raise ValueError("transport inválido")
    port_raw = raw.get("port")
    port = None
    if port_raw not in (None, ""):
        port = int(port_raw)
        if port <= 0:
            raise ValueError("port inválida")
    options = raw.get("options") if isinstance(raw.get("options"), dict) else {}
    return {
        "profile_id": profile_id or resource_slug(name, "profile"),
        "name": name,
        "transport": transport,
        "username": str(raw.get("username") or "").strip(),
        "port": port,
        "command": str(raw.get("command") or "").strip(),
        "credential_ref": str(raw.get("credential_ref") or "").strip(),
        "auth_mode": str(raw.get("auth_mode") or "external").strip().lower(),
        "options": options,
    }


def list_target_environments(con) -> list[dict]:
    rows = query_all(
        con,
        """
        SELECT id, env_id, name, host, port, platform, transport_hint,
               gateway_required, direct_ssh_policy, capture_start_mode,
               capture_compliance_mode, allow_admin_direct_access,
               description, metadata_json, created_at_ms, updated_at_ms
        FROM target_environments
        ORDER BY name, id
        """,
        (),
    )
    items = []
    for row in rows:
        item = dict(row)
        try:
            item["metadata"] = json.loads(item.pop("metadata_json") or "{}")
        except Exception:
            item["metadata"] = {}
        item["gateway_host"] = str(item["metadata"].get("gateway_host") or "")
        item["gateway_user"] = str(item["metadata"].get("gateway_user") or "")
        item["gateway_port"] = int(item["metadata"].get("gateway_port") or 0)
        item["target_policy"] = normalize_target_policy(item)
        item["target_policy_reason"] = target_policy_reason(item)
        items.append(item)
    return items


def list_connection_profiles(con) -> list[dict]:
    rows = query_all(
        con,
        """
        SELECT id, profile_id, name, transport, username, port, command, credential_ref, auth_mode, options_json, created_at_ms, updated_at_ms
        FROM connection_profiles
        ORDER BY name, id
        """,
        (),
    )
    items = []
    for row in rows:
        item = dict(row)
        try:
            item["options"] = json.loads(item.pop("options_json") or "{}")
        except Exception:
            item["options"] = {}
        items.append(item)
    return items


def resolve_run_target_request(con, payload: dict | None) -> tuple[dict, dict]:
    raw = payload or {}
    params = dict(raw.get("params") if isinstance(raw.get("params"), dict) else {})
    target_env_id_raw = raw.get("target_env_id")
    connection_profile_id_raw = raw.get("connection_profile_id")
    target_env = None
    profile = None
    if target_env_id_raw not in (None, ""):
        target_env = query_one(con, "SELECT * FROM target_environments WHERE id=?", (int(target_env_id_raw),))
        if not target_env:
            raise ValueError("target_env_id inexistente")
    if connection_profile_id_raw not in (None, ""):
        profile = query_one(con, "SELECT * FROM connection_profiles WHERE id=?", (int(connection_profile_id_raw),))
        if not profile:
            raise ValueError("connection_profile_id inexistente")

    target_host = str(raw.get("target_host") or (target_env["host"] if target_env else "")).strip()
    target_user = str(raw.get("target_user") or (profile["username"] if profile else "")).strip()
    target_command = str(raw.get("target_command") or (profile["command"] if profile else "")).strip()
    if not target_host:
        raise ValueError("target_host obrigatório")

    resolved_params = dict(params)
    if target_env:
        resolved_params.setdefault("target_environment", str(target_env["env_id"] or ""))
        resolved_params.setdefault("environment", str(target_env["name"] or target_env["env_id"] or ""))
        resolved_params.setdefault("target_platform", str(target_env["platform"] or "linux"))
        if target_env["port"] not in (None, ""):
            resolved_params.setdefault("target_port", int(target_env["port"]))
        resolved_params.setdefault("target_transport_hint", str(target_env["transport_hint"] or "ssh"))
        resolved_params.setdefault("target_policy", policy_to_params(target_env))
        try:
            target_metadata = json.loads(target_env["metadata_json"] or "{}")
        except Exception:
            target_metadata = {}
        if isinstance(target_metadata, dict):
            gateway_host = str(target_metadata.get("gateway_host") or "").strip()
            gateway_user = str(target_metadata.get("gateway_user") or "").strip()
            gateway_port = int(target_metadata.get("gateway_port") or 0)
            if gateway_host:
                resolved_params.setdefault("gateway_host", gateway_host)
                resolved_params.setdefault("gateway_route_mode", "proxyjump")
            if gateway_user:
                resolved_params.setdefault("gateway_user", gateway_user)
            if gateway_port:
                resolved_params.setdefault("gateway_port", gateway_port)
    if profile:
        resolved_params.setdefault("connection_profile_id", int(profile["id"]))
        resolved_params.setdefault("connection_profile_name", str(profile["name"] or ""))
        resolved_params["transport"] = str(profile["transport"] or "ssh")
        if profile["port"] not in (None, ""):
            resolved_params["target_port"] = int(profile["port"])
        if profile["credential_ref"]:
            resolved_params["credential_ref"] = str(profile["credential_ref"])
        if profile["auth_mode"]:
            resolved_params["auth_mode"] = str(profile["auth_mode"])
        try:
            options = json.loads(profile["options_json"] or "{}")
        except Exception:
            options = {}
        if isinstance(options, dict) and options:
            merged_options = dict(resolved_params.get("connection_options") or {})
            merged_options.update(options)
            resolved_params["connection_options"] = merged_options
    elif "transport" not in resolved_params:
        resolved_params["transport"] = str((target_env["transport_hint"] if target_env else "ssh") or "ssh")

    return (
        {
            "target_env_id": int(target_env["id"]) if target_env else None,
            "connection_profile_id": int(profile["id"]) if profile else None,
            "target_host": target_host,
            "target_user": target_user,
            "target_command": target_command,
            "target_policy": normalize_target_policy(target_env) if target_env else normalize_target_policy({}),
        },
        resolved_params,
    )

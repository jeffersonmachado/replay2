"""
Serviço de estado do gateway — fonte única de verdade para ativação/desativação
e detecção automática de ambiente.
"""
from __future__ import annotations

import json
import os
import platform
import socket

from dakota_gateway.state_db import query_one


def build_operational_policy(*, logical_active: bool, service_running: bool | None = None) -> dict:
    """Monta a política operacional desejada/efetiva para o gateway."""
    desired_ssh_route = "gateway_proxy" if logical_active else "direct_port_22"
    capture_available = bool(logical_active)

    if service_running is None:
        return {
            "desired_mode": "gateway_active" if logical_active else "gateway_inactive",
            "desired_ssh_route": desired_ssh_route,
            "effective_ssh_route": None,
            "capture_available": capture_available,
            "policy_ok": None,
            "reason": "estado efetivo do serviço não informado",
        }

    effective_ssh_route = "gateway_proxy" if bool(service_running) else "direct_port_22"
    policy_ok = effective_ssh_route == desired_ssh_route
    reason = "ok"
    if not policy_ok and logical_active:
        reason = "gateway lógico ativo, mas serviço SSH/Gateway não está ativo"
    elif not policy_ok and not logical_active:
        reason = "gateway lógico inativo, mas serviço SSH/Gateway permanece ativo"

    return {
        "desired_mode": "gateway_active" if logical_active else "gateway_inactive",
        "desired_ssh_route": desired_ssh_route,
        "effective_ssh_route": effective_ssh_route,
        "capture_available": capture_available,
        "policy_ok": policy_ok,
        "reason": reason,
    }


def detect_runtime_environment() -> dict:
    """Detecta ambiente pelo runtime da instância atual (sem input manual)."""
    hostname = ""
    try:
        hostname = socket.gethostname()
    except Exception:
        pass
    fqdn = ""
    try:
        fqdn = socket.getfqdn()
    except Exception:
        pass
    return {
        "hostname": hostname,
        "fqdn": fqdn,
        "platform": platform.system().lower(),
        "platform_release": platform.release(),
        "pid": os.getpid(),
        "env_name": (
            os.environ.get("DAKOTA_ENV_NAME")
            or os.environ.get("ENV_NAME")
            or os.environ.get("ENVIRONMENT")
            or ""
        ),
        "instance_id": (
            os.environ.get("DAKOTA_INSTANCE_ID")
            or os.environ.get("INSTANCE_ID")
            or ""
        ),
        "tenant": (
            os.environ.get("DAKOTA_TENANT")
            or os.environ.get("TENANT")
            or ""
        ),
    }


def get_gateway_state(con) -> dict:
    """Retorna o estado atual do gateway a partir do banco (fonte única de verdade)."""
    row = query_one(con, "SELECT * FROM gateway_state WHERE id=1")
    if not row:
        policy = build_operational_policy(logical_active=False)
        return {
            "active": False,
            "activated_at_ms": None,
            "activated_by_id": None,
            "activated_by_username": None,
            "deactivated_at_ms": None,
            "deactivated_by_username": None,
            "environment": detect_runtime_environment(),
            "connection_profile_id": None,
            "operational_user_id": None,
            "capture_enabled": False,
            "policy": policy,
            "updated_at_ms": None,
        }
    state = dict(row)
    try:
        env = json.loads(state.pop("environment_json") or "{}")
    except Exception:
        env = {}
    # Runtime env always overrides stored env with fresh values
    state["environment"] = detect_runtime_environment()
    # Preserve stored env_name/instance_id/tenant if set
    stored_env = env or {}
    if stored_env.get("env_name"):
        state["environment"]["env_name"] = stored_env["env_name"]
    if stored_env.get("instance_id"):
        state["environment"]["instance_id"] = stored_env["instance_id"]
    if stored_env.get("tenant"):
        state["environment"]["tenant"] = stored_env["tenant"]
    state["active"] = bool(state.get("active"))
    state["policy"] = build_operational_policy(logical_active=state["active"])
    state["capture_enabled"] = bool(state["policy"].get("capture_available"))
    return state


def activate_gateway(
    con,
    *,
    user_id: int,
    username: str,
    connection_profile_id: int | None = None,
    operational_user_id: int | None = None,
    now_ms_fn,
) -> dict:
    """Ativa o gateway com rastreio completo de quem ativou e quando."""
    env = detect_runtime_environment()
    ts = now_ms_fn()
    con.execute(
        """
        INSERT INTO gateway_state(
            id, active, activated_at_ms, activated_by_id, activated_by_username,
            deactivated_at_ms, deactivated_by_username,
            environment_json, connection_profile_id, operational_user_id,
            capture_enabled, updated_at_ms
        ) VALUES (1, 1, ?, ?, ?, NULL, NULL, ?, ?, ?, 0, ?)
        ON CONFLICT(id) DO UPDATE SET
            active = 1,
            activated_at_ms = excluded.activated_at_ms,
            activated_by_id = excluded.activated_by_id,
            activated_by_username = excluded.activated_by_username,
            deactivated_at_ms = NULL,
            deactivated_by_username = NULL,
            environment_json = excluded.environment_json,
            connection_profile_id = excluded.connection_profile_id,
            operational_user_id = excluded.operational_user_id,
            capture_enabled = 0,
            updated_at_ms = excluded.updated_at_ms
        """,
        (ts, user_id, username, json.dumps(env), connection_profile_id, operational_user_id, ts),
    )
    return get_gateway_state(con)


def deactivate_gateway(con, *, username: str, now_ms_fn) -> dict:
    """Desativa o gateway e registra quem desativou."""
    ts = now_ms_fn()
    con.execute(
        """
        INSERT INTO gateway_state(id, active, deactivated_at_ms, deactivated_by_username, updated_at_ms)
        VALUES (1, 0, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            active = 0,
            deactivated_at_ms = excluded.deactivated_at_ms,
            deactivated_by_username = excluded.deactivated_by_username,
            capture_enabled = 0,
            updated_at_ms = excluded.updated_at_ms
        """,
        (ts, username, ts),
    )
    return get_gateway_state(con)

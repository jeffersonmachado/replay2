"""
Serviço de estado do gateway — fonte única de verdade para ativação/desativação
e detecção automática de ambiente.
"""
from __future__ import annotations

import json
import os
import platform
import socket

import subprocess

from dakota_gateway.state_db import query_one


def _check_capture_process_alive() -> bool:
    """Verifica se ha pelo menos um processo capture-session rodando e saudavel.

    Procura por 'dakota-gateway capture-session' no ps.
    No AIX, usa 'ps -ef'. No Linux, 'ps aux'.
    """
    try:
        result = subprocess.run(
            ["ps", "-ef"],
            capture_output=True, text=True, timeout=5,
        )
        for line in result.stdout.splitlines():
            if "dakota-gateway" in line and "capture-session" in line:
                # Ignora o propio grep se aparecer
                if "grep" not in line:
                    return True
    except Exception:
        pass
    return False


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
    }


def _check_ssh_service_running() -> bool:
    """Verifica se o servico SSH esta rodando no sistema operacional.

    AIX: lssrc -s sshd
    Linux: systemctl is-active sshd ou ssh
    Outros: verifica processo sshd no ps.
    """
    system = platform.system().lower()
    try:
        if "aix" in system:
            result = subprocess.run(
                ["lssrc", "-s", "sshd"],
                capture_output=True, text=True, timeout=5,
            )
            return "active" in result.stdout.lower() and result.returncode == 0
        elif "linux" in system:
            # Tenta systemctl, fallback para ps
            for unit in ("sshd", "ssh"):
                result = subprocess.run(
                    ["systemctl", "is-active", unit],
                    capture_output=True, text=True, timeout=5,
                )
                if result.stdout.strip() == "active" and result.returncode == 0:
                    return True
            # Fallback: procura sshd no ps
            result = subprocess.run(
                ["ps", "-ef"], capture_output=True, text=True, timeout=5,
            )
            for line in result.stdout.splitlines():
                if "sshd" in line and "grep" not in line:
                    return True
            return False
        else:
            # Fallback generico: ps -ef
            result = subprocess.run(
                ["ps", "-ef"], capture_output=True, text=True, timeout=5,
            )
            return any("sshd" in line and "grep" not in line for line in result.stdout.splitlines())
    except Exception:
        return False


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
    # Preserve stored env_name/instance_id if set
    stored_env = env or {}
    if stored_env.get("env_name"):
        state["environment"]["env_name"] = stored_env["env_name"]
    if stored_env.get("instance_id"):
        state["environment"]["instance_id"] = stored_env["instance_id"]
    state["active"] = bool(state.get("active"))
    state["policy"] = build_operational_policy(
        logical_active=state["active"],
        service_running=_check_ssh_service_running(),
    )
    state["capture_enabled"] = bool(state["policy"].get("capture_available"))
    # Modo de captura: 'strict' = fail-closed (aborta login se nao capturar)
    state["capture_mode"] = "strict" if state["active"] else "permissive"
    # Escopo de captura (usuarios/grupos a capturar)
    try:
        state["capture_scope"] = json.loads(state.get("capture_scope_json") or '{"users":"*","groups":"*"}')
    except Exception:
        state["capture_scope"] = {"users": "*", "groups": "*"}
    state.pop("capture_scope_json", None)
    # Verifica se ha processo capture-session realmente rodando
    # Se active=True mas sem processo vivo, reporta como unhealthy
    state["capture_process_healthy"] = _check_capture_process_alive() if state["active"] else None
    return state


def build_full_gateway_status(con, service_status: dict) -> dict:
    """Status completo do gateway: serviço + estado lógico + política + saúde do processo.

    Payload único compartilhado pelo endpoint /api/gateway/status e pelo
    WebSocket /ws/gateway-status (antes divergiam).
    """
    try:
        logical_state = get_gateway_state(con)
    except Exception:
        logical_state = {}
    policy = build_operational_policy(
        logical_active=bool(logical_state.get("active")),
        service_running=bool(service_status.get("running")),
    )
    # Saúde real do processo: só está saudável se logical_active E processo
    # capture-session vivo.
    capture_process_healthy = bool(logical_state.get("capture_process_healthy"))
    return {
        **service_status,
        "logical_active": bool(logical_state.get("active")),
        "activated_by_username": logical_state.get("activated_by_username"),
        "activated_at_ms": logical_state.get("activated_at_ms"),
        "environment": logical_state.get("environment") or {},
        "policy": policy,
        "policy_ok": bool(policy.get("policy_ok")),
        "capture_available": bool(policy.get("capture_available")),
        "capture_process_healthy": capture_process_healthy,
        "ssh_desired_route": policy.get("desired_ssh_route"),
        "ssh_effective_route": policy.get("effective_ssh_route"),
    }


def _fix_capture_dir_owner(log_dir: str, *, log=None) -> None:
    """Corrige ownership do diretório de captura no AIX.

    No AIX, o control plane roda como root mas os processos capture-session
    rodam como 'results'. Sem essa correção, o processo de captura não
    consegue escrever no diretório.
    """
    import pwd

    if "aix" not in platform.system().lower():
        return
    try:
        pw = pwd.getpwnam("results")
        uid, gid = pw.pw_uid, pw.pw_gid
    except KeyError:
        return
    try:
        os.chown(log_dir, uid, gid)
    except OSError as exc:
        if log:
            log.warning("[startup] nao foi possivel corrigir ownership de %s: %s", log_dir, exc)


def auto_activate_gateway(con, *, capture_log_dir: str, now_ms_fn, log=None) -> bool:
    """Ativa o gateway automaticamente no boot do servidor.

    Usa o primeiro admin cadastrado como activated_by. Retorna True se ativou.
    """
    import uuid

    admin = con.execute(
        "SELECT id, username FROM users WHERE role='admin' ORDER BY id LIMIT 1"
    ).fetchone()
    if not admin:
        if log:
            log.warning("[startup] gateway auto-ativacao abortada: nenhum admin encontrado")
        return False
    env = {
        "hostname": platform.node(), "fqdn": platform.node(),
        "platform": platform.system(), "platform_release": platform.release(),
        "pid": os.getpid(), "env_name": platform.node(),
    }
    env_json = json.dumps(env)
    session_uuid = str(uuid.uuid4())
    log_dir = os.path.join(capture_log_dir, session_uuid)
    os.makedirs(log_dir, exist_ok=True)
    # Corrige ownership do diretório: control plane roda como root,
    # mas capture-session roda como results no AIX.
    _fix_capture_dir_owner(log_dir, log=log)
    con.execute(
        "INSERT OR REPLACE INTO gateway_state (id, active, activated_by_username, activated_by_id,"
        " activated_at_ms, environment_json, connection_profile_id)"
        " VALUES (1, 1, ?, ?, ?, ?, NULL)",
        (admin["username"], admin["id"], now_ms_fn(), env_json),
    )
    con.execute(
        "INSERT INTO capture_sessions (session_uuid, status, created_by, created_by_username,"
        " started_at_ms, environment_json, log_dir, notes, session_count, event_count)"
        " VALUES (?, 'active', ?, ?, ?, ?, ?, 'auto-activado no boot', 0, 0)",
        (session_uuid, admin["id"], admin["username"], now_ms_fn(), env_json, log_dir),
    )
    con.commit()
    if log:
        log.info("[startup] gateway auto-ativado por %s (DAKOTA_GATEWAY_AUTO_ACTIVATE=true)", admin["username"])
    return True


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
        ) VALUES (1, 1, ?, ?, ?, NULL, NULL, ?, ?, ?, 1, ?)
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
            capture_enabled = 1,
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

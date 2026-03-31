"""
Serviço de sessões de captura — UI-first.
Captura nascida da UI, vinculada ao estado do gateway, sem dependência de CLI.
"""
from __future__ import annotations

import json
import os
import uuid

from dakota_gateway.state_db import query_all, query_one
from control.services.gateway_state_service import detect_runtime_environment, get_gateway_state


def _serialize(row) -> dict:
    if not row:
        return {}
    item = dict(row)
    for field in ("environment_json", "gateway_state_snapshot_json"):
        raw = item.pop(field, None)
        key = field.replace("_json", "")
        try:
            item[key] = json.loads(raw or "{}")
        except Exception:
            item[key] = {}
    item["active"] = item.get("status") == "active"
    return item


def start_capture(
    con,
    *,
    user_id: int,
    username: str,
    log_dir_base: str,
    connection_profile_id: int | None = None,
    connection_profile_name: str | None = None,
    operational_user_id: int | None = None,
    target_env_id: int | None = None,
    notes: str | None = None,
    now_ms_fn,
) -> dict:
    """
    Inicia uma sessão de captura.
    Requer gateway ativo. Cria log_dir único derivado do session_uuid.
    """
    gw = get_gateway_state(con)
    if not gw.get("active"):
        raise ValueError("Ative o gateway para iniciar captura.")
    existing = query_one(con, "SELECT id FROM capture_sessions WHERE status='active' ORDER BY id DESC LIMIT 1", ())
    if existing:
        raise ValueError(f"Já existe captura ativa (id={int(existing['id'])}).")

    session_uuid = str(uuid.uuid4())
    log_dir = os.path.join(log_dir_base, session_uuid)
    os.makedirs(log_dir, exist_ok=True)

    env = detect_runtime_environment()
    gw_snapshot = {
        k: v
        for k, v in gw.items()
        if k not in ("environment",)
    }
    ts = now_ms_fn()

    # Resolve profile name if not provided
    if connection_profile_id and not connection_profile_name:
        row = query_one(con, "SELECT name FROM connection_profiles WHERE id=?", (connection_profile_id,))
        connection_profile_name = row["name"] if row else None

    con.execute(
        """
        INSERT INTO capture_sessions(
            session_uuid, status, created_by, created_by_username,
            started_at_ms, environment_json,
            connection_profile_id, connection_profile_name,
            operational_user_id, gateway_state_snapshot_json,
            log_dir, target_env_id, notes
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            session_uuid,
            "active",
            user_id,
            username,
            ts,
            json.dumps(env),
            connection_profile_id,
            connection_profile_name,
            operational_user_id,
            json.dumps(gw_snapshot),
            log_dir,
            target_env_id,
            notes or "",
        ),
    )
    row = query_one(con, "SELECT * FROM capture_sessions WHERE session_uuid=?", (session_uuid,))
    return _serialize(row)


def stop_capture(con, capture_id: int, *, username: str, now_ms_fn) -> dict:
    """Encerra uma sessão de captura ativa."""
    row = query_one(con, "SELECT * FROM capture_sessions WHERE id=?", (capture_id,))
    if not row:
        raise ValueError("Sessão de captura não encontrada.")
    if row["status"] != "active":
        raise ValueError(f"Sessão não está ativa (status atual: {row['status']}).")
    ts = now_ms_fn()
    con.execute(
        "UPDATE capture_sessions SET status='finished', ended_at_ms=? WHERE id=?",
        (ts, capture_id),
    )
    row = query_one(con, "SELECT * FROM capture_sessions WHERE id=?", (capture_id,))
    return _serialize(row)


def interrupt_stale_captures(con, *, now_ms_fn) -> int:
    """Marca capturas 'active' como 'interrupted' ao iniciar o servidor.
    Isso resolve capturas órfãs de ciclos anteriores do processo.
    Retorna o número de capturas marcadas.
    """
    ts = now_ms_fn()
    result = con.execute(
        "UPDATE capture_sessions SET status='interrupted', ended_at_ms=? "
        "WHERE status='active'",
        (ts,),
    )
    return result.rowcount


def list_captures(con, *, limit: int = 60) -> list[dict]:
    """Lista sessões de captura ordenadas por data de início (mais recentes primeiro)."""
    rows = query_all(
        con,
        "SELECT * FROM capture_sessions ORDER BY started_at_ms DESC LIMIT ?",
        (limit,),
    )
    return [_serialize(row) for row in rows]


def get_capture(con, capture_id: int) -> dict | None:
    """Retorna detalhe de uma sessão de captura."""
    row = query_one(con, "SELECT * FROM capture_sessions WHERE id=?", (capture_id,))
    return _serialize(row) if row else None


def ensure_active_capture_for_gateway(
    con,
    *,
    log_dir_base: str,
    now_ms_fn,
    notes: str | None = None,
) -> dict | None:
    """Garante uma captura ativa quando o gateway lógico já está ativo.

    Usado no startup do control server para reconciliar o estado persistido
    (gateway ativo) com o estado de processo (nenhum sampler/captura ligada).
    """
    gw = get_gateway_state(con)
    if not gw.get("active"):
        return None

    existing = query_one(
        con,
        "SELECT * FROM capture_sessions WHERE status='active' ORDER BY id DESC LIMIT 1",
        (),
    )
    if existing:
        return _serialize(existing)

    actor_id = gw.get("activated_by_id")
    actor_username = str(gw.get("activated_by_username") or "").strip()
    if not int(actor_id or 0) or not actor_username:
        return None

    return start_capture(
        con,
        user_id=int(actor_id),
        username=actor_username,
        log_dir_base=log_dir_base,
        connection_profile_id=gw.get("connection_profile_id"),
        operational_user_id=gw.get("operational_user_id"),
        notes=notes or "captura retomada automaticamente na inicialização do control server",
        now_ms_fn=now_ms_fn,
    )

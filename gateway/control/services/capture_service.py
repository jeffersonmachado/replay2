"""
Serviço de sessões de captura — UI-first.
Captura nascida da UI, vinculada ao estado do gateway, sem dependência de CLI.
"""
from __future__ import annotations

import glob
import json
import os
import uuid

from dakota_gateway.state_db import query_all, query_one
from control.services.gateway_state_service import detect_runtime_environment, get_gateway_state

# Cache de contagem de linhas dos audit-*.jsonl por (path, mtime) — evita
# reler todos os arquivos de log a cada request (M6).
_AUDIT_COUNT_CACHE_MAX = 1000
_audit_count_cache: dict[str, tuple[float, int]] = {}


def _count_audit_lines(fpath: str) -> int:
    """Conta linhas de um audit-*.jsonl com cache por (path, mtime)."""
    try:
        mtime = os.path.getmtime(fpath)
    except OSError:
        return 0
    cached = _audit_count_cache.get(fpath)
    if cached and cached[0] == mtime:
        return cached[1]
    try:
        with open(fpath) as fh:
            count = sum(1 for _ in fh)
    except OSError:
        return 0
    if len(_audit_count_cache) >= _AUDIT_COUNT_CACHE_MAX:
        _audit_count_cache.clear()
    _audit_count_cache[fpath] = (mtime, count)
    return count


def count_audit_sessions_events(log_dir: str) -> tuple[int, int]:
    """Conta (sessões, eventos) dos audit-*.jsonl sob log_dir.

    Procura no próprio log_dir e, como fallback de compatibilidade com
    capturas antigas, em subdiretórios imediatos.
    """
    if not log_dir or not os.path.isdir(log_dir):
        return 0, 0
    audit_files = glob.glob(os.path.join(log_dir, "audit-*.jsonl"))
    if not audit_files:
        audit_files = glob.glob(os.path.join(log_dir, "*", "audit-*.jsonl"))
    event_count = sum(_count_audit_lines(fpath) for fpath in audit_files)
    return len(audit_files), event_count


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

    # Para capturas ativas, session_count no banco pode estar desatualizado.
    # Computa do sistema de arquivos (audit-*.jsonl no log_dir), com cache.
    session_count = item.get("session_count", 0)
    event_count = item.get("event_count", 0)
    if item["active"]:
        live_session_count, live_event_count = count_audit_sessions_events(item.get("log_dir", ""))
        if live_session_count > 0:
            session_count = live_session_count
            event_count = live_event_count
    item["session_count"] = session_count
    item["event_count"] = event_count
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
            log_dir, target_env_id, notes,
            session_count, event_count
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
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
            0,
            0
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
    # Conta sessoes e eventos do disco antes de finalizar
    log_dir = row["log_dir"]
    session_count = 0
    event_count = 0
    if log_dir and os.path.isdir(log_dir):
        session_count = len(glob.glob(os.path.join(log_dir, "audit-*.jsonl")))
        for f in glob.glob(os.path.join(log_dir, "audit-*.jsonl")):
            try:
                with open(f) as fh:
                    event_count += sum(1 for _ in fh)
            except Exception:
                pass
    con.execute(
        "UPDATE capture_sessions SET status='finished', ended_at_ms=?, session_count=?, event_count=? WHERE id=?",
        (ts, session_count, event_count, capture_id),
    )
    row = query_one(con, "SELECT * FROM capture_sessions WHERE id=?", (capture_id,))
    return _serialize(row)


def interrupt_stale_captures(con, *, now_ms_fn) -> int:
    """Marca capturas 'active' como 'interrupted' ao iniciar o servidor.
    Isso resolve capturas órfãs de ciclos anteriores do processo.
    Conta sessoes/eventos do disco antes de finalizar.
    Retorna o número de capturas marcadas.
    """
    ts = now_ms_fn()
    stale = con.execute(
        "SELECT id, log_dir FROM capture_sessions WHERE status='active'"
    ).fetchall()
    count = 0
    for row in stale:
        session_count = 0
        event_count = 0
        log_dir = row["log_dir"]
        if log_dir and os.path.isdir(log_dir):
            session_count = len(glob.glob(os.path.join(log_dir, "audit-*.jsonl")))
            for f in glob.glob(os.path.join(log_dir, "audit-*.jsonl")):
                try:
                    with open(f) as fh:
                        event_count += sum(1 for _ in fh)
                except Exception:
                    pass
        con.execute(
            "UPDATE capture_sessions SET status='interrupted', ended_at_ms=?,"
            " session_count=?, event_count=? WHERE id=?",
            (ts, session_count, event_count, row["id"]),
        )
        count += 1
    return count


def ensure_active_capture_for_gateway(
    con,
    *,
    log_dir_base: str,
    now_ms_fn,
) -> dict | None:
    """Recria automaticamente uma captura quando o gateway permanece ativo.

    Fluxo esperado no startup:
    - capturas órfãs do processo anterior já foram marcadas como interrupted;
    - se o gateway lógico segue ativo, abrimos uma nova captura para retomar a trilha.
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

    user_id = gw.get("activated_by_id")
    username = str(gw.get("activated_by_username") or "").strip()
    if not user_id or not username:
        return None

    notes = "captura retomada automaticamente na inicializacao do control server"
    return start_capture(
        con,
        user_id=int(user_id),
        username=username,
        log_dir_base=log_dir_base,
        connection_profile_id=gw.get("connection_profile_id"),
        operational_user_id=gw.get("operational_user_id"),
        notes=notes,
        now_ms_fn=now_ms_fn,
    )


def list_captures(con, *, limit: int = 20, offset: int = 0, search: str = "",
                  created_by: str = "", ts_from: int = 0, ts_to: int = 0, status: str = "") -> tuple:
    """Lista capturas com paginacao, busca e filtros. Retorna (items, total)."""
    search_clean = (search or "").strip()
    author_clean = (created_by or "").strip()
    status_clean = (status or "").strip()

    # Busca por ID exato
    if search_clean:
        try:
            search_id = int(search_clean)
            rows = query_all(con,
                "SELECT * FROM capture_sessions WHERE id = ? ORDER BY started_at_ms DESC",
                (search_id,))
            total = len(rows)
            return [_serialize(row) for row in rows[offset:offset + limit]], total
        except ValueError:
            pass

    # Query com filtros
    where = ["1=1"]
    params = []

    if search_clean:
        where.append("session_uuid LIKE ?")
        params.append(f"%{search_clean}%")

    if author_clean:
        where.append("created_by_username LIKE ?")
        params.append(f"%{author_clean}%")

    if ts_from > 0:
        where.append("started_at_ms >= ?")
        params.append(ts_from)

    if ts_to > 0:
        where.append("started_at_ms <= ?")
        params.append(ts_to)

    if status_clean:
        where.append("status = ?")
        params.append(status_clean)

    where_clause = " AND ".join(where)

    total_row = query_one(con, f"SELECT COUNT(*) as c FROM capture_sessions WHERE {where_clause}", tuple(params))
    total = total_row["c"] if total_row else 0

    rows = query_all(con,
        f"SELECT * FROM capture_sessions WHERE {where_clause} ORDER BY started_at_ms DESC LIMIT ? OFFSET ?",
        tuple(params) + (limit, offset))
    return [_serialize(row) for row in rows], total


def get_capture(con, capture_id: int) -> dict | None:
    """Retorna detalhe de uma sessão de captura."""
    row = query_one(con, "SELECT * FROM capture_sessions WHERE id=?", (capture_id,))
    return _serialize(row) if row else None

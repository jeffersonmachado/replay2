"""Fluxo Synthetic → Replay real (dívida X5).

Materializa os inputs de uma jornada sintética como trilha auditável
(arquivos ``audit-*.jsonl`` com hash-chain + HMAC, via
``ReplayAdapter.generate_synthetic_jsonl``) e executa um run real pelo
replay_control — sem simulação. O reuso de
``run_service.create_run_request_payload`` garante o mesmo caminho de
``POST /api/runs``, incluindo resolução de target e compliance gateway-only.

O ``log_dir`` é efêmero (``params.ephemeral_log_dir``): o Runner o remove
ao fim do run (sucesso ou falha).
"""
from __future__ import annotations

import shutil
import uuid
from pathlib import Path

from dakota_gateway.synthetic.journey_builder import JourneyBuilder
from dakota_gateway.synthetic.replay_adapter import ReplayAdapter

from control.services.run_service import create_run_request_payload

_VALID_MODES = ("strict-global", "parallel-sessions")


def _int_param(body: dict, name: str, default: int, minimum: int = 0) -> int:
    try:
        value = int(body.get(name) or default)
    except (TypeError, ValueError):
        value = default
    return max(minimum, value)


def start_synthetic_replay_run(
    con,
    *,
    created_by: int,
    body: dict,
    db_path: str,
    hmac_key: bytes,
    runner,
) -> dict:
    """Cria e dispara um run real a partir de uma jornada sintética.

    Retorna ``{"status_code": int, "payload": dict}`` (padrão de
    ``apply_run_action``): 202 com ``run_id``/``log_dir``/``sessions`` em
    caso de sucesso; 400 para body inválido; 404 para jornada inexistente.
    """
    body = body if isinstance(body, dict) else {}
    journey_id = str(body.get("journey_id") or body.get("scenario") or "").strip()
    if not journey_id:
        return {"status_code": 400, "payload": {"error": "journey_id required"}}

    mode = str(body.get("mode") or "parallel-sessions").strip()
    if mode not in _VALID_MODES:
        return {"status_code": 400, "payload": {"error": f"mode inválido: {mode!r} (válidos: {', '.join(_VALID_MODES)})"}}

    builder = JourneyBuilder(db_connection=con)
    journey = builder.load_journey(journey_id)
    if not journey:
        return {"status_code": 404, "payload": {"error": f"journey '{journey_id}' not found"}}

    concurrency = _int_param(body, "concurrency", 10, minimum=1)
    sessions = _int_param(body, "sessions", _int_param(body, "max_sessions", 0), minimum=0) or concurrency * 5
    seed = _int_param(body, "seed", 0, minimum=0)

    log_dir = Path(db_path).resolve().parent / "synthetic_runs" / uuid.uuid4().hex
    session_files = ReplayAdapter().generate_synthetic_jsonl(
        journey,
        session_count=sessions,
        seed=seed,
        output_dir=str(log_dir),
        hmac_key=hmac_key,
    )

    params = dict(body.get("params") if isinstance(body.get("params"), dict) else {})
    params.update({
        "synthetic": True,
        "journey_id": journey_id,
        "seed": seed,
        "ephemeral_log_dir": True,
        "concurrency": concurrency,
    })
    run_body = {
        "log_dir": str(log_dir),
        "mode": mode,
        "params": params,
    }
    for key in ("target_env_id", "connection_profile_id", "target_host", "target_user", "target_command"):
        if body.get(key) not in (None, ""):
            run_body[key] = body[key]

    try:
        created = create_run_request_payload(con, created_by=created_by, body=run_body)
    except ValueError as exc:
        shutil.rmtree(log_dir, ignore_errors=True)
        return {"status_code": 400, "payload": {"error": str(exc)}}

    run_id = int(created["id"])
    runner.start_run_async(run_id)
    return {
        "status_code": 202,
        "payload": {
            "run_id": run_id,
            "status": "queued",
            "log_dir": str(log_dir),
            "sessions": len(session_files),
            "simulation": False,
        },
    }

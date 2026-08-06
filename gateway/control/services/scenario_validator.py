"""Validação e normalização de payloads de cenários operacionais.

Extraído de `operational_scenario_service.py` (dívida S2): a lógica de
validação/normalização do payload de entrada fica isolada aqui, sem
dependência de banco — o service persiste/consulta, este módulo valida.
"""
from __future__ import annotations

from control.services.scenario_shared import normalize_scenario_tags


def normalize_operational_scenario_payload(payload: dict | None) -> dict:
    raw = payload or {}
    scenario_type = str(raw.get("scenario_type") or "replay").strip().lower()
    if scenario_type not in {"replay", "stress"}:
        raise ValueError("scenario_type inválido")
    mode = str(raw.get("mode") or "strict-global").strip()
    if mode not in {"strict-global", "parallel-sessions"}:
        raise ValueError("mode inválido")

    def _as_optional_pct(value):
        text = str(value or "").strip()
        if not text:
            return None
        number = float(text)
        if number < 0 or number > 100:
            raise ValueError("sla_max_failure_rate_pct inválido")
        return round(number, 1)

    def _as_optional_score(value):
        text = str(value or "").strip()
        if not text:
            return None
        number = float(text)
        if number < 0 or number > 100:
            raise ValueError("sla_max_criticality_score inválido")
        return round(number, 1)

    params = raw.get("params") if isinstance(raw.get("params"), dict) else {}
    return {
        "name": str(raw.get("name") or "").strip(),
        "description": str(raw.get("description") or "").strip(),
        "scenario_type": scenario_type,
        "squad": str(raw.get("squad") or "").strip(),
        "area": str(raw.get("area") or "").strip(),
        "tags": normalize_scenario_tags(raw.get("tags")),
        "owner_name": str(raw.get("owner_name") or "").strip(),
        "owner_contact": str(raw.get("owner_contact") or "").strip(),
        "sla_max_failure_rate_pct": _as_optional_pct(raw.get("sla_max_failure_rate_pct")),
        "sla_max_criticality_score": _as_optional_score(raw.get("sla_max_criticality_score")),
        "target_env_id": int(raw.get("target_env_id")) if raw.get("target_env_id") not in (None, "") else None,
        "connection_profile_id": int(raw.get("connection_profile_id")) if raw.get("connection_profile_id") not in (None, "") else None,
        "log_dir": str(raw.get("log_dir") or "").strip(),
        "target_host": str(raw.get("target_host") or "").strip(),
        "target_user": str(raw.get("target_user") or "").strip(),
        "target_command": str(raw.get("target_command") or "").strip(),
        "mode": mode,
        "params": params,
    }

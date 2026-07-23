"""Serviço de métricas operacionais internas do endpoint /metrics.

Regra de negócio extraída do server.py para manter o dispatcher enxuto.
"""
from __future__ import annotations


def collect_operational_metrics(con) -> dict:
    """Coleta contadores operacionais do banco para o endpoint /metrics."""
    metrics: dict = {}
    # Runs
    metrics["runs_total"] = con.execute("SELECT COUNT(*) as c FROM replay_runs").fetchone()["c"]
    metrics["runs_active"] = con.execute(
        "SELECT COUNT(*) as c FROM replay_runs WHERE status IN ('queued','running')"
    ).fetchone()["c"]
    metrics["runs_success"] = con.execute(
        "SELECT COUNT(*) as c FROM replay_runs WHERE status='success'"
    ).fetchone()["c"]
    metrics["runs_failed"] = con.execute(
        "SELECT COUNT(*) as c FROM replay_runs WHERE status='failed'"
    ).fetchone()["c"]
    # Failures
    metrics["failures_total"] = con.execute("SELECT COUNT(*) as c FROM replay_failures").fetchone()["c"]
    metrics["failures_critical"] = con.execute(
        "SELECT COUNT(*) as c FROM replay_failures WHERE severity='critical'"
    ).fetchone()["c"]
    # Captures
    metrics["captures_active"] = con.execute(
        "SELECT COUNT(*) as c FROM capture_sessions WHERE status='active'"
    ).fetchone()["c"]
    metrics["captures_total"] = con.execute("SELECT COUNT(*) as c FROM capture_sessions").fetchone()["c"]
    # Gateway
    gw = con.execute("SELECT active FROM gateway_state ORDER BY id DESC LIMIT 1").fetchone()
    metrics["gateway_active"] = bool(gw["active"]) if gw else False
    # Synthetic
    metrics["screens"] = con.execute("SELECT COUNT(*) as c FROM screens").fetchone()["c"]
    metrics["entities"] = con.execute("SELECT COUNT(*) as c FROM source_entities").fetchone()["c"]
    return metrics

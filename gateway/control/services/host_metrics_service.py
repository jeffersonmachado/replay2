"""Serviço do painel de recursos do host (/observability/resources).

Consulta amostras de `host_metrics` por janela (explícita ou derivada de uma
run), reduz a resolução para o gráfico e monta o payload de exportação usado
na comparação de estresse entre ambientes.
"""
from __future__ import annotations

import os
import socket
import time

EXPORT_FORMAT = "dakota-host-metrics/v1"

# Campos numéricos exportados/ponderados no downsample.
METRIC_FIELDS = (
    "cpu_pct",
    "load1",
    "load5",
    "load15",
    "mem_total_mb",
    "mem_used_mb",
    "mem_pct",
    "swap_pct",
    "disk_read_kbs",
    "disk_write_kbs",
)

_SELECT_SQL = (
    "SELECT ts_ms, " + ", ".join(METRIC_FIELDS) + " FROM host_metrics "
    "WHERE ts_ms >= ? AND ts_ms <= ? ORDER BY ts_ms"
)


def _row_to_sample(row) -> dict:
    sample = {"ts_ms": row["ts_ms"]}
    for field in METRIC_FIELDS:
        sample[field] = row[field]
    return sample


def downsample(samples: list[dict], max_points: int) -> list[dict]:
    """Reduz a série agrupando em buckets com média dos campos numéricos."""
    if max_points < 1 or len(samples) <= max_points:
        return samples
    bucket_size = -(-len(samples) // max_points)  # ceil
    out: list[dict] = []
    for start in range(0, len(samples), bucket_size):
        bucket = samples[start:start + bucket_size]
        merged = {"ts_ms": bucket[0]["ts_ms"]}
        for field in METRIC_FIELDS:
            values = [s[field] for s in bucket if s.get(field) is not None]
            merged[field] = round(sum(values) / len(values), 2) if values else None
        out.append(merged)
    return out


def run_window(con, run_id: int) -> dict | None:
    """Janela temporal da run (started_at_ms → finished_at_ms; aberta → agora)."""
    row = con.execute(
        "SELECT id, status, mode, started_at_ms, finished_at_ms, created_at_ms "
        "FROM replay_runs WHERE id = ?",
        (run_id,),
    ).fetchone()
    if not row:
        return None
    start = row["started_at_ms"] or row["created_at_ms"]
    end = row["finished_at_ms"] or int(time.time() * 1000)
    return {
        "run_id": row["id"],
        "status": row["status"],
        "mode": row["mode"],
        "from_ms": start,
        "to_ms": max(end, start),
    }


def query_host_metrics(con, from_ms: int, to_ms: int, max_points: int = 360) -> dict:
    """Amostras da janela, reduzidas a no máximo `max_points` pontos."""
    rows = con.execute(_SELECT_SQL, (from_ms, to_ms)).fetchall()
    samples = [_row_to_sample(row) for row in rows]
    return {
        "window": {"from_ms": from_ms, "to_ms": to_ms},
        "total_samples": len(samples),
        "samples": downsample(samples, max_points),
    }


def build_export(con, run_id: int) -> dict | None:
    """Payload completo (sem downsample) para comparar ambientes fora da UI."""
    window = run_window(con, run_id)
    if not window:
        return None
    rows = con.execute(_SELECT_SQL, (window["from_ms"], window["to_ms"])).fetchall()
    return {
        "format": EXPORT_FORMAT,
        "env": os.environ.get("DAKOTA_ENV", "lab"),
        "host": socket.gethostname(),
        "exported_at_ms": int(time.time() * 1000),
        "run": {"id": window["run_id"], "status": window["status"], "mode": window["mode"]},
        "window": {"from_ms": window["from_ms"], "to_ms": window["to_ms"]},
        "samples": [_row_to_sample(row) for row in rows],
    }

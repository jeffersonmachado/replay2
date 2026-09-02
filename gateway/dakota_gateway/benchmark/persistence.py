"""Persistência do benchmark real no SQLite (contrato §17).

Cinco tabelas novas — ``benchmark_experiments``, ``benchmark_runs``,
``benchmark_app_samples``, ``benchmark_host_samples`` e
``benchmark_comparisons`` — criadas de forma idempotente por
``dakota_gateway.db.migrations.migrate_benchmark_tables`` (schema em
``dakota_gateway.db.schema.BENCHMARK_SCHEMA_SQL``). Campos de host
indisponíveis ficam NULL — nunca zero fingindo medição.
"""
from __future__ import annotations

import json
import sqlite3
import time

from ..db.connection import batch_insert
from ..db.migrations import migrate_benchmark_tables
from .contract import ExperimentContract
from .models import EnvironmentRunResult

#: Colunas diretas de benchmark_host_samples (o restante vai para extra_json).
_HOST_COLUNAS = (
    "cpu_user", "cpu_system", "cpu_wait", "cpu_idle", "load1",
    "mem_total_mb", "mem_used_mb", "swap_pct",
    "disk_read_kbs", "disk_write_kbs", "iops", "disk_latency_ms",
    "net_rx_kbs", "net_tx_kbs", "processes", "run_queue",
)


def _now_ms() -> int:
    return int(time.time() * 1000)


def ensure_benchmark_tables(con: sqlite3.Connection) -> None:
    """Garante as tabelas benchmark_* (idempotente)."""
    migrate_benchmark_tables(con)


def save_experiment(con: sqlite3.Connection, contract: ExperimentContract,
                    *, status: str = "CREATED", verdict: str = "INCONCLUSIVE",
                    reason: str = "") -> None:
    """Registra (ou substitui) o experimento com o contrato canônico."""
    ensure_benchmark_tables(con)
    con.execute(
        "INSERT OR REPLACE INTO benchmark_experiments"
        "(experiment_id, contract_json, contract_sha256, created_at_ms,"
        " status, verdict, reason) VALUES(?,?,?,?,?,?,?)",
        (contract.experiment_id, contract.canonical_json(), contract.sha256(),
         _now_ms(), status, verdict, reason),
    )
    con.commit()


def update_experiment_status(con: sqlite3.Connection, experiment_id: str,
                             *, status: str, verdict: str, reason: str) -> None:
    """Atualiza status/verdict/reason do experimento após a decisão."""
    con.execute(
        "UPDATE benchmark_experiments SET status=?, verdict=?, reason=?"
        " WHERE experiment_id=?",
        (status, verdict, reason, experiment_id),
    )
    con.commit()


def save_run(con: sqlite3.Connection, run_id: str, experiment_id: str,
             result: EnvironmentRunResult, *, phase_order: list[str],
             started_at_ms: int = 0, finished_at_ms: int = 0) -> None:
    """Registra uma run (ambiente × iteração × nível) e sua ordem pareada."""
    ensure_benchmark_tables(con)
    con.execute(
        "INSERT OR REPLACE INTO benchmark_runs"
        "(run_id, experiment_id, environment_id, iteration, concurrency,"
        " phase_order, status, started_at_ms, finished_at_ms, error_reason)"
        " VALUES(?,?,?,?,?,?,?,?,?,?)",
        (run_id, experiment_id, result.environment_id, result.iteration,
         result.concurrency, json.dumps(list(phase_order)), result.status,
         started_at_ms or None, finished_at_ms or None, result.error_reason),
    )
    con.commit()


def save_app_samples(con: sqlite3.Connection, run_id: str, samples: list,
                     *, chunk_size: int = 500) -> int:
    """Grava amostras de aplicação (todas as fases) de uma run.

    Escrita em lote: transação explícita + executemany em chunks curtos
    (``chunk_size``) — a conexão roda em autocommit e o executemany solto
    fazia um fsync por linha.
    """
    if not samples:
        return 0
    linhas = [
        (run_id,
         str(getattr(s, "virtual_user_id", "")),
         str(getattr(s, "journey_id", "")),
         str(getattr(s, "step_id", "")),
         str(getattr(s, "phase", "")),
         int(getattr(s, "started_ns", 0)),
         int(getattr(s, "finished_ns", 0)),
         float(getattr(s, "latency_ms", 0.0)),
         1 if getattr(s, "success", False) else 0,
         1 if getattr(s, "timeout", False) else 0,
         1 if getattr(s, "functional_divergence", False) else 0,
         getattr(s, "error_code", None))
        for s in samples
    ]
    return batch_insert(
        con,
        "INSERT INTO benchmark_app_samples"
        "(run_id, virtual_user_id, journey_id, step_id, phase, started_ns,"
        " finished_ns, latency_ms, success, timeout, functional_divergence,"
        " error_code) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        linhas,
        chunk_size=chunk_size,
    )


def save_host_samples(con: sqlite3.Connection, *, experiment_id: str,
                      environment_id: str, run_id: str, iteration: int,
                      concurrency: int, phase: str,
                      samples: list[dict], chunk_size: int = 500) -> int:
    """Grava amostras de host associadas a experimento/ambiente/run (§13).

    Campos ausentes na amostra ficam NULL (nunca zero fingido); campos fora
    das colunas diretas vão para ``extra_json``. Escrita em lote com chunks
    curtos (``chunk_size``), como em ``save_app_samples``.
    """
    if not samples:
        return 0
    linhas = []
    for amostra in samples:
        diretas = {c: amostra.get(c) for c in _HOST_COLUNAS}
        extras = {k: v for k, v in amostra.items()
                  if k not in _HOST_COLUNAS
                  and k not in ("ts_ms", "host_id", "platform", "architecture")}
        linhas.append((
            experiment_id, environment_id, run_id, iteration, concurrency,
            phase,
            str(amostra.get("host_id", "")),
            str(amostra.get("platform", "")),
            str(amostra.get("architecture", "")),
            amostra.get("ts_ms"),
            *[diretas[c] for c in _HOST_COLUNAS],
            json.dumps(extras, sort_keys=True, ensure_ascii=False),
        ))
    return batch_insert(
        con,
        "INSERT INTO benchmark_host_samples"
        "(experiment_id, environment_id, run_id, iteration, concurrency,"
        " phase, host_id, platform, architecture, ts_ms,"
        " cpu_user, cpu_system, cpu_wait, cpu_idle, load1,"
        " mem_total_mb, mem_used_mb, swap_pct,"
        " disk_read_kbs, disk_write_kbs, iops, disk_latency_ms,"
        " net_rx_kbs, net_tx_kbs, processes, run_queue, extra_json)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        linhas,
        chunk_size=chunk_size,
    )


def save_comparison(con: sqlite3.Connection, experiment_id: str,
                    payload: dict) -> None:
    """Grava o payload de comparação/decisão do experimento."""
    ensure_benchmark_tables(con)
    con.execute(
        "INSERT INTO benchmark_comparisons(experiment_id, payload_json, created_at_ms)"
        " VALUES(?,?,?)",
        (experiment_id, json.dumps(payload, sort_keys=True, ensure_ascii=False),
         _now_ms()),
    )
    con.commit()


def get_experiment(con: sqlite3.Connection, experiment_id: str) -> dict | None:
    """Lê o experimento (dict) ou None."""
    row = con.execute(
        "SELECT * FROM benchmark_experiments WHERE experiment_id=?",
        (experiment_id,),
    ).fetchone()
    return dict(row) if row else None


def list_experiments(con: sqlite3.Connection) -> list[dict]:
    """Lista experimentos ordenados do mais recente ao mais antigo."""
    rows = con.execute(
        "SELECT * FROM benchmark_experiments ORDER BY created_at_ms DESC",
    ).fetchall()
    return [dict(r) for r in rows]


def list_runs(con: sqlite3.Connection, experiment_id: str) -> list[dict]:
    """Lista as runs de um experimento (ordem de execução)."""
    rows = con.execute(
        "SELECT * FROM benchmark_runs WHERE experiment_id=?"
        " ORDER BY iteration, concurrency, environment_id",
        (experiment_id,),
    ).fetchall()
    return [dict(r) for r in rows]

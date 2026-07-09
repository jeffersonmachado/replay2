"""Agendamento de execuções periódicas com regressão e histórico."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable, Optional


@dataclass
class ScheduleConfig:
    """Configuração de agendamento."""
    schedule_id: str = ""
    journey_id: str = ""
    name: str = ""
    interval_hours: int = 24
    session_count: int = 10
    seed: int = 0
    concurrency: int = 5
    enabled: bool = True
    alert_threshold_pct: float = 10.0  # alerta se taxa de falha subir > X%
    created_at: str = ""
    last_run_at: str = ""


@dataclass
class RegressionComparison:
    """Comparação entre duas execuções (regressão)."""
    current_run_id: int = 0
    previous_run_id: int = 0
    success_rate_current: float = 0.0
    success_rate_previous: float = 0.0
    delta_pct: float = 0.0
    is_regression: bool = False
    new_error_types: list[str] = field(default_factory=list)
    resolved_error_types: list[str] = field(default_factory=list)
    summary: str = ""


class Scheduler:
    """Agendador de execuções com histórico e detecção de regressão."""

    def __init__(self, db_path: str = ""):
        self.db_path = db_path
        self._running = False
        self._thread: Optional[threading.Thread] = None

    # ------------------------------------------------------------------
    # Agendamento
    # ------------------------------------------------------------------

    def add_schedule(self, config: ScheduleConfig) -> str:
        """Adiciona agendamento ao banco."""
        con = sqlite3.connect(self.db_path)
        con.row_factory = sqlite3.Row

        now = datetime.now().isoformat()
        config.created_at = now

        con.execute(
            """INSERT OR REPLACE INTO synthetic_schedules
               (schedule_id, journey_id, name, interval_hours, session_count,
                seed, concurrency, enabled, alert_threshold_pct, created_at, last_run_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (config.schedule_id, config.journey_id, config.name,
             config.interval_hours, config.session_count, config.seed,
             config.concurrency, 1 if config.enabled else 0,
             config.alert_threshold_pct, config.created_at, config.last_run_at or ""),
        )
        con.commit()
        con.close()
        return config.schedule_id

    def list_schedules(self) -> list[dict]:
        """Lista agendamentos."""
        con = sqlite3.connect(self.db_path)
        con.row_factory = sqlite3.Row
        rows = con.execute(
            "SELECT * FROM synthetic_schedules ORDER BY name"
        ).fetchall()
        con.close()
        return [dict(r) for r in rows]

    def get_schedule(self, schedule_id: str) -> Optional[dict]:
        """Obtém agendamento por ID."""
        con = sqlite3.connect(self.db_path)
        con.row_factory = sqlite3.Row
        row = con.execute(
            "SELECT * FROM synthetic_schedules WHERE schedule_id=?", (schedule_id,)
        ).fetchone()
        con.close()
        return dict(row) if row else None

    # ------------------------------------------------------------------
    # Execução programada
    # ------------------------------------------------------------------

    def run_schedule(self, schedule_id: str) -> dict:
        """Executa um agendamento e registra resultado."""
        config = self.get_schedule(schedule_id)
        if not config:
            return {"error": "schedule not found"}

        from .stress_runner import SyntheticStressRunner, SyntheticStressConfig
        stress_config = SyntheticStressConfig(
            journey_id=config["journey_id"],
            concurrency=config["concurrency"],
            max_sessions=config["session_count"],
            seed=config["seed"],
            db_path=self.db_path,
        )

        runner = SyntheticStressRunner(db_path=self.db_path)
        result = runner.run(stress_config)

        # Salvar resultado
        run_id = self._save_run_result(schedule_id, result)

        # Atualizar last_run_at
        con = sqlite3.connect(self.db_path)
        con.execute(
            "UPDATE synthetic_schedules SET last_run_at=? WHERE schedule_id=?",
            (datetime.now().isoformat(), schedule_id),
        )
        con.commit()
        con.close()

        # Verificar regressão
        regression = self.check_regression(schedule_id, run_id)

        return {
            "schedule_id": schedule_id,
            "run_id": run_id,
            "completed": result.completed,
            "failed": result.failed,
            "is_regression": regression.is_regression if regression else False,
            "delta_pct": regression.delta_pct if regression else 0.0,
        }

    def start_background(self, check_interval_sec: int = 60):
        """Inicia thread de background que verifica agendamentos pendentes."""
        if self._running:
            return
        self._running = True

        def _loop():
            while self._running:
                schedules = self.list_schedules()
                now = datetime.now()

                for s in schedules:
                    if not s.get("enabled"):
                        continue
                    last_run = s.get("last_run_at", "")
                    if last_run:
                        try:
                            last_dt = datetime.fromisoformat(last_run)
                            interval = timedelta(hours=s["interval_hours"])
                            if now - last_dt < interval:
                                continue
                        except (ValueError, TypeError):
                            pass
                    # Executar
                    try:
                        self.run_schedule(s["schedule_id"])
                    except Exception:
                        pass

                time.sleep(check_interval_sec)

        self._thread = threading.Thread(target=_loop, daemon=True)
        self._thread.start()

    def stop_background(self):
        """Para thread de background."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)

    # ------------------------------------------------------------------
    # Regressão
    # ------------------------------------------------------------------

    def check_regression(self, schedule_id: str, current_run_id: int) -> Optional[RegressionComparison]:
        """Compara execução atual com a anterior e detecta regressão."""
        con = sqlite3.connect(self.db_path)
        con.row_factory = sqlite3.Row

        # Buscar execução atual
        current = con.execute(
            "SELECT * FROM synthetic_schedule_runs WHERE id=?", (current_run_id,)
        ).fetchone()
        if not current:
            con.close()
            return None

        # Buscar execução anterior
        previous = con.execute(
            """SELECT * FROM synthetic_schedule_runs
               WHERE schedule_id=? AND id < ? ORDER BY id DESC LIMIT 1""",
            (schedule_id, current_run_id),
        ).fetchone()

        if not previous:
            con.close()
            return None

        total_cur = int(current["total_sessions"] or 1)
        total_prev = int(previous["total_sessions"] or 1)
        rate_cur = int(current["completed"] or 0) / max(1, total_cur) * 100
        rate_prev = int(previous["completed"] or 0) / max(1, total_prev) * 100

        delta = rate_prev - rate_cur  # positivo = piorou
        threshold = 10.0
        try:
            threshold = float(current["alert_threshold_pct"] or 10.0)
        except (KeyError, IndexError, TypeError):
            pass
        is_regression = delta > threshold

        # Comparar tipos de erro
        cur_errors = set()
        prev_errors = set()
        try:
            cur_data = json.loads(current["error_summary_json"] or "{}")
            prev_data = json.loads(previous["error_summary_json"] or "{}")
            cur_errors = set(cur_data.get("by_type", {}).keys())
            prev_errors = set(prev_data.get("by_type", {}).keys())
        except (json.JSONDecodeError, TypeError):
            pass

        new_errors = list(cur_errors - prev_errors)
        resolved = list(prev_errors - cur_errors)

        comparison = RegressionComparison(
            current_run_id=current_run_id,
            previous_run_id=previous["id"],
            success_rate_current=round(rate_cur, 1),
            success_rate_previous=round(rate_prev, 1),
            delta_pct=round(delta, 1),
            is_regression=is_regression,
            new_error_types=new_errors,
            resolved_error_types=resolved,
            summary=f"Taxa: {rate_prev:.1f}% → {rate_cur:.1f}% (Δ={delta:+.1f}%)"
                    + (f" | REGRESSÃO detectada!" if is_regression else ""),
        )

        con.close()
        return comparison

    def get_run_history(self, schedule_id: str, limit: int = 20) -> list[dict]:
        """Histórico de execuções de um agendamento."""
        con = sqlite3.connect(self.db_path)
        con.row_factory = sqlite3.Row
        rows = con.execute(
            """SELECT id, total_sessions, completed, failed, errors,
                      success_rate_pct, duration_ms, created_at
               FROM synthetic_schedule_runs
               WHERE schedule_id=? ORDER BY id DESC LIMIT ?""",
            (schedule_id, limit),
        ).fetchall()
        con.close()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Persistência
    # ------------------------------------------------------------------

    def _save_run_result(self, schedule_id: str, result) -> int:
        total = max(1, result.total_sessions)
        rate = round(result.completed / total * 100, 1)

        error_summary = {}
        if result.aggregate_verification:
            error_summary = {
                "by_type": result.aggregate_verification.get("errors_by_type", {}),
                "by_severity": result.aggregate_verification.get("errors_by_severity", {}),
                "most_failing_steps": result.aggregate_verification.get("most_failing_steps", []),
            }

        con = sqlite3.connect(self.db_path)
        cur = con.execute(
            """INSERT INTO synthetic_schedule_runs
               (schedule_id, total_sessions, completed, failed, errors,
                success_rate_pct, duration_ms, error_summary_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (schedule_id, result.total_sessions, result.completed, result.failed,
             result.errors, rate, result.duration_ms,
             json.dumps(error_summary, ensure_ascii=False),
             datetime.now().isoformat()),
        )
        con.commit()
        run_id = cur.lastrowid or 0
        con.close()
        return run_id

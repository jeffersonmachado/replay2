"""Export CSV de datasets sintéticos e comparação entre quickstarts."""
from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional


class CSVExporter:
    """Exporta datasets sintéticos em formato CSV."""

    @staticmethod
    def export_dataset(dataset: Any, delimiter: str = ",") -> str:
        """Exporta um Dataset para CSV."""
        output = io.StringIO()
        if not dataset.records:
            return ""

        # Coletar todos os nomes de campo
        all_fields: list[str] = []
        for rec in dataset.records[:1]:
            all_fields = list(rec.data.keys())
            break

        writer = csv.DictWriter(output, fieldnames=all_fields, delimiter=delimiter)
        writer.writeheader()
        for rec in dataset.records:
            writer.writerow(rec.data)

        return output.getvalue()

    @staticmethod
    def export_dataset_jsonl(dataset: Any) -> str:
        """Exporta dataset como JSONL."""
        lines = []
        for rec in dataset.records:
            lines.append(json.dumps(rec.data, ensure_ascii=False))
        return "\n".join(lines)

    @staticmethod
    def export_stress_results_csv(stress_result: Any) -> str:
        """Exporta resultados de stress como CSV."""
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["session", "status", "duration_ms", "errors", "error_types"])

        for sr in stress_result.session_results:
            error_types = ", ".join(
                e.get("type", "?") for e in (sr.errors or [])
            ) if sr.errors else ""
            writer.writerow([
                sr.session_index, sr.status, sr.duration_ms,
                len(sr.errors) if sr.errors else 0, error_types,
            ])

        return output.getvalue()

    @staticmethod
    def save_csv(content: str, path: str) -> str:
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(content)
        return path


@dataclass
class QuickstartDiff:
    """Resultado de diff entre dois quickstarts."""
    screens_before: int = 0
    screens_after: int = 0
    screens_new: list[str] = field(default_factory=list)
    screens_removed: list[str] = field(default_factory=list)
    entities_before: int = 0
    entities_after: int = 0
    entities_new: list[str] = field(default_factory=list)
    entities_removed: list[str] = field(default_factory=list)
    journeys_before: int = 0
    journeys_after: int = 0
    success_rate_before: float = 0.0
    success_rate_after: float = 0.0
    summary: str = ""


class QuickstartDiffer:
    """Compara duas execuções de quickstart e gera diff."""

    @staticmethod
    def diff(before_json: str, after_json: str) -> QuickstartDiff:
        """Compara dois JSONs de quickstart."""
        try:
            before = json.loads(before_json) if isinstance(before_json, str) else before_json
            after = json.loads(after_json) if isinstance(after_json, str) else after_json
        except (json.JSONDecodeError, TypeError):
            return QuickstartDiff(summary="Erro ao parsear JSONs")

        # Screens
        before_screens = {s.get("title", ""): s for s in before.get("screens_detail", [])}
        after_screens = {s.get("title", ""): s for s in after.get("screens_detail", [])}
        new_screens = list(set(after_screens.keys()) - set(before_screens.keys()))
        removed_screens = list(set(before_screens.keys()) - set(after_screens.keys()))

        # Entities
        before_entities = {e.get("name", ""): e for e in before.get("entities_detail", [])}
        after_entities = {e.get("name", ""): e for e in after.get("entities_detail", [])}
        new_entities = list(set(after_entities.keys()) - set(before_entities.keys()))
        removed_entities = list(set(before_entities.keys()) - set(after_entities.keys()))

        # Journeys
        before_journeys = [j.get("name", "") for j in before.get("journeys_detail", [])]
        after_journeys = [j.get("name", "") for j in after.get("journeys_detail", [])]

        # Build summary
        parts = []
        if new_screens:
            parts.append(f"+{len(new_screens)} telas novas")
        if removed_screens:
            parts.append(f"-{len(removed_screens)} telas removidas")
        if new_entities:
            parts.append(f"+{len(new_entities)} entidades novas")
        if removed_entities:
            parts.append(f"-{len(removed_entities)} entidades removidas")

        return QuickstartDiff(
            screens_before=len(before_screens),
            screens_after=len(after_screens),
            screens_new=new_screens,
            screens_removed=removed_screens,
            entities_before=len(before_entities),
            entities_after=len(after_entities),
            entities_new=new_entities,
            entities_removed=removed_entities,
            journeys_before=len(before_journeys),
            journeys_after=len(after_journeys),
            summary="; ".join(parts) if parts else "Sem alterações detectadas",
        )

    @staticmethod
    def diff_dirs(before_dir: str, after_dir: str) -> QuickstartDiff:
        """Compara diretórios de relatório de quickstart."""
        from pathlib import Path
        before_path = Path(before_dir)
        after_path = Path(after_dir)

        before_json_files = sorted(before_path.glob("*.json"))
        after_json_files = sorted(after_path.glob("*.json"))

        before_data = {}
        after_data = {}

        for f in before_json_files:
            try:
                before_data[f.stem] = json.loads(f.read_text())
            except Exception:
                pass

        for f in after_json_files:
            try:
                after_data[f.stem] = json.loads(f.read_text())
            except Exception:
                pass

        # Comparar módulo a módulo
        all_modules = set(before_data.keys()) | set(after_data.keys())
        diffs: dict[str, QuickstartDiff] = {}

        for mod in all_modules:
            b = before_data.get(mod, {})
            a = after_data.get(mod, {})
            diffs[mod] = QuickstartDiffer.diff(b, a)

        # Agregado
        total = QuickstartDiff()
        for d in diffs.values():
            total.screens_new.extend(d.screens_new)
            total.screens_removed.extend(d.screens_removed)
            total.entities_new.extend(d.entities_new)
            total.entities_removed.extend(d.entities_removed)
            total.screens_before += d.screens_before
            total.screens_after += d.screens_after

        total.summary = f"{total.screens_after - total.screens_before:+d} telas, " \
                       f"{len(total.entities_new)} entidades novas, " \
                       f"{len(total.entities_removed)} removidas" if total.entities_new or total.entities_removed else "Sem alterações"

        return total


class WatchMode:
    """Monitora diretório de código-fonte e re-executa análise em alterações."""

    def __init__(self, source_dir: str, callback, poll_interval_sec: float = 5.0):
        self.source_dir = source_dir
        self.callback = callback
        self.poll_interval = poll_interval_sec
        self._running = False
        self._file_states: dict[str, float] = {}
        import os
        self._os = os

    def start(self):
        """Inicia monitoramento em background thread."""
        import threading
        self._running = True
        self._scan_initial()
        t = threading.Thread(target=self._loop, daemon=True)
        t.start()
        return t

    def stop(self):
        self._running = False

    def _scan_initial(self):
        self._file_states = {}
        for root, _, files in self._os.walk(self.source_dir):
            for f in files:
                if f.endswith((".prg", ".src", ".sql", ".dbo")):
                    full = self._os.path.join(root, f)
                    try:
                        self._file_states[full] = self._os.path.getmtime(full)
                    except OSError:
                        pass

    def _loop(self):
        import time
        while self._running:
            changed = self._scan_changes()
            if changed:
                self.callback(changed)
            time.sleep(self.poll_interval)

    def _scan_changes(self) -> list[str]:
        changed: list[str] = []
        current: dict[str, float] = {}

        for root, _, files in self._os.walk(self.source_dir):
            for f in files:
                if f.endswith((".prg", ".src", ".sql", ".dbo")):
                    full = self._os.path.join(root, f)
                    try:
                        mtime = self._os.path.getmtime(full)
                        current[full] = mtime
                        if full not in self._file_states:
                            changed.append(full)
                        elif mtime != self._file_states[full]:
                            changed.append(full)
                    except OSError:
                        pass

        self._file_states = current
        return changed


class MetricsCollector:
    """Coleta métricas consolidadas da plataforma synthetic."""

    @staticmethod
    def collect(db_connection) -> dict:
        """Coleta todas as métricas do banco."""
        con = db_connection
        metrics: dict = {
            "collected_at": datetime.now().isoformat(),
            "coverage": {},
            "performance": {},
            "quality": {},
            "history": {},
        }

        # Cobertura
        try:
            metrics["coverage"]["total_screens"] = con.execute(
                "SELECT COUNT(*) as c FROM screens"
            ).fetchone()["c"]
        except Exception:
            metrics["coverage"]["total_screens"] = 0

        try:
            metrics["coverage"]["total_entities"] = con.execute(
                "SELECT COUNT(*) as c FROM source_entities"
            ).fetchone()["c"]
        except Exception:
            metrics["coverage"]["total_entities"] = 0

        try:
            metrics["coverage"]["total_journeys"] = con.execute(
                "SELECT COUNT(*) as c FROM journeys"
            ).fetchone()["c"]
        except Exception:
            metrics["coverage"]["total_journeys"] = 0

        try:
            metrics["coverage"]["total_datasets"] = con.execute(
                "SELECT COUNT(*) as c FROM synthetic_datasets"
            ).fetchone()["c"]
        except Exception:
            metrics["coverage"]["total_datasets"] = 0

        # Entidades sem jornada
        try:
            entities_with_journeys = set()
            for row in con.execute("SELECT journey_id FROM journeys").fetchall():
                entities_with_journeys.add(row["journey_id"])
            all_entities = {row["name"] for row in con.execute("SELECT name FROM source_entities").fetchall()}
            metrics["coverage"]["entities_without_journey"] = sorted(
                all_entities - entities_with_journeys
            )[:20]
        except Exception:
            metrics["coverage"]["entities_without_journey"] = []

        # Jornadas sem stress
        try:
            schedule_journeys = set()
            for row in con.execute("SELECT DISTINCT journey_id FROM synthetic_schedule_runs").fetchall():
                schedule_journeys.add(row["journey_id"])
            all_journeys = {row["journey_id"] for row in con.execute("SELECT journey_id FROM journeys").fetchall()}
            metrics["coverage"]["journeys_without_stress"] = sorted(
                all_journeys - schedule_journeys
            )[:20]
        except Exception:
            metrics["coverage"]["journeys_without_stress"] = []

        # Performance
        try:
            row = con.execute(
                "SELECT AVG(duration_ms) as avg_ms, MAX(duration_ms) as max_ms, COUNT(*) as total FROM synthetic_schedule_runs"
            ).fetchone()
            if row:
                metrics["performance"]["avg_duration_ms"] = round(row["avg_ms"] or 0, 0)
                metrics["performance"]["max_duration_ms"] = row["max_ms"] or 0
                metrics["performance"]["total_runs"] = row["total"] or 0
        except Exception:
            pass

        # Qualidade (taxa de sucesso histórica)
        try:
            row = con.execute(
                "SELECT AVG(success_rate_pct) as avg_rate FROM synthetic_schedule_runs"
            ).fetchone()
            metrics["quality"]["avg_success_rate_pct"] = round(row["avg_rate"] or 100, 1) if row else 100.0
        except Exception:
            metrics["quality"]["avg_success_rate_pct"] = 100.0

        # Histórico (últimos 10 runs)
        try:
            rows = con.execute(
                "SELECT schedule_id, success_rate_pct, created_at FROM synthetic_schedule_runs ORDER BY id DESC LIMIT 10"
            ).fetchall()
            metrics["history"]["last_runs"] = [dict(r) for r in rows]
        except Exception:
            metrics["history"]["last_runs"] = []

        return metrics

#!/usr/bin/env python3
"""Dry-run replay: simula execucao de sessoes sinteticas como replay.

Faz parte da entrega Sprint 5 — Replay Engine.

Le sessoes sinteticas geradas pelo JourneySynthesizer e simula a execucao
como se fosse um replay real, produzindo metricas de:
- inputs enviados
- comandos executados
- telas visitadas
- tempo estimado
- taxa de sucesso
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ReplayStepResult:
    seq: int = 0
    step_type: str = ""       # input, command, checkpoint
    value: str = ""
    field: str = ""
    entity: str = ""
    duration_ms: float = 0.0
    status: str = "ok"        # ok, warning, error


@dataclass
class ReplaySessionResult:
    session_file: str = ""
    total_steps: int = 0
    input_steps: int = 0
    command_steps: int = 0
    entities_visited: list[str] = field(default_factory=list)
    fields_filled: list[str] = field(default_factory=list)
    duration_ms: float = 0.0
    status: str = "ok"
    steps: list[ReplayStepResult] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class ReplayReport:
    total_sessions: int = 0
    completed: int = 0
    failed: int = 0
    total_inputs: int = 0
    total_commands: int = 0
    total_duration_ms: float = 0.0
    avg_duration_ms: float = 0.0
    entities_covered: list[str] = field(default_factory=list)
    session_results: list[ReplaySessionResult] = field(default_factory=list)


class DryRunReplay:
    """Simula replay de sessoes sinteticas sem conexao real."""

    def __init__(self, input_delay_ms: float = 10.0):
        self.input_delay_ms = input_delay_ms

    def replay_sessions(self, sessions_dir: Path) -> ReplayReport:
        sessions_dir = Path(sessions_dir)
        session_files = sorted(sessions_dir.glob("session_*.jsonl"))

        report = ReplayReport(total_sessions=len(session_files))
        all_entities: set[str] = set()
        all_durations: list[float] = []

        for sf in session_files:
            t0 = time.time()
            try:
                content = sf.read_text(encoding="utf-8").strip()
                if not content:
                    report.failed += 1
                    continue

                lines = [json.loads(l) for l in content.split("\n") if l.strip()]
                session = ReplaySessionResult(session_file=sf.name)
                entities: set[str] = set()
                fields: list[str] = []

                for obj in lines:
                    step = ReplayStepResult(
                        seq=obj.get("seq", 0),
                        step_type=obj.get("type", "?"),
                        value=str(obj.get("value", "")),
                        field=str(obj.get("field", "")),
                        entity=str(obj.get("entity", "")),
                        duration_ms=self.input_delay_ms,
                    )

                    if obj.get("type") == "input":
                        session.input_steps += 1
                        report.total_inputs += 1
                        if obj.get("field"):
                            fields.append(obj["field"])
                        if obj.get("entity"):
                            entities.add(obj["entity"])
                    elif obj.get("type") == "command":
                        session.command_steps += 1
                        report.total_commands += 1

                    # Simula delay de input
                    time.sleep(self.input_delay_ms / 1000.0)
                    session.steps.append(step)

                session.total_steps = len(lines)
                session.entities_visited = sorted(entities)
                session.fields_filled = fields
                session.duration_ms = (time.time() - t0) * 1000

                all_entities.update(entities)
                all_durations.append(session.duration_ms)
                report.completed += 1
                report.session_results.append(session)

            except Exception as e:
                report.failed += 1
                report.session_results.append(ReplaySessionResult(
                    session_file=sf.name, status="error",
                    warnings=[str(e)],
                ))

        report.total_duration_ms = sum(all_durations)
        report.avg_duration_ms = report.total_duration_ms / max(len(all_durations), 1)
        report.entities_covered = sorted(all_entities)

        return report

    def replay_to_json(self, sessions_dir: Path) -> str:
        report = self.replay_sessions(sessions_dir)
        return json.dumps({
            "mode": "dry-run",
            "total_sessions": report.total_sessions,
            "completed": report.completed,
            "failed": report.failed,
            "total_inputs": report.total_inputs,
            "total_commands": report.total_commands,
            "total_duration_ms": round(report.total_duration_ms, 2),
            "avg_duration_ms": round(report.avg_duration_ms, 2),
            "entities_covered": report.entities_covered,
            "sessions": [
                {
                    "session": s.session_file,
                    "steps": s.total_steps,
                    "inputs": s.input_steps,
                    "commands": s.command_steps,
                    "entities": s.entities_visited,
                    "duration_ms": round(s.duration_ms, 2),
                    "status": s.status,
                    "warnings": s.warnings,
                }
                for s in report.session_results
            ],
        }, ensure_ascii=False, indent=2)

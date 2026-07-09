"""Macro-jornada: orquestração de múltiplas jornadas de módulo em sequência.

Simula um ciclo completo de negócio:
  modulo_cad → modulo_fin → modulo_cop → modulo_cor
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from .journey import JourneyDefinition, JourneyStep, JourneyDataset
from .journey_builder import JourneyBuilder
from .journey_verifier import JourneyVerifier, JourneyVerificationResult
from .error_detector import ErrorDetector
from .stress_runner import (
    SyntheticStressConfig,
    StressRunResult,
    StressSessionResult,
    SyntheticStressRunner,
)
from .homologation_report import HomologationReport


@dataclass
class MacroJourneyStep:
    """Um passo da macro-jornada (uma jornada de módulo completa)."""
    module_name: str = ""
    journey_id: str = ""
    order: int = 0
    session_count: int = 10
    seed_offset: int = 0
    depends_on: list[str] = field(default_factory=list)  # módulos que devem executar antes
    config_overrides: dict = field(default_factory=dict)


@dataclass
class MacroJourneyDefinition:
    """Definição de macro-jornada: sequência de jornadas de módulo."""
    macro_id: str = ""
    name: str = ""
    description: str = ""
    steps: list[MacroJourneyStep] = field(default_factory=list)
    global_seed: int = 0
    global_concurrency: int = 10


@dataclass
class MacroJourneyResult:
    """Resultado de execução da macro-jornada."""
    macro_id: str = ""
    module_results: dict[str, StressRunResult] = field(default_factory=dict)
    total_sessions: int = 0
    total_completed: int = 0
    total_failed: int = 0
    total_errors: int = 0
    duration_ms: int = 0
    report_html: str = ""
    report_json: dict = field(default_factory=dict)


class MacroJourneyRunner:
    """Executa múltiplas jornadas de módulo em sequência com dependências."""

    def __init__(self, db_path: str = ""):
        self.db_path = db_path

    def run(
        self,
        macro: MacroJourneyDefinition,
        on_module_start: Optional[callable] = None,
        on_module_end: Optional[callable] = None,
    ) -> MacroJourneyResult:
        """Executa a macro-jornada completa."""
        result = MacroJourneyResult(macro_id=macro.macro_id)
        start_ms = int(time.time() * 1000)

        # Ordenar passos por dependências
        ordered_steps = self._topological_sort(macro.steps)

        for step in ordered_steps:
            if on_module_start:
                on_module_start(step.module_name, step.order)

            config = SyntheticStressConfig(
                journey_id=step.journey_id,
                concurrency=macro.global_concurrency,
                seed=macro.global_seed + step.seed_offset,
                max_sessions=step.session_count,
                db_path=self.db_path,
                **step.config_overrides,
            )

            runner = SyntheticStressRunner(db_path=self.db_path)
            module_result = runner.run(config)
            result.module_results[step.module_name] = module_result

            result.total_sessions += module_result.total_sessions
            result.total_completed += module_result.completed
            result.total_failed += module_result.failed
            result.total_errors += module_result.errors

            if on_module_end:
                on_module_end(step.module_name, module_result)

        result.duration_ms = int(time.time() * 1000) - start_ms

        # Gerar relatório
        report = HomologationReport(title=f"Homologação: {macro.name}")
        result.report_json = report.generate_json(
            StressRunResult(
                total_sessions=result.total_sessions,
                completed=result.total_completed,
                failed=result.total_failed,
                errors=result.total_errors,
                duration_ms=result.duration_ms,
            )
        )
        result.report_html = report.generate_html(
            journey_name=macro.name,
            extra_sections=[
                {
                    "title": "Resumo por Módulo",
                    "content": self._render_module_summary_html(result.module_results),
                }
            ],
        )

        return result

    def run_sequential(
        self,
        journey_ids: list[str],
        session_count: int = 10,
        seed: int = 0,
        concurrency: int = 5,
    ) -> MacroJourneyResult:
        """Versão simplificada: executa lista de journey_ids em sequência."""
        steps = [
            MacroJourneyStep(
                module_name=jid,
                journey_id=jid,
                order=i,
                session_count=session_count,
                seed_offset=i * 1000,
            )
            for i, jid in enumerate(journey_ids)
        ]

        macro = MacroJourneyDefinition(
            macro_id="sequential_" + "_".join(journey_ids),
            name=" → ".join(journey_ids),
            description=f"Execução sequencial: {' → '.join(journey_ids)}",
            steps=steps,
            global_seed=seed,
            global_concurrency=concurrency,
        )

        return self.run(macro)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _topological_sort(steps: list[MacroJourneyStep]) -> list[MacroJourneyStep]:
        """Ordena passos respeitando dependências."""
        visited: set[str] = set()
        result: list[MacroJourneyStep] = []
        step_map = {s.module_name: s for s in steps}

        def visit(name: str):
            if name in visited:
                return
            visited.add(name)
            step = step_map.get(name)
            if step:
                for dep in step.depends_on:
                    if dep not in visited:
                        visit(dep)
                result.append(step)

        for s in sorted(steps, key=lambda s: s.order):
            visit(s.module_name)

        return result

    @staticmethod
    def _render_module_summary_html(module_results: dict[str, StressRunResult]) -> str:
        rows = ""
        for module_name, mr in module_results.items():
            rate = round(mr.completed / max(1, mr.total_sessions) * 100, 1)
            color = "#4caf50" if rate > 90 else "#ff9800" if rate > 70 else "#f44336"
            rows += f"""<tr>
<td><strong>{module_name}</strong></td>
<td>{mr.total_sessions}</td>
<td>{mr.completed}</td>
<td>{mr.failed}</td>
<td>{mr.errors}</td>
<td>{rate}%</td>
<td>
  <div class="progress-bar">
    <div class="progress-fill" style="width:{rate}%;background:{color};"></div>
  </div>
</td>
</tr>"""

        return f"""<table>
<tr><th>Módulo</th><th>Sessões</th><th>Sucesso</th><th>Falhas</th><th>Erros</th><th>Taxa</th><th></th></tr>
{rows}
</table>"""

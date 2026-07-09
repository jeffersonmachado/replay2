"""Relatório de homologação: gera HTML com análise completa de execução de jornadas."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Optional

from .journey_verifier import JourneyVerificationResult, JourneyVerifier
from .stress_runner import StressRunResult


class HomologationReport:
    """Gera relatório HTML de homologação a partir de resultados de stress/jornada."""

    def __init__(self, title: str = "Relatório de Homologação"):
        self.title = title
        self.generated_at = datetime.now().isoformat()

    def generate_html(
        self,
        stress_result: Optional[StressRunResult] = None,
        verification_results: Optional[list[JourneyVerificationResult]] = None,
        journey_name: str = "",
        extra_sections: Optional[list[dict]] = None,
    ) -> str:
        """Gera relatório HTML completo."""
        sections: list[str] = []

        sections.append(self._render_header(journey_name))
        sections.append(self._render_summary(stress_result, verification_results))
        sections.append(self._render_error_distribution(stress_result, verification_results))

        if stress_result and stress_result.aggregate_verification:
            sections.append(self._render_aggregate_analysis(stress_result.aggregate_verification))

        if stress_result and stress_result.session_results:
            sections.append(self._render_session_table(stress_result))
            sections.append(self._render_heatmap(stress_result))

        if extra_sections:
            for sec in extra_sections:
                sections.append(self._render_custom_section(sec))

        sections.append(self._render_footer())

        return "\n".join(sections)

    def generate_json(self, stress_result: StressRunResult | None = None) -> dict:
        """Gera relatório em formato JSON para consumo por API/dashboard."""
        report: dict = {
            "title": self.title,
            "generated_at": self.generated_at,
            "summary": {},
            "errors": {},
            "sessions": [],
        }

        if stress_result:
            report["summary"] = {
                "total_sessions": stress_result.total_sessions,
                "completed": stress_result.completed,
                "failed": stress_result.failed,
                "errors": stress_result.errors,
                "success_rate_pct": round(
                    stress_result.completed / max(1, stress_result.total_sessions) * 100, 1
                ),
                "duration_ms": stress_result.duration_ms,
                "duration_sec": round(stress_result.duration_ms / 1000, 1),
            }

            if stress_result.aggregate_verification:
                report["errors"] = stress_result.aggregate_verification

            for sr in stress_result.session_results:
                session_data = {
                    "session": sr.session_index,
                    "status": sr.status,
                    "duration_ms": sr.duration_ms,
                    "errors": sr.errors,
                }
                if sr.verification:
                    session_data["verification"] = {
                        "passed": sr.verification.passed,
                        "steps_passed": sr.verification.steps_passed,
                        "steps_failed": sr.verification.steps_failed,
                        "failure_rate_pct": sr.verification.failure_rate_pct,
                    }
                report["sessions"].append(session_data)

        return report

    # ------------------------------------------------------------------
    # Seções HTML
    # ------------------------------------------------------------------

    def _render_header(self, journey_name: str) -> str:
        return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<title>{self.title}</title>
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; margin: 40px; background: #f5f5f5; color: #333; }}
.container {{ max-width: 1200px; margin: 0 auto; }}
.header {{ background: linear-gradient(135deg, #1a237e, #283593); color: white; padding: 30px; border-radius: 8px; margin-bottom: 30px; }}
.header h1 {{ margin: 0; font-size: 28px; }}
.header .subtitle {{ opacity: 0.8; margin-top: 8px; }}
.card {{ background: white; border-radius: 8px; padding: 24px; margin-bottom: 20px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }}
.card h2 {{ margin-top: 0; color: #1a237e; border-bottom: 2px solid #e8eaf6; padding-bottom: 12px; }}
.stats {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 16px; }}
.stat {{ text-align: center; padding: 16px; border-radius: 8px; }}
.stat-success {{ background: #e8f5e9; color: #2e7d32; }}
.stat-fail {{ background: #ffebee; color: #c62828; }}
.stat-info {{ background: #e3f2fd; color: #1565c0; }}
.stat-warn {{ background: #fff3e0; color: #e65100; }}
.stat-value {{ font-size: 36px; font-weight: bold; }}
.stat-label {{ font-size: 14px; opacity: 0.8; margin-top: 4px; }}
table {{ width: 100%; border-collapse: collapse; }}
th, td {{ padding: 10px 14px; text-align: left; border-bottom: 1px solid #e0e0e0; }}
th {{ background: #f5f5f5; font-weight: 600; }}
tr:hover {{ background: #fafafa; }}
.badge {{ padding: 4px 10px; border-radius: 12px; font-size: 12px; font-weight: 600; }}
.badge-success {{ background: #c8e6c9; color: #2e7d32; }}
.badge-fail {{ background: #ffcdd2; color: #c62828; }}
.badge-warn {{ background: #ffe0b2; color: #e65100; }}
.badge-error {{ background: #f3e5f5; color: #6a1b9a; }}
.heatmap {{ display: flex; gap: 2px; flex-wrap: wrap; }}
.heatmap-cell {{ width: 20px; height: 20px; border-radius: 2px; }}
.footer {{ text-align: center; color: #999; font-size: 12px; margin-top: 40px; }}
.severity-critical {{ color: #c62828; font-weight: bold; }}
.severity-high {{ color: #e65100; }}
.severity-medium {{ color: #f9a825; }}
.severity-low {{ color: #78909c; }}
.progress-bar {{ background: #e0e0e0; border-radius: 4px; height: 8px; overflow: hidden; }}
.progress-fill {{ height: 100%; border-radius: 4px; }}
</style>
</head>
<body>
<div class="container">
<div class="header">
  <h1>{self.title}</h1>
  <div class="subtitle">{journey_name} &mdash; Gerado em {self.generated_at[:19]}</div>
</div>"""

    def _render_summary(
        self,
        stress_result: Optional[StressRunResult],
        verification_results: Optional[list[JourneyVerificationResult]],
    ) -> str:
        total = 0
        completed = 0
        failed = 0
        total_errors = 0

        if stress_result:
            total = stress_result.total_sessions
            completed = stress_result.completed
            failed = stress_result.failed
            total_errors = stress_result.errors

        success_rate = round(completed / max(1, total) * 100, 1) if total > 0 else 0

        duration_sec = round(stress_result.duration_ms / 1000, 1) if stress_result else 0

        return f"""<div class="card">
<h2>Resumo da Execução</h2>
<div class="stats">
  <div class="stat stat-info">
    <div class="stat-value">{total}</div>
    <div class="stat-label">Sessões Totais</div>
  </div>
  <div class="stat stat-success">
    <div class="stat-value">{completed}</div>
    <div class="stat-label">Sucesso</div>
  </div>
  <div class="stat stat-fail">
    <div class="stat-value">{failed}</div>
    <div class="stat-label">Falhas</div>
  </div>
  <div class="stat stat-warn">
    <div class="stat-value">{total_errors}</div>
    <div class="stat-label">Erros</div>
  </div>
  <div class="stat stat-info">
    <div class="stat-value">{success_rate}%</div>
    <div class="stat-label">Taxa de Sucesso</div>
  </div>
  <div class="stat stat-info">
    <div class="stat-value">{duration_sec}s</div>
    <div class="stat-label">Duração</div>
  </div>
</div>
<div style="margin-top: 16px;">
  <div class="progress-bar">
    <div class="progress-fill" style="width:{success_rate}%;background: {'#4caf50' if success_rate > 90 else '#ff9800' if success_rate > 70 else '#f44336'};"></div>
  </div>
</div>
</div>"""

    def _render_error_distribution(
        self,
        stress_result: Optional[StressRunResult],
        verification_results: Optional[list[JourneyVerificationResult]],
    ) -> str:
        by_type: dict[str, int] = {}
        by_severity: dict[str, int] = {}

        if stress_result:
            for sr in stress_result.session_results:
                for err in sr.errors:
                    etype = err.get("type", "unknown")
                    sev = err.get("severity", "medium")
                    by_type[etype] = by_type.get(etype, 0) + 1
                    by_severity[sev] = by_severity.get(sev, 0) + 1

        if not by_type:
            return ""

        type_rows = "".join(
            f"<tr><td>{t}</td><td>{c}</td></tr>"
            for t, c in sorted(by_type.items(), key=lambda x: -x[1])
        )
        sev_rows = "".join(
            f"<tr><td><span class='severity-{s}'>{s.upper()}</span></td><td>{c}</td></tr>"
            for s, c in sorted(by_severity.items(), key=lambda x: -x[1])
        )

        return f"""<div class="card">
<h2>Distribuição de Erros</h2>
<div style="display:grid;grid-template-columns:1fr 1fr;gap:24px;">
<div>
  <h3>Por Tipo</h3>
  <table><tr><th>Tipo</th><th>Ocorrências</th></tr>{type_rows}</table>
</div>
<div>
  <h3>Por Severidade</h3>
  <table><tr><th>Severidade</th><th>Ocorrências</th></tr>{sev_rows}</table>
</div>
</div>
</div>"""

    def _render_aggregate_analysis(self, analysis: dict) -> str:
        most_failing = analysis.get("most_failing_steps", [])
        fail_rows = "".join(
            f"<tr><td>Passo {step}</td><td>{count} falhas</td></tr>"
            for step, count in most_failing[:10]
        ) if most_failing else "<tr><td colspan='2'>Nenhum passo com falha</td></tr>"

        return f"""<div class="card">
<h2>Análise Agregada</h2>
<div class="stats">
  <div class="stat stat-info">
    <div class="stat-value">{analysis.get('total_sessions', 0)}</div>
    <div class="stat-label">Sessões Analisadas</div>
  </div>
  <div class="stat stat-fail">
    <div class="stat-value">{analysis.get('total_sessions_with_errors', 0)}</div>
    <div class="stat-label">Sessões com Erro</div>
  </div>
  <div class="stat stat-info">
    <div class="stat-value">{analysis.get('overall_pass_rate_pct', 0)}%</div>
    <div class="stat-label">Taxa de Aprovação</div>
  </div>
</div>
<h3 style="margin-top:20px;">Passos que Mais Falham</h3>
<table><tr><th>Passo</th><th>Falhas</th></tr>{fail_rows}</table>
</div>"""

    def _render_session_table(self, stress_result: StressRunResult) -> str:
        rows = ""
        for sr in stress_result.session_results[:50]:  # Limitar a 50
            badge_class = {
                "success": "badge-success",
                "failed": "badge-fail",
                "error": "badge-error",
            }.get(sr.status, "badge-warn")

            error_count = len(sr.errors)
            errors_str = ", ".join(
                f"{e.get('type','?')}({e.get('severity','?')})" for e in sr.errors[:3]
            ) if sr.errors else "-"

            rows += f"""<tr>
<td>{sr.session_index}</td>
<td><span class="badge {badge_class}">{sr.status.upper()}</span></td>
<td>{sr.duration_ms}ms</td>
<td>{error_count}</td>
<td style="font-size:12px;">{errors_str}</td>
</tr>"""

        return f"""<div class="card">
<h2>Sessões ({min(50, len(stress_result.session_results))} de {len(stress_result.session_results)})</h2>
<table>
<tr><th>#</th><th>Status</th><th>Duração</th><th>Erros</th><th>Detalhes</th></tr>
{rows}
</table>
</div>"""

    def _render_heatmap(self, stress_result: StressRunResult) -> str:
        cells = ""
        for sr in stress_result.session_results[:200]:
            color = {
                "success": "#4caf50",
                "failed": "#f44336",
                "error": "#9c27b0",
            }.get(sr.status, "#ff9800")
            cells += f'<div class="heatmap-cell" style="background:{color};" title="Sessão {sr.session_index}: {sr.status}"></div>'

        return f"""<div class="card">
<h2>Heatmap de Sessões</h2>
<div class="heatmap">{cells}</div>
<div style="margin-top:8px;font-size:12px;color:#999;">
  <span style="color:#4caf50;">Sucesso</span> &middot;
  <span style="color:#f44336;">Falha</span> &middot;
  <span style="color:#9c27b0;">Erro</span>
</div>
</div>"""

    def _render_custom_section(self, section: dict) -> str:
        title = section.get("title", "")
        content = section.get("content", "")
        return f"""<div class="card">
<h2>{title}</h2>
{content}
</div>"""

    def _render_footer(self) -> str:
        return f"""<div class="footer">
Relatório gerado automaticamente pelo Dakota Replay &mdash; {self.generated_at[:19]}
</div>
</div>
</body>
</html>"""

from __future__ import annotations

import csv
import io

from control.services.report_common import extract_run_environment
from control.services.report_overview_service import (
    build_observability_overview,
    build_ops_overview,
    build_reprocess_analytics,
    build_runs_trend_report,
)
from control.services.report_run_service import (
    build_reprocess_trace,
    build_run_comparison,
    build_run_family,
    build_run_report,
    create_reprocess_run_from_failure,
    find_baseline_run,
)


def report_to_markdown(report: dict, comparison: dict | None = None) -> str:
    report = report or {}
    run = report.get("run") or {}
    summary = report.get("summary") or {}
    lines = [
        f"# Run Report #{run.get('id', '-')}",
        "",
        f"environment: {report.get('environment') or '-'}",
        f"status: {run.get('status') or '-'}",
        f"target: {run.get('target_user') or '-'}@{run.get('target_host') or '-'}",
        "",
        "## Summary",
        "",
        f"- failure_count: {summary.get('failure_count', 0)}",
        f"- session_count_with_failures: {summary.get('session_count_with_failures', 0)}",
        f"- flow_count_with_failures: {summary.get('flow_count_with_failures', 0)}",
        "",
        "## Flows",
        "",
    ]
    flows = report.get("flows") or []
    if flows:
        for flow in flows:
            lines.append(f"- {flow.get('flow_name') or 'sem_fluxo'}: {flow.get('failure_count', 0)}")
    else:
        lines.append("- sem_fluxo: 0")
    if comparison:
        cmp_summary = comparison.get("summary") or {}
        lines.extend(
            [
                "",
                "## Comparison",
                "",
                f"- new_failure_groups: {cmp_summary.get('new_failure_groups', 0)}",
                f"- recurring_failure_groups: {cmp_summary.get('recurring_failure_groups', 0)}",
                f"- resolved_failure_groups: {cmp_summary.get('resolved_failure_groups', 0)}",
                f"- regression: {bool(cmp_summary.get('regression'))}",
            ]
        )
    return "\n".join(lines)


def report_to_csv(report: dict) -> str:
    report = report or {}
    output = io.StringIO()
    writer = csv.writer(output, quoting=csv.QUOTE_ALL)
    writer.writerow(["section", "name", "value"])
    writer.writerow(["run", "id", report.get("run", {}).get("id", "")])
    writer.writerow(["run", "environment", report.get("environment") or ""])
    for key, value in (report.get("summary") or {}).items():
        if isinstance(value, dict):
            for subkey, subvalue in value.items():
                writer.writerow(["summary", f"{key}.{subkey}", subvalue])
        else:
            writer.writerow(["summary", key, value])
    for flow in report.get("flows") or []:
        writer.writerow(["flow", flow.get("flow_name") or "sem_fluxo", flow.get("failure_count") or 0])
    return output.getvalue()


__all__ = [
    "extract_run_environment",
    "build_run_report",
    "build_run_family",
    "build_reprocess_trace",
    "build_reprocess_analytics",
    "create_reprocess_run_from_failure",
    "build_ops_overview",
    "build_observability_overview",
    "build_runs_trend_report",
    "find_baseline_run",
    "build_run_comparison",
    "report_to_markdown",
    "report_to_csv",
]

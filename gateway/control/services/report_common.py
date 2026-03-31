from __future__ import annotations

from control.services.scenario_shared import extract_environment


def extract_run_environment(run) -> str:
    return extract_environment(run)

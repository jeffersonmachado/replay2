"""Serviço capture-to-synthetic para sessões gravadas pela UI."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from dakota_gateway.synthetic.journey_synthesizer import JourneySynthesizer

from control.services.capture_service import get_capture


def _slug(value: str, fallback: str = "capture") -> str:
    clean = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(value or "").strip()).strip("-._")
    return clean[:80] or fallback


def _find_capture_jsonl(log_dir: str) -> list[Path]:
    base = Path(str(log_dir or "").strip())
    if not base.exists() or not base.is_dir():
        return []
    audit_files = sorted(base.glob("audit-*.jsonl"))
    if audit_files:
        return audit_files
    return sorted(base.glob("*.jsonl"))


def _combine_jsonl(files: list[Path], destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8") as out:
        for path in files:
            with path.open("r", encoding="utf-8", errors="replace") as src:
                for line in src:
                    if line.strip():
                        out.write(line if line.endswith("\n") else line + "\n")
    return destination


def synthesize_capture(
    con,
    capture_id: int,
    *,
    source_dir: str,
    samples: int = 10,
    seed: int | None = None,
    name: str = "",
    out_dir: str = "",
    include_validation: bool = True,
    include_stress: bool = False,
    concurrency: int = 5,
) -> dict[str, Any]:
    """Transforma uma captura registrada em template + dataset + sessões sintéticas."""
    capture = get_capture(con, capture_id)
    if not capture:
        raise ValueError("captura não encontrada")

    source_path = Path(str(source_dir or "").strip())
    if not source_path.exists() or not source_path.is_dir():
        raise ValueError("source_dir inválido ou inexistente")

    log_dir = str(capture.get("log_dir") or "").strip()
    files = _find_capture_jsonl(log_dir)
    if not files:
        raise ValueError("nenhum arquivo .jsonl encontrado na captura")

    samples = max(1, min(int(samples or 10), 10000))
    concurrency = max(1, min(int(concurrency or 5), 500))
    run_name = _slug(name or f"capture-{capture_id}-synthetic")
    output_dir = Path(out_dir or Path(log_dir) / "synthetic" / run_name)
    output_dir.mkdir(parents=True, exist_ok=True)

    capture_jsonl = files[0] if len(files) == 1 else _combine_jsonl(files, output_dir / "capture_combined.jsonl")

    synthesizer = JourneySynthesizer()
    template = synthesizer.from_capture(capture_jsonl, source_path, name=name or run_name)
    result = synthesizer.synthesize(template, samples=samples, out_dir=output_dir, seed=seed)

    validation = None
    if include_validation:
        validation = synthesizer.validate_sessions(Path(result.sessions_dir), template)

    stress = None
    if include_stress:
        stress = synthesizer.simulate_stress(Path(result.sessions_dir), concurrency=concurrency)

    report = {}
    report_path = Path(result.report_path)
    if report_path.exists():
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
        except Exception:
            report = {}

    return {
        "ok": True,
        "capture_id": capture_id,
        "capture": capture,
        "source_dir": str(source_path),
        "capture_files": [str(path) for path in files],
        "capture_jsonl": str(capture_jsonl),
        "output_dir": str(output_dir),
        "journey_id": result.journey_id,
        "name": result.name,
        "samples": result.samples,
        "generated_sessions": result.generated_sessions,
        "entities_involved": result.entities_involved,
        "mapped_inputs": result.mapped_inputs,
        "command_inputs": result.command_inputs,
        "unmapped_inputs": result.unmapped_inputs,
        "artifacts": {
            "template": result.template_path,
            "dataset": result.dataset_path,
            "sessions_dir": result.sessions_dir,
            "report": result.report_path,
        },
        "screen_mappings": result.screen_mappings,
        "warnings": result.warnings,
        "evidence": result.evidence,
        "validation": validation,
        "stress": stress,
        "report": report,
    }

#!/usr/bin/env python3
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
ARTIFACTS = ROOT / "artifacts"
LOG_ROOT = ARTIFACTS / "acceptance-logs"
SID = "val-001"


def run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_tree(paths: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in sorted(paths, key=lambda p: p.relative_to(ROOT).as_posix()):
        if not path.is_file():
            continue
        rel = path.relative_to(ROOT).as_posix()
        if "/__pycache__/" in f"/{rel}" or rel.endswith((".pyc", ".pyo")):
            continue
        digest.update(rel.encode("utf-8") + b"\0")
        digest.update(sha256_file(path).encode("ascii") + b"\n")
    return digest.hexdigest()


def source_tree_files() -> list[Path]:
    roots = ["bin", "lib", "screens", "examples", "gateway", "tests", "scripts"]
    files: list[Path] = []
    for name in roots:
        root = ROOT / name
        if root.exists():
            files.extend(path for path in root.rglob("*") if path.is_file())
    for name in ["install.sh", "uninstall.sh", "VERSION", "README.md"]:
        path = ROOT / name
        if path.is_file():
            files.append(path)
    return files


def parse_gate_steps(log_text: str) -> list[dict]:
    steps: list[dict] = []
    starts: dict[str, str] = {}
    start_pattern = re.compile(r"START name=(?P<name>\S+) timeout=(?P<timeout>\d+)s command=(?P<command>.*)$")
    end_pattern = re.compile(r"END name=(?P<name>\S+) duration=(?P<duration>\d+)s exit_code=(?P<exit>\d+) timeout=(?P<timeout>true|false)")
    for match in start_pattern.finditer(log_text):
        starts[match.group("name")] = match.group("command")
    for match in end_pattern.finditer(log_text):
        name = match.group("name")
        steps.append(
            {
                "name": name,
                "command": starts.get(name),
                "started_at": None,
                "finished_at": None,
                "duration_seconds": int(match.group("duration")),
                "exit_code": int(match.group("exit")),
                "timed_out": match.group("timeout") == "true",
                "passed": 0,
                "failed": 0,
                "skipped": 0,
                "subtests": 0,
            }
        )
    return steps


def summarize_logs() -> list[dict]:
    summaries: list[dict] = []
    if not LOG_ROOT.exists():
        return summaries
    for path in sorted(LOG_ROOT.rglob("*.log")):
        rel = path.relative_to(ROOT).as_posix()
        text = path.read_text(encoding="utf-8", errors="replace")
        summaries.append(
            {
                "path": rel,
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
                "gate_passed_markers": text.count("GATE PASSED"),
                "gate_failed_markers": text.count("GATE FAILED"),
                "timeout_markers": text.count("timeout=true"),
                "tail": text.splitlines()[-12:],
            }
        )
    return summaries


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def manual_audit() -> list[dict]:
    ts = 1000
    seq = 0

    def ev(etype: str, **kw) -> dict:
        nonlocal ts, seq
        seq += 1
        ts += 100
        base = {"v": "1.0", "type": etype, "seq_global": seq, "ts_ms": ts, "actor": "report", "session_id": SID, "seq_session": seq}
        base.update(kw)
        return base

    return [
        ev("session_start", rows=2, cols=3, term="xterm", encoding="utf-8"),
        ev("bytes", dir="out", n=1, data_b64=b64(b"A")),
        ev("bytes", dir="out", n=4, data_b64=b64(b"\x1b[8;")),
        ev("bytes", dir="out", n=3, data_b64=b64(b"3;4t")),
        ev("bytes", dir="out", n=1, data_b64=b64(b"B")),
        ev("bytes", dir="out", n=9, data_b64=b64(b"\x1b[7mREV\x1b[0m")),
        ev("bytes", dir="out", n=12, data_b64=b64(b"\x1b[42m  \x1b[0m")),
        ev("bytes", dir="out", n=1, data_b64=b64(b"\xc3")),
        ev("bytes", dir="out", n=1, data_b64=b64(b"\xa1")),
        ev("bytes", dir="out", n=2, data_b64=b64(b"\xf0\x9f")),
        ev("bytes", dir="out", n=2, data_b64=b64(b"\x98\x80")),
        ev("bytes", dir="out", n=1, data_b64=b64(b"\x1b")),
        ev("bytes", dir="out", n=1, data_b64=b64(b"c")),
        ev("bytes", dir="out", n=5, data_b64=b64(b"final")),
        ev("session_end"),
    ]


def build_manual_validation() -> dict:
    sys.path.insert(0, str(ROOT / "gateway"))
    from control.services.session_replay_service import prepare_session_replay_data

    with tempfile.TemporaryDirectory() as tmp:
        audit_path = Path(tmp) / "audit-000001.jsonl"
        with audit_path.open("w", encoding="utf-8") as f:
            for ev in manual_audit():
                f.write(json.dumps(ev, ensure_ascii=False) + "\n")
        result = prepare_session_replay_data(tmp, SID)

    if result.get("error"):
        return {"passed": False, "error": result["error"]}

    events = result.get("events", [])
    playback = result.get("playback", {})
    out_events = [e for e in events if e.get("direction") == "out"]
    summary = {
        "passed": True,
        "session_id": SID,
        "initial_geometry": [result["initial_snapshot"]["rows"], result["initial_snapshot"]["cols"]],
        "final_geometry": [result["final_snapshot"]["rows"], result["final_snapshot"]["cols"]],
        "event_count": len(events),
        "timeline_item_count": len(result.get("timeline_items", [])),
        "checkpoint_count": len(result.get("checkpoints", [])),
        "diff_count": sum(1 for e in out_events if e.get("diff")),
        "playback_event_refs": len(playback.get("event_refs", [])),
        "text_sig": result["canonical_signatures"]["text_sig"],
        "visual_sig": result["canonical_signatures"]["visual_sig"],
        "semantic_sig": result["canonical_signatures"]["semantic_sig"],
        "payload_bytes": len(json.dumps(result, ensure_ascii=False, default=dict).encode("utf-8")),
    }
    return summary


def latest_tarball() -> dict | None:
    tarballs = sorted((ROOT / "dist").glob("dakota-replay2-*.tar.gz"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not tarballs:
        return None
    path = tarballs[0]
    return {
        "path": path.relative_to(ROOT).as_posix(),
        "sha256": sha256_file(path),
        "bytes": path.stat().st_size,
        "entries": int(run(["bash", "-c", f"tar -tzf {path.as_posix()!r} | wc -l"]).stdout.strip() or "0"),
    }


def log_record(path: Path) -> dict | None:
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8", errors="replace")
    steps = parse_gate_steps(text)
    return {
        "name": path.stem,
        "command": None,
        "started_at": None,
        "finished_at": None,
        "duration_seconds": sum(step["duration_seconds"] for step in steps),
        "exit_code": 0 if "GATE PASSED" in text and "GATE FAILED" not in text else 1,
        "timed_out": any(step["timed_out"] for step in steps),
        "passed": text.count("[PASS]") + text.count(" passed"),
        "failed": text.count("[FAIL]") + text.count(" failed"),
        "skipped": text.count(" skipped"),
        "subtests": text.count("subtests passed"),
        "log_path": path.relative_to(ROOT).as_posix(),
        "log_sha256": sha256_file(path),
    }


def acceptance_baseline() -> list[dict]:
    baseline = ARTIFACTS / "acceptance-test-baseline.sha256"
    if not baseline.exists():
        return []
    entries: list[dict] = []
    for line in baseline.read_text(encoding="utf-8", errors="replace").splitlines():
        parts = line.strip().split(maxsplit=1)
        if len(parts) == 2:
            entries.append({"sha256": parts[0], "path": parts[1]})
    return entries


def main() -> int:
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    log_summaries = summarize_logs()
    (ARTIFACTS / "acceptance-log-summary.json").write_text(json.dumps(log_summaries, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    manual = build_manual_validation()
    (ARTIFACTS / "manual-validation.json").write_text(json.dumps(manual, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    final_log = LOG_ROOT / "final" / "run-phase-08-full.log"
    final_text = final_log.read_text(encoding="utf-8", errors="replace") if final_log.exists() else ""
    steps = parse_gate_steps(final_text)
    git_head = run(["git", "rev-parse", "--short=12", "HEAD"]).stdout.strip()
    completed_at = os.environ.get("DAKOTA_REPORT_TIMESTAMP") or datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    working_tree_dirty = bool(run(["git", "status", "--porcelain"]).stdout.strip())
    final_gate_passed = bool(final_text.count("GATE PASSED")) and all(s["exit_code"] == 0 and not s["timed_out"] for s in steps)
    manual_passed = bool(manual.get("passed"))
    logs_exist = bool(log_summaries)
    tarball = latest_tarball() if os.environ.get("DAKOTA_REPORT_EMBED_TARBALL") == "1" else None
    no_pending = final_gate_passed and manual_passed and logs_exist and not working_tree_dirty
    results = {
        "completed_at": completed_at,
        "no_pending_issues": no_pending,
        "final_result": "passed" if no_pending else ("validated_with_dirty_tree" if final_gate_passed and manual_passed else "unknown"),
        "git_head": git_head,
        "source_tree_sha256": sha256_tree(source_tree_files()),
        "acceptance_baseline": acceptance_baseline(),
        "working_tree_dirty": working_tree_dirty,
        "final_gate_log": final_log.relative_to(ROOT).as_posix() if final_log.exists() else None,
        "final_gate_log_sha256": sha256_file(final_log) if final_log.exists() else None,
        "final_gate_steps": steps,
        "manual_validation": manual,
        "log_summary": "artifacts/acceptance-log-summary.json",
        "tarball": tarball,
        "commands": [record for record in (log_record(final_log),) if record],
    }
    (ARTIFACTS / "final-acceptance-results.json").write_text(json.dumps(results, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    report = [
        "# Replay2 final acceptance",
        "",
        f"- Completed at: {results['completed_at']}",
        f"- Result: {results['final_result']}",
        f"- No pending issues: {results['no_pending_issues']}",
        f"- Git HEAD: {git_head}",
        f"- Source tree sha256: {results['source_tree_sha256']}",
        f"- Dirty working tree: {results['working_tree_dirty']}",
        f"- Manual validation: {'passed' if manual.get('passed') else 'failed'}",
        f"- Logs summarized: {len(log_summaries)}",
        f"- Final gate log sha256: {results['final_gate_log_sha256']}",
    ]
    if results["tarball"]:
        report.append(f"- Latest tarball: {results['tarball']['path']}")
        report.append(f"- Tarball sha256: {results['tarball']['sha256']}")
        report.append(f"- Tarball size: {results['tarball']['bytes']} bytes")
        report.append(f"- Tarball entries: {results['tarball']['entries']}")
    report.extend(["", "## Final Gate Steps"])
    for step in steps:
        report.append(f"- {step['name']}: exit={step['exit_code']} duration={step['duration_seconds']}s timeout={step['timed_out']}")
    (ARTIFACTS / "final-acceptance-report.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    return 0 if manual.get("passed") else 1


if __name__ == "__main__":
    raise SystemExit(main())

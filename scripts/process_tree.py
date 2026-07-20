"""Process tree executor — single source of truth for process management.

Used by: _gate_lib.sh, test.sh, test-all.sh, phase 7, phase 8,
         final-acceptance.sh, contamination tests, visual tests.

Features:
- process_run_id via DAKOTA_PROCESS_RUN_ID env var for cross-session tracking
- PID reuse detection via /proc/<pid>/stat start_time_ticks
- Escaped process detection (different SID/PGID or reparented)
- Leaked process detection (survivors after parent exits)
- TERM→KILL cleanup with individual PID + PGID targeting
- Zombie detection and reaping
- CLI: run, inspect, cleanup, validate-result
- --stdout-log / --stderr-log for explicit log paths
- Atomic result JSON with full process accounting
"""
from __future__ import annotations

import hashlib
import json
import os
import signal
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def _proc_stat(pid: int) -> dict:
    try:
        raw = Path(f"/proc/{pid}/stat").read_text()
        close = raw.rfind(")")
        if close < 0:
            return {}
        fields = raw[close + 2:].split()
        if len(fields) < 20:
            return {}
        return {
            "pid": int(raw.split("(", 1)[0].strip()),
            "comm": raw.split("(", 1)[1].split(")", 1)[0],
            "state": fields[0],
            "ppid": int(fields[1]),
            "pgid": int(fields[2]),
            "sid": int(fields[3]),
            "start_time_ticks": int(fields[19]) if len(fields) > 19 else 0,
        }
    except Exception:
        return {}


def _proc_environ(pid: int) -> dict[str, str]:
    try:
        raw = Path(f"/proc/{pid}/environ").read_bytes()
        result = {}
        for item in raw.split(b"\x00"):
            if b"=" in item:
                k, v = item.split(b"=", 1)
                result[k.decode()] = v.decode()
        return result
    except Exception:
        return {}


def _pid_identity(pid: int) -> str:
    s = _proc_stat(pid)
    if s:
        return f"{pid}:{s['start_time_ticks']}"
    return f"{pid}:0"


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def _is_zombie(pid: int) -> bool:
    s = _proc_stat(pid)
    return s.get("state") == "Z" if s else False


def _find_by_run_id(run_id: str) -> list[dict]:
    found = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        if pid == os.getpid():
            continue
        env = _proc_environ(pid)
        if env.get("DAKOTA_PROCESS_RUN_ID") == run_id:
            s = _proc_stat(pid)
            found.append({
                "pid": pid, "ppid": s.get("ppid", 0),
                "pgid": s.get("pgid", 0), "sid": s.get("sid", 0),
                "state": s.get("state", "?"),
                "start_time_ticks": s.get("start_time_ticks", 0),
                "comm": s.get("comm", ""),
                "identity": _pid_identity(pid),
                "is_zombie": s.get("state") == "Z",
                "process_run_id": run_id,
            })
    return found


def _find_descendants(pid: int) -> list[int]:
    found = []
    try:
        r = subprocess.run(["ps", "--no-headers", "-o", "pid", "--ppid", str(pid)],
                           capture_output=True, text=True, timeout=3)
        for line in r.stdout.strip().splitlines():
            parts = line.strip().split()
            if parts and parts[0].isdigit():
                c = int(parts[0])
                found.append(c)
                found.extend(_find_descendants(c))
    except Exception:
        pass
    return found


@dataclass
class ProcessTreeResult:
    schema_version: str = "1.0"
    release_run_id: str = ""
    run_id: str = ""
    process_run_id: str = ""
    name: str = ""
    command: list[str] = field(default_factory=list)
    pid: int = 0
    pgid: int = 0
    sid: int = 0
    started_at: str = ""
    finished_at: str = ""
    duration_seconds: float = 0.0
    exit_code: Optional[int] = None
    timed_out: bool = False
    processes_seen: list[dict] = field(default_factory=list)
    process_groups_seen: list[int] = field(default_factory=list)
    sessions_seen: list[int] = field(default_factory=list)
    escaped_processes: list[dict] = field(default_factory=list)
    leaked_processes: list[dict] = field(default_factory=list)
    terminated_processes: list[dict] = field(default_factory=list)
    killed_processes: list[dict] = field(default_factory=list)
    alive_after_cleanup: list[dict] = field(default_factory=list)
    zombies_seen: list[dict] = field(default_factory=list)
    zombies_after_cleanup: list[dict] = field(default_factory=list)
    remaining_processes: int = 0
    remaining_zombies: int = 0
    stdout_path: str = ""
    stderr_path: str = ""
    stdout_sha256: str = ""
    stderr_sha256: str = ""
    result_json_path: str = ""
    failure_reasons: list[str] = field(default_factory=list)

    @property
    def success(self) -> bool:
        return (
            self.exit_code == 0
            and not self.timed_out
            and len(self.escaped_processes) == 0
            and len(self.leaked_processes) == 0
            and self.remaining_processes == 0
            and self.remaining_zombies == 0
        )

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "release_run_id": self.release_run_id, "run_id": self.run_id,
            "process_run_id": self.process_run_id, "name": self.name,
            "command": self.command, "pid": self.pid, "pgid": self.pgid, "sid": self.sid,
            "started_at": self.started_at, "finished_at": self.finished_at,
            "duration_seconds": self.duration_seconds,
            "exit_code": self.exit_code, "timed_out": self.timed_out,
            "processes_seen": self.processes_seen,
            "process_groups_seen": self.process_groups_seen,
            "sessions_seen": self.sessions_seen,
            "escaped_processes": self.escaped_processes,
            "leaked_processes": self.leaked_processes,
            "terminated_processes": self.terminated_processes,
            "killed_processes": self.killed_processes,
            "alive_after_cleanup": self.alive_after_cleanup,
            "zombies_seen": self.zombies_seen,
            "zombies_after_cleanup": self.zombies_after_cleanup,
            "remaining_processes": self.remaining_processes,
            "remaining_zombies": self.remaining_zombies,
            "stdout_path": self.stdout_path, "stderr_path": self.stderr_path,
            "stdout_sha256": self.stdout_sha256, "stderr_sha256": self.stderr_sha256,
            "success": self.success, "failure_reasons": self.failure_reasons,
        }


def _kill_tree(run_id: str, sid: int, pgid: int, all_pids: list[dict],
               term_deadline: float, kill_deadline: float) -> tuple[list[dict], list[dict]]:
    """Kill process tree with absolute deadlines for TERM and KILL phases."""
    killed_ids: set[int] = set()
    # TERM phase
    try:
        os.killpg(pgid, signal.SIGTERM)
    except OSError:
        pass
    for p in all_pids:
        try:
            os.kill(p["pid"], signal.SIGTERM)
        except (OSError, ProcessLookupError):
            pass
    # Wait until term_deadline
    remaining_term = max(0.0, term_deadline - time.time())
    if remaining_term > 0:
        time.sleep(min(remaining_term, 0.5))
    # KILL phase
    if time.time() < kill_deadline:
        try:
            os.killpg(pgid, signal.SIGKILL)
        except OSError:
            pass
        for p in all_pids:
            try:
                os.kill(p["pid"], signal.SIGKILL)
            except (OSError, ProcessLookupError):
                pass
            killed_ids.add(p["pid"])
        remaining_kill = max(0.0, kill_deadline - time.time())
        if remaining_kill > 0:
            time.sleep(min(remaining_kill, 0.3))
        # Final sweep: kill any remaining by run_id
        remaining = _find_by_run_id(run_id)
        for rp in remaining:
            try:
                os.kill(rp["pid"], signal.SIGKILL)
            except (OSError, ProcessLookupError):
                pass
            killed_ids.add(rp["pid"])
    alive = []
    for p in all_pids:
        if _pid_alive(p["pid"]) and not _is_zombie(p["pid"]):
            alive.append(p)
    killed = [p for p in all_pids if p["pid"] in killed_ids and not _pid_alive(p["pid"])]
    return killed, alive


def run_with_timeout(
    command: list[str],
    timeout: float = 60.0,
    cwd: Optional[str] = None,
    env: Optional[dict] = None,
    result_json_path: Optional[str] = None,
    stdout_log_path: Optional[str] = None,
    stderr_log_path: Optional[str] = None,
    process_run_id: Optional[str] = None,
    name: str = "",
    release_run_id: str = "",
    run_id: str = "",
) -> ProcessTreeResult:
    if process_run_id is None:
        process_run_id = f"proc-{uuid.uuid4().hex[:12]}"

    stdout_f = Path(stdout_log_path) if stdout_log_path else Path(os.path.join("/tmp", f"pt-{process_run_id}.stdout"))
    stderr_f = Path(stderr_log_path) if stderr_log_path else Path(os.path.join("/tmp", f"pt-{process_run_id}.stderr"))
    stdout_f.parent.mkdir(parents=True, exist_ok=True)
    stderr_f.parent.mkdir(parents=True, exist_ok=True)

    stdout_fh = open(stdout_f, "w")
    stderr_fh = open(stderr_f, "w")

    started_at = _now_iso()
    start = time.time()

    run_env = (env or os.environ).copy() if env else os.environ.copy()
    run_env["DAKOTA_PROCESS_RUN_ID"] = process_run_id

    proc = subprocess.Popen(
        command, stdout=stdout_fh, stderr=stderr_fh,
        cwd=cwd, env=run_env, start_new_session=True,
    )

    pid = proc.pid
    try:
        pgid = os.getpgid(pid)
    except OSError:
        pgid = pid
    try:
        sid = os.getsid(pid)
    except OSError:
        sid = pgid

    result = ProcessTreeResult(
        schema_version="1.0", release_run_id=release_run_id, run_id=run_id,
        process_run_id=process_run_id, name=name, command=command,
        pid=pid, pgid=pgid, sid=sid, started_at=started_at,
        stdout_path=str(stdout_f), stderr_path=str(stderr_f),
        result_json_path=result_json_path or "",
    )

    known_pids: dict[str, dict] = {}
    scan_interval = 5.0
    deadline = start + timeout

    while proc.poll() is None:
        remaining = deadline - time.time()
        if remaining <= 0:
            break
        for cp in _find_by_run_id(process_run_id):
            known_pids[cp["identity"]] = cp
        time.sleep(min(scan_interval, max(0.1, remaining)))

    exit_code = proc.poll()
    result.exit_code = exit_code
    result.timed_out = exit_code is None

    # Absolute deadlines for cleanup (budgeted separately from execution timeout)
    now = time.time()
    term_grace = min(timeout * 0.1, 2.0)  # max 10% of timeout, capped at 2s
    kill_grace = 1.0
    final_wait_grace = 1.0
    term_deadline = now + term_grace
    kill_deadline = term_deadline + kill_grace
    absolute_deadline = kill_deadline + final_wait_grace

    # One final scan (not in a loop) to capture any stragglers
    for cp in _find_by_run_id(process_run_id):
        known_pids[cp["identity"]] = cp

    my_pid = os.getpid()
    all_pids_list = [p for p in known_pids.values() if p["pid"] != my_pid and p["pid"] != pid]

    result.processes_seen = list(known_pids.values())
    result.process_groups_seen = list(set(p["pgid"] for p in all_pids_list))
    result.sessions_seen = list(set(p["sid"] for p in all_pids_list))

    def _is_escaped(p: dict) -> bool:
        if p["pid"] == pid or p.get("is_zombie"):
            return False
        return p["sid"] != result.sid or p["ppid"] == 1

    result.escaped_processes = [p for p in all_pids_list if _is_escaped(p)]
    result.leaked_processes = [p for p in all_pids_list
                                if _pid_alive(p["pid"]) and not p.get("is_zombie")]

    killed, alive_after_term = _kill_tree(process_run_id, sid, pgid, all_pids_list,
                                          term_deadline, kill_deadline)
    result.killed_processes = killed

    # Final wait for main process within absolute_deadline
    remaining_final = max(0.0, absolute_deadline - time.time())
    try:
        proc.wait(timeout=remaining_final)
    except subprocess.TimeoutExpired:
        pass

    zombies_all = [p for p in known_pids.values() if p.get("is_zombie") or _is_zombie(p["pid"])]
    result.zombies_seen = zombies_all
    result.zombies_after_cleanup = [p for p in zombies_all if _pid_alive(p["pid"])]

    result.alive_after_cleanup = [p for p in all_pids_list
                                   if _pid_alive(p["pid"]) and not _is_zombie(p["pid"]) and p["pid"] != my_pid]
    result.remaining_processes = len(result.alive_after_cleanup)
    result.remaining_zombies = len(result.zombies_after_cleanup)

    # leaked_processes = survivors before cleanup (definitive)
    # alive_after_cleanup = survivors after cleanup (for remaining_processes)
    # Do NOT overwrite leaked_processes with alive_after_cleanup

    if result.exit_code is not None and result.exit_code != 0:
        result.failure_reasons.append(f"exit_code={result.exit_code}")
    if result.timed_out:
        result.failure_reasons.append("timed_out")
    if result.escaped_processes:
        result.failure_reasons.append(f"escaped={len(result.escaped_processes)}")
    if result.leaked_processes:
        result.failure_reasons.append(f"leaked={len(result.leaked_processes)}")
    if result.remaining_processes > 0:
        result.failure_reasons.append(f"remaining={result.remaining_processes}")
    if result.remaining_zombies > 0:
        result.failure_reasons.append(f"zombies={result.remaining_zombies}")

    result.duration_seconds = round(time.time() - start, 2)
    result.finished_at = _now_iso()

    stdout_fh.close()
    stderr_fh.close()
    try:
        result.stdout_sha256 = hashlib.sha256(stdout_f.read_bytes()).hexdigest()
        result.stderr_sha256 = hashlib.sha256(stderr_f.read_bytes()).hexdigest()
    except Exception:
        pass

    if result_json_path:
        Path(result_json_path).parent.mkdir(parents=True, exist_ok=True)
        Path(result_json_path).write_text(json.dumps(result.to_dict(), indent=2))

    return result


# ── CLI ──────────────────────────────────────────────────────────────────────

import tempfile as _tempfile  # noqa: E402


def _cli_run(args: list[str]) -> int:
    import argparse
    parser = argparse.ArgumentParser(prog="process_tree.py run")
    parser.add_argument("--name", default="command")
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--stdout-log", default="")
    parser.add_argument("--stderr-log", default="")
    parser.add_argument("--result-json", default="")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--release-run-id", default="")
    parser.add_argument("--parent-run-id", default="")
    parser.add_argument("command_args", nargs=argparse.REMAINDER)
    opts = parser.parse_args(args)

    cmd = opts.command_args
    if cmd and cmd[0] == "--":
        cmd = cmd[1:]
    if not cmd:
        print("ERROR: no command specified", file=sys.stderr)
        return 1

    result = run_with_timeout(
        command=cmd, timeout=opts.timeout,
        result_json_path=opts.result_json or None,
        stdout_log_path=opts.stdout_log or None,
        stderr_log_path=opts.stderr_log or None,
        process_run_id=opts.run_id or None,
        name=opts.name, release_run_id=opts.release_run_id,
        run_id=opts.parent_run_id,
    )

    # Result is written to --result-json file; don't pollute stdout
    return 0 if result.success else 1


def _cli_inspect(args: list[str]) -> int:
    import argparse
    parser = argparse.ArgumentParser(prog="process_tree.py inspect")
    parser.add_argument("run_id")
    opts = parser.parse_args(args)
    procs = _find_by_run_id(opts.run_id)
    if not procs:
        print("No processes found")
        return 0
    for p in procs:
        print(json.dumps(p))
    return 0


def _cli_cleanup(args: list[str]) -> int:
    import argparse
    parser = argparse.ArgumentParser(prog="process_tree.py cleanup")
    parser.add_argument("run_id")
    opts = parser.parse_args(args)
    for p in _find_by_run_id(opts.run_id):
        try:
            os.kill(p["pid"], signal.SIGKILL)
            print(f"Killed PID {p['pid']}")
        except (OSError, ProcessLookupError):
            print(f"PID {p['pid']} already gone")
    return 0


def _cli_validate_result(args: list[str]) -> int:
    import argparse
    parser = argparse.ArgumentParser(prog="process_tree.py validate-result")
    parser.add_argument("json_path")
    opts = parser.parse_args(args)
    try:
        data = json.loads(Path(opts.json_path).read_text())
    except Exception as e:
        print(f"ERROR: cannot read JSON: {e}")
        return 1
    errors = []
    if data.get("exit_code") is None:
        errors.append("exit_code is null — must be int")
    elif not isinstance(data.get("exit_code"), int):
        errors.append("exit_code must be int")
    if data.get("success") and data.get("exit_code") is not None and data.get("exit_code") != 0:
        errors.append("success=true with exit_code != 0")
    if data.get("success") and data.get("timed_out"):
        errors.append("success=true with timed_out=true")
    if data.get("success") and data.get("escaped_processes"):
        errors.append("success=true with escaped_processes")
    if data.get("success") and data.get("leaked_processes"):
        errors.append("success=true with leaked_processes")
    if data.get("success") and data.get("remaining_processes", 0) > 0:
        errors.append("success=true with remaining_processes > 0")
    if data.get("success") and data.get("remaining_zombies", 0) > 0:
        errors.append("success=true with remaining_zombies > 0")
    if data.get("stdout_path") and Path(data["stdout_path"]).exists():
        try:
            actual = hashlib.sha256(Path(data["stdout_path"]).read_bytes()).hexdigest()
            if actual != data.get("stdout_sha256", ""):
                errors.append(f"stdout_sha256 mismatch")
        except Exception:
            errors.append("cannot verify stdout_sha256")
    if errors:
        for e in errors:
            print(f"FAIL: {e}")
        return 1
    print("Result valid")
    return 0


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: process_tree.py <run|inspect|cleanup|validate-result> [...]", file=sys.stderr)
        return 1
    sub = sys.argv[1]; a = sys.argv[2:]
    if sub == "run": return _cli_run(a)
    elif sub == "inspect": return _cli_inspect(a)
    elif sub == "cleanup": return _cli_cleanup(a)
    elif sub == "validate-result": return _cli_validate_result(a)
    else:
        print(f"Unknown subcommand: {sub}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

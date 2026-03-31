from __future__ import annotations

import base64
import json
import os
import pty
import selectors
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from .screen import build_screen_snapshot_from_bytes


@dataclass
class ReplayConfig:
    log_dir: str
    target_host: str
    target_user: str = ""
    target_command: str = ""  # empty => shell
    transport: str = "ssh"
    target_port: int = 0
    gateway_host: str = ""
    gateway_user: str = ""
    gateway_port: int = 0

    checkpoint_quiet_ms: int = 250
    checkpoint_timeout_ms: int = 5000
    max_screen_bytes: int = 65535
    input_mode: str = "raw"
    on_deterministic_mismatch: str = "fail-fast"


class ReplayError(Exception):
    pass


class _TargetSession:
    def __init__(self, cfg: ReplayConfig, session_id: str, *, target_user_override: str | None = None):
        self.cfg = cfg
        self.session_id = session_id
        self.target_user_override = target_user_override
        self.master_fd, self.slave_fd = pty.openpty()
        self.proc = subprocess.Popen(
            self._ssh_argv(),
            stdin=self.slave_fd,
            stdout=self.slave_fd,
            stderr=self.slave_fd,
            preexec_fn=os.setsid,
            close_fds=True,
        )
        os.close(self.slave_fd)
        self.screen_buf = b""
        self.last_out_ms = int(time.time() * 1000)

    def _ssh_argv(self) -> list[str]:
        if not self.cfg.target_host:
            raise ValueError("target_host required")
        transport = str(self.cfg.transport or "ssh").strip().lower()
        if transport == "telnet":
            argv = ["telnet", self.cfg.target_host]
            if int(self.cfg.target_port or 0) > 0:
                argv.append(str(int(self.cfg.target_port)))
            return argv
        if transport != "ssh":
            raise ValueError(f"unsupported transport: {transport}")
        dest = self.cfg.target_host
        user = self.target_user_override if self.target_user_override is not None else self.cfg.target_user
        if user:
            dest = f"{user}@{dest}"
        argv = ["ssh", "-tt", "-o", "BatchMode=yes"]
        gateway_host = str(self.cfg.gateway_host or "").strip()
        if gateway_host:
            gateway_dest = gateway_host
            gateway_user = str(self.cfg.gateway_user or "").strip()
            if gateway_user:
                gateway_dest = f"{gateway_user}@{gateway_dest}"
            if int(self.cfg.gateway_port or 0) > 0:
                gateway_dest = f"{gateway_dest}:{int(self.cfg.gateway_port)}"
            argv += ["-J", gateway_dest]
        if int(self.cfg.target_port or 0) > 0:
            argv += ["-p", str(int(self.cfg.target_port))]
        argv.append(dest)
        if self.cfg.target_command:
            argv += ["--", self.cfg.target_command]
        return argv

    def close(self):
        try:
            os.close(self.master_fd)
        except Exception:
            pass
        try:
            self.proc.terminate()
        except Exception:
            pass

    def write_in(self, data: bytes):
        os.write(self.master_fd, data)

    def read_out(self) -> bytes:
        data = os.read(self.master_fd, 8192)
        if data:
            self.last_out_ms = int(time.time() * 1000)
            self.screen_buf += data
            if len(self.screen_buf) > self.cfg.max_screen_bytes:
                self.screen_buf = self.screen_buf[-self.cfg.max_screen_bytes :]
        return data

    def signature_now(self) -> str:
        return build_screen_snapshot_from_bytes(self.screen_buf).screen_sig


def _decode_replay_input(ev: dict) -> bytes:
    if str(ev.get("type") or "") == "deterministic_input":
        return base64.b64decode(ev.get("key_b64") or "")
    return base64.b64decode(ev.get("data_b64") or "")


def _normalize_deterministic_mismatch_mode(value: str) -> str:
    mode = str(value or "fail-fast").strip().lower()
    return mode if mode in {"fail-fast", "skip", "send-anyway"} else "fail-fast"


def _wait_for_screen_signature(
    s: _TargetSession,
    sel: selectors.BaseSelector,
    expected_sig: str,
    *,
    checkpoint_quiet_ms: int,
    checkpoint_timeout_ms: int,
 ) -> dict:
    deadline = int(time.time() * 1000) + checkpoint_timeout_ms
    start_ms = int(time.time() * 1000)
    last_observed = ""
    while int(time.time() * 1000) < deadline:
        events = sel.select(timeout=0.05)
        for key, _ in events:
            sid2 = key.data
            try:
                _ = s.read_out() if sid2 == s.session_id else None
            except Exception:
                pass
        quiet = int(time.time() * 1000) - s.last_out_ms
        if quiet >= checkpoint_quiet_ms:
            last_observed = s.signature_now()
            if last_observed == expected_sig:
                return {
                    "matched": True,
                    "expected_sig": expected_sig,
                    "observed_sig": last_observed,
                    "waited_ms": int(time.time() * 1000) - start_ms,
                    "quiet_ms": quiet,
                }
        time.sleep(0.02)
    got = s.signature_now() or last_observed
    return {
        "matched": False,
        "expected_sig": expected_sig,
        "observed_sig": got,
        "waited_ms": int(time.time() * 1000) - start_ms,
        "quiet_ms": max(0, int(time.time() * 1000) - s.last_out_ms),
    }


def _handle_deterministic_mismatch(cfg: ReplayConfig, sid: str, match: dict) -> bool:
    mode = _normalize_deterministic_mismatch_mode(cfg.on_deterministic_mismatch)
    if match.get("matched"):
        return True
    message = (
        f"deterministic screen mismatch session={sid}: expected={match.get('expected_sig')!r} "
        f"got={match.get('observed_sig')!r} waited_ms={int(match.get('waited_ms') or 0)} "
        f"mode={mode}"
    )
    if mode == "skip":
        return False
    if mode == "send-anyway":
        return True
    raise ReplayError(message)


def _iter_jsonl_files(log_dir: str) -> list[Path]:
    return sorted(Path(log_dir).glob("audit-*.jsonl"))


def replay_strict_global(cfg: ReplayConfig) -> None:
    """
    Replays all input bytes in global order.
    For checkpoints, waits and validates signature on target session output.
    """
    sessions: dict[str, _TargetSession] = {}
    sel = selectors.DefaultSelector()

    def get_sess(sid: str) -> _TargetSession:
        if sid not in sessions:
            s = _TargetSession(cfg, sid)
            sessions[sid] = s
            sel.register(s.master_fd, selectors.EVENT_READ, data=sid)
        return sessions[sid]

    def wait_checkpoint(sid: str, expected_sig: str):
        s = get_sess(sid)
        deadline = int(time.time() * 1000) + cfg.checkpoint_timeout_ms
        while int(time.time() * 1000) < deadline:
            # drain readable output for all sessions
            events = sel.select(timeout=0.05)
            for key, _ in events:
                sid2 = key.data
                try:
                    _ = sessions[sid2].read_out()
                except Exception:
                    pass

            quiet = int(time.time() * 1000) - s.last_out_ms
            if quiet >= cfg.checkpoint_quiet_ms:
                got = s.signature_now()
                if got == expected_sig:
                    return
                # not matched yet; keep waiting a bit (maybe more output coming)
            time.sleep(0.02)

        got = s.signature_now()
        raise ReplayError(f"checkpoint mismatch session={sid}: expected={expected_sig!r} got={got!r}")

    try:
        for f in _iter_jsonl_files(cfg.log_dir):
            with open(f, "r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        ev = json.loads(line)
                    except Exception:
                        continue
                    if not isinstance(ev, dict):
                        continue
                    sid = ev.get("session_id") or ""
                    typ = ev.get("type") or ""

                    if cfg.input_mode == "deterministic":
                        if typ != "deterministic_input":
                            continue
                        expected_sig = str(ev.get("screen_sig") or "")
                        if expected_sig:
                            match = _wait_for_screen_signature(
                                get_sess(sid),
                                sel,
                                expected_sig,
                                checkpoint_quiet_ms=cfg.checkpoint_quiet_ms,
                                checkpoint_timeout_ms=cfg.checkpoint_timeout_ms,
                            )
                            if not _handle_deterministic_mismatch(cfg, sid, match):
                                continue
                        data = _decode_replay_input(ev)
                    elif typ == "bytes" and ev.get("dir") == "in":
                        data = _decode_replay_input(ev)
                    else:
                        data = b""
                    if data:
                        if not sid:
                            continue
                        s = get_sess(sid)
                        s.write_in(data)
                    elif typ == "checkpoint":
                        if not sid:
                            continue
                        expected_sig = ev.get("sig") or ""
                        if expected_sig:
                            wait_checkpoint(sid, expected_sig)
                    else:
                        # ignore out bytes and session markers for replay
                        pass

        # drain some output
        end_deadline = time.time() + 0.5
        while time.time() < end_deadline:
            events = sel.select(timeout=0.05)
            for key, _ in events:
                sid2 = key.data
                try:
                    _ = sessions[sid2].read_out()
                except Exception:
                    pass
    finally:
        try:
            sel.close()
        except Exception:
            pass
        for s in sessions.values():
            s.close()


def replay_parallel_sessions(cfg: ReplayConfig) -> None:
    """
    Replays each session independently (order total por sessão).
    Útil quando você não precisa reproduzir o interleaving global.
    """
    # Collect per-session input bytes and checkpoints in order.
    per_session_events: dict[str, list[dict]] = {}
    for f in _iter_jsonl_files(cfg.log_dir):
        with open(f, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except Exception:
                    continue
                if not isinstance(ev, dict):
                    continue
                sid = ev.get("session_id") or ""
                if not sid:
                    continue
                per_session_events.setdefault(sid, []).append(ev)

    # Replay sequentially per session (simpler + deterministic).
    for sid, events in per_session_events.items():
        s = _TargetSession(cfg, sid)
        sel = selectors.DefaultSelector()
        sel.register(s.master_fd, selectors.EVENT_READ, data=sid)
        try:
            for ev in events:
                typ = ev.get("type") or ""
                if cfg.input_mode == "deterministic":
                    if typ != "deterministic_input":
                        continue
                    expected_sig = str(ev.get("screen_sig") or "")
                    if expected_sig:
                        match = _wait_for_screen_signature(
                            s,
                            sel,
                            expected_sig,
                            checkpoint_quiet_ms=cfg.checkpoint_quiet_ms,
                            checkpoint_timeout_ms=cfg.checkpoint_timeout_ms,
                        )
                        if not _handle_deterministic_mismatch(cfg, sid, match):
                            continue
                    data = _decode_replay_input(ev)
                    if data:
                        s.write_in(data)
                elif typ == "bytes" and ev.get("dir") == "in":
                    data = _decode_replay_input(ev)
                    if data:
                        s.write_in(data)
                elif typ == "checkpoint":
                    expected_sig = ev.get("sig") or ""
                    if expected_sig:
                        # wait for quiet + match
                        deadline = int(time.time() * 1000) + cfg.checkpoint_timeout_ms
                        while int(time.time() * 1000) < deadline:
                            events2 = sel.select(timeout=0.05)
                            for _, _ in events2:
                                try:
                                    _ = s.read_out()
                                except Exception:
                                    pass
                            quiet = int(time.time() * 1000) - s.last_out_ms
                            if quiet >= cfg.checkpoint_quiet_ms:
                                got = s.signature_now()
                                if got == expected_sig:
                                    break
                            time.sleep(0.02)
                        else:
                            got = s.signature_now()
                            raise ReplayError(
                                f"checkpoint mismatch session={sid}: expected={expected_sig!r} got={got!r}"
                            )

            # drain a bit
            end_deadline = time.time() + 0.25
            while time.time() < end_deadline:
                events2 = sel.select(timeout=0.05)
                if not events2:
                    break
                for _, _ in events2:
                    try:
                        _ = s.read_out()
                    except Exception:
                        pass
        finally:
            try:
                sel.close()
            except Exception:
                pass
            s.close()

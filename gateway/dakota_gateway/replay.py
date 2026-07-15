from __future__ import annotations

import base64
import fcntl
import json
import os
import pty
import selectors
import struct
import subprocess
import time
from dataclasses import dataclass, replace
from pathlib import Path

try:
    import termios
except Exception:  # pragma: no cover
    termios = None

from .screen import TerminalScreenState
from .terminal_config import normalize_encoding, validate_terminal_geometry
from dakota_terminal.comparison import compare_signatures


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

    # Session terminal geometry
    rows: int = 25
    cols: int = 80
    term: str = "xterm"
    encoding: str = "utf-8"

    checkpoint_quiet_ms: int = 250
    checkpoint_timeout_ms: int = 5000
    max_screen_bytes: int = 65535
    input_mode: str = "raw"
    on_deterministic_mismatch: str = "fail-fast"
    comparison_mode: str = "visual"  # visual | text | semantic | hybrid

    def __post_init__(self) -> None:
        geom = validate_terminal_geometry(int(self.rows), int(self.cols))
        self.rows = geom.rows
        self.cols = geom.cols
        self.encoding = normalize_encoding(self.encoding)


class ReplayError(Exception):
    pass


@dataclass
class SessionReplayState:
    session_id: str
    config: ReplayConfig
    rows: int = 25
    cols: int = 80
    term: str = "xterm"
    encoding: str = "utf-8"
    comparison_mode: str = "visual"
    engine: object | None = None
    scanner: object | None = None
    decoder: object | None = None
    warnings: list = None
    checkpoints: list = None
    current_seq_global: int = 0
    last_out_seq_global: int = 0
    last_snapshot: dict | None = None
    versions: dict = None

    def __post_init__(self) -> None:
        self.rows = int(self.rows or self.config.rows)
        self.cols = int(self.cols or self.config.cols)
        self.term = self.term or self.config.term
        self.encoding = normalize_encoding(self.encoding or self.config.encoding)
        self.comparison_mode = _normalize_comparison_mode(self.comparison_mode or self.config.comparison_mode)
        self.warnings = [] if self.warnings is None else self.warnings
        self.checkpoints = [] if self.checkpoints is None else self.checkpoints
        self.versions = {} if self.versions is None else self.versions


def _session_config_from_event(cfg: ReplayConfig, ev: dict) -> ReplayConfig:
    session_cfg = replace(cfg)
    if ev.get("rows") is not None or ev.get("cols") is not None:
        geom = validate_terminal_geometry(int(ev.get("rows", session_cfg.rows)), int(ev.get("cols", session_cfg.cols)))
        session_cfg.rows = geom.rows
        session_cfg.cols = geom.cols
    if ev.get("term"):
        session_cfg.term = str(ev["term"])
    if ev.get("encoding"):
        session_cfg.encoding = normalize_encoding(str(ev["encoding"]))
    if ev.get("comparison_mode"):
        session_cfg.comparison_mode = _normalize_comparison_mode(str(ev["comparison_mode"]))
    return session_cfg


class _TargetSession:
    def __init__(self, cfg: ReplayConfig, session_id: str, *, target_user_override: str | None = None):
        self.cfg = cfg
        self.session_id = session_id
        self.target_user_override = target_user_override
        self.master_fd, self.slave_fd = pty.openpty()
        self._configure_pty(rows=cfg.rows, cols=cfg.cols)
        self.proc = subprocess.Popen(
            self._ssh_argv(),
            stdin=self.slave_fd,
            stdout=self.slave_fd,
            stderr=self.slave_fd,
            preexec_fn=os.setsid,
            close_fds=True,
            env=dict(os.environ, TERM=cfg.term),
        )
        os.close(self.slave_fd)
        self.screen_state = TerminalScreenState(rows=cfg.rows, cols=cfg.cols, encoding=cfg.encoding, session_id=session_id)
        self.last_out_ms = int(time.time() * 1000)

    @staticmethod
    def _configure_pty(slave_fd: int, *, rows: int = 25, cols: int = 80) -> None:
        """Apply TIOCSWINSZ to set terminal window size on the PTY."""
        if termios is None:
            return
        try:
            winsize = struct.pack("HHHH", rows, cols, 0, 0)
            fcntl.ioctl(slave_fd, termios.TIOCSWINSZ, winsize)
        except Exception:
            pass

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
            self.screen_state.feed_bytes(data)
        return data

    def legacy_screen_signature_now(self) -> str:
        """Retorna screen_sig legado para adaptadores externos antigos."""
        return self.screen_state.snapshot().screen_sig

    def canonical_snapshot_now(self) -> dict:
        """Retorna assinaturas canonicas do estado atual."""
        snap = self.screen_state.snapshot()
        return {
            "text_sig": snap.text_sig or "",
            "visual_sig": snap.visual_sig or "",
            "semantic_sig": snap.semantic_sig or snap.screen_sig or "",
            "screen_sig": snap.screen_sig or "",
        }


def _normalize_comparison_mode(value: str) -> str:
    mode = str(value or "visual").strip().lower()
    return mode if mode in {"visual", "text", "semantic", "hybrid"} else "visual"


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
    expected_event: dict,
    *,
    checkpoint_quiet_ms: int,
    checkpoint_timeout_ms: int,
    comparison_mode: str = "visual",
) -> dict:
    """Espera estabilizacao da tela e compara assinaturas canonicas.

    Usa compare_signatures com o modo configurado (visual/text/semantic/hybrid).
    Retorna resultado estruturado com expected_sig, observed_sig, matched, etc.
    """
    deadline = int(time.time() * 1000) + checkpoint_timeout_ms
    start_ms = int(time.time() * 1000)
    mode = _normalize_comparison_mode(comparison_mode)

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
            if hasattr(s, "canonical_snapshot_now"):
                observed = s.canonical_snapshot_now()
            else:
                observed = {"text_sig": "", "visual_sig": "", "semantic_sig": "", "screen_sig": ""}

            # Constrói snapshots mínimos para comparação
            expected_snap = {
                "text_sig": expected_event.get("expected_text_sig") or expected_event.get("text_sig") or "",
                "visual_sig": expected_event.get("expected_visual_sig") or expected_event.get("visual_sig") or "",
                "semantic_sig": expected_event.get("expected_semantic_sig") or expected_event.get("semantic_sig") or "",
            }
            observed_snap = {
                "text_sig": observed["text_sig"],
                "visual_sig": observed["visual_sig"],
                "semantic_sig": observed["semantic_sig"],
            }

            result = compare_signatures(
                expected_snap, observed_snap, mode=mode,
                legacy_expected_screen_sig=expected_event.get("screen_sig") or expected_event.get("sig") or "",
                legacy_observed_screen_sig=observed.get("screen_sig", ""),
            )
            result["waited_ms"] = int(time.time() * 1000) - start_ms
            result["quiet_ms"] = quiet
            return result
        time.sleep(0.02)

    # Timeout: compara com o estado atual
    if hasattr(s, "canonical_snapshot_now"):
        observed = s.canonical_snapshot_now()
    else:
        observed = {"text_sig": "", "visual_sig": "", "semantic_sig": "", "screen_sig": ""}
    expected_snap = {
        "text_sig": expected_event.get("expected_text_sig") or expected_event.get("text_sig") or "",
        "visual_sig": expected_event.get("expected_visual_sig") or expected_event.get("visual_sig") or "",
        "semantic_sig": expected_event.get("expected_semantic_sig") or expected_event.get("semantic_sig") or "",
    }
    observed_snap = {
        "text_sig": observed["text_sig"],
        "visual_sig": observed["visual_sig"],
        "semantic_sig": observed["semantic_sig"],
    }
    result = compare_signatures(
        expected_snap, observed_snap, mode=mode,
        legacy_expected_screen_sig=expected_event.get("screen_sig") or expected_event.get("sig") or "",
        legacy_observed_screen_sig=observed.get("screen_sig", ""),
    )
    result["waited_ms"] = int(time.time() * 1000) - start_ms
    result["quiet_ms"] = max(0, int(time.time() * 1000) - s.last_out_ms)
    return result


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

    session_configs: dict[str, ReplayConfig] = {}

    def get_sess(sid: str, ev: dict | None = None) -> _TargetSession:
        if sid not in sessions:
            session_cfg = session_configs.get(sid)
            if session_cfg is None:
                session_cfg = _session_config_from_event(cfg, ev or {"session_id": sid})
                session_configs[sid] = session_cfg
            session_configs[sid] = session_cfg
            s = _TargetSession(session_cfg, sid)
            sessions[sid] = s
            sel.register(s.master_fd, selectors.EVENT_READ, data=sid)
        return sessions[sid]

    def wait_checkpoint(sid: str, expected_sig: str):
        s = get_sess(sid)
        deadline = int(time.time() * 1000) + cfg.checkpoint_timeout_ms
        last_match = None
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
                observed = s.canonical_snapshot_now()
                last_match = compare_signatures(
                    {},
                    observed,
                    mode="hybrid",
                    legacy_expected_screen_sig=expected_sig,
                    legacy_observed_screen_sig=observed.get("screen_sig", ""),
                )
                if last_match.get("matched"):
                    return
                # not matched yet; keep waiting a bit (maybe more output coming)
            time.sleep(0.02)

        observed = s.canonical_snapshot_now()
        got = (last_match or {}).get("observed_sig") or observed.get("screen_sig") or ""
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

                    if typ == "session_start" and sid:
                        session_configs[sid] = _session_config_from_event(cfg, ev)
                        continue

                    if cfg.input_mode == "deterministic":
                        if typ != "deterministic_input":
                            continue
                        expected_sig = str(ev.get("screen_sig") or "")
                        has_canonical = bool(
                            ev.get("expected_text_sig") or ev.get("expected_visual_sig")
                        )
                        if expected_sig or has_canonical:
                            match = _wait_for_screen_signature(
                                get_sess(sid, ev),
                                sel,
                                ev,
                                checkpoint_quiet_ms=cfg.checkpoint_quiet_ms,
                                checkpoint_timeout_ms=cfg.checkpoint_timeout_ms,
                                comparison_mode=cfg.comparison_mode,
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
                        s = get_sess(sid, ev)
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
        start_event = next((ev for ev in events if ev.get("type") == "session_start"), {"session_id": sid})
        session_cfg = _session_config_from_event(cfg, start_event)
        s = _TargetSession(session_cfg, sid)
        sel = selectors.DefaultSelector()
        sel.register(s.master_fd, selectors.EVENT_READ, data=sid)
        try:
            for ev in events:
                typ = ev.get("type") or ""
                if cfg.input_mode == "deterministic":
                    if typ != "deterministic_input":
                        continue
                    expected_sig = str(ev.get("screen_sig") or "")
                    has_canonical = bool(
                        ev.get("expected_text_sig") or ev.get("expected_visual_sig")
                    )
                    if expected_sig or has_canonical:
                        match = _wait_for_screen_signature(
                            s,
                            sel,
                            ev,
                            checkpoint_quiet_ms=cfg.checkpoint_quiet_ms,
                            checkpoint_timeout_ms=cfg.checkpoint_timeout_ms,
                            comparison_mode=cfg.comparison_mode,
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
                        # wait for quiet + match through canonical comparison
                        deadline = int(time.time() * 1000) + cfg.checkpoint_timeout_ms
                        last_match = None
                        while int(time.time() * 1000) < deadline:
                            events2 = sel.select(timeout=0.05)
                            for _, _ in events2:
                                try:
                                    _ = s.read_out()
                                except Exception:
                                    pass
                            quiet = int(time.time() * 1000) - s.last_out_ms
                            if quiet >= cfg.checkpoint_quiet_ms:
                                observed = s.canonical_snapshot_now()
                                last_match = compare_signatures(
                                    {},
                                    observed,
                                    mode="hybrid",
                                    legacy_expected_screen_sig=expected_sig,
                                    legacy_observed_screen_sig=observed.get("screen_sig", ""),
                                )
                                if last_match.get("matched"):
                                    break
                            time.sleep(0.02)
                        else:
                            observed = s.canonical_snapshot_now()
                            got = (last_match or {}).get("observed_sig") or observed.get("screen_sig") or ""
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

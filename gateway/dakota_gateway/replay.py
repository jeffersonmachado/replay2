from __future__ import annotations

import base64
import fcntl
import json
import os
import pty
import re
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
from .audit_writer import AuditWriter, b64, write_manifest
from .schema import AuditEvent
from .replay_compare import (
    apply_volatile_mask_fallback,
    event_requires_comparison,
    expected_snapshot_from_event,
    normalize_deterministic_mismatch_mode,
    wait_for_signature_match,
)
from dakota_terminal.comparison import compare_signatures, normalize_comparison_mode, resolve_comparison_mode


def _write_all(fd: int, data: bytes) -> None:
    """Escreve o buffer inteiro no descritor (os.write pode gravar parcial)."""
    view = memoryview(data)
    while view:
        written = os.write(fd, view)
        view = view[written:]


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
    # Quando preenchido (param `term` da run), vence o `term` do session_start
    # da captura — o TERM gravado é o do terminal do usuário (ex.: dk100 do
    # TeraTerm) e termos com porta auxiliar (ESC[5i) travam o replay headless.
    term_override: str = ""

    checkpoint_quiet_ms: int = 250
    checkpoint_timeout_ms: int = 5000
    max_screen_bytes: int = 65535
    input_mode: str = "raw"
    on_deterministic_mismatch: str = "fail-fast"
    comparison_mode: str = "visual"  # visual | text | semantic | hybrid

    # Gravação da sessão observada (v0.8.66): quando `observed_dir` está
    # preenchido, a saída real do destino é gravada como trilha auditável
    # assinada (hash-chain + HMAC), no mesmo formato das capturas, em
    # <observed_dir>/<session_id>/.
    observed_dir: str = ""
    observed_hmac_key: bytes = b""

    def __post_init__(self) -> None:
        geom = validate_terminal_geometry(int(self.rows), int(self.cols))
        self.rows = geom.rows
        self.cols = geom.cols
        self.encoding = normalize_encoding(self.encoding)


class ReplayError(Exception):
    pass


def _sanitize_session_dir_name(session_id: str) -> str:
    """Nome de diretório seguro para o session_id (sem '/' nem '..')."""
    return re.sub(r"[^A-Za-z0-9._-]", "_", str(session_id or "")).strip(".") or "session"


class ObservedTrailRecorder:
    """Gravador da sessão observada da run como trilha auditável assinada.

    Mesmo formato das capturas do gateway (audit-*.jsonl com hash-chain +
    HMAC via AuditWriter), um diretório por session_id. Falhas de gravação
    NUNCA derrubam a run: qualquer erro desativa o recorder e o replay
    segue sem a trilha observada.
    """

    def __init__(
        self,
        observed_dir: str,
        session_id: str,
        hmac_key: bytes,
        *,
        actor: str,
        rows: int,
        cols: int,
        term: str,
        encoding: str,
    ):
        self.session_id = str(session_id or "")
        self.actor = str(actor or "")
        self.rows = int(rows)
        self.cols = int(cols)
        self.term = str(term or "")
        self.encoding = str(encoding or "")
        self._seq_session = 0
        self._disabled = False
        self.last_out_seq = 0
        session_dir = Path(observed_dir) / _sanitize_session_dir_name(self.session_id)
        self.session_dir = session_dir
        self._writer = AuditWriter(str(session_dir), hmac_key)

    def _append(self, ev_type: str, **fields) -> AuditEvent | None:
        if self._disabled:
            return None
        try:
            self._seq_session += 1
            ev = AuditEvent(
                v="",
                seq_global=0,
                ts_ms=int(time.time() * 1000),
                type=ev_type,
                actor=self.actor,
                session_id=self.session_id,
                seq_session=self._seq_session,
                **fields,
            )
            # O writer atribui v/seq_global/prev_hash/hash/hmac.
            return self._writer.append(ev)
        except Exception:
            self._disabled = True
            return None

    def start(self) -> None:
        self._append(
            "session_start",
            rows=self.rows,
            cols=self.cols,
            term=self.term,
            encoding=self.encoding,
            geometry_source="replay_config",
            entry_mode="replay",
        )

    def record_out(self, data: bytes) -> None:
        ev = self._append(
            "bytes",
            dir="out",
            data_b64=b64(data),
            n=len(data),
            rows=self.rows,
            cols=self.cols,
            term=self.term,
            encoding=self.encoding,
        )
        if ev is not None:
            self.last_out_seq = int(ev.seq_global or 0)

    def end(self) -> None:
        try:
            self._append("session_end")
            for path in sorted(self.session_dir.glob("audit-*.jsonl")):
                write_manifest(str(path))
        except Exception:
            pass
        try:
            self._writer.close()
        except Exception:
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
    if ev.get("term") and not session_cfg.term_override:
        session_cfg.term = str(ev["term"])
    if session_cfg.term_override:
        session_cfg.term = session_cfg.term_override
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
        self._configure_pty(self.slave_fd, rows=cfg.rows, cols=cfg.cols)
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
        # Gravação da sessão observada (v0.8.66): trilha auditável da saída
        # real do destino. Nunca derruba a run — falha vira recorder None.
        self.observed_recorder: ObservedTrailRecorder | None = None
        self.observed_seq = 0
        if str(cfg.observed_dir or "").strip():
            try:
                self.observed_recorder = ObservedTrailRecorder(
                    cfg.observed_dir,
                    session_id,
                    cfg.observed_hmac_key,
                    actor=target_user_override if target_user_override is not None else cfg.target_user,
                    rows=cfg.rows,
                    cols=cfg.cols,
                    term=cfg.term,
                    encoding=cfg.encoding,
                )
                self.observed_recorder.start()
            except Exception:
                self.observed_recorder = None

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
            argv.append(self.cfg.target_command)
        return argv

    def close(self):
        if self.observed_recorder is not None:
            try:
                self.observed_recorder.end()
            except Exception:
                pass
            self.observed_recorder = None
        try:
            os.close(self.master_fd)
        except Exception:
            pass
        try:
            self.proc.terminate()
        except Exception:
            pass

    def write_in(self, data: bytes):
        try:
            _write_all(self.master_fd, data)
        except OSError:
            # Sessão remota já terminou (ex.: trilha com `exit` no fim) —
            # inputs tardios são descartados em vez de derrubar a run.
            pass

    def read_out(self) -> bytes:
        try:
            data = os.read(self.master_fd, 8192)
        except OSError:
            # EIO: o lado slave do PTY fechou (a sessão remota terminou —
            # ex.: trilha que encerra com `exit`). Tratar como EOF: sem isso
            # a run morria com "OSError: [Errno 5] I/O error" nos eventos
            # finais da trilha.
            return b""
        if data:
            self.last_out_ms = int(time.time() * 1000)
            self.screen_state.feed_bytes(data)
            if self.observed_recorder is not None:
                self.observed_recorder.record_out(data)
                self.observed_seq = self.observed_recorder.last_out_seq
        return data

    def canonical_snapshot_now(self) -> dict:
        """Retorna assinaturas canonicas do estado atual."""
        snap = self.screen_state.snapshot()
        return {
            "text_sig": snap.text_sig or "",
            "visual_sig": snap.visual_sig or "",
            "semantic_sig": snap.semantic_sig or snap.screen_sig or "",
            "screen_sig": snap.screen_sig or "",
            # Texto da tela para a segunda chance com máscara de voláteis
            # (ruído ambiental, ex.: "Kb livres" da linha de status).
            "screen_text": str(self.screen_state.text() or ""),
        }


def _normalize_comparison_mode(value: str) -> str:
    return normalize_comparison_mode(value)


# Helpers compartilhados com replay_control (dakota_gateway.replay_compare).
_expected_snapshot_from_event = expected_snapshot_from_event
_event_requires_comparison = event_requires_comparison
_normalize_deterministic_mismatch_mode = normalize_deterministic_mismatch_mode


def _decode_replay_input(ev: dict) -> bytes:
    if str(ev.get("type") or "") == "deterministic_input":
        return base64.b64decode(ev.get("key_b64") or "")
    return base64.b64decode(ev.get("data_b64") or "")


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
    start_ms = int(time.time() * 1000)
    mode = _normalize_comparison_mode(comparison_mode)
    expected_snap = _expected_snapshot_from_event(expected_event)

    def compare(observed: dict) -> dict:
        # Constrói snapshots mínimos para comparação
        observed_snap = {
            "text_sig": observed.get("text_sig", ""),
            "visual_sig": observed.get("visual_sig", ""),
            "semantic_sig": observed.get("semantic_sig", ""),
            "screen_text": observed.get("screen_text", ""),
        }
        match = compare_signatures(
            expected_snap, observed_snap, mode=mode,
            legacy_expected_screen_sig=expected_event.get("screen_sig") or expected_event.get("sig") or "",
            legacy_observed_screen_sig=observed.get("screen_sig", ""),
        )
        return apply_volatile_mask_fallback(
            match,
            expected_event=expected_event,
            observed_snapshot=observed_snap,
            session_config=getattr(s, "cfg", None),
        )

    _, match, _ = wait_for_signature_match(
        s,
        sel,
        compare=compare,
        checkpoint_quiet_ms=checkpoint_quiet_ms,
        checkpoint_timeout_ms=checkpoint_timeout_ms,
        drain_event=lambda key: s.read_out() if key.data == s.session_id else None,
        return_first_result=True,
    )
    match["waited_ms"] = int(time.time() * 1000) - start_ms
    match["quiet_ms"] = max(0, int(time.time() * 1000) - s.last_out_ms)
    return match


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

    NOTA DE INTEGRIDADE: esta função não verifica a trilha; o chamador deve
    rodar verifier.verify_log(log_dir, hmac_key) antes (o CLI `replay` e o
    Runner do replay_control já o fazem).
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

    def wait_checkpoint(sid: str, checkpoint_event: dict):
        s = get_sess(sid)
        session_cfg = session_configs.get(sid)
        mode = resolve_comparison_mode(event=checkpoint_event, session=session_cfg, replay=cfg)["comparison_mode"]
        expected_snapshot = _expected_snapshot_from_event(checkpoint_event)

        def compare(observed: dict) -> dict:
            match = compare_signatures(
                expected_snapshot,
                observed,
                mode=mode,
                legacy_expected_screen_sig=expected_snapshot.get("screen_sig", ""),
                legacy_observed_screen_sig=observed.get("screen_sig", ""),
            )
            return apply_volatile_mask_fallback(
                match,
                expected_event=checkpoint_event,
                observed_snapshot=observed,
                session_config=session_cfg,
            )

        matched, last_match, observed = wait_for_signature_match(
            s,
            sel,
            compare=compare,
            checkpoint_quiet_ms=cfg.checkpoint_quiet_ms,
            checkpoint_timeout_ms=cfg.checkpoint_timeout_ms,
            drain_event=lambda key: sessions[key.data].read_out(),
        )
        if matched:
            return
        got = (last_match or {}).get("observed_sig") or observed.get("screen_sig") or ""
        expected_sig = (last_match or {}).get("expected_sig") or expected_snapshot.get("screen_sig") or ""
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
                        session_cfg = session_configs.get(sid) or _session_config_from_event(cfg, ev)
                        mode = resolve_comparison_mode(event=ev, session=session_cfg, replay=cfg)["comparison_mode"]
                        if _event_requires_comparison(ev, mode=mode):
                            match = _wait_for_screen_signature(
                                get_sess(sid, ev),
                                sel,
                                ev,
                                checkpoint_quiet_ms=cfg.checkpoint_quiet_ms,
                                checkpoint_timeout_ms=cfg.checkpoint_timeout_ms,
                                comparison_mode=mode,
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
                        session_cfg = session_configs.get(sid) or _session_config_from_event(cfg, ev)
                        mode = resolve_comparison_mode(event=ev, session=session_cfg, replay=cfg)["comparison_mode"]
                        if _event_requires_comparison(ev, mode=mode):
                            wait_checkpoint(sid, ev)
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

    NOTA DE INTEGRIDADE: esta função não verifica a trilha; o chamador deve
    rodar verifier.verify_log(log_dir, hmac_key) antes (o CLI `replay` e o
    Runner do replay_control já o fazem).
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
                    mode = resolve_comparison_mode(event=ev, session=session_cfg, replay=cfg)["comparison_mode"]
                    if _event_requires_comparison(ev, mode=mode):
                        match = _wait_for_screen_signature(
                            s,
                            sel,
                            ev,
                            checkpoint_quiet_ms=cfg.checkpoint_quiet_ms,
                            checkpoint_timeout_ms=cfg.checkpoint_timeout_ms,
                            comparison_mode=mode,
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
                    mode = resolve_comparison_mode(event=ev, session=session_cfg, replay=cfg)["comparison_mode"]
                    if _event_requires_comparison(ev, mode=mode):
                        # wait for quiet + match through canonical comparison
                        expected_snapshot = _expected_snapshot_from_event(ev)

                        def compare(observed: dict, _snap=expected_snapshot, _mode=mode, _ev=ev, _scfg=session_cfg) -> dict:
                            match = compare_signatures(
                                _snap,
                                observed,
                                mode=_mode,
                                legacy_expected_screen_sig=_snap.get("screen_sig", ""),
                                legacy_observed_screen_sig=observed.get("screen_sig", ""),
                            )
                            return apply_volatile_mask_fallback(
                                match,
                                expected_event=_ev,
                                observed_snapshot=observed,
                                session_config=_scfg,
                            )

                        matched, last_match, observed = wait_for_signature_match(
                            s,
                            sel,
                            compare=compare,
                            checkpoint_quiet_ms=cfg.checkpoint_quiet_ms,
                            checkpoint_timeout_ms=cfg.checkpoint_timeout_ms,
                        )
                        if not matched:
                            got = (last_match or {}).get("observed_sig") or observed.get("screen_sig") or ""
                            expected_sig = (last_match or {}).get("expected_sig") or expected_snapshot.get("screen_sig") or ""
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

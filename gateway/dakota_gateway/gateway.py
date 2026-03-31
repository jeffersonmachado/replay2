from __future__ import annotations

import os
import pty
import selectors
import subprocess
import time
import tty
import uuid
from dataclasses import dataclass

try:
    import termios
except Exception:  # pragma: no cover
    termios = None

from .audit_writer import AuditWriter, b64
from .schema import AuditEvent
from .screen import (
    ScreenSnapshot,
    build_screen_snapshot_from_bytes,
    split_input_for_deterministic_record,
)


_CLEAR_PATTERNS = [
    b"\x1b[2J\x1b[H",
    b"\x1b[H\x1b[2J",
]


def _apply_screen_boundaries(buf: bytes) -> bytes:
    last_end = -1
    for pat in _CLEAR_PATTERNS:
        start = 0
        while True:
            idx = buf.find(pat, start)
            if idx < 0:
                break
            last_end = max(last_end, idx + len(pat) - 1)
            start = idx + 1
    if last_end >= 0:
        return buf[last_end + 1 :]
    return buf


@dataclass
class GatewayConfig:
    log_dir: str
    hmac_key: bytes
    rotate_bytes: int = 0

    source_host: str = ""
    source_user: str = ""
    source_command: str = ""  # if empty, open login shell
    gateway_endpoint: str = ""
    ssh_batch_mode: str = "no"
    capture_id: int = 0
    capture_session_uuid: str = ""

    checkpoint_quiet_ms: int = 250
    checkpoint_min_bytes: int = 512
    max_screen_bytes: int = 65535


@dataclass
class _StableScreenState:
    snapshot: ScreenSnapshot | None = None
    ts_ms: int = 0
    source: str = "fallback"


class TerminalGateway:
    def __init__(self, cfg: GatewayConfig):
        self.cfg = cfg
        self.writer = AuditWriter(cfg.log_dir, cfg.hmac_key, rotate_bytes=cfg.rotate_bytes)

        self.session_id = str(uuid.uuid4())
        self.actor = os.environ.get("SUDO_USER") or os.environ.get("LOGNAME") or os.environ.get("USER") or "unknown"
        self.seq_session = 0

    def _ts_ms(self) -> int:
        return int(time.time() * 1000)

    def _append(self, ev: AuditEvent) -> None:
        self.writer.append(ev)

    def _setup_stdin_raw(self):
        if termios is None:
            return None
        try:
            if not os.isatty(0):
                return None
        except Exception:
            return None
        try:
            saved = termios.tcgetattr(0)
            tty.setraw(0)
            return saved
        except Exception:
            return None

    def _restore_stdin_raw(self, saved_state) -> None:
        if termios is None or saved_state is None:
            return
        try:
            termios.tcsetattr(0, termios.TCSADRAIN, saved_state)
        except Exception:
            pass

    def _next_seq_session(self) -> int:
        self.seq_session += 1
        return self.seq_session

    def _capture_refs(self) -> dict:
        return {
            "capture_id": int(self.cfg.capture_id or 0) or None,
            "capture_session_uuid": str(self.cfg.capture_session_uuid or "").strip() or None,
        }

    def _screen_snapshot_from_buf(self, screen_buf: bytes) -> ScreenSnapshot:
        return build_screen_snapshot_from_bytes(screen_buf)

    def _select_snapshot_for_input(
        self,
        *,
        screen_buf: bytes,
        stable_state: _StableScreenState,
        now_ms: int,
    ) -> tuple[ScreenSnapshot, str, int | None]:
        if stable_state.snapshot is not None and stable_state.ts_ms > 0:
            return stable_state.snapshot, "stable", max(0, now_ms - stable_state.ts_ms)
        if screen_buf:
            return self._screen_snapshot_from_buf(screen_buf), "buffer", None
        return self._screen_snapshot_from_buf(b""), "empty", None

    def _build_deterministic_events_for_input(
        self,
        *,
        data: bytes,
        screen_buf: bytes,
        stable_state: _StableScreenState,
        now_ms: int,
    ) -> list[AuditEvent]:
        snapshot, screen_source, snapshot_age_ms = self._select_snapshot_for_input(
            screen_buf=screen_buf,
            stable_state=stable_state,
            now_ms=now_ms,
        )
        refs = self._capture_refs()
        events: list[AuditEvent] = []
        actions = split_input_for_deterministic_record(data)
        for action in actions:
            events.append(
                AuditEvent(
                    v="v1",
                    seq_global=0,
                    ts_ms=now_ms,
                    type="deterministic_input",
                    actor=self.actor,
                    session_id=self.session_id,
                    seq_session=self._next_seq_session(),
                    screen_sig=snapshot.screen_sig,
                    screen_sample=snapshot.screen_sample,
                    norm_sha256=snapshot.norm_sha256,
                    norm_len=snapshot.norm_len,
                    key_b64=b64(action.raw_bytes),
                    key_text=action.key_text,
                    key_kind=action.key_kind,
                    input_len=action.input_len,
                    contains_newline=action.contains_newline,
                    contains_escape=action.contains_escape,
                    is_probable_paste=action.is_probable_paste,
                    is_probable_command=action.is_probable_command,
                    logical_parts=action.logical_parts,
                    screen_raw_b64=b64(snapshot.raw_bytes) if (snapshot.raw_bytes and screen_source != "empty") else None,
                    screen_source=screen_source,
                    screen_snapshot_ts_ms=stable_state.ts_ms or None,
                    screen_snapshot_age_ms=snapshot_age_ms,
                    source="gateway_record",
                    **refs,
                )
            )
        return events

    def _build_audit_events_for_input(
        self,
        *,
        data: bytes,
        screen_buf: bytes,
        stable_state: _StableScreenState,
        now_ms: int,
    ) -> list[AuditEvent]:
        events = self._build_deterministic_events_for_input(
            data=data,
            screen_buf=screen_buf,
            stable_state=stable_state,
            now_ms=now_ms,
        )
        events.append(
            AuditEvent(
                v="v1",
                seq_global=0,
                ts_ms=now_ms,
                type="bytes",
                actor=self.actor,
                session_id=self.session_id,
                seq_session=self._next_seq_session(),
                **self._capture_refs(),
                dir="in",
                data_b64=b64(data),
                n=len(data),
            )
        )
        return events

    def _ssh_argv(self) -> list[str]:
        # Use ssh client; force tty with -tt.
        if not self.cfg.source_host:
            raise ValueError("source_host is required")
        dest = self.cfg.source_host
        if self.cfg.source_user:
            dest = f"{self.cfg.source_user}@{dest}"

        batch_mode = str(self.cfg.ssh_batch_mode or "no").strip().lower()
        if batch_mode not in {"yes", "no"}:
            batch_mode = "no"
        argv = ["ssh", "-tt", "-o", f"BatchMode={batch_mode}", dest]
        if self.cfg.source_command:
            argv += ["--", self.cfg.source_command]
        return argv

    def _session_argv(self) -> list[str]:
        # Modo padrão: sessão via SSH para host de origem.
        if self.cfg.source_host:
            return self._ssh_argv()

        # Modo local (ex.: sshd ForceCommand): executa comando remoto original
        # ou shell de login e captura todo o stdin/stdout da sessão.
        original_cmd = str(os.environ.get("SSH_ORIGINAL_COMMAND") or "").strip()
        command = str(self.cfg.source_command or "").strip() or original_cmd
        if command:
            return ["/bin/sh", "-lc", command]

        shell = str(os.environ.get("SHELL") or "/bin/sh").strip() or "/bin/sh"
        return [shell, "-l"]

    def run(self) -> int:
        gateway_endpoint = (
            self.cfg.gateway_endpoint
            or os.environ.get("DAKOTA_GATEWAY_ENDPOINT")
            or os.environ.get("HOSTNAME")
            or ""
        )
        master_fd, slave_fd = pty.openpty()
        batch_mode = str(self.cfg.ssh_batch_mode or "no").strip().lower()
        use_setsid = batch_mode == "yes"
        try:
            proc = subprocess.Popen(
                self._session_argv(),
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                preexec_fn=os.setsid if use_setsid else None,
                close_fds=True,
            )
        finally:
            os.close(slave_fd)

        sel = selectors.DefaultSelector()
        sel.register(master_fd, selectors.EVENT_READ, data="pty")
        saved_tty_state = self._setup_stdin_raw()
        # Em execução daemonizada (sem TTY), registrar stdin pode falhar.
        # Nesse caso seguimos apenas com captura de saída do PTY.
        try:
            sel.register(0, selectors.EVENT_READ, data="stdin")  # user input
        except Exception:
            pass

        # Captura inicia assim que a conexão de gateway é estabelecida.
        self._append(
            AuditEvent(
                v="v1",
                seq_global=0,
                ts_ms=self._ts_ms(),
                type="session_start",
                actor=self.actor,
                session_id=self.session_id,
                seq_session=self._next_seq_session(),
                **self._capture_refs(),
                entry_mode="gateway_ssh",
                via_gateway=True,
                gateway_session_id=self.session_id,
                gateway_endpoint=gateway_endpoint,
                source_host=self.cfg.source_host,
                source_user=self.cfg.source_user,
                source_command=self.cfg.source_command,
            )
        )

        screen_buf = b""
        last_out_ms = self._ts_ms()
        last_checkpoint_ms = 0
        stable_state = _StableScreenState()
        screen_dirty = False

        def maybe_checkpoint(force: bool = False):
            nonlocal screen_buf, last_checkpoint_ms, stable_state, screen_dirty
            now = self._ts_ms()
            quiet = now - last_out_ms
            if not force:
                if quiet < self.cfg.checkpoint_quiet_ms:
                    return
            if screen_dirty or (force and stable_state.snapshot is None and screen_buf):
                stable_state = _StableScreenState(
                    snapshot=self._screen_snapshot_from_buf(screen_buf),
                    ts_ms=now,
                    source="stable",
                )
                screen_dirty = False
            if len(screen_buf) < self.cfg.checkpoint_min_bytes:
                return
            if not force and now - last_checkpoint_ms < self.cfg.checkpoint_quiet_ms:
                return

            snapshot = stable_state.snapshot or self._screen_snapshot_from_buf(screen_buf)
            # only checkpoint when signature has TIT (like replay2 loop)
            if "TIT=" not in snapshot.screen_sig:
                return
            ev = AuditEvent(
                v="v1",
                seq_global=0,
                ts_ms=now,
                type="checkpoint",
                actor=self.actor,
                session_id=self.session_id,
                seq_session=self._next_seq_session(),
                **self._capture_refs(),
                sig=snapshot.screen_sig,
                norm_sha256=snapshot.norm_sha256,
                norm_len=snapshot.norm_len,
            )
            self._append(ev)
            last_checkpoint_ms = now

        # proxy loop
        try:
            while True:
                if proc.poll() is not None:
                    break
                events = sel.select(timeout=0.05)
                for key, _ in events:
                    if key.data == "stdin":
                        try:
                            data = os.read(0, 4096)
                        except OSError:
                            data = b""
                        if not data:
                            # client closed
                            proc.terminate()
                            break
                        event_ts_ms = self._ts_ms()
                        for ev in self._build_audit_events_for_input(
                            data=data,
                            screen_buf=screen_buf,
                            stable_state=stable_state,
                            now_ms=event_ts_ms,
                        ):
                            self._append(ev)
                        os.write(master_fd, data)
                    elif key.data == "pty":
                        try:
                            data = os.read(master_fd, 8192)
                        except OSError:
                            data = b""
                        if not data:
                            proc.terminate()
                            break
                        os.write(1, data)
                        self._append(
                            AuditEvent(
                                v="v1",
                                seq_global=0,
                                ts_ms=self._ts_ms(),
                                type="bytes",
                                actor=self.actor,
                                session_id=self.session_id,
                                seq_session=self._next_seq_session(),
                                **self._capture_refs(),
                                dir="out",
                                data_b64=b64(data),
                                n=len(data),
                            )
                        )
                        last_out_ms = self._ts_ms()
                        screen_buf += data
                        screen_buf = _apply_screen_boundaries(screen_buf)
                        if len(screen_buf) > self.cfg.max_screen_bytes:
                            screen_buf = screen_buf[-self.cfg.max_screen_bytes :]
                        screen_dirty = True

                maybe_checkpoint(force=False)

            maybe_checkpoint(force=True)
        finally:
            try:
                sel.close()
            except Exception:
                pass
            self._restore_stdin_raw(saved_tty_state)
            try:
                os.close(master_fd)
            except Exception:
                pass
            rc = proc.wait(timeout=2) if proc.poll() is None else proc.returncode

            self._append(
                AuditEvent(
                    v="v1",
                    seq_global=0,
                    ts_ms=self._ts_ms(),
                    type="session_end",
                    actor=self.actor,
                    session_id=self.session_id,
                    seq_session=self._next_seq_session(),
                    **self._capture_refs(),
                )
            )
            self.writer.close()
            return int(rc or 0)

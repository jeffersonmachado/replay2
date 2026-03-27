from __future__ import annotations

import os
import pty
import selectors
import subprocess
import time
import uuid
from dataclasses import dataclass

from .audit_writer import AuditWriter, b64
from .schema import AuditEvent
from .screen import normalize_screen, signature_from_screen, sha256_hex_text


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

    checkpoint_quiet_ms: int = 250
    checkpoint_min_bytes: int = 512
    max_screen_bytes: int = 65535


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

    def _next_seq_session(self) -> int:
        self.seq_session += 1
        return self.seq_session

    def _ssh_argv(self) -> list[str]:
        # Use ssh client; force tty with -tt.
        if not self.cfg.source_host:
            raise ValueError("source_host is required")
        dest = self.cfg.source_host
        if self.cfg.source_user:
            dest = f"{self.cfg.source_user}@{dest}"

        argv = ["ssh", "-tt", "-o", "BatchMode=yes", dest]
        if self.cfg.source_command:
            argv += ["--", self.cfg.source_command]
        return argv

    def run(self) -> int:
        # session_start
        self._append(
            AuditEvent(
                v="v1",
                seq_global=0,
                ts_ms=self._ts_ms(),
                type="session_start",
                actor=self.actor,
                session_id=self.session_id,
                seq_session=self._next_seq_session(),
            )
        )

        master_fd, slave_fd = pty.openpty()
        try:
            proc = subprocess.Popen(
                self._ssh_argv(),
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                preexec_fn=os.setsid,
                close_fds=True,
            )
        finally:
            os.close(slave_fd)

        sel = selectors.DefaultSelector()
        sel.register(master_fd, selectors.EVENT_READ, data="pty")
        sel.register(0, selectors.EVENT_READ, data="stdin")  # user input

        screen_buf = b""
        last_out_ms = self._ts_ms()
        last_checkpoint_ms = 0

        def maybe_checkpoint(force: bool = False):
            nonlocal screen_buf, last_checkpoint_ms
            now = self._ts_ms()
            quiet = now - last_out_ms
            if not force:
                if quiet < self.cfg.checkpoint_quiet_ms:
                    return
                if len(screen_buf) < self.cfg.checkpoint_min_bytes:
                    return
                if now - last_checkpoint_ms < self.cfg.checkpoint_quiet_ms:
                    return

            # best-effort decode
            try:
                raw_text = screen_buf.decode("utf-8", errors="replace")
            except Exception:
                raw_text = screen_buf.decode(errors="replace")
            norm = normalize_screen(raw_text)
            sig = signature_from_screen(norm)
            # only checkpoint when signature has TIT (like replay2 loop)
            if "TIT=" not in sig:
                return
            ev = AuditEvent(
                v="v1",
                seq_global=0,
                ts_ms=now,
                type="checkpoint",
                actor=self.actor,
                session_id=self.session_id,
                seq_session=self._next_seq_session(),
                sig=sig,
                norm_sha256=sha256_hex_text(norm),
                norm_len=len(norm),
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
                        os.write(master_fd, data)
                        self._append(
                            AuditEvent(
                                v="v1",
                                seq_global=0,
                                ts_ms=self._ts_ms(),
                                type="bytes",
                                actor=self.actor,
                                session_id=self.session_id,
                                seq_session=self._next_seq_session(),
                                dir="in",
                                data_b64=b64(data),
                                n=len(data),
                            )
                        )
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

                maybe_checkpoint(force=False)

            maybe_checkpoint(force=True)
        finally:
            try:
                sel.close()
            except Exception:
                pass
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
                )
            )
            self.writer.close()
            return int(rc or 0)


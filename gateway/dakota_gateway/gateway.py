from __future__ import annotations

import ctypes
import ctypes.util
import fcntl
import os
import pty
import selectors
import struct
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
    TerminalScreenState,
    build_screen_snapshot_from_bytes,
    split_input_for_deterministic_record,
)
from .terminal_config import normalize_encoding, validate_terminal_geometry


def _configure_pty(slave_fd: int, *, rows: int = 25, cols: int = 80) -> None:
    """Apply TIOCSWINSZ to set terminal window size on the PTY."""
    if termios is None:
        return
    try:
        winsize = struct.pack("HHHH", rows, cols, 0, 0)
        fcntl.ioctl(slave_fd, termios.TIOCSWINSZ, winsize)
    except Exception:
        pass


def _open_pty_robust() -> tuple[int, int]:
    """Abre um par PTY master/slave usando multiplas estrategias de fallback.

    Tenta em sequencia:
      1. os.openpty()          — chamada C de baixo nivel
      2. pty.openpty()         — wrapper Python padrao
      3. posix_openpt() (ctypes) — API POSIX direta (compativel AIX 7)
      4. /dev/ptmx manual      — abertura manual do clone device
      5. /dev/ptc (STREAMS)    — AIX STREAMS master clone

    Returns:
        (master_fd, slave_fd) em caso de sucesso.

    Raises:
        OSError: se nenhuma estrategia funcionar.
    """
    errors: list[str] = []

    # Estrategia 1: os.openpty() — chamada C direta
    if hasattr(os, "openpty"):
        try:
            master, slave = os.openpty()
            return master, slave
        except Exception as e:
            errors.append(f"os.openpty: {e}")

    # Estrategia 2: pty.openpty() — wrapper Python
    try:
        master, slave = pty.openpty()
        return master, slave
    except Exception as e:
        errors.append(f"pty.openpty: {e}")

    # Estrategia 3: posix_openpt() via ctypes — API POSIX direta
    libc_name = None
    try:
        libc_name = ctypes.util.find_library("c") or "libc.so"
    except Exception:
        libc_name = "libc.so"
    if libc_name:
        try:
            libc = ctypes.CDLL(libc_name)
        except Exception as e:
            errors.append(f"posix_openpt(ctypes) CDLL: {e}")
            libc = None
        if libc is not None:
            try:
                O_RDWR = 2
                O_NOCTTY = getattr(os, "O_NOCTTY", 0x800)

                libc.posix_openpt.argtypes = [ctypes.c_int]
                libc.posix_openpt.restype = ctypes.c_int
                master = libc.posix_openpt(O_RDWR | O_NOCTTY)
                if master < 0:
                    raise OSError(ctypes.get_errno(), "posix_openpt failed")

                libc.grantpt.argtypes = [ctypes.c_int]
                libc.grantpt.restype = ctypes.c_int
                if libc.grantpt(master) != 0:
                    os.close(master)
                    raise OSError(ctypes.get_errno(), "grantpt failed")

                libc.unlockpt.argtypes = [ctypes.c_int]
                libc.unlockpt.restype = ctypes.c_int
                if libc.unlockpt(master) != 0:
                    os.close(master)
                    raise OSError(ctypes.get_errno(), "unlockpt failed")

                libc.ptsname.argtypes = [ctypes.c_int]
                libc.ptsname.restype = ctypes.c_char_p
                slave_name_b = libc.ptsname(master)
                if slave_name_b is None:
                    os.close(master)
                    raise OSError(ctypes.get_errno() or 0, "ptsname failed")
                slave_name = slave_name_b.decode("utf-8", errors="replace")

                slave = os.open(slave_name, os.O_RDWR | O_NOCTTY)
                return master, slave
            except OSError:
                raise
            except Exception as e:
                errors.append(f"posix_openpt(ctypes): {e}")

    # Estrategia 4: abertura manual de /dev/ptmx
    for ptmx_path in ("/dev/ptmx", "/dev/ptm"):
        try:
            master = os.open(ptmx_path, os.O_RDWR | getattr(os, "O_NOCTTY", 0x800))
            try:
                slave_name = os.ptsname(master) if hasattr(os, "ptsname") else None
                if slave_name is None:
                    raise OSError(0, "ptsname not available")
                os.grantpt(master) if hasattr(os, "grantpt") else None
                os.unlockpt(master) if hasattr(os, "unlockpt") else None
                slave = os.open(slave_name, os.O_RDWR | getattr(os, "O_NOCTTY", 0x800))
                return master, slave
            except Exception:
                os.close(master)
                raise
        except Exception as e:
            errors.append(f"/dev/ptmx({ptmx_path}): {e}")

    # Estrategia 5: AIX STREAMS /dev/ptc (master clone)
    try:
        master = os.open("/dev/ptc", os.O_RDWR)
        # No AIX STREAMS, ptsname no master retorna o slave
        if hasattr(os, "ptsname"):
            slave_name = os.ptsname(master)
            slave = os.open(slave_name, os.O_RDWR)
            return master, slave
        os.close(master)
        raise OSError(0, "ptsname not available for /dev/ptc")
    except OSError as e:
        errors.append(f"/dev/ptc: {e}")
    except Exception as e:
        errors.append(f"/dev/ptc: {e}")

    raise OSError(f"Nenhuma estrategia PTY funcionou: {'; '.join(errors)}")


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

    # Geometry metadata
    rows: int = 25
    cols: int = 80
    term: str = "xterm"
    encoding: str = "utf-8"
    geometry_source: str = "legacy_fallback"

    checkpoint_quiet_ms: int = 250
    checkpoint_min_bytes: int = 512
    max_screen_bytes: int = 65535

    def __post_init__(self) -> None:
        geom = validate_terminal_geometry(int(self.rows), int(self.cols))
        self.rows = geom.rows
        self.cols = geom.cols
        self.encoding = normalize_encoding(self.encoding)


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
        self.logname = os.environ.get("LOGNAME") or os.environ.get("USER") or "unknown"
        
        # Extract uid and gid from current process
        self.uid = os.getuid() if hasattr(os, 'getuid') else None
        self.gid = os.getgid() if hasattr(os, 'getgid') else None
        
        self.seq_session = 0

    def _ts_ms(self) -> int:
        return int(time.time() * 1000)

    def _append(self, ev: AuditEvent) -> None:
        # Ensure uid, gid, and logname are set if not already provided
        if ev.uid is None:
            ev.uid = self.uid
        if ev.gid is None:
            ev.gid = self.gid
        if ev.logname is None:
            ev.logname = self.logname
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
        """Converte buffer bruto de tela (screen_buf) em ScreenSnapshot.

        O screen_buf e o buffer incremental de bytes capturados do terminal.
        A renderizacao visual (atributos, cores, box-drawing) e delegada ao
        terminal virtual no frontend (virtual_terminal.cjs), que processa os
        mesmos bytes e produz a matriz canonica de celulas.

        Esta funcao faz a ponte: extrai texto normalizado e assinatura (text_sig)
        do buffer cru. A assinatura visual (visual_sig) e gerada no frontend
        pelo terminal virtual.
        """
        return build_screen_snapshot_from_bytes(
            screen_buf,
            encoding=self.cfg.encoding,
            rows=self.cfg.rows,
            cols=self.cfg.cols,
        )

    def _screen_snapshot_from_state(self, screen_state: TerminalScreenState) -> ScreenSnapshot:
        return screen_state.snapshot()

    def _select_snapshot_for_input(
        self,
        *,
        screen_buf: bytes = b"",
        screen_state: TerminalScreenState | None = None,
        stable_state: _StableScreenState,
        now_ms: int,
    ) -> tuple[ScreenSnapshot, str, int | None]:
        if stable_state.snapshot is not None and stable_state.ts_ms > 0:
            return stable_state.snapshot, "stable", max(0, now_ms - stable_state.ts_ms)
        if screen_state is not None and screen_state.bytes_seen > 0:
            return self._screen_snapshot_from_state(screen_state), "terminal_state", None
        if screen_buf:
            return self._screen_snapshot_from_buf(screen_buf), "buffer", None
        return self._screen_snapshot_from_buf(b""), "empty", None

    def _build_deterministic_events_for_input(
        self,
        *,
        data: bytes,
        screen_buf: bytes = b"",
        screen_state: TerminalScreenState | None = None,
        stable_state: _StableScreenState,
        now_ms: int,
    ) -> list[AuditEvent]:
        snapshot, screen_source, snapshot_age_ms = self._select_snapshot_for_input(
            screen_buf=screen_buf,
            screen_state=screen_state,
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
                    # Assinaturas canonicas (v0.5.0+)
                    text_sig=snapshot.text_sig or None,
                    visual_sig=snapshot.visual_sig or None,
                    semantic_sig=snapshot.semantic_sig or None,
                    expected_text_sig=snapshot.text_sig or None,
                    expected_visual_sig=snapshot.visual_sig or None,
                    expected_semantic_sig=snapshot.semantic_sig or None,
                    engine_version=snapshot.canonical_snapshot.get("engine_version") if snapshot.canonical_snapshot else None,
                    **refs,
                )
            )
        return events

    def _build_audit_events_for_input(
        self,
        *,
        data: bytes,
        screen_buf: bytes = b"",
        screen_state: TerminalScreenState | None = None,
        stable_state: _StableScreenState,
        now_ms: int,
    ) -> list[AuditEvent]:
        events = self._build_deterministic_events_for_input(
            data=data,
            screen_buf=screen_buf,
            screen_state=screen_state,
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

    def _append_session_start(self, *, gateway_endpoint: str, command: str | None = None) -> None:
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
                source_command=self.cfg.source_command if command is None else command,
                rows=self.cfg.rows,
                cols=self.cfg.cols,
                term=self.cfg.term,
                encoding=self.cfg.encoding,
                geometry_source=self.cfg.geometry_source,
            )
        )

    def _append_session_end(self) -> None:
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

    def _append_out_bytes_event(
        self,
        *,
        data: bytes,
        screen_state: TerminalScreenState,
        ts_ms: int | None = None,
        seq_session: int | None = None,
    ) -> ScreenSnapshot:
        event_ts_ms = self._ts_ms() if ts_ms is None else int(ts_ms)
        out_seq_session = self._next_seq_session() if seq_session is None else int(seq_session)
        screen_state.feed_bytes(data, seq_global=out_seq_session, direction="out")
        out_snapshot = screen_state.snapshot()
        canonical = out_snapshot.canonical_snapshot or {}
        rows = int(canonical.get("rows") or screen_state.rows)
        cols = int(canonical.get("cols") or screen_state.cols)
        self._append(
            AuditEvent(
                v="v1",
                seq_global=0,
                ts_ms=event_ts_ms,
                timestamp_ms=event_ts_ms,
                type="bytes",
                actor=self.actor,
                session_id=self.session_id,
                seq_session=out_seq_session,
                **self._capture_refs(),
                dir="out",
                data_b64=b64(data),
                n=len(data),
                text_sig=out_snapshot.text_sig or None,
                visual_sig=out_snapshot.visual_sig or None,
                semantic_sig=out_snapshot.semantic_sig or out_snapshot.screen_sig or None,
                engine_version=canonical.get("engine_version") or None,
                snapshot_version=canonical.get("snapshot_version") or None,
                signature_version=canonical.get("signature_version") or None,
                rows=rows,
                cols=cols,
                term=canonical.get("term") or self.cfg.term,
                encoding=canonical.get("encoding") or screen_state.encoding,
                geometry_source="terminal_snapshot",
                comparison_mode="visual",
            )
        )
        return out_snapshot

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
            return ["/bin/sh", "-c", command]

        shell = str(os.environ.get("SHELL") or "/bin/sh").strip() or "/bin/sh"
        # AIX: ksh/sh nao aceitam flag -l (login shell se invoca com argv[0]="-ksh")
        if hasattr(os, "uname") and os.uname().sysname == "AIX":
            return [shell]
        return [shell, "-l"]

    def _run_batch_pipe(self, gateway_endpoint: str) -> int:
        """Modo batch sem PTY: captura saida de comando via pipe (compativel com AIX)."""
        command = str(self.cfg.source_command or "").strip()
        self._append_session_start(gateway_endpoint=gateway_endpoint, command=command)
        screen_state = TerminalScreenState(
            rows=self.cfg.rows,
            cols=self.cfg.cols,
            encoding=self.cfg.encoding,
            session_id=self.session_id,
        )
        try:
            proc = subprocess.Popen(
                ["/bin/sh", "-c", command],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                close_fds=True,
            )
            out, _ = proc.communicate(timeout=300)
            if out:
                os.write(1, out)
                self._append_out_bytes_event(data=out, screen_state=screen_state)
            rc = proc.returncode
        except Exception as e:
            out = str(e).encode()
            os.write(1, out)
            rc = 1
        self._append_session_end()
        self.writer.close()
        return int(rc or 0)

    def run(self) -> int:
        gateway_endpoint = (
            self.cfg.gateway_endpoint
            or os.environ.get("DAKOTA_GATEWAY_ENDPOINT")
            or os.environ.get("HOSTNAME")
            or ""
        )
        batch_mode = str(self.cfg.ssh_batch_mode or "no").strip().lower()
        command = str(self.cfg.source_command or "").strip()

        # ── Fast path: batch mode sem PTY (compativel com AIX) ──
        if batch_mode == "yes" and command and not self.cfg.source_host:
            return self._run_batch_pipe(gateway_endpoint)

        # ── Abertura robusta de PTY com multiplos fallbacks ──
        try:
            master_fd, slave_fd = _open_pty_robust()
        except OSError:
            # Nenhuma estrategia PTY funcionou — fallback para batch pipe
            if not command:
                command = str(os.environ.get("SHELL") or "/bin/sh").strip() or "/bin/sh"
            self.cfg.source_command = command
            return self._run_batch_pipe(gateway_endpoint)

        # Configurar geometria do terminal usando metadata da config
        _configure_pty(slave_fd, rows=self.cfg.rows, cols=self.cfg.cols)
        use_setsid = batch_mode == "yes"
        try:
            proc = subprocess.Popen(
                self._session_argv(),
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                preexec_fn=os.setsid if use_setsid else None,
                close_fds=True,
                env=dict(os.environ, TERM=self.cfg.term),
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
        self._append_session_start(gateway_endpoint=gateway_endpoint)

        screen_state = TerminalScreenState(
            rows=self.cfg.rows,
            cols=self.cfg.cols,
            encoding=self.cfg.encoding,
            session_id=self.session_id,
        )
        last_out_ms = self._ts_ms()
        last_checkpoint_ms = 0
        stable_state = _StableScreenState()
        screen_dirty = False

        def maybe_checkpoint(force: bool = False):
            nonlocal last_checkpoint_ms, stable_state, screen_dirty
            now = self._ts_ms()
            quiet = now - last_out_ms
            if not force:
                if quiet < self.cfg.checkpoint_quiet_ms:
                    return
            if screen_dirty or (force and stable_state.snapshot is None and screen_state.bytes_seen > 0):
                stable_state = _StableScreenState(
                    snapshot=self._screen_snapshot_from_state(screen_state),
                    ts_ms=now,
                    source="stable",
                )
                screen_dirty = False
            if screen_state.bytes_seen < self.cfg.checkpoint_min_bytes:
                return
            if not force and now - last_checkpoint_ms < self.cfg.checkpoint_quiet_ms:
                return

            snapshot = stable_state.snapshot or self._screen_snapshot_from_state(screen_state)
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
                screen_sig=snapshot.screen_sig,
                screen_sample=snapshot.screen_sample,
                # Assinaturas canonicas (v0.5.0+)
                text_sig=snapshot.text_sig or None,
                visual_sig=snapshot.visual_sig or None,
                semantic_sig=snapshot.semantic_sig or None,
                engine_version=snapshot.canonical_snapshot.get("engine_version") if snapshot.canonical_snapshot else None,
                rows=screen_state.rows,
                cols=screen_state.cols,
                term=self.cfg.term,
                encoding=screen_state.encoding,
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
                            screen_state=screen_state,
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
                        last_out_ms = self._ts_ms()
                        out_seq_session = self._next_seq_session()
                        screen_dirty = True
                        self._append_out_bytes_event(
                            data=data,
                            screen_state=screen_state,
                            ts_ms=last_out_ms,
                            seq_session=out_seq_session,
                        )

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

            self._append_session_end()
            self.writer.close()
            return int(rc or 0)

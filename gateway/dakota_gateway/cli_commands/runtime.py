from __future__ import annotations

import os
import sys
import json
from pathlib import Path

from ..gateway import GatewayConfig, GatewayCaptureError, TerminalGateway
from ..replay import ReplayConfig, ReplayError, replay_strict_global
from ..terminal_config import TerminalGeometry, geometry_from_environment, geometry_from_tty, normalize_encoding, validate_terminal_geometry
from ..verifier import VerificationError, verify_log


def register_runtime_parsers(subparsers) -> None:
    ap_start = subparsers.add_parser("start", help="Executa gateway (uma sessão)")
    ap_start.add_argument("--log-dir", required=True)
    ap_start.add_argument("--hmac-key-file", required=True)
    ap_start.add_argument("--rotate-bytes", type=int, default=0)
    ap_start.add_argument("--source-host", required=True)
    ap_start.add_argument("--source-user", default="")
    ap_start.add_argument("--source-command", default="")
    ap_start.add_argument("--ssh-batch-mode", choices=["yes", "no"], default="no")
    ap_start.add_argument("--gateway-endpoint", default="")
    ap_start.add_argument("--checkpoint-quiet-ms", type=int, default=250)
    ap_start.add_argument("--checkpoint-min-bytes", type=int, default=512)
    _add_terminal_args(ap_start)

    ap_verify = subparsers.add_parser("verify", help="Verifica integridade do log")
    ap_verify.add_argument("--log-dir", required=True)
    ap_verify.add_argument("--hmac-key-file", required=True)

    ap_replay = subparsers.add_parser("replay", help="Reproduz no destino (strict global order)")
    ap_replay.add_argument("--log-dir", required=True)
    ap_replay.add_argument("--hmac-key-file", required=True)
    ap_replay.add_argument("--target-host", required=True)
    ap_replay.add_argument("--target-user", default="")
    ap_replay.add_argument("--target-command", default="")
    ap_replay.add_argument("--checkpoint-quiet-ms", type=int, default=250)
    ap_replay.add_argument("--checkpoint-timeout-ms", type=int, default=5000)
    ap_replay.add_argument("--mode", choices=["strict-global", "parallel-sessions"], default="strict-global")
    ap_replay.add_argument("--input-mode", choices=["raw", "deterministic"], default="raw")
    ap_replay.add_argument("--on-deterministic-mismatch", choices=["fail-fast", "skip", "send-anyway"], default="fail-fast")
    _add_terminal_args(ap_replay)

    ap_capture = subparsers.add_parser(
        "capture-session",
        help="Captura uma sessão SSH na captura ativa (uso via ForceCommand)",
    )
    ap_capture.add_argument("--db", default="")
    ap_capture.add_argument("--hmac-key-file", required=True)
    ap_capture.add_argument("--capture-id", type=int, default=0)
    ap_capture.add_argument("--source-host", default="")
    ap_capture.add_argument("--source-user", default="")
    ap_capture.add_argument("--source-command", default="")
    ap_capture.add_argument("--ssh-batch-mode", choices=["yes", "no"], default="no")
    ap_capture.add_argument("--gateway-endpoint", default="")
    ap_capture.add_argument("--checkpoint-quiet-ms", type=int, default=250)
    ap_capture.add_argument("--checkpoint-min-bytes", type=int, default=512)
    _add_terminal_args(ap_capture)


def _add_terminal_args(parser) -> None:
    parser.add_argument("--rows", type=int, default=None)
    parser.add_argument("--cols", type=int, default=None)
    parser.add_argument("--term", default="")
    parser.add_argument("--encoding", default="")


def _read_session_terminal_metadata(log_dir: str, session_id: str | None = None) -> dict:
    clean_sid = str(session_id or "").strip()
    for file_path in sorted(Path(log_dir).glob("audit-*.jsonl")):
        try:
            lines = file_path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                item = json.loads(line)
            except Exception:
                continue
            if not isinstance(item, dict) or item.get("type") != "session_start":
                continue
            if clean_sid and str(item.get("session_id") or "").strip() != clean_sid:
                continue
            return item
    return {}


def _resolve_terminal_options(ns, *, session_metadata: dict | None = None) -> dict:
    source = "legacy_fallback"
    rows = getattr(ns, "rows", None)
    cols = getattr(ns, "cols", None)
    term = str(getattr(ns, "term", "") or "").strip()
    encoding = str(getattr(ns, "encoding", "") or "").strip()

    if session_metadata and session_metadata.get("rows") is not None and session_metadata.get("cols") is not None:
        geom = validate_terminal_geometry(int(session_metadata.get("rows")), int(session_metadata.get("cols")))
        source = "session_metadata"
    else:
        tty_geom, tty_source = geometry_from_tty()
        if tty_geom:
            geom = tty_geom
            source = tty_source
        else:
            env_geom, env_source = geometry_from_environment()
            if env_geom:
                geom = env_geom
                source = env_source
            else:
                geom = TerminalGeometry(25, 80)

    if rows is not None or cols is not None:
        explicit_rows = geom.rows if rows is None else int(rows)
        explicit_cols = geom.cols if cols is None else int(cols)
        geom = validate_terminal_geometry(explicit_rows, explicit_cols)
        source = "explicit"

    if not term and session_metadata:
        term = str(session_metadata.get("term") or "").strip()
    if not term:
        term = str(os.environ.get("TERM") or "xterm").strip() or "xterm"
    if not encoding and session_metadata:
        encoding = str(session_metadata.get("encoding") or "").strip()

    return {
        "rows": geom.rows,
        "cols": geom.cols,
        "term": term,
        "encoding": normalize_encoding(encoding or "utf-8"),
        "geometry_source": source,
    }


def _resolve_capture_session(db_path: str, capture_id: int = 0) -> dict:
    from ..state_db import connect, default_db_path

    path = db_path or default_db_path()
    con = connect(path)
    try:
        if int(capture_id or 0) > 0:
            row = con.execute(
                "SELECT id, session_uuid, log_dir FROM capture_sessions WHERE id=?",
                (int(capture_id),),
            ).fetchone()
        else:
            row = con.execute(
                "SELECT id, session_uuid, log_dir FROM capture_sessions WHERE status='active' ORDER BY id DESC LIMIT 1"
            ).fetchone()
        if not row:
            raise RuntimeError("nenhuma captura ativa encontrada")
        log_dir = str(row["log_dir"] or "").strip()
        if not log_dir:
            raise RuntimeError("captura sem log_dir")
        os.makedirs(log_dir, exist_ok=True)
        return {
            "id": int(row["id"] or 0),
            "session_uuid": str(row["session_uuid"] or "").strip(),
            "log_dir": log_dir,
        }
    finally:
        con.close()


def _resolve_capture_log_dir(db_path: str, capture_id: int = 0) -> str:
    return _resolve_capture_session(db_path, capture_id)["log_dir"]


def handle_runtime_command(ns, read_key) -> int:
    if ns.cmd == "start":
        key = read_key(ns.hmac_key_file)
        term_opts = _resolve_terminal_options(ns)
        cfg = GatewayConfig(
            log_dir=ns.log_dir,
            hmac_key=key,
            rotate_bytes=ns.rotate_bytes,
            source_host=ns.source_host,
            source_user=ns.source_user,
            source_command=ns.source_command,
            ssh_batch_mode=ns.ssh_batch_mode,
            gateway_endpoint=ns.gateway_endpoint,
            checkpoint_quiet_ms=ns.checkpoint_quiet_ms,
            checkpoint_min_bytes=ns.checkpoint_min_bytes,
            rows=term_opts["rows"],
            cols=term_opts["cols"],
            term=term_opts["term"],
            encoding=term_opts["encoding"],
            geometry_source=term_opts["geometry_source"],
        )
        return TerminalGateway(cfg).run()

    if ns.cmd == "verify":
        key = read_key(ns.hmac_key_file)
        try:
            verify_log(ns.log_dir, key)
        except VerificationError as exc:
            print(f"FAIL: {exc}", file=sys.stderr)
            return 2
        print("OK")
        return 0

    if ns.cmd == "replay":
        key = read_key(ns.hmac_key_file)
        try:
            verify_log(ns.log_dir, key)
        except VerificationError as exc:
            print(f"FAIL integrity: {exc}", file=sys.stderr)
            return 2
        try:
            term_opts = _resolve_terminal_options(
                ns,
                session_metadata=_read_session_terminal_metadata(ns.log_dir, getattr(ns, "replay_session_id", None)),
            )
            cfg = ReplayConfig(
                log_dir=ns.log_dir,
                target_host=ns.target_host,
                target_user=ns.target_user,
                target_command=ns.target_command,
                checkpoint_quiet_ms=ns.checkpoint_quiet_ms,
                checkpoint_timeout_ms=ns.checkpoint_timeout_ms,
                input_mode=ns.input_mode,
                on_deterministic_mismatch=ns.on_deterministic_mismatch,
                rows=term_opts["rows"],
                cols=term_opts["cols"],
                term=term_opts["term"],
                encoding=term_opts["encoding"],
            )
            if ns.mode == "strict-global":
                replay_strict_global(cfg)
            else:
                from ..replay import replay_parallel_sessions

                replay_parallel_sessions(cfg)
        except ReplayError as exc:
            print(f"FAIL replay: {exc}", file=sys.stderr)
            return 3
        print("OK")
        return 0

    if ns.cmd == "capture-session":
        # --source-user vazio = captura TODOS os usuarios (fail-closed)
        source_user = str(ns.source_user or "").strip()
        actor = os.environ.get("USER") or os.environ.get("LOGNAME") or "unknown"

        key = read_key(ns.hmac_key_file)
        capture = _resolve_capture_session(getattr(ns, "db", ""), getattr(ns, "capture_id", 0))
        term_opts = _resolve_terminal_options(ns)
        log_dir = str(capture["log_dir"])
        source_command = str(ns.source_command or "").strip()
        if not source_command:
            source_command = str(os.environ.get("SSH_ORIGINAL_COMMAND") or "").strip()
        cfg = GatewayConfig(
            log_dir=log_dir,
            hmac_key=key,
            capture_id=int(capture.get("id") or 0),
            capture_session_uuid=str(capture.get("session_uuid") or "").strip(),
            source_host=str(ns.source_host or "").strip(),
            source_user=source_user or actor,
            source_command=source_command,
            ssh_batch_mode=ns.ssh_batch_mode,
            gateway_endpoint=ns.gateway_endpoint,
            checkpoint_quiet_ms=ns.checkpoint_quiet_ms,
            checkpoint_min_bytes=ns.checkpoint_min_bytes,
            rows=term_opts["rows"],
            cols=term_opts["cols"],
            term=term_opts["term"],
            encoding=term_opts["encoding"],
            geometry_source=term_opts["geometry_source"],
        )
        try:
            return TerminalGateway(cfg).run()
        except GatewayCaptureError as exc:
            print(f"ERRO: {exc}", file=sys.stderr)
            return 1
        except Exception as exc:
            print(f"ERRO: Gateway ativo mas falha na captura: {exc}", file=sys.stderr)
            print("Contate o administrador. Login abortado.", file=sys.stderr)
            return 1

    return 1

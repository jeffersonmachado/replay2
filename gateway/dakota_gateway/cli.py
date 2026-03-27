from __future__ import annotations

import argparse
import json
import os
import sys
import time

from .gateway import GatewayConfig, TerminalGateway
from .verifier import verify_log, VerificationError
from .replay import ReplayConfig, replay_strict_global, ReplayError


def _read_key(path: str) -> bytes:
    with open(path, "rb") as f:
        key = f.read().strip()
    if not key:
        raise SystemExit("hmac key file vazio")
    return key


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="dakota-gateway")
    sub = ap.add_subparsers(dest="cmd", required=True)

    ap_start = sub.add_parser("start", help="Executa gateway (uma sessão)")
    ap_start.add_argument("--log-dir", required=True)
    ap_start.add_argument("--hmac-key-file", required=True)
    ap_start.add_argument("--rotate-bytes", type=int, default=0)
    ap_start.add_argument("--source-host", required=True)
    ap_start.add_argument("--source-user", default="")
    ap_start.add_argument("--source-command", default="")
    ap_start.add_argument("--checkpoint-quiet-ms", type=int, default=250)
    ap_start.add_argument("--checkpoint-min-bytes", type=int, default=512)

    ap_verify = sub.add_parser("verify", help="Verifica integridade do log")
    ap_verify.add_argument("--log-dir", required=True)
    ap_verify.add_argument("--hmac-key-file", required=True)

    ap_replay = sub.add_parser("replay", help="Reproduz no destino (strict global order)")
    ap_replay.add_argument("--log-dir", required=True)
    ap_replay.add_argument("--hmac-key-file", required=True)
    ap_replay.add_argument("--target-host", required=True)
    ap_replay.add_argument("--target-user", default="")
    ap_replay.add_argument("--target-command", default="")
    ap_replay.add_argument("--checkpoint-quiet-ms", type=int, default=250)
    ap_replay.add_argument("--checkpoint-timeout-ms", type=int, default=5000)
    ap_replay.add_argument("--mode", choices=["strict-global", "parallel-sessions"], default="strict-global")

    # Control-plane ops
    ap_user = sub.add_parser("user", help="Gerencia usuários do dashboard/control plane")
    ap_user.add_argument("--db", default="")
    ap_user_sub = ap_user.add_subparsers(dest="user_cmd", required=True)
    ap_user_add = ap_user_sub.add_parser("add", help="Cria usuário")
    ap_user_add.add_argument("--username", required=True)
    ap_user_add.add_argument("--password", required=True)
    ap_user_add.add_argument("--role", choices=["admin", "operator", "viewer"], required=True)

    ap_runs = sub.add_parser("runs", help="Opera replay runs (SQLite)")
    ap_runs.add_argument("--db", default="")
    ap_runs.add_argument("--hmac-key-file", required=False, default="")
    ap_runs_sub = ap_runs.add_subparsers(dest="runs_cmd", required=True)
    ap_runs_create = ap_runs_sub.add_parser("create")
    ap_runs_create.add_argument("--created-by", required=True, help="username")
    ap_runs_create.add_argument("--log-dir", required=True)
    ap_runs_create.add_argument("--target-host", required=True)
    ap_runs_create.add_argument("--target-user", default="")
    ap_runs_create.add_argument("--target-command", default="")
    ap_runs_create.add_argument("--mode", choices=["strict-global", "parallel-sessions"], default="strict-global")
    # load-test options (used when mode=parallel-sessions)
    ap_runs_create.add_argument("--concurrency", type=int, default=0)
    ap_runs_create.add_argument("--ramp-up-per-sec", type=float, default=1.0)
    ap_runs_create.add_argument("--speed", type=float, default=1.0)
    ap_runs_create.add_argument("--jitter-ms", type=int, default=0)
    ap_runs_create.add_argument("--on-checkpoint-mismatch", choices=["continue", "fail-fast"], default="continue")
    ap_runs_create.add_argument("--target-user-pool", default="", help="csv: user1,user2,... (opcional)")

    ap_runs_start = ap_runs_sub.add_parser("start")
    ap_runs_start.add_argument("--run-id", type=int, required=True)
    ap_runs_start.add_argument("--hmac-key-file", required=True)

    for name in ["pause", "resume", "cancel", "status", "retry"]:
        p2 = ap_runs_sub.add_parser(name)
        p2.add_argument("--run-id", type=int, required=True)
        if name in ("resume",):
            p2.add_argument("--hmac-key-file", required=True)
        if name in ("retry",):
            p2.add_argument("--created-by", required=True, help="username")

    ns = ap.parse_args(argv)

    if ns.cmd == "start":
        key = _read_key(ns.hmac_key_file)
        cfg = GatewayConfig(
            log_dir=ns.log_dir,
            hmac_key=key,
            rotate_bytes=ns.rotate_bytes,
            source_host=ns.source_host,
            source_user=ns.source_user,
            source_command=ns.source_command,
            checkpoint_quiet_ms=ns.checkpoint_quiet_ms,
            checkpoint_min_bytes=ns.checkpoint_min_bytes,
        )
        gw = TerminalGateway(cfg)
        return gw.run()

    if ns.cmd == "verify":
        key = _read_key(ns.hmac_key_file)
        try:
            verify_log(ns.log_dir, key)
        except VerificationError as e:
            print(f"FAIL: {e}", file=sys.stderr)
            return 2
        print("OK")
        return 0

    if ns.cmd == "replay":
        key = _read_key(ns.hmac_key_file)
        # first verify integrity before replay
        try:
            verify_log(ns.log_dir, key)
        except VerificationError as e:
            print(f"FAIL integrity: {e}", file=sys.stderr)
            return 2
        try:
            cfg = ReplayConfig(
                log_dir=ns.log_dir,
                target_host=ns.target_host,
                target_user=ns.target_user,
                target_command=ns.target_command,
                checkpoint_quiet_ms=ns.checkpoint_quiet_ms,
                checkpoint_timeout_ms=ns.checkpoint_timeout_ms,
            )
            if ns.mode == "strict-global":
                replay_strict_global(cfg)
            else:
                from .replay import replay_parallel_sessions

                replay_parallel_sessions(cfg)
        except ReplayError as e:
            print(f"FAIL replay: {e}", file=sys.stderr)
            return 3
        print("OK")
        return 0

    if ns.cmd == "user":
        from . import auth as _auth
        from .state_db import connect as _connect, default_db_path as _default_db_path, init_db as _init_db, query_one as _q1

        db = ns.db or _default_db_path()
        con = _connect(db)
        _init_db(con)
        try:
            if ns.user_cmd == "add":
                ph = _auth.pbkdf2_hash_password(ns.password)
                con.execute(
                    "INSERT INTO users(username,password_hash,role,created_at_ms) VALUES(?,?,?,?)",
                    (ns.username, ph, ns.role, int(time.time() * 1000)),
                )
                print("OK")
                return 0
        finally:
            con.close()

    if ns.cmd == "runs":
        from .state_db import connect as _connect, default_db_path as _default_db_path, init_db as _init_db, query_one as _q1, query_all as _qa
        from .replay_control import Runner as _Runner, create_run as _create_run, pause_run as _pause, resume_run as _resume, cancel_run as _cancel, retry_run as _retry
        from .auth import sha256_hex as _sha

        db = ns.db or _default_db_path()
        con = _connect(db)
        _init_db(con)
        try:
            if ns.runs_cmd == "create":
                u = _q1(con, "SELECT id FROM users WHERE username=?", (ns.created_by,))
                if not u:
                    print("Erro: created-by inexistente", file=sys.stderr)
                    return 2
                rid = _create_run(
                    con,
                    created_by=int(u["id"]),
                    log_dir=ns.log_dir,
                    target_host=ns.target_host,
                    target_user=ns.target_user,
                    target_command=ns.target_command,
                    mode=ns.mode,
                )
                # persist load-test params (if any)
                params = {}
                if ns.mode == "parallel-sessions":
                    if ns.concurrency and ns.concurrency > 0:
                        params["concurrency"] = ns.concurrency
                    params["ramp_up_per_sec"] = ns.ramp_up_per_sec
                    params["speed"] = ns.speed
                    params["jitter_ms"] = ns.jitter_ms
                    params["on_checkpoint_mismatch"] = ns.on_checkpoint_mismatch
                    pool = [p.strip() for p in (ns.target_user_pool or "").split(",") if p.strip()]
                    if pool:
                        params["target_user_pool"] = pool
                if params:
                    con.execute("UPDATE replay_runs SET params_json=? WHERE id=?", (json.dumps(params, ensure_ascii=False), rid))
                print(rid)
                return 0

            if ns.runs_cmd == "status":
                r = _q1(con, "SELECT * FROM replay_runs WHERE id=?", (ns.run_id,))
                if not r:
                    print("Erro: run inexistente", file=sys.stderr)
                    return 2
                print(dict(r))
                return 0

            if ns.runs_cmd == "resume":
                # handled after closing connection (foreground runner)
                pass

            if ns.runs_cmd == "pause":
                _pause(con, ns.run_id)
                print("OK")
                return 0
            if ns.runs_cmd == "cancel":
                _cancel(con, ns.run_id)
                print("OK")
                return 0
            if ns.runs_cmd == "retry":
                u = _q1(con, "SELECT id FROM users WHERE username=?", (ns.created_by,))
                if not u:
                    print("Erro: created-by inexistente", file=sys.stderr)
                    return 2
                nid = _retry(con, ns.run_id, created_by=int(u["id"]))
                print(nid)
                return 0
        finally:
            con.close()

        # start/resume execute runner in foreground (control via DB)
        if ns.runs_cmd in ("start", "resume"):
            if not ns.hmac_key_file:
                print("Erro: falta --hmac-key-file", file=sys.stderr)
                return 2
            key = _read_key(ns.hmac_key_file)
            runner = _Runner(db, key)
            con2 = _connect(db)
            _init_db(con2)
            try:
                if ns.runs_cmd == "resume":
                    from .replay_control import resume_run as _rsm
                    _rsm(con2, ns.run_id)
                else:
                    con2.execute("UPDATE replay_runs SET status='running' WHERE id=? AND status='queued'", (ns.run_id,))
                con2.close()
                # run synchronously so it actually completes even without dashboard server
                runner.run_foreground(ns.run_id)
                print("OK")
                return 0
            finally:
                try:
                    con2.close()
                except Exception:
                    pass

    return 1


if __name__ == "__main__":
    raise SystemExit(main())


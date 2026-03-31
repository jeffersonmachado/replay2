from __future__ import annotations

import argparse
import json
import os
import sys
import time

from .cli_commands.catalog import (
    handle_profiles,
    handle_targets,
    register_profiles_parser,
    register_targets_parser,
)
from .cli_commands.runtime import handle_runtime_command, register_runtime_parsers
from .compliance import derive_gateway_route_from_capture, evaluate_run_compliance


def _read_key(path: str) -> bytes:
    with open(path, "rb") as f:
        key = f.read().strip()
    if not key:
        raise SystemExit("hmac key file vazio")
    return key


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="dakota-gateway")
    sub = ap.add_subparsers(dest="cmd", required=True)

    register_runtime_parsers(sub)

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
    ap_runs_create.add_argument("--target-host", default="")
    ap_runs_create.add_argument("--target-user", default="")
    ap_runs_create.add_argument("--target-command", default="")
    ap_runs_create.add_argument("--target-env-id", type=int, default=0)
    ap_runs_create.add_argument("--connection-profile-id", type=int, default=0)
    ap_runs_create.add_argument("--mode", choices=["strict-global", "parallel-sessions"], default="strict-global")
    # load-test options (used when mode=parallel-sessions)
    ap_runs_create.add_argument("--concurrency", type=int, default=0)
    ap_runs_create.add_argument("--ramp-up-per-sec", type=float, default=1.0)
    ap_runs_create.add_argument("--speed", type=float, default=1.0)
    ap_runs_create.add_argument("--jitter-ms", type=int, default=0)
    ap_runs_create.add_argument("--on-checkpoint-mismatch", choices=["continue", "fail-fast"], default="continue")
    ap_runs_create.add_argument("--target-user-pool", default="", help="csv: user1,user2,... (opcional)")
    ap_runs_create.add_argument("--replay-from-seq-global", type=int, default=0)
    ap_runs_create.add_argument("--replay-to-seq-global", type=int, default=0)
    ap_runs_create.add_argument("--replay-session-id", default="")
    ap_runs_create.add_argument("--replay-from-checkpoint-sig", default="")
    ap_runs_create.add_argument("--input-mode", choices=["raw", "deterministic"], default="raw")
    ap_runs_create.add_argument("--on-deterministic-mismatch", choices=["fail-fast", "skip", "send-anyway"], default="fail-fast")
    ap_runs_create.add_argument("--match-mode", choices=["strict", "contains", "regex", "fuzzy"], default="strict")
    ap_runs_create.add_argument("--match-threshold", type=float, default=0.92)
    ap_runs_create.add_argument("--match-ignore-case", action="store_true")

    register_targets_parser(sub)
    register_profiles_parser(sub)

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

    if ns.cmd in {"start", "verify", "replay", "capture-session"}:
        return handle_runtime_command(ns, _read_key)

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
        from .state_db import connect as _connect, default_db_path as _default_db_path, init_db as _init_db, query_one as _q1
        from .replay_control import Runner as _Runner, create_run as _create_run, pause_run as _pause, cancel_run as _cancel, retry_run as _retry, set_run_compliance as _set_run_compliance

        db = ns.db or _default_db_path()
        con = _connect(db)
        _init_db(con)
        try:
            if ns.runs_cmd == "create":
                u = _q1(con, "SELECT id FROM users WHERE username=?", (ns.created_by,))
                if not u:
                    print("Erro: created-by inexistente", file=sys.stderr)
                    return 2
                resolved_host = ns.target_host
                resolved_user = ns.target_user
                resolved_command = ns.target_command
                params = {}
                target_env_id = int(ns.target_env_id or 0) or None
                connection_profile_id = int(ns.connection_profile_id or 0) or None
                target_policy = {}
                if target_env_id:
                    env = _q1(con, "SELECT * FROM target_environments WHERE id=?", (target_env_id,))
                    if not env:
                        print("Erro: target-env-id inexistente", file=sys.stderr)
                        return 2
                    target_policy = dict(env)
                    if not resolved_host:
                        resolved_host = str(env["host"] or "")
                    params["target_environment"] = str(env["env_id"] or "")
                    params["environment"] = str(env["name"] or env["env_id"] or "")
                    params["target_platform"] = str(env["platform"] or "linux")
                    if env["port"]:
                        params["target_port"] = int(env["port"])
                    params["target_transport_hint"] = str(env["transport_hint"] or "ssh")
                if connection_profile_id:
                    profile = _q1(con, "SELECT * FROM connection_profiles WHERE id=?", (connection_profile_id,))
                    if not profile:
                        print("Erro: connection-profile-id inexistente", file=sys.stderr)
                        return 2
                    if not resolved_user:
                        resolved_user = str(profile["username"] or "")
                    if not resolved_command:
                        resolved_command = str(profile["command"] or "")
                    params["connection_profile_id"] = int(profile["id"])
                    params["connection_profile_name"] = str(profile["name"] or "")
                    params["transport"] = str(profile["transport"] or "ssh")
                    if profile["port"]:
                        params["target_port"] = int(profile["port"])
                    if profile["credential_ref"]:
                        params["credential_ref"] = str(profile["credential_ref"])
                    if profile["auth_mode"]:
                        params["auth_mode"] = str(profile["auth_mode"])
                if not resolved_host:
                    print("Erro: target-host inexistente e target-env-id ausente", file=sys.stderr)
                    return 2
                if target_policy and target_policy.get("metadata_json"):
                    try:
                        target_metadata = json.loads(target_policy["metadata_json"] or "{}")
                    except Exception:
                        target_metadata = {}
                    if isinstance(target_metadata, dict):
                        if target_metadata.get("gateway_host"):
                            params.setdefault("gateway_host", str(target_metadata.get("gateway_host")))
                            params.setdefault("gateway_route_mode", "proxyjump")
                        if target_metadata.get("gateway_user"):
                            params.setdefault("gateway_user", str(target_metadata.get("gateway_user")))
                        if target_metadata.get("gateway_port"):
                            params.setdefault("gateway_port", int(target_metadata.get("gateway_port") or 0))
                rid = _create_run(
                    con,
                    created_by=int(u["id"]),
                    log_dir=ns.log_dir,
                    target_host=resolved_host,
                    target_user=resolved_user,
                    target_command=resolved_command,
                    mode=ns.mode,
                    target_env_id=target_env_id,
                    connection_profile_id=connection_profile_id,
                )
                # persist load-test params (if any)
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
                if target_policy and target_policy.get("gateway_required") and not str(params.get("gateway_host") or "").strip():
                    params.update(
                        {
                            key: value
                            for key, value in derive_gateway_route_from_capture(ns.log_dir, target_policy=target_policy).items()
                            if value not in (None, "")
                        }
                    )
                    con.execute("UPDATE replay_runs SET params_json=? WHERE id=?", (json.dumps(params, ensure_ascii=False), rid))
                compliance = evaluate_run_compliance(
                    ns.log_dir,
                    target_policy=target_policy,
                    resolved_target={
                        "target_host": resolved_host,
                        "target_user": resolved_user,
                        "target_command": resolved_command,
                    },
                    resolved_params=params,
                )
                _set_run_compliance(con, rid, compliance)
                partial = {}
                if ns.replay_from_seq_global and ns.replay_from_seq_global > 0:
                    partial["replay_from_seq_global"] = ns.replay_from_seq_global
                if ns.replay_to_seq_global and ns.replay_to_seq_global > 0:
                    partial["replay_to_seq_global"] = ns.replay_to_seq_global
                if ns.replay_session_id:
                    partial["replay_session_id"] = ns.replay_session_id
                if ns.replay_from_checkpoint_sig:
                    partial["replay_from_checkpoint_sig"] = ns.replay_from_checkpoint_sig
                params["match_mode"] = ns.match_mode
                params["match_threshold"] = ns.match_threshold
                params["input_mode"] = ns.input_mode
                params["on_deterministic_mismatch"] = ns.on_deterministic_mismatch
                if ns.match_ignore_case:
                    params["match_ignore_case"] = True
                if partial:
                    merged = {}
                    if params:
                        merged.update(params)
                    merged.update(partial)
                    con.execute("UPDATE replay_runs SET params_json=? WHERE id=?", (json.dumps(merged, ensure_ascii=False), rid))
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

    if ns.cmd == "targets":
        return handle_targets(ns)

    if ns.cmd == "profiles":
        return handle_profiles(ns)

    return 1


if __name__ == "__main__":
    raise SystemExit(main())

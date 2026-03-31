from __future__ import annotations

import argparse
import json
import sys

from ..compliance import normalize_direct_ssh_policy_payload
from ..state_db import connect, default_db_path, exec1, init_db, now_ms


def register_targets_parser(subparsers) -> None:
    ap_targets = subparsers.add_parser("targets", help="Gerencia target environments")
    ap_targets.add_argument("--db", default="")
    ap_targets_sub = ap_targets.add_subparsers(dest="targets_cmd", required=True)
    ap_targets_sub.add_parser("list")

    ap_targets_add = ap_targets_sub.add_parser("add")
    ap_targets_add.add_argument("--env-id", default="")
    ap_targets_add.add_argument("--name", required=True)
    ap_targets_add.add_argument("--host", required=True)
    ap_targets_add.add_argument("--port", type=int, default=0)
    ap_targets_add.add_argument("--platform", default="linux")
    ap_targets_add.add_argument("--transport-hint", choices=["ssh", "telnet"], default="ssh")
    ap_targets_add.add_argument("--description", default="")
    ap_targets_add.add_argument("--gateway-required", action="store_true")
    ap_targets_add.add_argument("--direct-ssh-policy", choices=["gateway_only", "admin_only", "unrestricted", "disabled"], default="unrestricted")
    ap_targets_add.add_argument("--capture-start-mode", choices=["login_required", "session_start_required"], default="session_start_required")
    ap_targets_add.add_argument("--capture-compliance-mode", choices=["strict", "warn", "off"], default="off")
    ap_targets_add.add_argument("--allow-admin-direct-access", action="store_true")
    ap_targets_add.add_argument("--gateway-host", default="")
    ap_targets_add.add_argument("--gateway-user", default="")
    ap_targets_add.add_argument("--gateway-port", type=int, default=0)

    ap_targets_policy = ap_targets_sub.add_parser("policy")
    ap_targets_policy.add_argument("--target-id", type=int, required=True)
    ap_targets_policy.add_argument("--gateway-required", action="store_true")
    ap_targets_policy.add_argument("--direct-ssh-policy", choices=["gateway_only", "admin_only", "unrestricted", "disabled"], default="unrestricted")
    ap_targets_policy.add_argument("--capture-start-mode", choices=["login_required", "session_start_required"], default="session_start_required")
    ap_targets_policy.add_argument("--capture-compliance-mode", choices=["strict", "warn", "off"], default="off")
    ap_targets_policy.add_argument("--allow-admin-direct-access", action="store_true")
    ap_targets_policy.add_argument("--gateway-host", default="")
    ap_targets_policy.add_argument("--gateway-user", default="")
    ap_targets_policy.add_argument("--gateway-port", type=int, default=0)


def register_profiles_parser(subparsers) -> None:
    ap_profiles = subparsers.add_parser("profiles", help="Gerencia connection profiles")
    ap_profiles.add_argument("--db", default="")
    ap_profiles_sub = ap_profiles.add_subparsers(dest="profiles_cmd", required=True)
    ap_profiles_sub.add_parser("list")

    ap_profiles_add = ap_profiles_sub.add_parser("add")
    ap_profiles_add.add_argument("--profile-id", default="")
    ap_profiles_add.add_argument("--name", required=True)
    ap_profiles_add.add_argument("--transport", choices=["ssh", "telnet"], default="ssh")
    ap_profiles_add.add_argument("--username", default="")
    ap_profiles_add.add_argument("--port", type=int, default=0)
    ap_profiles_add.add_argument("--command", default="")
    ap_profiles_add.add_argument("--credential-ref", default="")
    ap_profiles_add.add_argument("--auth-mode", default="external")


def _build_target_policy(ns: argparse.Namespace) -> dict:
    return normalize_direct_ssh_policy_payload(
        {
            "gateway_required": ns.gateway_required,
            "direct_ssh_policy": ns.direct_ssh_policy,
            "capture_start_mode": ns.capture_start_mode,
            "capture_compliance_mode": ns.capture_compliance_mode,
            "allow_admin_direct_access": ns.allow_admin_direct_access,
        }
    )


def _build_gateway_metadata(ns: argparse.Namespace) -> dict:
    metadata = {}
    if ns.gateway_host:
        metadata["gateway_host"] = ns.gateway_host
    if ns.gateway_user:
        metadata["gateway_user"] = ns.gateway_user
    if ns.gateway_port:
        metadata["gateway_port"] = int(ns.gateway_port)
    return metadata


def handle_targets(ns: argparse.Namespace) -> int:
    db = ns.db or default_db_path()
    con = connect(db)
    init_db(con)
    try:
        if ns.targets_cmd == "list":
            rows = con.execute(
                """
                SELECT id, env_id, name, host, port, platform, transport_hint,
                       gateway_required, direct_ssh_policy, capture_start_mode,
                       capture_compliance_mode, allow_admin_direct_access
                FROM target_environments
                ORDER BY name, id
                """
            ).fetchall()
            for row in rows:
                print(dict(row))
            return 0

        if ns.targets_cmd == "add":
            policy = _build_target_policy(ns)
            metadata = _build_gateway_metadata(ns)
            env_id = (ns.env_id or ns.name.lower().replace(" ", "-")).strip()
            rid = exec1(
                con,
                """
                INSERT INTO target_environments(
                    env_id, name, host, port, platform, transport_hint,
                    gateway_required, direct_ssh_policy, capture_start_mode,
                    capture_compliance_mode, allow_admin_direct_access,
                    description, metadata_json, created_at_ms, updated_at_ms
                )
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    env_id,
                    ns.name,
                    ns.host,
                    ns.port or None,
                    ns.platform,
                    ns.transport_hint,
                    1 if policy["gateway_required"] else 0,
                    policy["direct_ssh_policy"],
                    policy["capture_start_mode"],
                    policy["capture_compliance_mode"],
                    1 if policy["allow_admin_direct_access"] else 0,
                    ns.description or None,
                    json.dumps(metadata, ensure_ascii=False),
                    now_ms(),
                    now_ms(),
                ),
            )
            print(rid)
            return 0

        if ns.targets_cmd == "policy":
            policy = _build_target_policy(ns)
            metadata = _build_gateway_metadata(ns)
            con.execute(
                """
                UPDATE target_environments
                SET gateway_required=?, direct_ssh_policy=?, capture_start_mode=?,
                    capture_compliance_mode=?, allow_admin_direct_access=?, metadata_json=?, updated_at_ms=?
                WHERE id=?
                """,
                (
                    1 if policy["gateway_required"] else 0,
                    policy["direct_ssh_policy"],
                    policy["capture_start_mode"],
                    policy["capture_compliance_mode"],
                    1 if policy["allow_admin_direct_access"] else 0,
                    json.dumps(metadata, ensure_ascii=False),
                    now_ms(),
                    ns.target_id,
                ),
            )
            print("OK")
            return 0
    finally:
        con.close()

    print("Erro: comando de target inválido", file=sys.stderr)
    return 2


def handle_profiles(ns: argparse.Namespace) -> int:
    db = ns.db or default_db_path()
    con = connect(db)
    init_db(con)
    try:
        if ns.profiles_cmd == "list":
            rows = con.execute(
                "SELECT id, profile_id, name, transport, username, port, credential_ref, auth_mode FROM connection_profiles ORDER BY name, id"
            ).fetchall()
            for row in rows:
                print(dict(row))
            return 0

        if ns.profiles_cmd == "add":
            profile_id = (ns.profile_id or ns.name.lower().replace(" ", "-")).strip()
            rid = exec1(
                con,
                """
                INSERT INTO connection_profiles(profile_id, name, transport, username, port, command, credential_ref, auth_mode, options_json, created_at_ms, updated_at_ms)
                VALUES(?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    profile_id,
                    ns.name,
                    ns.transport,
                    ns.username or None,
                    ns.port or None,
                    ns.command or None,
                    ns.credential_ref or None,
                    ns.auth_mode,
                    "{}",
                    now_ms(),
                    now_ms(),
                ),
            )
            print(rid)
            return 0
    finally:
        con.close()

    print("Erro: comando de profile inválido", file=sys.stderr)
    return 2

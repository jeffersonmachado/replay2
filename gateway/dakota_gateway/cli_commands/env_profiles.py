#!/usr/bin/env python3
"""Perfis pre-definidos para target environments: lab e producao.

Uso:
    python3 -m dakota_gateway.cli_commands.env_profiles lab \
      --name "Dakota Lab" --host 10.10.2.30 --db gateway/state/replay.db

    python3 -m dakota_gateway.cli_commands.env_profiles production \
      --name "Dakota Producao" --host 10.10.2.30 --db gateway/state/replay.db
"""
from __future__ import annotations

import json
import time


def now_ms() -> int:
    return int(time.time() * 1000)


LAB_PROFILE = {
    "gateway_required": False,
    "direct_ssh_policy": "unrestricted",
    "capture_start_mode": "manual",
    "capture_compliance_mode": "off",
    "allow_admin_direct_access": True,
    "description": "Ambiente de laboratorio — acesso direto permitido, gateway opcional",
}

PRODUCTION_PROFILE = {
    "gateway_required": True,
    "direct_ssh_policy": "gateway_only",
    "capture_start_mode": "session_start_required",
    "capture_compliance_mode": "audit",
    "allow_admin_direct_access": False,
    "description": "Ambiente de producao — gateway obrigatorio, SSH operacional bloqueado",
}

HOMOLOGATION_PROFILE = {
    "gateway_required": True,
    "direct_ssh_policy": "restricted",
    "capture_start_mode": "session_start_required",
    "capture_compliance_mode": "on",
    "allow_admin_direct_access": False,
    "description": "Ambiente de homologacao — gateway obrigatorio, acesso admin restrito",
}


def get_profile(profile_name: str) -> dict:
    """Retorna o perfil pelo nome."""
    profiles = {
        "lab": LAB_PROFILE,
        "production": PRODUCTION_PROFILE,
        "homologation": HOMOLOGATION_PROFILE,
        "laboratorio": LAB_PROFILE,
        "producao": PRODUCTION_PROFILE,
        "homologacao": HOMOLOGATION_PROFILE,
    }
    return profiles.get(profile_name.lower(), {})


def apply_profile(
    con,
    env_id: str,
    name: str,
    host: str,
    profile: dict,
    port: int = 22,
    platform: str = "linux",
) -> int:
    """Aplica perfil a um target environment (cria ou atualiza)."""
    existing = con.execute(
        "SELECT id FROM target_environments WHERE env_id=?",
        (env_id,),
    ).fetchone()

    if existing:
        con.execute(
            """UPDATE target_environments SET
               name=?, host=?, port=?, platform=?,
               gateway_required=?, direct_ssh_policy=?, capture_start_mode=?,
               capture_compliance_mode=?, allow_admin_direct_access=?,
               description=?, updated_at_ms=?
               WHERE env_id=?""",
            (
                name, host, port, platform,
                1 if profile.get("gateway_required") else 0,
                profile.get("direct_ssh_policy", "unrestricted"),
                profile.get("capture_start_mode", "manual"),
                profile.get("capture_compliance_mode", "off"),
                1 if profile.get("allow_admin_direct_access") else 0,
                profile.get("description", ""),
                now_ms(),
                env_id,
            ),
        )
        return existing["id"]

    rid = con.execute(
        """INSERT INTO target_environments(
            env_id, name, host, port, platform, transport_hint,
            gateway_required, direct_ssh_policy, capture_start_mode,
            capture_compliance_mode, allow_admin_direct_access,
            description, metadata_json, created_at_ms, updated_at_ms
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            env_id,
            name,
            host,
            port,
            platform,
            "ssh",
            1 if profile.get("gateway_required") else 0,
            profile.get("direct_ssh_policy", "unrestricted"),
            profile.get("capture_start_mode", "manual"),
            profile.get("capture_compliance_mode", "off"),
            1 if profile.get("allow_admin_direct_access") else 0,
            profile.get("description", ""),
            json.dumps({}, ensure_ascii=False),
            now_ms(),
            now_ms(),
        ),
    ).lastrowid
    con.commit()
    return rid


def main():
    import argparse
    import sys
    import os

    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
    from dakota_gateway.state_db import connect, init_db, default_db_path

    ap = argparse.ArgumentParser(description="Aplica perfil lab/producao a target environment")
    ap.add_argument("profile", choices=["lab", "production", "homologation", "laboratorio", "producao", "homologacao"],
                    help="Perfil a aplicar")
    ap.add_argument("--name", required=True, help="Nome do ambiente")
    ap.add_argument("--host", required=True, help="Hostname ou IP")
    ap.add_argument("--env-id", default="", help="ID do ambiente (default: nome lowercased)")
    ap.add_argument("--port", type=int, default=22, help="Porta SSH (default: 22)")
    ap.add_argument("--platform", default="linux", help="Plataforma (default: linux)")
    ap.add_argument("--db", default="", help="Caminho do banco SQLite")
    args = ap.parse_args()

    db_path = args.db or default_db_path()
    con = connect(db_path)
    init_db(con)

    profile = get_profile(args.profile)
    if not profile:
        print(f"Perfil '{args.profile}' nao encontrado", file=sys.stderr)
        sys.exit(2)

    env_id = args.env_id or args.name.lower().replace(" ", "-")
    rid = apply_profile(con, env_id, args.name, args.host, profile, port=args.port, platform=args.platform)
    con.close()

    print(json.dumps({
        "id": rid,
        "env_id": env_id,
        "name": args.name,
        "host": args.host,
        "profile": args.profile,
        "gateway_required": profile.get("gateway_required"),
        "direct_ssh_policy": profile.get("direct_ssh_policy"),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

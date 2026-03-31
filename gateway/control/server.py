#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import platform
import re
import secrets
import sqlite3
import subprocess
import shutil
import time
import threading
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from dakota_gateway import auth
from dakota_gateway.compliance import (
    normalize_direct_ssh_policy_payload,
    normalize_target_policy,
)
from dakota_gateway.replay_control import (
    Runner,
    query_all,
    query_one,
    retry_run,
)
from dakota_gateway.state_db import connect, exec1, init_db, now_ms
from dakota_gateway.state_db import ConnectionPool
from control.routes import (
    handle_admin_get_route,
    handle_admin_post_route,
    handle_capture_get_route,
    handle_capture_post_route,
    handle_catalog_get_route,
    handle_catalog_post_route,
    handle_gateway_get_route,
    handle_gateway_post_route,
    handle_observability_delete_route,
    handle_observability_get_route,
    handle_observability_post_route,
    handle_operational_delete_route,
    handle_operational_get_route,
    handle_operational_post_route,
    handle_run_get_route,
    handle_run_post_route,
    handle_ui_get_route,
)
from control.services.environment_service import (
    list_target_environments as _list_target_environments,
    resolve_run_target_request as _resolve_run_target_request,
)
from control.services.gateway_observability_service import (
    read_gateway_monitor as _read_gateway_monitor,
    read_gateway_session_detail as _read_gateway_session_detail,
    read_gateway_sessions as _read_gateway_sessions,
)
from control.services.scenario_service import (
    delete_analytics_scenario as _delete_analytics_scenario,
    delete_operational_scenario as _delete_operational_scenario,
    instantiate_run_from_scenario as _instantiate_run_from_scenario,
    list_analytics_scenarios as _list_analytics_scenarios,
    list_operational_scenarios as _list_operational_scenarios,
    save_analytics_scenario as _save_analytics_scenario,
    save_operational_scenario as _save_operational_scenario,
    set_analytics_scenario_favorite as _set_analytics_scenario_favorite,
    set_operational_scenario_favorite as _set_operational_scenario_favorite,
)
from control.services.report_service import (
    build_observability_overview as _build_observability_overview,
    build_reprocess_analytics as _build_reprocess_analytics,
    build_reprocess_trace as _build_reprocess_trace,
    build_run_family as _build_run_family,
    build_run_comparison as _build_run_comparison,
    build_run_report as _build_run_report,
    build_runs_trend_report as _build_runs_trend_report,
    create_reprocess_run_from_failure as _create_reprocess_run_from_failure,
    report_to_csv as _report_to_csv,
    report_to_markdown as _report_to_markdown,
)
from control.services.run_service import (
    export_run_report_payload as _export_run_report_payload,
    get_run_comparison_payload as _get_run_comparison_payload,
    get_run_compliance_payload as _get_run_compliance_payload,
    get_run_events_payload as _get_run_events_payload,
    get_run_failures_payload as _get_run_failures_payload,
    get_run_report_payload as _get_run_report_payload,
)
from control.services.capture_service import interrupt_stale_captures as _interrupt_stale_captures


def _is_weak_password(password: str) -> bool:
  p = (password or "").strip()
  if len(p) < 8:
    return True
  lower = p.lower()
  common = {
    "admin123",
    "password",
    "password123",
    "12345678",
    "qwerty123",
    "dakota123",
  }
  if lower in common:
    return True
  if lower.startswith("admin") and len(lower) <= 10:
    return True
  return False

def _read_json(req: BaseHTTPRequestHandler) -> dict:
    ln = int(req.headers.get("Content-Length") or "0")
    data = req.rfile.read(ln) if ln else b"{}"
    try:
        d = json.loads(data.decode("utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}

def _run_cmd(cmd: list[str]) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        out = (p.stdout or "") + ("\n" + p.stderr if p.stderr else "")
        return p.returncode, out.strip()
    except FileNotFoundError:
        return 127, f"comando não encontrado: {cmd[0]}"
    except subprocess.TimeoutExpired:
        return 124, "timeout executando comando"
    except Exception as exc:
        return 1, str(exc)


def _env_bool(name: str, default: bool = False) -> bool:
    raw = str(os.environ.get(name, "")).strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "y", "on", "sim"}


def _linux_find_systemd_unit(candidates: tuple[str, ...]) -> str | None:
    for candidate in candidates:
        rc_probe, out_probe = _run_cmd(["systemctl", "status", candidate, "--no-pager"])
        if "could not be found" not in out_probe.lower():
            return candidate
    return None


def _linux_service_is_active(unit: str) -> tuple[bool, str]:
    rc, out = _run_cmd(["systemctl", "is-active", unit])
    return out.strip() == "active" and rc == 0, out.strip() or out


def _linux_gateway_units() -> tuple[str | None, str | None]:
    service = _linux_find_systemd_unit(("sshd", "ssh"))
    socket = _linux_find_systemd_unit(("sshd.socket", "ssh.socket"))
    return service, socket


def _gateway_service_status() -> dict:
    system = platform.system().lower()

    if "aix" in system:
        if not shutil.which("lssrc"):
            return {"platform": "aix", "service": "sshd", "running": False, "available": False, "error": "lssrc não encontrado"}
        rc, out = _run_cmd(["lssrc", "-s", "sshd"])
        running = ("active" in out.lower()) and rc == 0
        return {
            "platform": "aix",
            "service": "sshd",
            "running": running,
            "available": True,
            "error": None if running else (out or "sshd inativo"),
        }

    if "linux" in system:
        if not shutil.which("systemctl"):
            return {"platform": "linux", "service": "sshd", "running": False, "available": False, "error": "systemctl não encontrado"}

        service, socket = _linux_gateway_units()
        if not service and not socket:
            return {
                "platform": "linux",
                "service": "unavailable",
                "socket": "unavailable",
                "running": False,
                "available": False,
                "error": "serviço ssh/sshd não encontrado neste host",
            }

        service_running = False
        service_state = "not-found"
        if service:
            service_running, service_state = _linux_service_is_active(service)

        socket_running = False
        socket_state = "not-found"
        if socket:
            socket_running, socket_state = _linux_service_is_active(socket)

        running = service_running or socket_running
        return {
            "platform": "linux",
            "service": service or "unavailable",
            "socket": socket or "unavailable",
            "running": running,
            "service_running": service_running,
            "socket_running": socket_running,
            "available": True,
            "error": None,
        }

    return {"platform": system or "unknown", "service": "unknown", "running": False, "available": False, "error": "sistema não suportado"}


def _gateway_toggle(enabled: bool) -> dict:
    st = _gateway_service_status()
    platform_name = st.get("platform", "")
    service = st.get("service", "sshd")
    socket = st.get("socket")

    if not st.get("available", True):
        return {**st, "error": st.get("error") or "gateway indisponível para alternância"}

    if bool(st.get("running")) == enabled and not st.get("error"):
        return {**st, "changed": False}

    if platform_name == "aix":
        cmd = ["startsrc", "-s", "sshd"] if enabled else ["stopsrc", "-s", "sshd"]
    elif platform_name == "linux":
        units = [unit for unit in (socket, service) if unit and unit != "unavailable"]
        action = "start" if enabled else "stop"
        preferred = ["sudo", "-n", "systemctl", action, *units]
        fallback = ["systemctl", action, *units]
        rc, out = _run_cmd(preferred)
        if rc != 0 and not out:
            out = "falha executando systemctl"
        if rc != 0 and ("password is required" in out.lower() or "a password is required" in out.lower()):
            out = "permissão negada: configure sudo sem senha para controlar o gateway"
        elif rc != 0 and "sudo:" in out.lower():
            out = f"sudo falhou: {out}"
        if rc != 0 and ("not in the sudoers" in out.lower() or "permission denied" in out.lower()):
            out = "permissão negada: configure sudo sem senha para controlar o gateway"
        if rc != 0 and "sudo" in preferred[0]:
            rc, direct_out = _run_cmd(fallback)
            if rc == 0:
                out = direct_out
            elif direct_out:
                out = direct_out
        new_state = _gateway_service_status()
        if rc != 0 or new_state.get("running") != enabled:
            new_state["error"] = out or new_state.get("error") or "falha ao alterar estado do gateway"
        return new_state
    else:
        return {**st, "error": st.get("error") or "plataforma não suportada para toggle"}

    rc, out = _run_cmd(cmd)
    new_state = _gateway_service_status()
    if rc != 0 or new_state.get("running") != enabled:
        new_state["error"] = out or new_state.get("error") or "falha ao alterar estado do gateway"
    return new_state


class _Port22CaptureSampler:
    def __init__(self):
        self._thread = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._capture = None
        self._seq = 0
        self._seen = set()
        self._file_path = ""

    def start(self, capture: dict | None) -> dict:
        if not capture:
            return {"started": False, "reason": "capture ausente"}
        with self._lock:
            self.stop()
            self._capture = dict(capture)
            log_dir = str(self._capture.get("log_dir") or "").strip()
            session_uuid = str(self._capture.get("session_uuid") or "").strip()
            if not log_dir or not session_uuid:
                return {"started": False, "reason": "capture sem log_dir/session_uuid"}
            os.makedirs(log_dir, exist_ok=True)
            self._seq = 0
            self._seen = set()
            self._file_path = os.path.join(log_dir, f"audit-{time.strftime('%Y%m%d-%H%M%S')}.part001.jsonl")
            self._stop.clear()
            self._emit("session_start")
            self._thread = threading.Thread(target=self._loop, daemon=True)
            self._thread.start()
            return {"started": True, "file": self._file_path}

    def stop(self) -> dict:
        if self._thread is None:
            return {"stopped": False, "reason": "sampler inativo"}
        self._stop.set()
        self._thread.join(timeout=2)
        self._thread = None
        self._emit("session_end")
        self._capture = None
        self._file_path = ""
        return {"stopped": True}

    def _emit(self, event_type: str, **extra) -> None:
        capture = self._capture or {}
        if not self._file_path:
            return
        self._seq += 1
        payload = {
            "v": "v1",
            "seq_global": self._seq,
            "ts_ms": int(time.time() * 1000),
            "type": event_type,
            "actor": "gateway",
            "session_id": str(capture.get("session_uuid") or ""),
            "seq_session": self._seq,
            "capture_id": capture.get("id"),
            "source_port": 22,
        }
        payload.update(extra)
        try:
            with open(self._file_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
        except Exception:
            return

    def _sample_established_ssh(self) -> set[tuple[str, str]]:
        rc, out = _run_cmd(["ss", "-tn", "state", "established", "sport", "=", ":22"])
        if rc != 0 or not out:
            return set()
        conns = set()
        lines = [line.strip() for line in out.splitlines() if line.strip()]
        for line in lines[1:]:
            parts = line.split()
            if len(parts) < 4:
                continue
            local = parts[-2]
            peer = parts[-1]
            conns.add((local, peer))
        return conns

    def _loop(self) -> None:
        while not self._stop.wait(1.0):
            current = self._sample_established_ssh()
            opened = current - self._seen
            closed = self._seen - current
            for local, peer in sorted(opened):
                self._emit("port22_connection_open", local=local, peer=peer)
            for local, peer in sorted(closed):
                self._emit("port22_connection_close", local=local, peer=peer)
            self._seen = current


class _RuntimeContentCaptureRunner:
    def __init__(self, *, project_root: str, hmac_key_file: str = ""):
        self._project_root = project_root
        self._hmac_key_file = str(hmac_key_file or "").strip()
        self._lock = threading.Lock()
        self._proc = None
        self._capture_id = None
        self._log_handle = None

    def _resolve_runtime_config(self, body: dict) -> dict:
        payload = body.get("runtime_capture") if isinstance(body, dict) else None
        cfg = payload if isinstance(payload, dict) else {}

        enabled = bool(cfg.get("enabled", _env_bool("DAKOTA_RUNTIME_CAPTURE_ENABLED", False)))
        if not enabled:
            return {"enabled": False, "reason": "runtime desabilitado"}

        source_host = str(cfg.get("source_host") or os.environ.get("DAKOTA_RUNTIME_SOURCE_HOST") or "").strip()
        source_user = str(cfg.get("source_user") or os.environ.get("DAKOTA_RUNTIME_SOURCE_USER") or "").strip()
        source_command = str(cfg.get("source_command") or os.environ.get("DAKOTA_RUNTIME_SOURCE_COMMAND") or "").strip()
        ssh_batch_mode = str(cfg.get("ssh_batch_mode") or os.environ.get("DAKOTA_RUNTIME_SSH_BATCH_MODE") or "yes").strip().lower()
        if ssh_batch_mode not in {"yes", "no"}:
            ssh_batch_mode = "yes"

        if not source_host:
            return {"enabled": False, "reason": "runtime sem source_host"}
        if not source_command:
            return {"enabled": False, "reason": "runtime sem source_command"}

        return {
            "enabled": True,
            "source_host": source_host,
            "source_user": source_user,
            "source_command": source_command,
            "ssh_batch_mode": ssh_batch_mode,
            "gateway_endpoint": str(cfg.get("gateway_endpoint") or os.environ.get("DAKOTA_RUNTIME_GATEWAY_ENDPOINT") or "").strip(),
        }

    def start(self, capture: dict | None, body: dict | None = None) -> dict:
        if not capture:
            return {"started": False, "reason": "capture ausente"}
        runtime_cfg = self._resolve_runtime_config(body or {})
        if not runtime_cfg.get("enabled"):
            return {"started": False, "reason": runtime_cfg.get("reason") or "runtime desabilitado"}
        if not self._hmac_key_file:
            return {"started": False, "reason": "hmac_key_file ausente"}

        log_dir = str(capture.get("log_dir") or "").strip()
        if not log_dir:
            return {"started": False, "reason": "capture sem log_dir"}
        os.makedirs(log_dir, exist_ok=True)

        gateway_wrapper = os.path.join(self._project_root, "gateway", "dakota-gateway")
        if not os.path.exists(gateway_wrapper):
            return {"started": False, "reason": "wrapper dakota-gateway não encontrado"}

        cmd = [
            "python3",
            gateway_wrapper,
            "start",
            "--log-dir",
            log_dir,
            "--hmac-key-file",
            self._hmac_key_file,
            "--source-host",
            runtime_cfg["source_host"],
            "--source-command",
            runtime_cfg["source_command"],
            "--ssh-batch-mode",
            runtime_cfg["ssh_batch_mode"],
        ]
        if runtime_cfg["source_user"]:
            cmd += ["--source-user", runtime_cfg["source_user"]]
        if runtime_cfg["gateway_endpoint"]:
            cmd += ["--gateway-endpoint", runtime_cfg["gateway_endpoint"]]

        self.stop()
        with self._lock:
            runtime_log_path = os.path.join(log_dir, "runtime-capture.log")
            self._log_handle = open(runtime_log_path, "a", encoding="utf-8")
            self._proc = subprocess.Popen(
                cmd,
                cwd=self._project_root,
                stdout=self._log_handle,
                stderr=self._log_handle,
                close_fds=True,
            )
            self._capture_id = capture.get("id")
            return {
                "started": True,
                "pid": int(self._proc.pid),
                "capture_id": self._capture_id,
                "mode": "runtime_content",
                "log": runtime_log_path,
            }

    def stop(self) -> dict:
        with self._lock:
            if self._proc is None:
                return {"stopped": False, "reason": "runtime inativo"}
            proc = self._proc
            self._proc = None
            capture_id = self._capture_id
            self._capture_id = None
            try:
                if proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait(timeout=2)
            finally:
                if self._log_handle is not None:
                    try:
                        self._log_handle.flush()
                    except Exception:
                        pass
                    try:
                        self._log_handle.close()
                    except Exception:
                        pass
                    self._log_handle = None
            return {
                "stopped": True,
                "capture_id": capture_id,
                "returncode": proc.returncode,
            }


class ControlServer(ThreadingHTTPServer):
    def __init__(
        self,
        addr,
        handler,
        *,
        db_path: str,
        cookie_secret: bytes,
        hmac_key: bytes,
        capture_log_dir: str = "",
        hmac_key_file: str = "",
    ):
        super().__init__(addr, handler)
        self.db_path = db_path
        self.cookie_secret = cookie_secret
        self.hmac_key = hmac_key
        self.capture_log_dir = capture_log_dir or os.path.join(os.path.dirname(db_path), "captures")
        os.makedirs(self.capture_log_dir, exist_ok=True)
        self.db_pool = ConnectionPool(db_path, min_size=1, max_size=16)
        con = self.db_pool.acquire()
        try:
            init_db(con)
            stale = _interrupt_stale_captures(con, now_ms_fn=now_ms)
            if stale:
                print(f"[startup] {stale} captura(s) ativa(s) marcada(s) como interrupted (processo anterior encerrado)")
        finally:
            self.db_pool.release(con)
        self.runner = Runner(db_path, hmac_key)
        self.port22_sampler = _Port22CaptureSampler()
        self.runtime_capture = _RuntimeContentCaptureRunner(
            project_root=str(Path(__file__).resolve().parents[2]),
            hmac_key_file=hmac_key_file,
        )


class Handler(BaseHTTPRequestHandler):
    def _db(self):
        return self.server.db_pool.acquire()

    def _db_release(self, con):
        self.server.db_pool.release(con)

    def _set_cookie(self, name: str, value: str, max_age: int = 3600 * 12):
        c = SimpleCookie()
        c[name] = value
        c[name]["path"] = "/"
        c[name]["max-age"] = str(max_age)
        # internal HTTP (no TLS) by default; don't set secure automatically
        self.send_header("Set-Cookie", c.output(header="").strip())

    def _clear_cookie(self, name: str):
        c = SimpleCookie()
        c[name] = ""
        c[name]["path"] = "/"
        c[name]["max-age"] = "0"
        self.send_header("Set-Cookie", c.output(header="").strip())

    def _get_cookie(self, name: str) -> str | None:
        raw = self.headers.get("Cookie") or ""
        c = SimpleCookie()
        c.load(raw)
        if name not in c:
            return None
        return c[name].value

    def _auth(self):
      cv = self._get_cookie("dakota_session")
      if not cv:
        return None
      parsed = auth.verify_cookie(self.server.cookie_secret, cv)
      if not parsed:
        return None

      username, token, _exp = parsed
      token_hash = auth.sha256_hex(token.encode("utf-8"))
      con = self._db()
      try:
        try:
          row = query_one(
            con,
            "SELECT u.id,u.username,u.role,s.expires_at_ms "
            "FROM users u JOIN sessions s ON s.user_id=u.id "
            "WHERE u.username=? AND s.token_hash=? "
            "ORDER BY s.id DESC LIMIT 1",
            (username, token_hash),
          )
        except sqlite3.OperationalError as exc:
          # Se o DB foi removido em runtime, recria schema e trata como sessão inválida.
          if "no such table" in str(exc).lower():
            init_db(con)
            return None
          raise
        if not row:
          return None
        if int(row["expires_at_ms"]) < int(time.time() * 1000):
          return None
        return {"id": int(row["id"]), "username": row["username"], "role": row["role"]}
      finally:
        self._db_release(con)

    def _require(self, roles: set[str] | None = None):
        u = self._auth()
        if not u:
            self.send_response(HTTPStatus.UNAUTHORIZED)
            self.end_headers()
            return None
        if roles and u["role"] not in roles:
            self.send_response(HTTPStatus.FORBIDDEN)
            self.end_headers()
            return None
        return u

    def do_GET(self):
        p = urlparse(self.path)
        if handle_ui_get_route(self, p):
            return
        if p.path == "/api/me":
            u = self._require()
            if not u:
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps(u).encode("utf-8"))
            return
        if handle_admin_get_route(self, p, gateway_service_status=_gateway_service_status, query_all_fn=query_all):
            return
        if handle_catalog_get_route(self, p):
            return
        if handle_observability_get_route(self, p):
            return
        if handle_operational_get_route(self, p, parse_qs):
            return
        if handle_gateway_get_route(
            self,
            p,
            parse_qs_fn=parse_qs,
            query_one_fn=query_one,
            read_gateway_monitor_fn=_read_gateway_monitor,
            read_gateway_sessions_fn=_read_gateway_sessions,
            read_gateway_session_detail_fn=_read_gateway_session_detail,
        ):
            return
        if handle_capture_get_route(
            self,
            p,
            parse_qs_fn=parse_qs,
            read_gateway_monitor_fn=_read_gateway_monitor,
        ):
            return
        if handle_run_get_route(self, p):
            return

        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        p = urlparse(self.path)
        body = _read_json(self)
        if handle_admin_post_route(
            self,
            p,
            body,
            auth_module=auth,
            query_one_fn=query_one,
            now_ms_fn=now_ms,
            gateway_toggle_fn=_gateway_toggle,
        ):
            return

        if handle_gateway_post_route(
            self,
            p,
            body,
            now_ms_fn=now_ms,
            capture_log_dir=self.server.capture_log_dir,
            start_port22_capture_fn=self.server.port22_sampler.start,
            stop_port22_capture_fn=self.server.port22_sampler.stop,
            start_runtime_capture_fn=self.server.runtime_capture.start,
            stop_runtime_capture_fn=self.server.runtime_capture.stop,
        ):
            return
        if handle_capture_post_route(
            self,
            p,
            body,
            now_ms_fn=now_ms,
            log_dir_base=self.server.capture_log_dir,
        ):
            return
        if handle_run_post_route(self, p, body):
            return
        if handle_observability_post_route(self, p, body):
            return
        if handle_catalog_post_route(self, p, body):
            return
        if handle_operational_post_route(self, p, body):
            return

        self.send_response(404)
        self.end_headers()

    def do_DELETE(self):
        p = urlparse(self.path)
        if handle_observability_delete_route(self, p):
            return
        if handle_operational_delete_route(self, p):
            return

        self.send_response(404)
        self.end_headers()

    def log_message(self, fmt, *args):
        return


def main():
        ap = argparse.ArgumentParser()
        ap.add_argument("--listen", default="127.0.0.1:8090")
        ap.add_argument("--db", default="")
        ap.add_argument("--capture-log-dir", default="")
        ap.add_argument("--cookie-secret-file", required=True)
        ap.add_argument("--hmac-key-file", required=True)
        ap.add_argument("--bootstrap-admin", default="")  # username:password
        args = ap.parse_args()

        host, port_s = args.listen.rsplit(":", 1)
        port = int(port_s)
        db_path = args.db or (Path(__file__).resolve().parents[1] / "state" / "replay.db")
        db_path = str(db_path)

        cookie_secret = Path(args.cookie_secret_file).read_bytes().strip()
        hmac_key = Path(args.hmac_key_file).read_bytes().strip()
        if not cookie_secret:
                raise SystemExit("cookie secret vazio")
        if not hmac_key:
                raise SystemExit("hmac key vazio")

        env_admin = os.environ.get("DAKOTA_ADMIN", "").strip()
        bootstrap_admin = (args.bootstrap_admin or env_admin).strip()
        if args.bootstrap_admin and env_admin:
                print("Aviso: DAKOTA_ADMIN foi ignorada porque --bootstrap-admin foi informado.")

        con = connect(db_path)
        init_db(con)
        print(f"Banco inicializado: {db_path}")

        existing_admin = con.execute(
                "SELECT username FROM users WHERE role='admin' ORDER BY id LIMIT 1"
        ).fetchone()
        if existing_admin:
                print(f"Admin já existente: {existing_admin['username']}")
        elif bootstrap_admin:
                if ":" not in bootstrap_admin:
                        raise SystemExit("bootstrap admin deve ser username:password (via --bootstrap-admin ou DAKOTA_ADMIN)")
                u, p = bootstrap_admin.split(":", 1)
                u = u.strip()
                if not u:
                        raise SystemExit("bootstrap admin inválido: username vazio")
                if _is_weak_password(p):
                        print("Aviso: senha de bootstrap parece fraca. Use uma senha forte em produção.")
                ph = auth.pbkdf2_hash_password(p)
                con.execute(
                        "INSERT INTO users(username,password_hash,role,created_at_ms) VALUES(?,?, 'admin', ?)",
                        (u, ph, now_ms()),
                )
                print(f"Admin criado: {u}")
        else:
                print("Admin não criado: informe --bootstrap-admin ou DAKOTA_ADMIN para bootstrap inicial.")
        con.close()

        srv = ControlServer(
                (host, port),
                Handler,
                db_path=db_path,
                cookie_secret=cookie_secret,
                hmac_key=hmac_key,
                capture_log_dir=args.capture_log_dir,
            hmac_key_file=args.hmac_key_file,
        )
        print(f"listening on http://{host}:{port}")
        srv.serve_forever()


if __name__ == "__main__":
    main()

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
from http.server import BaseHTTPRequestHandler


def is_weak_password(password: str) -> bool:
    value = (password or "").strip()
    if len(value) < 8:
        return True
    lower = value.lower()
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


def read_json(req: BaseHTTPRequestHandler) -> dict:
    ln = int(req.headers.get("Content-Length") or "0")
    data = req.rfile.read(ln) if ln else b"{}"
    try:
        parsed = json.loads(data.decode("utf-8"))
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def run_cmd(cmd: list[str]) -> tuple[int, str]:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        out = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
        return proc.returncode, out.strip()
    except FileNotFoundError:
        return 127, f"comando não encontrado: {cmd[0]}"
    except subprocess.TimeoutExpired:
        return 124, "timeout executando comando"
    except Exception as exc:
        return 1, str(exc)


def _linux_find_systemd_unit(candidates: tuple[str, ...], *, run_cmd_fn) -> str | None:
    for candidate in candidates:
        _rc_probe, out_probe = run_cmd_fn(["systemctl", "status", candidate, "--no-pager"])
        if "could not be found" not in out_probe.lower():
            return candidate
    return None


def _linux_service_is_active(unit: str, *, run_cmd_fn) -> tuple[bool, str]:
    rc, out = run_cmd_fn(["systemctl", "is-active", unit])
    return out.strip() == "active" and rc == 0, out.strip() or out


def _linux_gateway_units(*, run_cmd_fn) -> tuple[str | None, str | None]:
    service = _linux_find_systemd_unit(("sshd", "ssh"), run_cmd_fn=run_cmd_fn)
    socket = _linux_find_systemd_unit(("sshd.socket", "ssh.socket"), run_cmd_fn=run_cmd_fn)
    return service, socket


def gateway_service_status(*, run_cmd_fn) -> dict:
    system = platform.system().lower()

    if "aix" in system:
        if not shutil.which("lssrc"):
            return {"platform": "aix", "service": "sshd", "running": False, "available": False, "error": "lssrc não encontrado"}
        rc, out = run_cmd_fn(["lssrc", "-s", "sshd"])
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

        service, socket = _linux_gateway_units(run_cmd_fn=run_cmd_fn)
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
        if service:
            service_running, _service_state = _linux_service_is_active(service, run_cmd_fn=run_cmd_fn)

        socket_running = False
        if socket:
            socket_running, _socket_state = _linux_service_is_active(socket, run_cmd_fn=run_cmd_fn)

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


def gateway_toggle(enabled: bool, *, run_cmd_fn, service_status_fn) -> dict:
    st = service_status_fn()
    platform_name = st.get("platform", "")
    service = st.get("service", "sshd")
    socket = st.get("socket")

    if not st.get("available", True):
        return {**st, "error": st.get("error") or "gateway indisponível para alternância"}

    if bool(st.get("running")) == enabled and not st.get("error"):
        return {**st, "changed": False}

    if platform_name == "aix":
        cmd = ["startsrc", "-s", "sshd"] if enabled else ["stopsrc", "-s", "sshd"]
        rc, out = run_cmd_fn(cmd)
        new_state = service_status_fn()
        if rc != 0 or new_state.get("running") != enabled:
            new_state["error"] = out or new_state.get("error") or "falha ao alterar estado do gateway"
        return new_state

    if platform_name == "linux":
        units = [unit for unit in (socket, service) if unit and unit != "unavailable"]
        action = "start" if enabled else "stop"
        preferred = ["sudo", "-n", "systemctl", action, *units]
        fallback = ["systemctl", action, *units]
        rc, out = run_cmd_fn(preferred)
        if rc != 0 and not out:
            out = "falha executando systemctl"
        if rc != 0 and ("password is required" in out.lower() or "a password is required" in out.lower()):
            out = "permissão negada: configure sudo sem senha para controlar o gateway"
        elif rc != 0 and "sudo:" in out.lower():
            out = f"sudo falhou: {out}"
        if rc != 0 and ("not in the sudoers" in out.lower() or "permission denied" in out.lower()):
            out = "permissão negada: configure sudo sem senha para controlar o gateway"
        if rc != 0 and "sudo" in preferred[0]:
            rc, direct_out = run_cmd_fn(fallback)
            if rc == 0:
                out = direct_out
            elif direct_out:
                out = direct_out
        new_state = service_status_fn()
        if rc != 0 or new_state.get("running") != enabled:
            new_state["error"] = out or new_state.get("error") or "falha ao alterar estado do gateway"
        return new_state

    return {**st, "error": st.get("error") or "plataforma não suportada para toggle"}

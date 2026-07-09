from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from pathlib import Path

from control.services.capture_service import (
    ensure_active_capture_for_gateway,
    interrupt_stale_captures,
)


def env_bool(name: str, default: bool = False) -> bool:
    raw = str(os.environ.get(name, "")).strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "y", "on", "sim"}


def reconcile_gateway_capture_startup(
    con,
    *,
    capture_log_dir: str,
    now_ms_fn,
) -> dict:
    stale = interrupt_stale_captures(con, now_ms_fn=now_ms_fn)
    resumed = ensure_active_capture_for_gateway(
        con,
        log_dir_base=capture_log_dir,
        now_ms_fn=now_ms_fn,
    )
    return {
        "stale_captures_interrupted": int(stale or 0),
        "resumed_capture": resumed,
    }


class Port22CaptureSampler:
    def __init__(self, *, run_cmd):
        self._run_cmd = run_cmd
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

    @staticmethod
    def _looks_like_endpoint(value: str) -> bool:
        text = str(value or "").strip()
        if not text or ":" not in text:
            return False
        host, _, port = text.rpartition(":")
        if not host or not port.isdigit():
            return False
        if "address" in host.lower() or "process" in text.lower():
            return False
        return True

    @staticmethod
    def _decode_proc_net_hex_endpoint(value: str) -> str | None:
        text = str(value or "").strip()
        if ":" not in text:
            return None
        host_hex, port_hex = text.split(":", 1)
        try:
            host_bytes = bytes.fromhex(host_hex)
            port = int(port_hex, 16)
        except Exception:
            return None
        if len(host_bytes) == 4:
            host = ".".join(str(part) for part in host_bytes[::-1])
            return f"{host}:{port}"
        return None

    def _sample_proc_net_tcp(self) -> set[tuple[str, str]]:
        file_path = "/proc/net/tcp"
        if not os.path.exists(file_path):
            return set()
        conns = set()
        try:
            with open(file_path, "r", encoding="utf-8", errors="replace") as fh:
                lines = [line.strip() for line in fh.readlines()[1:] if line.strip()]
        except Exception:
            return set()

        for line in lines:
            parts = line.split()
            if len(parts) < 4:
                continue
            local = self._decode_proc_net_hex_endpoint(parts[1])
            peer = self._decode_proc_net_hex_endpoint(parts[2])
            state = parts[3]
            if state != "01" or not local or not peer:
                continue
            if not local.endswith(":22"):
                continue
            conns.add((local, peer))
        return conns

    def _sample_established_ssh(self) -> set[tuple[str, str]]:
        rc, out = self._run_cmd(["ss", "-tn", "state", "established", "sport", "=", ":22"])
        if rc != 0 or not out:
            return self._sample_proc_net_tcp()
        conns = set()
        lines = [line.strip() for line in out.splitlines() if line.strip()]
        for line in lines[1:]:
            parts = line.split()
            if len(parts) < 4:
                continue
            local = parts[-2]
            peer = parts[-1]
            if not self._looks_like_endpoint(local) or not self._looks_like_endpoint(peer):
                continue
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


class RuntimeContentCaptureRunner:
    def __init__(
        self,
        *,
        project_root: str,
        hmac_key_file: str = "",
        env_bool_fn=env_bool,
    ):
        self._project_root = project_root
        self._hmac_key_file = str(hmac_key_file or "").strip()
        self._env_bool = env_bool_fn
        self._lock = threading.Lock()
        self._proc = None
        self._capture_id = None
        self._log_handle = None

    def _resolve_runtime_config(self, body: dict) -> dict:
        payload = body.get("runtime_capture") if isinstance(body, dict) else None
        cfg = payload if isinstance(payload, dict) else {}

        enabled = bool(cfg.get("enabled", self._env_bool("DAKOTA_RUNTIME_CAPTURE_ENABLED", False)))
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


def default_project_root_from_file(file_path: str) -> str:
    return str(Path(file_path).resolve().parents[2])

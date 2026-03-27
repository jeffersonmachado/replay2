from __future__ import annotations

import base64
import json
import os
import time
from dataclasses import asdict
from pathlib import Path
from typing import Optional

import fcntl

from .canonical import canonical_string
from .crypto import sha256_hex, hmac_sha256_hex
from .schema import AuditEvent


class AuditWriter:
    """
    Global append-only writer with:
    - global total order (seq_global) across all processes
    - hash-chain + HMAC
    - optional rotation + manifest

    It uses a single lock in log_dir, so multiple gateway processes can safely append.
    """

    def __init__(self, log_dir: str, hmac_key: bytes, rotate_bytes: int = 0):
        self.log_dir = Path(log_dir)
        self.hmac_key = hmac_key
        self.rotate_bytes = int(rotate_bytes)
        self.log_dir.mkdir(parents=True, exist_ok=True)

        self.lock_path = self.log_dir / "audit.lock"
        self.state_path = self.log_dir / "audit.state"

        self._lock_fd = open(self.lock_path, "a+", encoding="utf-8")

    def close(self):
        try:
            self._lock_fd.close()
        except Exception:
            pass

    def _load_state_locked(self) -> dict:
        if not self.state_path.exists():
            return {"seq_global": 0, "prev_hash": "", "current_log": "", "part": 0}
        d = {"seq_global": 0, "prev_hash": "", "current_log": "", "part": 0}
        for ln in self.state_path.read_text(encoding="utf-8", errors="replace").splitlines():
            ln = ln.strip()
            if not ln or "=" not in ln:
                continue
            k, v = ln.split("=", 1)
            if k == "seq_global":
                d[k] = int(v or "0")
            elif k == "part":
                d[k] = int(v or "0")
            else:
                d[k] = v
        return d

    def _save_state_locked(self, st: dict) -> None:
        tmp = self.state_path.with_suffix(".tmp")
        content = (
            f"seq_global={st.get('seq_global', 0)}\n"
            f"prev_hash={st.get('prev_hash', '')}\n"
            f"current_log={st.get('current_log', '')}\n"
            f"part={st.get('part', 0)}\n"
        )
        tmp.write_text(content, encoding="utf-8")
        os.replace(tmp, self.state_path)

    def _current_log_path_locked(self, st: dict) -> Path:
        cur = st.get("current_log") or ""
        if cur:
            return Path(cur)
        ts = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
        st["part"] = 1
        cur = str(self.log_dir / f"audit-{ts}.part{st['part']:03d}.jsonl")
        st["current_log"] = cur
        return Path(cur)

    def _maybe_rotate_locked(self, st: dict) -> None:
        if self.rotate_bytes <= 0:
            return
        path = self._current_log_path_locked(st)
        if not path.exists():
            return
        if path.stat().st_size < self.rotate_bytes:
            return

        # finalize manifest for current file
        write_manifest(str(path))

        base = path.name
        prefix = base.split(".part")[0]
        st["part"] = int(st.get("part") or 0) + 1
        st["current_log"] = str(self.log_dir / f"{prefix}.part{st['part']:03d}.jsonl")

    def append(self, ev: AuditEvent) -> AuditEvent:
        ev.v = "v1"

        fcntl.flock(self._lock_fd.fileno(), fcntl.LOCK_EX)
        try:
            st = self._load_state_locked()
            self._maybe_rotate_locked(st)
            log_path = self._current_log_path_locked(st)

            st["seq_global"] = int(st.get("seq_global", 0)) + 1
            ev.seq_global = st["seq_global"]
            ev.prev_hash = st.get("prev_hash", "") or ""

            payload = canonical_string(ev).encode("utf-8")
            ev.hash = sha256_hex(payload)
            ev.hmac = hmac_sha256_hex(self.hmac_key, payload)

            log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(log_path, "a", encoding="utf-8") as out:
                out.write(json.dumps(asdict(ev), ensure_ascii=False) + "\n")

            st["prev_hash"] = ev.hash
            self._save_state_locked(st)
            return ev
        finally:
            fcntl.flock(self._lock_fd.fileno(), fcntl.LOCK_UN)


def write_manifest(jsonl_path: str) -> None:
    p = Path(jsonl_path)
    if not p.exists():
        return

    file_sha = sha256_hex(p.read_bytes())

    seq_start = 0
    seq_end = 0
    first_hash = ""
    last_hash = ""
    with open(p, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except Exception:
                continue
            if not isinstance(ev, dict):
                continue
            sg = int(ev.get("seq_global") or 0)
            h = ev.get("hash") or ""
            if seq_start == 0 and sg:
                seq_start = sg
                first_hash = h
            if sg:
                seq_end = sg
                last_hash = h

    manifest = {
        "path": p.name,
        "bytes": p.stat().st_size,
        "seq_start": seq_start,
        "seq_end": seq_end,
        "first_hash": first_hash,
        "last_hash": last_hash,
        "file_sha256": file_sha,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S.%fZ", time.gmtime()),
    }
    (p.with_suffix(p.suffix + ".manifest.json")).write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from .canonical import canonical_string
from .crypto import sha256_hex, hmac_sha256_hex
from .schema import AuditEvent


class VerificationError(Exception):
    pass


def _iter_jsonl_files(log_dir: str) -> list[Path]:
    p = Path(log_dir)
    files = sorted(p.glob("audit-*.jsonl"))
    return files


def verify_log(log_dir: str, hmac_key: bytes) -> None:
    """
    Verifies:
    - seq_global monotonic without gaps (across all files)
    - prev_hash chain matches
    - hash and hmac recompute matches
    - seq_session monotonic per session without gaps (best effort)
    """
    prev_hash = ""
    expected_seq_global = 1
    per_session_next = {}

    for f in _iter_jsonl_files(log_dir):
        with open(f, "r", encoding="utf-8", errors="replace") as fh:
            for ln_no, line in enumerate(fh, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except Exception as e:
                    raise VerificationError(f"{f}:{ln_no}: invalid JSON: {e}") from e
                if not isinstance(d, dict):
                    raise VerificationError(f"{f}:{ln_no}: JSON not object")

                ev = AuditEvent(**{k: d.get(k) for k in AuditEvent.__dataclass_fields__.keys() if k in d})
                # required fields
                if ev.v != "v1":
                    raise VerificationError(f"{f}:{ln_no}: unexpected v={ev.v}")
                if int(ev.seq_global) != expected_seq_global:
                    raise VerificationError(f"{f}:{ln_no}: seq_global gap: got {ev.seq_global} expected {expected_seq_global}")
                expected_seq_global += 1

                if ev.prev_hash != prev_hash:
                    raise VerificationError(f"{f}:{ln_no}: prev_hash mismatch: got {ev.prev_hash} expected {prev_hash}")

                payload = canonical_string(ev).encode("utf-8")
                want_hash = sha256_hex(payload)
                want_hmac = hmac_sha256_hex(hmac_key, payload)
                if ev.hash != want_hash:
                    raise VerificationError(f"{f}:{ln_no}: hash mismatch")
                if ev.hmac != want_hmac:
                    raise VerificationError(f"{f}:{ln_no}: hmac mismatch")

                prev_hash = ev.hash

                # per-session sequence
                sid = ev.session_id
                if sid:
                    nxt = per_session_next.get(sid, 1)
                    if int(ev.seq_session) != nxt:
                        raise VerificationError(
                            f"{f}:{ln_no}: seq_session gap for session={sid}: got {ev.seq_session} expected {nxt}"
                        )
                    per_session_next[sid] = nxt + 1


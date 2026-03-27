from __future__ import annotations

from dataclasses import asdict


_KEY_ORDER = [
    "v",
    "seq_global",
    "ts_ms",
    "type",
    "actor",
    "session_id",
    "seq_session",
    "dir",
    "n",
    "data_b64",
    "sig",
    "norm_sha256",
    "norm_len",
    "prev_hash",
]


def canonical_string(ev) -> str:
    """
    Deterministic payload used for hash-chain and HMAC.
    Missing fields are encoded as empty string.
    """
    d = asdict(ev)
    parts = []
    for k in _KEY_ORDER:
        v = d.get(k, "")
        if v is None:
            v = ""
        parts.append(f"{k}={v}\n")
    return "".join(parts)


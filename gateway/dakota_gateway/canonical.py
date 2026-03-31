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
    "capture_id",
    "capture_session_uuid",
    "dir",
    "n",
    "data_b64",
    "sig",
    "norm_sha256",
    "norm_len",
    "screen_sig",
    "screen_sample",
    "key_b64",
    "key_text",
    "key_kind",
    "input_len",
    "contains_newline",
    "contains_escape",
    "is_probable_paste",
    "is_probable_command",
    "logical_parts",
    "screen_raw_b64",
    "screen_source",
    "screen_snapshot_ts_ms",
    "screen_snapshot_age_ms",
    "source",
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

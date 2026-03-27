from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class AuditEvent:
    v: str
    seq_global: int
    ts_ms: int
    type: str
    actor: str
    session_id: str
    seq_session: int

    # bytes event
    dir: Optional[str] = None  # in|out
    data_b64: Optional[str] = None
    n: Optional[int] = None

    # checkpoint event
    sig: Optional[str] = None
    norm_sha256: Optional[str] = None
    norm_len: Optional[int] = None

    # integrity
    prev_hash: str = ""
    hash: str = ""
    hmac: str = ""


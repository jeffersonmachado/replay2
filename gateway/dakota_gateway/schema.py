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
    timestamp_ms: Optional[int] = None
    capture_id: Optional[int] = None
    capture_session_uuid: Optional[str] = None

    # audit context (user identification)
    logname: Optional[str] = None  # Login name from process
    uid: Optional[int] = None  # User ID numeric
    gid: Optional[int] = None  # Group ID numeric

    # bytes event
    dir: Optional[str] = None  # in|out
    data_b64: Optional[str] = None
    n: Optional[int] = None

    # checkpoint event
    sig: Optional[str] = None
    norm_sha256: Optional[str] = None
    norm_len: Optional[int] = None
    screen_sig: Optional[str] = None  # legado → semantic_sig
    screen_sample: Optional[str] = None
    key_b64: Optional[str] = None
    key_text: Optional[str] = None
    key_kind: Optional[str] = None
    input_len: Optional[int] = None
    contains_newline: Optional[bool] = None
    contains_escape: Optional[bool] = None
    is_probable_paste: Optional[bool] = None
    is_probable_command: Optional[bool] = None
    logical_parts: Optional[int] = None
    screen_raw_b64: Optional[str] = None
    screen_source: Optional[str] = None
    screen_snapshot_ts_ms: Optional[int] = None
    screen_snapshot_age_ms: Optional[int] = None
    source: Optional[str] = None

    # assinaturas canonicas (v0.3.19+) — TerminalEngine Python
    text_sig: Optional[str] = None
    visual_sig: Optional[str] = None
    semantic_sig: Optional[str] = None
    snapshot_version: Optional[str] = None
    signature_version: Optional[str] = None
    engine_version: Optional[str] = None
    snapshot_compact: Optional[str] = None  # JSON serializado
    diff: Optional[str] = None  # JSON serializado
    base_seq_global: Optional[int] = None
    checkpoint_seq_global: Optional[int] = None
    comparison_mode: Optional[str] = None  # visual|text|semantic|hybrid

    # assinaturas esperadas em eventos deterministicos
    expected_text_sig: Optional[str] = None
    expected_visual_sig: Optional[str] = None
    expected_semantic_sig: Optional[str] = None

    # capture compliance evidence
    entry_mode: Optional[str] = None
    via_gateway: Optional[bool] = None
    gateway_session_id: Optional[str] = None
    gateway_endpoint: Optional[str] = None
    source_host: Optional[str] = None
    source_user: Optional[str] = None
    source_command: Optional[str] = None

    # session terminal metadata (v0.3.14+)
    rows: Optional[int] = None
    cols: Optional[int] = None
    term: Optional[str] = None
    encoding: Optional[str] = None
    geometry_source: Optional[str] = None

    # integrity
    prev_hash: str = ""
    hash: str = ""
    hmac: str = ""

    def __post_init__(self) -> None:
        if self.timestamp_ms is None:
            self.timestamp_ms = int(self.ts_ms or 0)

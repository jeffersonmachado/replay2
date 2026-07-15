from .engine import TerminalEngine
from .model import Cell
from .snapshot import (
    snapshot_from_engine,
    encode_snapshot,
    decode_snapshot,
    encode_snapshot_compact,
    decode_snapshot_compact,
)
from .serializer import serialize_text_state, serialize_visual_state
from .signatures import text_sig, visual_sig, semantic_sig
from .diffs import create_diff, apply_diff, validate_diff, estimate_diff_size

__all__ = [
    "Cell",
    "TerminalEngine",
    "snapshot_from_engine",
    "encode_snapshot",
    "decode_snapshot",
    "encode_snapshot_compact",
    "decode_snapshot_compact",
    "serialize_text_state",
    "serialize_visual_state",
    "text_sig",
    "visual_sig",
    "semantic_sig",
    "create_diff",
    "apply_diff",
    "validate_diff",
    "estimate_diff_size",
]

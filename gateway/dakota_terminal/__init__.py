from .engine import TerminalEngine
from .model import Cell
from .snapshot import (
    CanonicalSnapshot,
    RenderSnapshot,
    snapshot_from_engine,
    encode_snapshot,
    decode_snapshot,
    encode_snapshot_compact,
    decode_snapshot_compact,
    encode_canonical_snapshot,
    decode_canonical_snapshot,
    encode_render_snapshot,
    decode_render_snapshot,
)
from .serializer import serialize_text_state, serialize_visual_state
from .signatures import text_sig, visual_sig, semantic_sig
from .diffs import create_diff, apply_diff, validate_diff, estimate_diff_size
from .comparison import select_signature, compare_signatures

__all__ = [
    "Cell",
    "TerminalEngine",
    "CanonicalSnapshot",
    "RenderSnapshot",
    "snapshot_from_engine",
    "encode_snapshot",
    "decode_snapshot",
    "encode_canonical_snapshot",
    "decode_canonical_snapshot",
    "encode_render_snapshot",
    "decode_render_snapshot",
    "serialize_text_state",
    "serialize_visual_state",
    "text_sig",
    "visual_sig",
    "semantic_sig",
    "create_diff",
    "apply_diff",
    "validate_diff",
    "estimate_diff_size",
    "select_signature",
    "compare_signatures",
]

from __future__ import annotations

import codecs
import base64


SUPPORTED_ENCODINGS = {
    "utf8": "utf-8",
    "utf-8": "utf-8",
    "cp850": "cp850",
    "ibm850": "cp850",
    "cp437": "cp437",
    "ibm437": "cp437",
    "iso-8859-1": "iso-8859-1",
    "latin1": "iso-8859-1",
    "latin-1": "iso-8859-1",
    "windows-1252": "cp1252",
    "cp1252": "cp1252",
}


def normalize_encoding(encoding: str | None) -> str:
    return SUPPORTED_ENCODINGS.get(str(encoding or "utf-8").lower(), "utf-8")


class TerminalDecoder:
    def __init__(self, encoding: str = "utf-8", session_id: str = ""):
        self.encoding = normalize_encoding(encoding)
        self.session_id = session_id
        self.warnings: list[dict] = []
        self._decoder = codecs.getincrementaldecoder(self.encoding)(errors="replace")

    def _pending(self) -> bytes:
        state = getattr(self._decoder, "getstate", lambda: (b"", 0))()
        return state[0] if isinstance(state, tuple) else b""

    def _warn(self, warning_type: str, pending: bytes, *, seq_global: int, direction: str, action: str = "replacement_character") -> None:
        self.warnings.append({
            "type": warning_type,
            "session_id": self.session_id,
            "seq_global": seq_global,
            "direction": direction,
            "encoding": self.encoding,
            "bytes_b64": base64.b64encode(pending).decode("ascii"),
            "bytes_hex": pending.hex(),
            "action": action,
        })

    def decode(self, data: bytes, *, seq_global: int = 0, direction: str = "out") -> str:
        before = self._pending()
        raw = bytes(data or b"")
        text = self._decoder.decode(raw, final=False)
        if "\ufffd" in text:
            self._warn("malformed_multibyte_sequence", before + raw, seq_global=seq_global, direction=direction)
        return text

    def feed(self, data: bytes, *, seq_global: int = 0, direction: str = "out") -> str:
        return self.decode(data, seq_global=seq_global, direction=direction)

    def finalize(self, *, seq_global: int = 0, direction: str = "out") -> str:
        pending = self._pending()
        text = self._decoder.decode(b"", final=True)
        if pending:
            self._warn("incomplete_multibyte_sequence", pending, seq_global=seq_global, direction=direction)
        return text

    def reset(self, *, seq_global: int = 0, direction: str = "out", reason: str = "reset") -> None:
        pending = self._pending()
        if pending:
            self._warn("pending_bytes_discarded_on_reset", pending, seq_global=seq_global, direction=direction, action=reason)
        self._decoder = codecs.getincrementaldecoder(self.encoding)(errors="replace")

    def clear_warnings(self) -> None:
        self.warnings.clear()


IncrementalDecoder = TerminalDecoder

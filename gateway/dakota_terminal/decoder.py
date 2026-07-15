from __future__ import annotations

import codecs


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


class IncrementalDecoder:
    def __init__(self, encoding: str = "utf-8"):
        self.encoding = normalize_encoding(encoding)
        self.warnings: list[str] = []
        self._make_decoder()

    def _make_decoder(self) -> None:
        self._decoder = codecs.getincrementaldecoder(self.encoding)(errors="replace")

    def feed(self, data: bytes) -> str:
        return self._decoder.decode(bytes(data or b""), final=False)

    def finalize(self) -> str:
        try:
            text = self._decoder.decode(b"", final=True)
        except UnicodeDecodeError:
            self.warnings.append("incomplete_or_invalid_sequence")
            text = ""
        state = getattr(self._decoder, "getstate", lambda: (b"", 0))()
        pending = state[0] if isinstance(state, tuple) else b""
        if pending:
            self.warnings.append("incomplete_sequence_at_end")
        return text

    def reset(self) -> None:
        self.warnings.clear()
        self._make_decoder()


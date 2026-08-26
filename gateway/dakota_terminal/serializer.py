from __future__ import annotations

from .attributes import DEFAULT_COLOR


MAGIC_TEXT = "DKT-TEXT"
MAGIC_VISUAL = "DKT-VISUAL"
FORMAT_VERSION = 1

# Cache de ord() para caracteres ASCII — a serialização de assinaturas passa
# por rows*cols células por snapshot e a imensa maioria repete poucos chars
# (espaço, letras, dígitos); lookup em dict é bem mais barato que str(ord()).
_CP1: dict[str, str] = {chr(i): str(i) for i in range(128)}


def _codepoints(ch: str) -> str:
    s = ch or " "
    if len(s) == 1:
        cached = _CP1.get(s)
        return cached if cached is not None else str(ord(s))
    return "+".join(str(ord(c)) for c in s)


def serialize_text_state(snapshot: dict) -> bytes:
    parts = [
        MAGIC_TEXT,
        str(FORMAT_VERSION),
        str(snapshot["rows"]),
        str(snapshot["cols"]),
        str(snapshot.get("encoding", "utf-8")),
        str(snapshot.get("term", "xterm")),
    ]
    parts.extend(_codepoints(cell["ch"]) for cell in snapshot["cells"])
    return ("\n".join(parts) + "\n").encode("ascii")


def serialize_visual_state(snapshot: dict) -> bytes:
    parts = [
        MAGIC_VISUAL,
        str(FORMAT_VERSION),
        str(snapshot["rows"]),
        str(snapshot["cols"]),
        str(snapshot.get("encoding", "utf-8")),
        str(snapshot.get("term", "xterm")),
    ]
    append = parts.append
    # Memo da linha serializada por (char, atributos): telas reais repetem
    # poucos padrões (célula em branco default domina), então a montagem da
    # linha por célula vira lookup. Saída permanece byte-idêntica.
    line_memo: dict[tuple, str] = {}
    memo_get = line_memo.get
    for raw in snapshot["cells"]:
        ch = raw.get("ch") or " "
        key = (
            ch,
            raw.get("fg", DEFAULT_COLOR),
            raw.get("bg", DEFAULT_COLOR),
            raw.get("bold"), raw.get("dim"), raw.get("underline"),
            raw.get("blink"), raw.get("reverse"), raw.get("hidden"),
        )
        line = memo_get(key)
        if line is None:
            flags = (
                (1 if key[3] else 0)
                | (2 if key[4] else 0)
                | (4 if key[5] else 0)
                | (8 if key[6] else 0)
                | (16 if key[7] else 0)
                | (32 if key[8] else 0)
            )
            line = f"{_codepoints(ch)}|{key[1]}|{key[2]}|{flags}"
            line_memo[key] = line
        append(line)
    return ("\n".join(parts) + "\n").encode("ascii")


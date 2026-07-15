from __future__ import annotations

import hashlib

from .serializer import serialize_text_state, serialize_visual_state


def sha256_prefixed(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def text_sig(snapshot: dict) -> str:
    return sha256_prefixed(serialize_text_state(snapshot))


def visual_sig(snapshot: dict) -> str:
    return sha256_prefixed(serialize_visual_state(snapshot))


def semantic_sig(snapshot: dict) -> str:
    """Assinatura semantica tolerante - normaliza whitespace e box drawing.

    Remove espacos extras, normaliza linhas vazias, converte box drawing.
    Usado para comparacao tolerante em modos hibridos.
    """
    rows = snapshot.get("rows", 25)
    cells = snapshot.get("cells", [])

    # Extrai linhas de texto normalizadas
    lines = []
    for r in range(rows):
        line_chars = []
        for c in range(snapshot.get("cols", 80)):
            idx = r * snapshot.get("cols", 80) + c
            ch = cells[idx]["ch"] if idx < len(cells) else " "
            # Normaliza box drawing para representacao generica
            if ch in "┌┐└┘├┤┬┴┼─│":
                ch = "#"
            line_chars.append(ch)
        line = "".join(line_chars).rstrip()
        lines.append(line)

    # Remove linhas vazias no inicio e fim
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()

    return sha256_prefixed("\n".join(lines).encode("utf-8"))

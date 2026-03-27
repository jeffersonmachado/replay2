from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass


_ANSI_RE = re.compile(r"\x1B\[[0-9;?]*[A-Za-z]")
_ANSI_OTHER_RE = re.compile(r"\x1B.")

# Unicode box-drawing -> ASCII approximations (aligned with lib/normalize.tcl)
_BOX_MAP = str.maketrans(
    {
        "─": "-",
        "━": "-",
        "│": "|",
        "┃": "|",
        "┌": "+",
        "┏": "+",
        "┐": "+",
        "┓": "+",
        "└": "+",
        "┗": "+",
        "┘": "+",
        "┛": "+",
        "├": "+",
        "┣": "+",
        "┤": "+",
        "┫": "+",
        "┬": "+",
        "┳": "+",
        "┴": "+",
        "┻": "+",
        "┼": "+",
        "╋": "+",
        "═": "=",
        "║": "|",
        "╔": "+",
        "╗": "+",
        "╚": "+",
        "╝": "+",
        "╠": "+",
        "╣": "+",
        "╦": "+",
        "╩": "+",
        "╬": "+",
    }
)


def strip_ansi(text: str) -> str:
    text = _ANSI_RE.sub("", text)
    text = _ANSI_OTHER_RE.sub("", text)
    return text


def normalize_whitespace(text: str) -> str:
    # normalize line breaks
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    out_lines = []
    empty_count = 0
    for line in text.split("\n"):
        line = re.sub(r"\s+$", "", line)  # rtrim only
        if line == "":
            empty_count += 1
            if empty_count > 1:
                continue
        else:
            empty_count = 0
        out_lines.append(line)
    return "\n".join(out_lines)


def normalize_screen(raw_text: str) -> str:
    txt = raw_text
    txt = strip_ansi(txt)
    txt = txt.translate(_BOX_MAP)
    txt = normalize_whitespace(txt)
    return txt


_LABEL_RE = re.compile(r"([A-Za-zÀ-ÿ][0-9A-Za-zÀ-ÿ _]{1,20}):")


def signature_from_screen(norm_screen: str) -> str:
    lines = norm_screen.split("\n")

    # trim empty top/bottom
    while lines and lines[0].strip() == "":
        lines = lines[1:]
    while lines and lines[-1].strip() == "":
        lines = lines[:-1]

    num_lines = len(lines)
    max_width = max((len(l) for l in lines), default=0)

    # titles: first N non-empty lines with many frame chars
    title_candidates = []
    N = 4
    for i in range(min(N, num_lines)):
        line = lines[i]
        trimmed = line.strip()
        if trimmed == "":
            continue
        frame_count = len(re.findall(r"\+|\-|\=|\|", line))
        if frame_count >= 3:
            title_candidates.append(line.strip())

    # labels
    label_candidates = []
    for line in lines:
        trimmed = line.strip()
        if trimmed == "":
            continue
        m = _LABEL_RE.search(trimmed)
        if m:
            label = m.group(1)
            label_norm = re.sub(r"\s+", " ", label)
            label_candidates.append(label_norm)

    parts = [f"L={num_lines}", f"W={max_width}"]
    if title_candidates:
        parts.append("TIT=" + "|".join(title_candidates))
    if label_candidates:
        parts.append("LBL=" + ";".join(label_candidates))
    return ";".join(parts)


def sha256_hex_text(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


@dataclass
class Checkpoint:
    sig: str
    norm_sha256: str
    norm_len: int


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


@dataclass
class ScreenSnapshot:
    raw_bytes: bytes
    raw_text: str
    norm_text: str
    screen_sig: str
    norm_sha256: str
    norm_len: int
    screen_sample: str


@dataclass
class InputAction:
    raw_bytes: bytes
    key_kind: str
    key_text: str
    input_len: int
    contains_newline: bool
    contains_escape: bool
    is_probable_paste: bool
    is_probable_command: bool
    logical_parts: int = 1


def screen_sample_from_norm(norm_screen: str, *, max_lines: int = 6, max_width: int = 120) -> str:
    sample_lines = []
    for line in norm_screen.split("\n"):
        clean = line.rstrip()
        if not clean.strip():
            continue
        sample_lines.append(clean[:max_width])
        if len(sample_lines) >= max(1, int(max_lines)):
            break
    return "\n".join(sample_lines)


def build_screen_snapshot(raw_text: str) -> ScreenSnapshot:
    norm_text = normalize_screen(raw_text)
    return ScreenSnapshot(
        raw_bytes=raw_text.encode("utf-8", errors="replace"),
        raw_text=raw_text,
        norm_text=norm_text,
        screen_sig=signature_from_screen(norm_text),
        norm_sha256=sha256_hex_text(norm_text),
        norm_len=len(norm_text),
        screen_sample=screen_sample_from_norm(norm_text),
    )


def build_screen_snapshot_from_bytes(raw_bytes: bytes, *, encoding: str = "utf-8") -> ScreenSnapshot:
    try:
        raw_text = raw_bytes.decode(encoding, errors="replace")
    except Exception:
        raw_text = raw_bytes.decode(errors="replace")
    snapshot = build_screen_snapshot(raw_text)
    snapshot.raw_bytes = bytes(raw_bytes)
    return snapshot


def input_text_sample(data: bytes, *, max_len: int = 120) -> str:
    if not data:
        return ""
    out: list[str] = []
    for b in data[: max(1, int(max_len))]:
        if b == 13:
            out.append("\\r")
        elif b == 10:
            out.append("\\n")
        elif b == 9:
            out.append("\\t")
        elif b == 27:
            out.append("\\e")
        elif b == 8:
            out.append("\\b")
        elif 32 <= b <= 126:
            out.append(chr(b))
        else:
            out.append(f"\\x{b:02x}")
    return "".join(out)


def classify_input_bytes(data: bytes) -> str:
    if not data:
        return "empty"
    if _is_probable_paste(data):
        return "paste"
    if _is_probable_command(data):
        return "command_with_enter"
    if data in {b"\r", b"\n", b"\r\n"}:
        return "enter"
    if data == b"\t":
        return "tab"
    if data in {b"\x08", b"\x7f"}:
        return "backspace"
    if data == b"\x1b":
        return "escape"
    if _is_control_bytes(data):
        return "control"
    if data.startswith(b"\x1b["):
        return "ansi_sequence"
    if len(data) == 1 and 32 <= data[0] <= 126:
        return "printable"
    if len(data) > 1 and _is_all_printable_ascii(data):
        return "multi_char"
    if all(32 <= b <= 126 for b in data):
        return "text"
    return "bytes"


def _is_all_printable_ascii(data: bytes) -> bool:
    return bool(data) and all(32 <= b <= 126 for b in data)


def _is_control_bytes(data: bytes) -> bool:
    return bool(data) and all(b < 32 or b == 127 for b in data)


def _is_probable_paste(data: bytes) -> bool:
    if len(data) >= 64:
        return True
    if data.count(b"\n") + data.count(b"\r") > 1:
        return True
    printable = sum(1 for b in data if 32 <= b <= 126)
    return len(data) >= 16 and printable >= max(8, len(data) - 2) and b"\x1b" not in data


def _is_probable_command(data: bytes) -> bool:
    if not data or not (data.endswith(b"\r") or data.endswith(b"\n") or data.endswith(b"\r\n")):
        return False
    core = data.rstrip(b"\r\n")
    if not core or len(core) > 120 or b"\x1b" in core:
        return False
    return _is_all_printable_ascii(core)


def analyze_input_chunk(data: bytes) -> InputAction:
    return InputAction(
        raw_bytes=data,
        key_kind=classify_input_bytes(data),
        key_text=input_text_sample(data),
        input_len=len(data),
        contains_newline=(b"\n" in data or b"\r" in data),
        contains_escape=(b"\x1b" in data),
        is_probable_paste=_is_probable_paste(data),
        is_probable_command=_is_probable_command(data),
    )


def split_input_for_deterministic_record(data: bytes) -> list[InputAction]:
    if not data:
        return [analyze_input_chunk(data)]

    # ANSI/control sequences should stay atomic.
    if data.startswith(b"\x1b") or _is_probable_paste(data):
        return [analyze_input_chunk(data)]

    if _is_probable_command(data):
        command = data.rstrip(b"\r\n")
        tail = data[len(command) :]
        parts = [analyze_input_chunk(bytes([b])) for b in command]
        if tail:
            parts.append(analyze_input_chunk(tail))
        logical_parts = len(parts)
        for item in parts:
            item.logical_parts = logical_parts
        return parts

    # Safe refinement: short printable burst likely came from fast typing on same screen.
    if _is_all_printable_ascii(data) and 1 < len(data) <= 8:
        parts = [analyze_input_chunk(bytes([b])) for b in data]
        logical_parts = len(parts)
        for item in parts:
            item.logical_parts = logical_parts
        return parts

    action = analyze_input_chunk(data)
    action.logical_parts = 1
    return [action]

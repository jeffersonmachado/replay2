"""Helpers de janela/hash/params do replay_control (decomposição do módulo monolítico)."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from ..replay import ReplayError  # type: ignore
from ..terminal_config import TerminalGeometry, normalize_encoding, validate_terminal_geometry

def compute_last_hash_hint(log_dir: str) -> str:
    """
    Best-effort: read newest manifest and use last_hash; fallback to scan last JSONL line.
    """
    p = Path(log_dir)
    manifests = sorted(p.glob("audit-*.jsonl.manifest.json"))
    if manifests:
        # newest by name
        m = manifests[-1]
        try:
            d = json.loads(m.read_text(encoding="utf-8"))
            lh = d.get("last_hash") or ""
            if lh:
                return str(lh)
        except Exception:
            pass

    jsonls = sorted(p.glob("audit-*.jsonl"))
    if not jsonls:
        return ""
    last = jsonls[-1]
    try:
        # read last non-empty line
        lines = last.read_text(encoding="utf-8", errors="replace").splitlines()
        for line in reversed(lines):
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            if isinstance(d, dict):
                return str(d.get("hash") or "")
    except Exception:
        return ""
    return ""


def compute_fingerprint(log_dir: str, target_host: str, target_user: str, target_command: str, mode: str) -> str:
    hint = compute_last_hash_hint(log_dir)
    raw = f"{log_dir}|{target_host}|{target_user}|{target_command}|{mode}|{hint}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _iter_events(log_dir: str):
    for f in sorted(Path(log_dir).glob("audit-*.jsonl")):
        with open(f, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except Exception:
                    continue
                if isinstance(ev, dict):
                    yield ev


def _first_session_start(log_dir: str, session_id: str | None = None) -> dict:
    clean_sid = str(session_id or "").strip()
    for ev in _iter_events(log_dir):
        if isinstance(ev, dict) and ev.get("type") == "session_start":
            if clean_sid and str(ev.get("session_id") or "").strip() != clean_sid:
                continue
            return ev
    return {}


def _terminal_options_from_run(log_dir: str, params: dict) -> dict:
    session_start = _first_session_start(log_dir, params.get("replay_session_id"))
    try:
        if session_start.get("rows") is not None and session_start.get("cols") is not None:
            geom = validate_terminal_geometry(int(session_start.get("rows")), int(session_start.get("cols")))
        else:
            geom = TerminalGeometry(25, 80)
        if params.get("rows") is not None or params.get("cols") is not None:
            rows = geom.rows if params.get("rows") is None else int(params.get("rows"))
            cols = geom.cols if params.get("cols") is None else int(params.get("cols"))
            geom = validate_terminal_geometry(rows, cols)
    except Exception:
        geom = TerminalGeometry(25, 80)
    return {
        "rows": geom.rows,
        "cols": geom.cols,
        "term": str(params.get("term") or session_start.get("term") or "xterm"),
        "encoding": normalize_encoding(params.get("encoding") or session_start.get("encoding") or "utf-8"),
    }


def _normalize_replay_window_params(params: dict | None) -> dict:
    raw = params if isinstance(params, dict) else {}

    def _as_int(name: str) -> int:
        value = raw.get(name)
        if value in (None, ""):
            return 0
        try:
            return max(0, int(value))
        except Exception as exc:
            raise ValueError(f"{name} inválido") from exc

    replay_from_seq_global = _as_int("replay_from_seq_global")
    replay_to_seq_global = _as_int("replay_to_seq_global")
    if replay_from_seq_global and replay_to_seq_global and replay_from_seq_global > replay_to_seq_global:
        raise ValueError("replay_from_seq_global maior que replay_to_seq_global")

    return {
        "replay_from_seq_global": replay_from_seq_global,
        "replay_to_seq_global": replay_to_seq_global,
        "replay_session_id": str(raw.get("replay_session_id") or "").strip(),
        "replay_from_checkpoint_sig": str(raw.get("replay_from_checkpoint_sig") or "").strip(),
        "input_mode": str(raw.get("input_mode") or "raw").strip().lower() or "raw",
        "on_deterministic_mismatch": str(raw.get("on_deterministic_mismatch") or "fail-fast").strip().lower() or "fail-fast",
    }


def _resolve_replay_window(log_dir: str, params: dict | None) -> dict:
    window = _normalize_replay_window_params(params)
    session_id_filter = window["replay_session_id"]
    checkpoint_sig = window["replay_from_checkpoint_sig"]
    if checkpoint_sig:
        checkpoint_event = None
        for ev in _iter_events(log_dir):
            if str(ev.get("type") or "") != "checkpoint":
                continue
            if session_id_filter and str(ev.get("session_id") or "") != session_id_filter:
                continue
            if str(ev.get("sig") or "") != checkpoint_sig:
                continue
            checkpoint_event = ev
            break
        if not checkpoint_event:
            raise ReplayError(f"checkpoint inicial não encontrado: sig={checkpoint_sig!r}")
        checkpoint_seq = int(checkpoint_event.get("seq_global") or 0)
        if checkpoint_seq > 0:
            current_from = int(window.get("replay_from_seq_global") or 0)
            window["replay_from_seq_global"] = max(current_from, checkpoint_seq) if current_from else checkpoint_seq
            window["resolved_checkpoint_seq_global"] = checkpoint_seq
            window["resolved_checkpoint_session_id"] = str(checkpoint_event.get("session_id") or "")
    return window


def _event_in_replay_window(ev: dict, window: dict | None) -> bool:
    if not isinstance(ev, dict):
        return False
    selected = window if isinstance(window, dict) else {}
    session_id_filter = str(selected.get("replay_session_id") or "")
    if session_id_filter and str(ev.get("session_id") or "") != session_id_filter:
        return False
    seq_global = int(ev.get("seq_global") or 0)
    seq_from = int(selected.get("replay_from_seq_global") or 0)
    seq_to = int(selected.get("replay_to_seq_global") or 0)
    if seq_from and seq_global < seq_from:
        return False
    if seq_to and seq_global > seq_to:
        return False
    return True


def _selected_events(log_dir: str, params: dict | None):
    window = _resolve_replay_window(log_dir, params)
    for ev in _iter_events(log_dir):
        if _event_in_replay_window(ev, window):
            yield ev


def compute_seq_end(log_dir: str, params: dict | None = None) -> int:
    if params:
        last_seq = 0
        for ev in _selected_events(log_dir, params):
            last_seq = max(last_seq, int(ev.get("seq_global") or 0))
        if last_seq:
            return last_seq
    # best-effort: read newest manifest and use seq_end; fallback 0
    p = Path(log_dir)
    manifests = sorted(p.glob("audit-*.jsonl.manifest.json"))
    if manifests:
        try:
            d = json.loads(manifests[-1].read_text(encoding="utf-8"))
            return int(d.get("seq_end") or 0)
        except Exception:
            return 0
    return 0


def _replay_input_mode(params: dict | None) -> str:
    mode = str((params or {}).get("input_mode") or "raw").strip().lower()
    return mode if mode in {"raw", "deterministic"} else "raw"


def _on_deterministic_mismatch(params: dict | None) -> str:
    mode = str((params or {}).get("on_deterministic_mismatch") or "fail-fast").strip().lower()
    return mode if mode in {"fail-fast", "skip", "send-anyway"} else "fail-fast"


def _is_replay_input_event(ev: dict, *, input_mode: str) -> bool:
    typ = str(ev.get("type") or "")
    if input_mode == "deterministic":
        return typ == "deterministic_input"
    return typ == "bytes" and ev.get("dir") == "in"


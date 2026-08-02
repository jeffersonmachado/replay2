"""Cache em disco de estados da TerminalEngine para replay (dívida X6).

Sessões enormes (ex.: 116k eventos) tornam janelas profundas quadráticas:
cada request com offset grande reprocessa o stream desde o evento 0. Este
módulo persiste o estado completo da engine a cada STATE_CACHE_INTERVAL
eventos "bytes" (ver session_replay_service) para que uma janela retome do
estado mais próximo.

Layout:
    <cache_dir>/<capture_sig>/<session_id>/state-<idx:08d>.json.gz

Onde capture_sig = sha256 (16 hex) de "nome:tamanho:mtime_ns" dos
audit-*.jsonl da captura — qualquer alteração no arquivo invalida o cache.

Tudo aqui é fail-safe: qualquer erro de leitura/validação vira miss e o
chamador reprocessa do início.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
from pathlib import Path

_STATE_FILE_RE = re.compile(r"^state-(\d{8})\.json\.gz$")


def capture_signature(log_dir: str | Path) -> str:
    """Assinatura de conteúdo-aparente dos audit-*.jsonl (nome+size+mtime)."""
    path = Path(log_dir)
    parts = []
    for f in sorted(path.glob("audit-*.jsonl")):
        try:
            st = f.stat()
        except OSError:
            continue
        parts.append(f"{f.name}:{st.st_size}:{st.st_mtime_ns}")
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]


def _session_dir(cache_dir: str | Path, capture_sig: str, session_id: str) -> Path:
    safe_sid = re.sub(r"[^A-Za-z0-9_.-]", "_", str(session_id or ""))
    return Path(cache_dir) / str(capture_sig) / safe_sid


def store_state(cache_dir: str | Path, capture_sig: str, session_id: str, bytes_index: int, envelope: dict) -> bool:
    """Persiste o envelope (engine + contadores) de forma atômica."""
    try:
        target_dir = _session_dir(cache_dir, capture_sig, session_id)
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"state-{int(bytes_index):08d}.json.gz"
        tmp = target.with_suffix(".tmp")
        with gzip.open(tmp, "wt", encoding="utf-8") as f:
            json.dump(envelope, f, separators=(",", ":"))
        os.replace(tmp, target)
        return True
    except Exception:
        return False


def _load_envelope(path: Path, capture_sig: str, session_id: str, *, rows: int, cols: int, term: str, encoding: str) -> dict | None:
    try:
        with gzip.open(path, "rt", encoding="utf-8") as f:
            envelope = json.load(f)
    except Exception:
        return None
    if not isinstance(envelope, dict):
        return None
    if int(envelope.get("state_version") or 0) != 1:
        return None
    if str(envelope.get("capture_sig") or "") != str(capture_sig):
        return None
    if str(envelope.get("session_id") or "") != str(session_id):
        return None
    if (
        int(envelope.get("rows") or 0) != int(rows)
        or int(envelope.get("cols") or 0) != int(cols)
        or str(envelope.get("term") or "") != str(term)
        or str(envelope.get("encoding") or "") != str(encoding)
    ):
        return None
    return envelope


def load_nearest_state(
    cache_dir: str | Path,
    capture_sig: str,
    session_id: str,
    max_index: int,
    *,
    rows: int,
    cols: int,
    term: str,
    encoding: str,
) -> tuple[int, dict] | None:
    """Retorna (bytes_index, envelope) do maior índice <= max_index, ou None.

    Se o arquivo do melhor índice estiver corrompido/inválido, tenta os
    índices imediatamente inferiores antes de desistir.
    """
    session_dir = _session_dir(cache_dir, capture_sig, session_id)
    try:
        candidates: list[tuple[int, Path]] = []
        for f in session_dir.iterdir():
            m = _STATE_FILE_RE.match(f.name)
            if m:
                idx = int(m.group(1))
                if 0 < idx <= int(max_index):
                    candidates.append((idx, f))
    except OSError:
        return None
    candidates.sort(key=lambda item: item[0], reverse=True)
    for idx, path in candidates[:4]:
        envelope = _load_envelope(
            path, capture_sig, session_id,
            rows=rows, cols=cols, term=term, encoding=encoding,
        )
        if envelope is not None:
            return idx, envelope
    return None

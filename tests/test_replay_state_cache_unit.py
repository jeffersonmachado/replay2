"""Testes do cache em disco de estados de replay (dívida X6).

O cache persiste estados da TerminalEngine a cada N eventos "bytes" para que
janelas profundas (offset grande) retomem do snapshot mais próximo em vez de
reprocessar o stream desde o início.

Contrato coberto:
  1. store/load round-trip;
  2. load_nearest escolhe o maior índice <= alvo;
  3. assinatura da captura (nome+size+mtime dos audit-*.jsonl) invalida o
     cache quando o arquivo muda;
  4. arquivo corrompido ou geometria divergente é ignorado (fail-safe).

Run:
  PYTHONPATH=gateway python3 -m pytest tests/test_replay_state_cache_unit.py -v
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from control.services import replay_state_cache as cache


def _mk_capture(tmp_path: Path, n_events: int = 3) -> Path:
    log_dir = tmp_path / "captura-1"
    log_dir.mkdir(parents=True, exist_ok=True)
    with open(log_dir / "audit-000001.jsonl", "w", encoding="utf-8") as f:
        for i in range(n_events):
            f.write(json.dumps({"type": "bytes", "seq_global": i + 1}) + "\n")
    return log_dir


def _envelope(sig: str, idx: int, rows: int = 24, cols: int = 80) -> dict:
    return {
        "state_version": 1,
        "capture_sig": sig,
        "session_id": "sess-1",
        "bytes_index": idx,
        "rows": rows,
        "cols": cols,
        "term": "xterm",
        "encoding": "utf-8",
        "engine": {"state_version": 1, "rows": rows, "cols": cols},
        "counters": {"out_event_count": idx},
    }


def test_store_e_load_nearest_roundtrip(tmp_path):
    log_dir = _mk_capture(tmp_path)
    sig = cache.capture_signature(log_dir)
    cache_dir = str(tmp_path / "cache")
    for idx in (1000, 2000, 3000):
        cache.store_state(cache_dir, sig, "sess-1", idx, _envelope(sig, idx))

    hit = cache.load_nearest_state(cache_dir, sig, "sess-1", 2500, rows=24, cols=80, term="xterm", encoding="utf-8")
    assert hit is not None
    assert hit[0] == 2000
    assert hit[1]["counters"]["out_event_count"] == 2000


def test_load_nearest_sem_indice_elegivel_retorna_none(tmp_path):
    log_dir = _mk_capture(tmp_path)
    sig = cache.capture_signature(log_dir)
    cache_dir = str(tmp_path / "cache")
    cache.store_state(cache_dir, sig, "sess-1", 1000, _envelope(sig, 1000))
    hit = cache.load_nearest_state(cache_dir, sig, "sess-1", 500, rows=24, cols=80, term="xterm", encoding="utf-8")
    assert hit is None


def test_assinatura_muda_quando_arquivo_muda(tmp_path):
    log_dir = _mk_capture(tmp_path)
    sig1 = cache.capture_signature(log_dir)
    with open(log_dir / "audit-000001.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps({"type": "bytes", "seq_global": 99}) + "\n")
    os.utime(log_dir / "audit-000001.jsonl", (time.time() + 5, time.time() + 5))
    sig2 = cache.capture_signature(log_dir)
    assert sig1 != sig2

    cache_dir = str(tmp_path / "cache")
    cache.store_state(cache_dir, sig1, "sess-1", 1000, _envelope(sig1, 1000))
    # assinatura nova não enxerga o estado gravado com a assinatura antiga
    hit = cache.load_nearest_state(cache_dir, sig2, "sess-1", 1500, rows=24, cols=80, term="xterm", encoding="utf-8")
    assert hit is None


def test_arquivo_corrompido_e_ignorado(tmp_path):
    log_dir = _mk_capture(tmp_path)
    sig = cache.capture_signature(log_dir)
    cache_dir = tmp_path / "cache"
    cache.store_state(str(cache_dir), sig, "sess-1", 1000, _envelope(sig, 1000))
    state_files = list(cache_dir.rglob("state-*.json.gz"))
    assert state_files
    state_files[0].write_bytes(b"isto nao e gzip valido")
    hit = cache.load_nearest_state(str(cache_dir), sig, "sess-1", 1500, rows=24, cols=80, term="xterm", encoding="utf-8")
    assert hit is None


def test_geometria_divergente_e_ignorada(tmp_path):
    log_dir = _mk_capture(tmp_path)
    sig = cache.capture_signature(log_dir)
    cache_dir = str(tmp_path / "cache")
    cache.store_state(cache_dir, sig, "sess-1", 1000, _envelope(sig, 1000, rows=24, cols=80))
    hit = cache.load_nearest_state(cache_dir, sig, "sess-1", 1500, rows=25, cols=80, term="xterm", encoding="utf-8")
    assert hit is None

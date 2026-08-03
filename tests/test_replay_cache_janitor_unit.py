"""Testes da limpeza de caches órfãos de replay (dívida X6).

Os caches de replay (estados da engine + índice de sessão) vivem em
<captures>/replay_state_cache/<capture_sig>/<session_id>/. A capture_sig é
nome+size+mtime dos audit-*.jsonl — qualquer append na captura muda a
assinatura e torna o cache anterior lixo inalcançável (capturas ativas
ficam horas gravando); capturas removidas fora de banda (rm -rf) também
deixam órfãos. O janitor remove os diretórios cuja assinatura não
corresponde a nenhuma captura existente, com guarda de recência para não
remover um cache em gravação concorrente.

Contrato coberto:
  1. remove sig sem captura correspondente; mantém a que corresponde;
  2. guarda de recência: sig desconhecida modificada há poucos minutos é
     preservada (pode estar em gravação por um request em andamento);
  3. diretório de cache inexistente não é erro;
  4. integração: append na captura muda a sig → sweep remove a antiga e
     preserva a nova.

Run:
  PYTHONPATH=gateway python3 -m pytest tests/test_replay_cache_janitor_unit.py -v
"""
from __future__ import annotations

import base64
import json
import os
import time
from pathlib import Path

import pytest

from control.services import replay_state_cache
from control.services import session_replay_service as svc


def _mk_capture(root: Path, name: str, n_events: int = 5) -> Path:
    sid = "sessao-janitor"
    out_b64 = base64.b64encode(b"linha\r\n").decode()
    d = root / name
    d.mkdir(parents=True)
    with open(d / "audit-000001.jsonl", "w", encoding="utf-8") as f:
        f.write(json.dumps({
            "type": "session_start", "session_id": sid, "seq_global": 0,
            "seq_session": 0, "ts_ms": 1000, "rows": 24, "cols": 80,
            "term": "xterm", "encoding": "utf-8",
        }) + "\n")
        for i in range(n_events):
            f.write(json.dumps({
                "type": "bytes", "session_id": sid, "seq_global": i + 1,
                "seq_session": i + 1, "ts_ms": 1000 + i * 10,
                "dir": "out", "data_b64": out_b64, "n": 7,
            }) + "\n")
    return d


def test_remove_sig_sem_captura_e_mantem_correspondente(tmp_path):
    captures = tmp_path / "captures"
    cap = _mk_capture(captures, "cap-a")
    cache_root = captures / "replay_state_cache"
    sig_valida = replay_state_cache.capture_signature(cap)
    (cache_root / sig_valida / "sessao-janitor").mkdir(parents=True)
    (cache_root / "sig-velha-de-append" / "sessao-janitor").mkdir(parents=True)

    resultado = replay_state_cache.cleanup_orphan_caches(captures, cache_root, min_age_seconds=0)
    assert resultado["removed"] == 1
    assert (cache_root / sig_valida).exists()
    assert not (cache_root / "sig-velha-de-append").exists()


def test_guarda_de_recencia_preserva_sig_recente(tmp_path):
    captures = tmp_path / "captures"
    _mk_capture(captures, "cap-a")
    cache_root = captures / "replay_state_cache"
    recente = cache_root / "sig-em-gravacao" / "sessao-janitor"
    recente.mkdir(parents=True)
    antigo = cache_root / "sig-velha" / "sessao-janitor"
    antigo.mkdir(parents=True)
    # envelhece o diretório "sig-velha" além da guarda
    velho = time.time() - 7200
    os.utime(antigo, (velho, velho))
    os.utime(cache_root / "sig-velha", (velho, velho))

    resultado = replay_state_cache.cleanup_orphan_caches(captures, cache_root, min_age_seconds=3600)
    assert resultado["removed"] == 1
    assert (cache_root / "sig-em-gravacao").exists()  # guarda de recência
    assert not (cache_root / "sig-velha").exists()


def test_cache_dir_inexistente_nao_e_erro(tmp_path):
    captures = tmp_path / "captures"
    _mk_capture(captures, "cap-a")
    resultado = replay_state_cache.cleanup_orphan_caches(captures, captures / "replay_state_cache")
    assert resultado["removed"] == 0


@pytest.fixture
def patches(monkeypatch):
    monkeypatch.setattr(svc, "MAX_FULL_REPLAY_EVENTS", 5)
    monkeypatch.setattr(svc, "STATE_CACHE_INTERVAL", 2)
    monkeypatch.setattr(svc, "STATE_CACHE_ENABLED", True)
    monkeypatch.setattr(svc, "SESSION_INDEX_ENABLED", True)
    return svc


def test_append_na_captura_torna_cache_orfao(patches, tmp_path):
    captures = tmp_path / "captures"
    cap = _mk_capture(captures, "cap-a", n_events=10)
    cache_root = str(captures / "replay_state_cache")
    sid = "sessao-janitor"

    patches.prepare_session_replay_data(str(cap), sid, offset=2, limit=3, state_cache_dir=cache_root)
    sig_v1 = replay_state_cache.capture_signature(cap)
    assert (Path(cache_root) / sig_v1).exists()

    # append muda a assinatura: o cache da sig_v1 vira órfão
    with open(cap / "audit-000001.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps({
            "type": "bytes", "session_id": sid, "seq_global": 99,
            "seq_session": 99, "ts_ms": 9999, "dir": "out",
            "data_b64": base64.b64encode(b"mais\r\n").decode(), "n": 6,
        }) + "\n")
    patches.prepare_session_replay_data(str(cap), sid, offset=2, limit=3, state_cache_dir=cache_root)
    sig_v2 = replay_state_cache.capture_signature(cap)
    assert sig_v2 != sig_v1
    assert (Path(cache_root) / sig_v2).exists()

    # envelhece a sig_v1 além da guarda de recência e roda o sweep
    velho = time.time() - 7200
    for raiz, _dirs, arquivos in os.walk(Path(cache_root) / sig_v1):
        for arq in arquivos:
            os.utime(Path(raiz) / arq, (velho, velho))
        os.utime(raiz, (velho, velho))
    resultado = replay_state_cache.cleanup_orphan_caches(captures, cache_root, min_age_seconds=3600)
    assert resultado["removed"] == 1
    assert not (Path(cache_root) / sig_v1).exists()
    assert (Path(cache_root) / sig_v2).exists()

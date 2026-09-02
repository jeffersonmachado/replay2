"""Testes do índice GLOBAL de captura em disco (FASE 9).

O session_index_cache já persistia um índice por (capture_sig, session_id)
para o replay; esta suíte cobre o índice por captura inteira — arquivos,
offsets, sessões, primeiro/último evento e seq_global, tipos, contagens,
timestamps, bytes e checkpoints — construído em UMA passagem de streaming,
com invalidação por mtime+size por arquivo e atualização incremental (só o
delta desde os offsets conhecidos).

Cobre também a regressão da validação impossível em load_index:
``len(files) != len(set(files)) and not files`` nunca era verdadeira (lista
vazia tem len==0==len(set); duplicata tem ``not files`` falso), então
arquivos duplicados jamais eram rejeitados.

Run:
  PYTHONPATH=gateway python3 -m pytest tests/test_capture_index_unit.py -v
"""
from __future__ import annotations

import base64
import gzip
import io
import json
from pathlib import Path

import pytest

from control.services import session_index_cache as sic


def _linha_bytes(seq: int, sid: str, direction: str, payload: bytes, ts: int) -> dict:
    return {
        "type": "bytes", "session_id": sid, "seq_global": seq, "seq_session": seq,
        "ts_ms": ts, "dir": direction,
        "data_b64": base64.b64encode(payload).decode(), "n": len(payload),
    }


def _gerar_captura(log_dir: Path, *, n_arquivos: int = 2, eventos_por_arquivo: int = 40,
                   sessoes: tuple[str, ...] = ("s1", "s2"), seq_inicial: int = 0) -> int:
    """Gera captura plana com 2 sessões intercaladas, bytes in/out,
    checkpoints e session_start/end. Retorna o próximo seq_global."""
    seq = seq_inicial
    ts = 10_000 + seq_inicial * 10
    for arq in range(n_arquivos):
        path = log_dir / f"audit-{arq + 1:06d}.jsonl"
        modo = "a" if path.exists() else "w"
        with open(path, modo, encoding="utf-8") as f:
            for i in range(eventos_por_arquivo):
                sid = sessoes[i % len(sessoes)]
                seq += 1
                ts += 10
                if i == 0:
                    ev = {"type": "session_start", "session_id": sid, "seq_global": seq,
                          "seq_session": seq, "ts_ms": ts, "actor": "op1",
                          "rows": 24, "cols": 80, "term": "xterm", "encoding": "utf-8"}
                elif i == eventos_por_arquivo - 1:
                    ev = {"type": "session_end", "session_id": sid, "seq_global": seq,
                          "seq_session": seq, "ts_ms": ts, "actor": "op1"}
                elif i % 7 == 3:
                    ev = {"type": "checkpoint", "session_id": sid, "seq_global": seq,
                          "seq_session": seq, "ts_ms": ts, "sig": f"sig-{seq}"}
                else:
                    out = (i % 2) == 0
                    ev = _linha_bytes(seq, sid, "out" if out else "in", b"x" * (10 + i), ts)
                f.write(json.dumps(ev) + "\n")
    return seq


def _conta_leituras(monkeypatch, modulo):
    """Conta bytes lidos via open() do módulo. Retorna dict mutável."""
    estado = {"bytes": 0, "aberturas": {}}
    open_real = io.open

    class _Contador:
        def __init__(self, fh):
            self._fh = fh

        def read(self, n=-1):
            dados = self._fh.read(n)
            estado["bytes"] += len(dados)
            return dados

        def readline(self, n=-1):
            dados = self._fh.readline(n)
            estado["bytes"] += len(dados)
            return dados

        def __getattr__(self, nome):
            return getattr(self._fh, nome)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self._fh.close()
            return False

    def _open_contando(path, mode="r", *args, **kwargs):
        estado["aberturas"][str(path)] = estado["aberturas"].get(str(path), 0) + 1
        if "b" in mode and "r" in mode:
            return _Contador(open_real(path, mode, *args, **kwargs))
        return open_real(path, mode, *args, **kwargs)

    monkeypatch.setattr(modulo, "open", _open_contando, raising=False)
    return estado


# ── Regressão: validação de duplicatas em load_index ─────────────────────


def _indice_sessao_valido(**override) -> dict:
    base = {
        "v": sic.INDEX_VERSION,
        "capture_sig": "sig123",
        "session_id": "s1",
        "files": ["a.jsonl"],
        "types": "s",
        "seq": [1],
        "file": [0],
        "off": [0],
        "bpos": [],
        "bdir": "",
        "blen": [],
        "n_bytes": 0,
        "total_in": 0,
        "total_out": 0,
    }
    base.update(override)
    return base


def test_load_index_rejeita_arquivos_duplicados(tmp_path):
    """A condição antiga (``and not files``) tornava a checagem impossível:
    duplicatas passavam. Agora files com nomes repetidos → miss."""
    store_dir = tmp_path / "cache"
    assert sic.store_index(store_dir, _indice_sessao_valido(files=["a.jsonl", "a.jsonl"]))
    assert sic.load_index(store_dir, "sig123", "s1") is None


def test_load_index_aceita_lista_de_arquivos_vazia(tmp_path):
    """Lista vazia não é duplicata: índice de sessão sem eventos é válido."""
    store_dir = tmp_path / "cache"
    assert sic.store_index(store_dir, _indice_sessao_valido(files=[], types="", seq=[], file=[], off=[]))
    assert sic.load_index(store_dir, "sig123", "s1") is not None


def test_load_index_aceita_arquivos_distintos(tmp_path):
    store_dir = tmp_path / "cache"
    assert sic.store_index(store_dir, _indice_sessao_valido(files=["a.jsonl", "b.jsonl"]))
    assert sic.load_index(store_dir, "sig123", "s1") is not None


# ── Índice global de captura ──────────────────────────────────────────────


def test_indice_global_conteudo_completo(tmp_path):
    log_dir = tmp_path / "cap"
    log_dir.mkdir()
    _gerar_captura(log_dir, n_arquivos=2, eventos_por_arquivo=40)
    cache_dir = tmp_path / "cache"

    idx = sic.get_capture_index(cache_dir, log_dir)
    assert idx is not None
    assert idx["total_events"] == 80
    assert idx["first_seq_global"] == 1
    assert idx["last_seq_global"] == 80
    assert len(idx["files"]) == 2
    for info in idx["files"]:
        real = (log_dir / info["name"]).stat()
        assert info["size"] == real.st_size
        assert info["mtime_ns"] == real.st_mtime_ns
        assert info["indexed_bytes"] == real.st_size

    # contagens por tipo batem com varredura independente
    tipos: dict[str, int] = {}
    checkpoints = 0
    sess_esperada: dict[str, dict] = {}
    for path in sorted(log_dir.glob("audit-*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            ev = json.loads(line)
            t = ev["type"]
            tipos[t] = tipos.get(t, 0) + 1
            if t == "checkpoint":
                checkpoints += 1
            agg = sess_esperada.setdefault(ev["session_id"], {"count": 0, "bytes_in": 0, "bytes_out": 0})
            agg["count"] += 1
            if t == "bytes":
                agg["bytes_in" if ev["dir"] == "in" else "bytes_out"] += int(ev["n"])
    assert idx["type_counts"] == tipos
    assert idx["checkpoints"] == checkpoints
    assert set(idx["sessions"]) == {"s1", "s2"}
    for sid, agg in sess_esperada.items():
        got = idx["sessions"][sid]
        assert got["count"] == agg["count"]
        assert got["bytes_in"] == agg["bytes_in"]
        assert got["bytes_out"] == agg["bytes_out"]
        assert len(got["locs"]) == agg["count"]
        assert got["first_seq_global"] < got["last_seq_global"]
        assert got["first_ts_ms"] <= got["last_ts_ms"]


def test_indice_global_construido_em_uma_passagem(tmp_path, monkeypatch):
    log_dir = tmp_path / "cap"
    log_dir.mkdir()
    _gerar_captura(log_dir, n_arquivos=3, eventos_por_arquivo=20)
    estado = _conta_leituras(monkeypatch, sic)

    idx = sic.get_capture_index(tmp_path / "cache", log_dir)
    assert idx is not None
    for path in sorted(log_dir.glob("audit-*.jsonl")):
        assert estado["aberturas"].get(str(path), 0) == 1, f"{path} aberto mais de uma vez"


def test_indice_global_hit_nao_rele_nada(tmp_path, monkeypatch):
    log_dir = tmp_path / "cap"
    log_dir.mkdir()
    _gerar_captura(log_dir, n_arquivos=2, eventos_por_arquivo=20)
    cache_dir = tmp_path / "cache"
    assert sic.get_capture_index(cache_dir, log_dir) is not None

    estado = _conta_leituras(monkeypatch, sic)
    idx = sic.get_capture_index(cache_dir, log_dir)
    assert idx is not None
    assert estado["bytes"] == 0, "hit de índice não deveria ler os audit-*.jsonl"


def test_indice_global_delta_incremental(tmp_path, monkeypatch):
    """Captura cresceu (append no último arquivo + arquivo novo): reindexar
    APENAS o delta, sem reler o que já estava indexado."""
    log_dir = tmp_path / "cap"
    log_dir.mkdir()
    _gerar_captura(log_dir, n_arquivos=1, eventos_por_arquivo=40)
    cache_dir = tmp_path / "cache"
    idx1 = sic.get_capture_index(cache_dir, log_dir)
    assert idx1 is not None
    bytes_indexados = idx1["files"][0]["indexed_bytes"]

    # append no arquivo existente (a partir de novo seq) + segundo arquivo
    seq = _gerar_captura(log_dir, n_arquivos=2, eventos_por_arquivo=10, seq_inicial=40)
    assert seq == 60

    estado = _conta_leituras(monkeypatch, sic)
    idx2 = sic.get_capture_index(cache_dir, log_dir)
    assert idx2 is not None
    assert idx2["total_events"] == 60
    assert idx2["last_seq_global"] == 60
    # leu no máximo o delta (arquivo cresceu 10 eventos + arquivo novo de 10)
    assert estado["bytes"] < bytes_indexados, "reindexou do zero em vez de processar só o delta"
    # primeiro arquivo foi lido só do offset conhecido em diante
    assert estado["aberturas"].get(str(log_dir / "audit-000001.jsonl"), 0) == 1


def test_indice_global_invalida_quando_arquivo_do_meio_muda(tmp_path):
    """mtime/size de arquivo que não o último mudou → rebuild completo."""
    log_dir = tmp_path / "cap"
    log_dir.mkdir()
    _gerar_captura(log_dir, n_arquivos=2, eventos_por_arquivo=20)
    cache_dir = tmp_path / "cache"
    idx1 = sic.get_capture_index(cache_dir, log_dir)
    assert idx1 is not None

    primeiro = log_dir / "audit-000001.jsonl"
    conteudo = primeiro.read_text(encoding="utf-8").replace('"s1"', '"s9"', 1)
    primeiro.write_text(conteudo, encoding="utf-8")

    idx2 = sic.get_capture_index(cache_dir, log_dir)
    assert idx2 is not None
    assert "s9" in idx2["sessions"], "alteração no arquivo do meio não invalidou o índice"


def test_indice_global_corrompido_reconstroi(tmp_path):
    log_dir = tmp_path / "cap"
    log_dir.mkdir()
    _gerar_captura(log_dir, n_arquivos=1, eventos_por_arquivo=20)
    cache_dir = tmp_path / "cache"
    assert sic.get_capture_index(cache_dir, log_dir) is not None

    path = sic.capture_index_path(cache_dir, log_dir)
    assert path.exists()
    path.write_bytes(b"isto-nao-e-gzip")

    idx = sic.get_capture_index(cache_dir, log_dir)
    assert idx is not None
    assert idx["total_events"] == 20


def test_indice_global_kill_switch_env(tmp_path, monkeypatch):
    monkeypatch.setenv("REPLAY_SESSION_INDEX", "0")
    log_dir = tmp_path / "cap"
    log_dir.mkdir()
    _gerar_captura(log_dir, n_arquivos=1, eventos_por_arquivo=10)
    cache_dir = tmp_path / "cache"
    assert sic.get_capture_index(cache_dir, log_dir) is None
    assert not sic.capture_index_path(cache_dir, log_dir).exists()


def test_indice_global_linha_parcial_no_fim(tmp_path):
    """Arquivo em gravação (última linha sem \\n) indexa só até a linha
    completa; o restante entra no próximo delta."""
    log_dir = tmp_path / "cap"
    log_dir.mkdir()
    path = log_dir / "audit-000001.jsonl"
    ev1 = _linha_bytes(1, "s1", "out", b"abc", 1000)
    ev2 = _linha_bytes(2, "s1", "in", b"de", 1010)
    path.write_text(json.dumps(ev1) + "\n" + json.dumps(ev2), encoding="utf-8")
    cache_dir = tmp_path / "cache"

    idx1 = sic.get_capture_index(cache_dir, log_dir)
    assert idx1 is not None
    assert idx1["total_events"] == 1  # só a linha completa
    assert idx1["files"][0]["indexed_bytes"] == len(json.dumps(ev1)) + 1

    # completa a segunda linha depois
    with open(path, "a", encoding="utf-8") as f:
        f.write("\n")
    idx2 = sic.get_capture_index(cache_dir, log_dir)
    assert idx2 is not None
    assert idx2["total_events"] == 2

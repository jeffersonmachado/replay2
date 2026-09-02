"""Testes do tail reverso e do detalhe de sessão via índice (FASE 9).

O monitor do gateway (`read_gateway_monitor`) e o detalhe de sessão
(`read_gateway_session_detail`) liam cada audit-*.jsonl INTEIRO com
read_text() para responder os últimos N eventos / os eventos de uma sessão —
em capturas de centenas de MB isso derruba o endpoint. Agora:

- o monitor usa tail reverso em blocos (do fim para o início), parando ao
  juntar `limit` eventos;
- o detalhe usa o índice global da captura (session_index_cache) para
  materializar os eventos da sessão por seek, com fallback à varredura.

Run:
  PYTHONPATH=gateway python3 -m pytest tests/test_gateway_observability_tail_unit.py -v
  PYTHONPATH=gateway python3 -m pytest tests/test_gateway_observability_tail_unit.py -v -m slow
"""
from __future__ import annotations

import base64
import io
import json
from pathlib import Path

import pytest

from control.services import gateway_observability_service as gobs
from control.services import session_index_cache as sic


def _linha(i: int, *, sid: str = "s1", tamanho: int = 400, tipo: str = "bytes") -> str:
    payload = base64.b64encode(b"y" * tamanho).decode()
    return json.dumps({
        "type": tipo, "session_id": sid, "seq_global": i, "seq_session": i,
        "ts_ms": 1000 + i * 10, "actor": "op",
        "dir": "out" if i % 2 else "in", "data_b64": payload, "n": tamanho,
    })


def _conta_leituras(monkeypatch, modulo):
    """Conta bytes lidos via open() do módulo e derruba read_text/read_bytes."""
    estado = {"bytes": 0}
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
        if "b" in mode and "r" in mode:
            return _Contador(open_real(path, mode, *args, **kwargs))
        return open_real(path, mode, *args, **kwargs)

    def _explode(self, *args, **kwargs):
        raise RuntimeError("leitura integral proibida: usar tail/índice")

    monkeypatch.setattr(modulo, "open", _open_contando, raising=False)
    monkeypatch.setattr(Path, "read_text", _explode)
    monkeypatch.setattr(Path, "read_bytes", _explode)
    return estado


# ── Tail reverso (unitário, arquivos pequenos) ────────────────────────────


def test_tail_reverso_ultimas_linhas_em_ordem_cronologica(tmp_path):
    path = tmp_path / "audit-000001.jsonl"
    path.write_text("".join(_linha(i, tamanho=10) + "\n" for i in range(1, 1001)), encoding="utf-8")

    eventos = gobs.read_last_audit_events(path, 40)
    assert len(eventos) == 40
    assert [ev["seq_global"] for ev in eventos] == list(range(961, 1001))


def test_tail_reverso_arquivo_menor_que_o_limite(tmp_path):
    path = tmp_path / "audit-000001.jsonl"
    path.write_text("".join(_linha(i, tamanho=10) + "\n" for i in range(1, 11)), encoding="utf-8")
    eventos = gobs.read_last_audit_events(path, 40)
    assert [ev["seq_global"] for ev in eventos] == list(range(1, 11))


def test_tail_reverso_bloco_minusculo_forca_varias_passadas(tmp_path):
    """Com bloco de 64 bytes, o tail precisa de várias passadas para trás —
    prova de que a montagem por blocos preserva as linhas corretas."""
    path = tmp_path / "audit-000001.jsonl"
    path.write_text("".join(_linha(i, tamanho=10) + "\n" for i in range(1, 201)), encoding="utf-8")
    eventos = gobs.read_last_audit_events(path, 30, block_size=64)
    assert [ev["seq_global"] for ev in eventos] == list(range(171, 201))


def test_tail_reverso_arquivo_inexistente_e_vazio(tmp_path):
    assert gobs.read_last_audit_events(tmp_path / "nao-existe.jsonl", 10) == []
    vazio = tmp_path / "audit-000001.jsonl"
    vazio.write_text("", encoding="utf-8")
    assert gobs.read_last_audit_events(vazio, 10) == []


def test_monitor_abrange_varios_arquivos(tmp_path):
    log_dir = tmp_path / "cap"
    log_dir.mkdir()
    (log_dir / "audit-000001.jsonl").write_text(
        "".join(_linha(i, tamanho=10) + "\n" for i in range(1, 31)), encoding="utf-8")
    (log_dir / "audit-000002.jsonl").write_text(
        "".join(_linha(i, tamanho=10) + "\n" for i in range(31, 61)), encoding="utf-8")

    payload = gobs.read_gateway_monitor(str(log_dir), limit=40)
    assert payload["error"] is None
    seqs = [ev["seq_global"] for ev in payload["events"]]
    assert seqs == list(range(21, 61))
    assert payload["summary"]["window_events"] == 40


# ── Tail reverso em arquivo grande (prova de I/O sublinear) ───────────────


@pytest.mark.slow
def test_monitor_arquivo_100mb_nao_le_arquivo_inteiro(tmp_path, monkeypatch):
    """~100MB: o monitor responde os últimos 40 eventos lendo uma fração
    ínfima do arquivo (blocos do fim), com read_text/read_bytes derrubados."""
    log_dir = tmp_path / "cap"
    log_dir.mkdir()
    path = log_dir / "audit-000001.jsonl"
    alvo = 100 * 1024 * 1024
    i = 0
    with open(path, "w", encoding="utf-8") as f:
        while f.tell() < alvo:
            i += 1
            f.write(_linha(i) + "\n")
    tamanho = path.stat().st_size
    assert tamanho >= alvo

    estado = _conta_leituras(monkeypatch, gobs)
    payload = gobs.read_gateway_monitor(str(log_dir), limit=40)

    assert payload["error"] is None
    assert [ev["seq_global"] for ev in payload["events"]] == list(range(i - 39, i + 1))
    assert estado["bytes"] < tamanho // 10, (
        f"leu {estado['bytes']} de {tamanho} bytes — tail não é sublinear")


# ── Detalhe de sessão via índice global ───────────────────────────────────


def _gerar_duas_sessoes(log_dir: Path, *, eventos: int = 60) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    seq = 0
    with open(log_dir / "audit-000001.jsonl", "w", encoding="utf-8") as f:
        for sid in ("alfa", "beta"):
            seq += 1
            f.write(json.dumps({
                "type": "session_start", "session_id": sid, "seq_global": seq,
                "seq_session": 1, "ts_ms": 1000, "actor": "op",
                "rows": 24, "cols": 80, "term": "xterm", "encoding": "utf-8",
            }) + "\n")
            for j in range(eventos):
                seq += 1
                f.write(_linha(seq, sid=sid, tamanho=20) + "\n")
            seq += 1
            f.write(json.dumps({
                "type": "session_end", "session_id": sid, "seq_global": seq,
                "seq_session": eventos + 2, "ts_ms": 1000 + seq * 10, "actor": "op",
            }) + "\n")


def _seqs(payload: dict) -> list[int]:
    return [int(ev["seq_global"]) for ev in payload["events"]]


def test_detalhe_sessao_com_indice_tem_paridade_com_varredura(tmp_path, monkeypatch):
    log_dir = tmp_path / "cap"
    _gerar_duas_sessoes(log_dir)
    monkeypatch.setattr(gobs, "CAPTURE_INDEX_MIN_BYTES", 0)
    cache_dir = tmp_path / "replay_state_cache"

    monkeypatch.setenv("REPLAY_SESSION_INDEX", "0")
    referencia = gobs.read_gateway_session_detail(str(log_dir), "alfa")
    monkeypatch.setenv("REPLAY_SESSION_INDEX", "1")
    com_indice = gobs.read_gateway_session_detail(
        str(log_dir), "alfa", state_cache_dir=str(cache_dir))
    assert referencia["error"] is None
    assert com_indice["error"] is None
    assert _seqs(com_indice) == _seqs(referencia)
    assert sic.capture_index_path(cache_dir, log_dir).exists()

    # Segunda chamada: índice morno — a varredura integral está derrubada.
    estado = _conta_leituras(monkeypatch, gobs)
    morno = gobs.read_gateway_session_detail(str(log_dir), "alfa", state_cache_dir=str(cache_dir))
    assert morno["error"] is None
    assert _seqs(morno) == _seqs(referencia)

    # filtros por faixa de seq_global seguem funcionando no caminho indexado
    faixa = gobs.read_gateway_session_detail(
        str(log_dir), "alfa", seq_global_from=10, seq_global_to=20, state_cache_dir=str(cache_dir))
    assert _seqs(faixa) == [s for s in _seqs(referencia) if 10 <= s <= 20]


def test_detalhe_sessao_kill_switch_nao_constroi_indice(tmp_path, monkeypatch):
    monkeypatch.setenv("REPLAY_SESSION_INDEX", "0")
    monkeypatch.setattr(gobs, "CAPTURE_INDEX_MIN_BYTES", 0)
    log_dir = tmp_path / "cap"
    _gerar_duas_sessoes(log_dir)
    cache_dir = tmp_path / "replay_state_cache"

    payload = gobs.read_gateway_session_detail(str(log_dir), "beta", state_cache_dir=str(cache_dir))
    assert payload["error"] is None
    assert len(payload["events"]) > 0
    assert not sic.capture_index_path(cache_dir, log_dir).exists()

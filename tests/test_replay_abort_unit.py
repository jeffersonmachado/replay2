"""Testes do cancelamento de request abandonada no replay (dívida X6).

Uma janela profunda em sessão enorme pode levar segundos processando; se o
cliente desconecta no meio (navegação, timeout de proxy), o thread do
control plane seguia queimando CPU até o fim — no AIX, vários requests
abandonados se acumulavam. prepare_session_replay_data aceita agora um
abort_check (callable → True para abortar), consultado periodicamente nos
loops de parse e de processamento; a rota passa uma sonda do socket do
cliente.

Contrato coberto:
  1. abort_check=True interrompe o processamento e retorna o marcador
     "aborted" sem materializar a janela;
  2. o abort também alcança o parse completo (sessão enorme sem índice);
  3. sem abort_check (default), o comportamento é inalterado;
  4. a sonda de socket detecta desconexão (socketpar: aberto → conectado,
     fechado → desconectado).

Run:
  PYTHONPATH=gateway python3 -m pytest tests/test_replay_abort_unit.py -v
"""
from __future__ import annotations

import base64
import json
import socket
from pathlib import Path

import pytest

from control.services import session_replay_service as svc
from control.routes.capture_routes import client_still_connected


def _gerar_sessao(tmpdir: str, n_eventos: int) -> str:
    sid = "sessao-abort"
    out_b64 = base64.b64encode(b"linha de teste 0123456789\r\n").decode()
    audit_path = Path(tmpdir) / "audit-000001.jsonl"
    with open(audit_path, "w", encoding="utf-8") as f:
        f.write(json.dumps({
            "type": "session_start", "session_id": sid, "seq_global": 0,
            "seq_session": 0, "ts_ms": 1000, "rows": 24, "cols": 80,
            "term": "xterm", "encoding": "utf-8",
        }) + "\n")
        for i in range(n_eventos):
            f.write(json.dumps({
                "type": "bytes", "session_id": sid, "seq_global": i + 1,
                "seq_session": i + 1, "ts_ms": 1000 + i * 10,
                "dir": "out", "data_b64": out_b64,
                "n": len(base64.b64decode(out_b64)),
            }) + "\n")
    return sid


@pytest.fixture
def patches(monkeypatch):
    monkeypatch.setattr(svc, "MAX_FULL_REPLAY_EVENTS", 50)
    monkeypatch.setattr(svc, "STATE_CACHE_INTERVAL", 20)
    monkeypatch.setattr(svc, "STATE_CACHE_ENABLED", True)
    return svc


def test_abort_no_loop_principal_interrompe(patches, tmp_path):
    log_dir = tmp_path / "cap"
    log_dir.mkdir()
    sid = _gerar_sessao(str(log_dir), 300)

    chamadas = {"n": 0}

    def abort_depois_de_3():
        chamadas["n"] += 1
        return chamadas["n"] > 3

    resultado = patches.prepare_session_replay_data(
        str(log_dir), sid, offset=0, limit=200, abort_check=abort_depois_de_3)
    assert resultado.get("aborted") is True
    assert resultado["error"]["code"] == "client_aborted"
    assert resultado["events"] == []


def test_abort_alcanca_parse_completo(patches, tmp_path):
    """Sem índice (primeira execução), o abort interrompe já na leitura."""
    log_dir = tmp_path / "cap"
    log_dir.mkdir()
    sid = _gerar_sessao(str(log_dir), 300)

    resultado = patches.prepare_session_replay_data(
        str(log_dir), sid, offset=0, limit=10, abort_check=lambda: True)
    assert resultado.get("aborted") is True


def test_sem_abort_check_comportamento_inalterado(patches, tmp_path):
    log_dir = tmp_path / "cap"
    log_dir.mkdir()
    sid = _gerar_sessao(str(log_dir), 120)

    resultado = patches.prepare_session_replay_data(str(log_dir), sid, offset=45, limit=20)
    assert resultado.get("aborted") is None
    assert resultado["error"] is None
    assert len(resultado["events"]) == 20


def test_abort_false_nao_interrompe(patches, tmp_path):
    log_dir = tmp_path / "cap"
    log_dir.mkdir()
    sid = _gerar_sessao(str(log_dir), 120)

    resultado = patches.prepare_session_replay_data(
        str(log_dir), sid, offset=45, limit=20, abort_check=lambda: False)
    assert resultado.get("aborted") is None
    assert len(resultado["events"]) == 20


class _FakeHandler:
    def __init__(self, conn):
        self.connection = conn


def test_sonda_detecta_desconexao():
    a, b = socket.socketpair()
    try:
        assert client_still_connected(_FakeHandler(a)) is True
        b.close()  # cliente desconectou
        assert client_still_connected(_FakeHandler(a)) is False
    finally:
        a.close()


def test_sonda_tolerante_a_erro():
    class _SemSocket:
        connection = None
    # sem socket válido, assume conectado (não aborta por falha da sonda)
    assert client_still_connected(_FakeHandler(None)) is True
    assert client_still_connected(_SemSocket()) is True

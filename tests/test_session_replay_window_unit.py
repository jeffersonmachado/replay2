"""Testes da janela de replay e da guarda de materialização (dívida X6).

Contexto: sessões enormes (ex.: 116k eventos) derrubavam o control plane —
o endpoint de replay materializava eventos/timeline/playback completos e
chamava snapshot_from_engine (1920 células) por evento OUT.

Contrato coberto aqui:
  1. prepare_session_replay_data aceita offset/limit e retorna só a fatia;
  2. o estado do terminal (sigs) da fatia é idêntico ao modo completo —
     o prefixo da sessão é processado mesmo fora da janela;
  3. sessão acima do limite sem janela explícita retorna fatia inicial
     marcada como truncada (nunca materializa tudo);
  4. sessão pequena sem janela mantém comportamento integral.

Run:
  PYTHONPATH=gateway python3 -m pytest tests/test_session_replay_window_unit.py -v
"""
from __future__ import annotations

import base64
import json
import tempfile
from pathlib import Path

import pytest

from control.services.session_replay_service import (
    DEFAULT_REPLAY_WINDOW_LIMIT,
    MAX_FULL_REPLAY_EVENTS,
    prepare_session_replay_data,
)

FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "capture8_replay_fixture.json"


def _fixture_session(tmpdir: str) -> tuple[str, int]:
    """Grava a fixture capture8 em disco e devolve (session_id, n_eventos_bytes)."""
    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    sid = payload["session_id"]
    n_bytes = 0
    audit_path = Path(tmpdir) / "audit-000001.jsonl"
    with open(audit_path, "w", encoding="utf-8") as f:
        for ev in payload["events"]:
            if ev.get("type") == "bytes":
                n_bytes += 1
            f.write(json.dumps(ev, ensure_ascii=False) + "\n")
    return sid, n_bytes


def _gerar_sessao_grande(tmpdir: str, n_eventos: int) -> str:
    """Gera sessão sintética com n_eventos bytes (2/3 OUT, 1/3 IN)."""
    sid = "sessao-grande-x6"
    out_b64 = base64.b64encode(b"linha de teste 0123456789\r\n").decode()
    in_b64 = base64.b64encode(b"x").decode()
    audit_path = Path(tmpdir) / "audit-000001.jsonl"
    seq = 0
    with open(audit_path, "w", encoding="utf-8") as f:
        f.write(json.dumps({
            "type": "session_start", "session_id": sid, "seq_global": 0,
            "seq_session": 0, "ts_ms": 1000, "rows": 24, "cols": 80,
            "term": "xterm", "encoding": "utf-8",
        }) + "\n")
        for i in range(n_eventos):
            seq += 1
            out = (i % 3) != 0
            f.write(json.dumps({
                "type": "bytes", "session_id": sid, "seq_global": seq,
                "seq_session": seq, "ts_ms": 1000 + i * 10,
                "dir": "out" if out else "in",
                "data_b64": out_b64 if out else in_b64,
                "n": 28 if out else 1,
            }) + "\n")
        seq += 1
        f.write(json.dumps({
            "type": "session_end", "session_id": sid, "seq_global": seq,
            "seq_session": seq, "ts_ms": 1000 + n_eventos * 10,
        }) + "\n")
    return sid


def test_window_returns_only_requested_slice():
    with tempfile.TemporaryDirectory() as tmpdir:
        sid, n_bytes = _fixture_session(tmpdir)
        assert n_bytes > 6, "fixture precisa de eventos suficientes para a janela"

        full = prepare_session_replay_data(tmpdir, sid)
        windowed = prepare_session_replay_data(tmpdir, sid, offset=2, limit=3)

        assert windowed.get("error") is None
        events = windowed["events"]
        assert len(events) == 3

        full_events = full["events"]
        expected_seqs = [ev["seq_global"] for ev in full_events[2:5]]
        assert [ev["seq_global"] for ev in events] == expected_seqs

        window = windowed["window"]
        assert window["offset"] == 2
        assert window["limit"] == 3
        assert window["total_events"] == len(full_events)
        assert window["truncated"] is True


def test_window_preserves_terminal_state_signatures():
    """Sigs de um item dentro da janela devem ser idênticas ao modo completo.

    Prova que o prefixo fora da janela alimentou a TerminalEngine — caso
    contrário text_sig/visual_sig divergirão.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        sid, n_bytes = _fixture_session(tmpdir)
        full = prepare_session_replay_data(tmpdir, sid)
        windowed = prepare_session_replay_data(tmpdir, sid, offset=2, limit=3)

        full_out = {ev["seq_global"]: ev for ev in full["events"] if ev["direction"] == "out" and ev.get("text_sig")}
        matched = 0
        for ev in windowed["events"]:
            if ev["direction"] == "out" and ev.get("text_sig"):
                ref = full_out.get(ev["seq_global"])
                assert ref is not None
                assert ev["text_sig"] == ref["text_sig"]
                assert ev["visual_sig"] == ref["visual_sig"]
                matched += 1
        assert matched > 0, "janela da fixture deveria conter ao menos um evento OUT com sig"


def test_window_offset_beyond_end_returns_empty():
    with tempfile.TemporaryDirectory() as tmpdir:
        sid, n_bytes = _fixture_session(tmpdir)
        windowed = prepare_session_replay_data(tmpdir, sid, offset=n_bytes + 100, limit=10)
        assert windowed.get("error") is None
        assert windowed["events"] == []
        assert windowed["window"]["total_events"] == n_bytes
        assert windowed["window"]["truncated"] is False


def test_default_guard_truncates_huge_session(monkeypatch):
    """Sem janela explícita, sessão acima do limite NUNCA materializa tudo."""
    import control.services.session_replay_service as svc

    # Constantes reduzidas: a lógica de guarda é a mesma, sem gerar 20k eventos.
    monkeypatch.setattr(svc, "MAX_FULL_REPLAY_EVENTS", 60)
    monkeypatch.setattr(svc, "DEFAULT_REPLAY_WINDOW_LIMIT", 25)

    with tempfile.TemporaryDirectory() as tmpdir:
        sid = _gerar_sessao_grande(tmpdir, 70)
        data = prepare_session_replay_data(tmpdir, sid)

        assert data.get("error") is None
        assert len(data["events"]) == 25
        window = data["window"]
        assert window["truncated"] is True
        assert window["total_events"] == 70
        # Metadados de playback refletem o total, não a fatia
        assert data["playback"]["event_count"] == 70


def test_small_session_without_window_unchanged():
    with tempfile.TemporaryDirectory() as tmpdir:
        sid, n_bytes = _fixture_session(tmpdir)
        assert n_bytes <= MAX_FULL_REPLAY_EVENTS
        data = prepare_session_replay_data(tmpdir, sid)
        assert data.get("error") is None
        assert len(data["events"]) == n_bytes
        assert data["window"]["truncated"] is False
        assert data["window"]["total_events"] == n_bytes


def test_checkpoints_respeitam_teto_com_redesenho_constante(monkeypatch):
    """Sessão com clear-screen em TODO evento OUT não gera checkpoint por evento.

    Reproduz o padrão das telas legadas (clear + redesenho por página) que
    inflou o RSS do control plane para 4 GB na captura de 116k eventos.
    """
    import control.services.session_replay_service as svc

    teto = 50
    monkeypatch.setattr(svc, "MAX_REPLAY_CHECKPOINTS", teto)

    n_eventos = teto * 4
    out_clear_b64 = base64.b64encode(b"\x1b[2J\x1b[1;1HTELA\r\n").decode()
    with tempfile.TemporaryDirectory() as tmpdir:
        sid = "sessao-clear-constante"
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
                    "dir": "out", "data_b64": out_clear_b64, "n": 14,
                }) + "\n")
            f.write(json.dumps({
                "type": "session_end", "session_id": sid,
                "seq_global": n_eventos + 1, "seq_session": n_eventos + 1,
                "ts_ms": 1000 + n_eventos * 10,
            }) + "\n")

        data = prepare_session_replay_data(tmpdir, sid)
        assert data.get("error") is None
        # +3: checkpoint inicial + session_start + session_end
        assert len(data["checkpoints"]) <= teto + 3
        assert data["checkpoints_capped"] is True


def test_modo_parcial_interrompe_stream_no_fim_da_janela(monkeypatch):
    """Sessão enorme em modo padrão: processa só até o fim da janela."""
    import control.services.session_replay_service as svc

    monkeypatch.setattr(svc, "MAX_FULL_REPLAY_EVENTS", 60)
    monkeypatch.setattr(svc, "DEFAULT_REPLAY_WINDOW_LIMIT", 25)

    with tempfile.TemporaryDirectory() as tmpdir:
        sid = _gerar_sessao_grande(tmpdir, 70)
        data = prepare_session_replay_data(tmpdir, sid)

        assert data["window"]["truncated"] is True
        assert data["window"]["partial_state"] is True
        # final_snapshot reflete o fim da janela, não o fim da sessão —
        # mas o payload segue íntegro e autoconsistente.
        assert data["final_snapshot"]
        assert data["canonical_signatures"]["text_sig"]


def test_modo_completo_nao_marca_partial_state():
    with tempfile.TemporaryDirectory() as tmpdir:
        sid, _ = _fixture_session(tmpdir)
        data = prepare_session_replay_data(tmpdir, sid)
        assert data["window"]["partial_state"] is False

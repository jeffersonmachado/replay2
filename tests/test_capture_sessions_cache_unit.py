"""Testes do cache de scan de sessões de captura (dívida X6).

Contexto: GET /api/captures/{id}/sessions rescanneava todos os JSONL de
auditoria a cada request (~28 s para uma captura de 116k eventos no AIX).
summarize_capture_sessions passa a ter cache por assinatura de arquivos
(nome+size+mtime) com TTL curto.

Contrato coberto aqui:
  1. segunda chamada idêntica é atendida pelo cache (cache_info);
  2. escrita no diretório invalida o cache (nova contagem aparece);
  3. mutações no resultado retornado não contaminam o cache.

Run:
  PYTHONPATH=gateway python3 -m pytest tests/test_capture_sessions_cache_unit.py -v
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from dakota_gateway.compliance import summarize_capture_sessions, capture_sessions_cache_info


def _emit(log_dir: Path, seq: int, sid: str = "sessao-cache") -> None:
    path = log_dir / "audit-000001.jsonl"
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps({
            "type": "bytes", "session_id": sid, "seq_global": seq,
            "seq_session": seq, "ts_ms": 1000 + seq, "dir": "out",
            "data_b64": "eA==", "n": 1,
        }) + "\n")


def test_segunda_chamada_usa_cache():
    with tempfile.TemporaryDirectory() as tmpdir:
        log_dir = Path(tmpdir)
        _emit(log_dir, 1)

        primeiro = summarize_capture_sessions(str(log_dir))
        info_antes = capture_sessions_cache_info()
        segundo = summarize_capture_sessions(str(log_dir))
        info_depois = capture_sessions_cache_info()

        assert segundo["sessions"] == primeiro["sessions"]
        assert info_depois["hits"] > info_antes["hits"]


def test_escrita_invalida_cache():
    with tempfile.TemporaryDirectory() as tmpdir:
        log_dir = Path(tmpdir)
        _emit(log_dir, 1)
        antes = summarize_capture_sessions(str(log_dir))
        count_antes = antes["sessions"][0]["event_count"]

        _emit(log_dir, 2)
        _emit(log_dir, 3)
        depois = summarize_capture_sessions(str(log_dir))
        count_depois = depois["sessions"][0]["event_count"]

        assert count_depois == count_antes + 2


def test_mutacao_no_resultado_nao_contamina_cache():
    with tempfile.TemporaryDirectory() as tmpdir:
        log_dir = Path(tmpdir)
        _emit(log_dir, 1)

        resultado = summarize_capture_sessions(str(log_dir))
        resultado["sessions"][0]["event_count"] = 999999
        resultado["sessions"][0]["matched"] = True

        limpo = summarize_capture_sessions(str(log_dir))
        assert limpo["sessions"][0]["event_count"] == 1
        assert "matched" not in limpo["sessions"][0]

"""Testes do índice de sessão em disco para replay (dívida X6).

Contexto: mesmo com o cache de estado da engine, cada request de replay em
sessão enorme relê e reparseia todos os audit-*.jsonl (ex.: 314 MB / 116k
linhas da captura 20 do MIG24) para extrair os eventos da sessão, calcular
totais de playback e localizar a janela — ~7,4 s dos ~18 s no AIX. O índice
de sessão persiste, por (capture_sig, session_id), o mapa tipado de eventos
(tipo, seq_global, arquivo, offset, direção e tamanho decodificado dos
eventos "bytes"), permitindo:
  - totais de playback sem decodificar base64 novamente;
  - materialização da janela por seek, sem varrer o arquivo inteiro.

Contrato coberto:
  1. execução fria constrói o índice; execução morna tem hit e NÃO lê o
     arquivo inteiro (read_text derrubado → ainda assim responde);
  2. paridade total do payload entre execução com índice e sem índice
     (exceto os campos informativos window.state_cache/session_index);
  3. reconexões e deterministic_input preservam bookkeeping e paginação;
  4. alteração no arquivo de captura invalida o índice;
  5. índice corrompido cai para o parse completo (fail-safe);
  6. REPLAY_SESSION_INDEX=0 desliga o índice (kill-switch).

Run:
  PYTHONPATH=gateway python3 -m pytest tests/test_session_replay_session_index_unit.py -v
"""
from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from control.services import session_replay_service as svc
from control.services import session_index_cache as sic
from control.services import replay_state_cache


def _gerar_sessao(tmpdir: str, n_eventos: int, *, deterministicos: tuple[int, ...] = (), reconexoes: tuple[int, ...] = ()) -> str:
    """Mesmo gerador do teste do cache de estado: 2/3 OUT, 1/3 IN, com
    clear-screen periódico e pares session_end/session_start intermediários."""
    sid = "sessao-indice-x6"
    out_b64 = base64.b64encode(b"linha de teste 0123456789\r\n").decode()
    clear_b64 = base64.b64encode(b"\x1b[2J\x1b[Hpagina\r\n").decode()
    in_b64 = base64.b64encode(b"x").decode()
    audit_path = Path(tmpdir) / "audit-000001.jsonl"
    seq = 0
    out_count = 0
    with open(audit_path, "w", encoding="utf-8") as f:
        f.write(json.dumps({
            "type": "session_start", "session_id": sid, "seq_global": 0,
            "seq_session": 0, "ts_ms": 1000, "rows": 24, "cols": 80,
            "term": "xterm", "encoding": "utf-8",
        }) + "\n")
        for i in range(n_eventos):
            seq += 1
            out = (i % 3) != 0
            if out:
                out_count += 1
            data = clear_b64 if (out and out_count % 7 == 0) else (out_b64 if out else in_b64)
            f.write(json.dumps({
                "type": "bytes", "session_id": sid, "seq_global": seq,
                "seq_session": seq, "ts_ms": 1000 + i * 10,
                "dir": "out" if out else "in",
                "data_b64": data,
                "n": len(base64.b64decode(data)),
            }) + "\n")
            if i in deterministicos:
                seq += 1
                f.write(json.dumps({
                    "type": "deterministic_input", "session_id": sid,
                    "seq_global": seq, "seq_session": seq, "ts_ms": 1000 + i * 10 + 5,
                    "screen_sig": "sig", "key_kind": "enter", "key_text": "",
                }) + "\n")
            if i in reconexoes:
                for tipo in ("session_end", "session_start"):
                    seq += 1
                    ev = {
                        "type": tipo, "session_id": sid, "seq_global": seq,
                        "seq_session": seq, "ts_ms": 1000 + i * 10 + 6,
                    }
                    if tipo == "session_start":
                        ev.update({"rows": 24, "cols": 80, "term": "xterm", "encoding": "utf-8"})
                    f.write(json.dumps(ev) + "\n")
        seq += 1
        f.write(json.dumps({
            "type": "session_end", "session_id": sid, "seq_global": seq,
            "seq_session": seq, "ts_ms": 1000 + n_eventos * 10,
        }) + "\n")
    return sid


def _sumario(payload: dict) -> dict:
    """Projeção comparável: exclui os campos informativos de cache."""
    return {
        "events": payload["events"],
        "timeline": list(payload["timeline"]),
        "playback": list(payload["playback"]),
        "playback_meta": {
            "total_bytes_in": payload["playback"].get("total_bytes_in"),
            "total_bytes_out": payload["playback"].get("total_bytes_out"),
            "event_count": payload["playback"].get("event_count"),
            "deterministic_event_count": payload["playback"].get("deterministic_event_count"),
        },
        "final_snapshot": payload["final_snapshot"],
        "canonical_signatures": payload["canonical_signatures"],
        "deterministic_events": payload["deterministic_events"],
        "geometry": payload["geometry"],
        "session_start": payload["session_start"],
        "session_end": payload["session_end"],
        "window": {
            k: v for k, v in payload["window"].items()
            if k not in ("state_cache", "session_index")
        },
    }


@pytest.fixture
def cache_patches(monkeypatch):
    monkeypatch.setattr(svc, "MAX_FULL_REPLAY_EVENTS", 50)
    monkeypatch.setattr(svc, "STATE_CACHE_INTERVAL", 20)
    monkeypatch.setattr(svc, "STATE_CACHE_ENABLED", True)
    monkeypatch.setattr(svc, "SESSION_INDEX_ENABLED", True)
    return svc


def test_frio_constroi_indice_e_morno_tem_hit_com_paridade(cache_patches, tmp_path):
    log_dir = tmp_path / "cap"
    log_dir.mkdir()
    sid = _gerar_sessao(str(log_dir), 120)
    cache_dir = str(tmp_path / "cache")

    frio = cache_patches.prepare_session_replay_data(
        str(log_dir), sid, offset=45, limit=20, state_cache_dir=cache_dir)
    assert frio["error"] is None
    assert frio["window"]["session_index"]["hit"] is False
    assert frio["window"]["session_index"]["stored"] is True

    morno = cache_patches.prepare_session_replay_data(
        str(log_dir), sid, offset=45, limit=20, state_cache_dir=cache_dir)
    assert morno["error"] is None
    assert morno["window"]["session_index"]["hit"] is True

    assert _sumario(morno) == _sumario(frio)


def test_execucao_com_indice_nao_le_arquivo_inteiro(cache_patches, tmp_path, monkeypatch):
    """Com o índice morno, derrubar Path.read_text (usado pelo parse
    completo) não pode impedir a resposta: a janela é materializada por
    seek a partir do índice."""
    log_dir = tmp_path / "cap"
    log_dir.mkdir()
    sid = _gerar_sessao(str(log_dir), 120)
    cache_dir = str(tmp_path / "cache")

    cache_patches.prepare_session_replay_data(
        str(log_dir), sid, offset=45, limit=20, state_cache_dir=cache_dir)

    def _explode(self, *args, **kwargs):
        raise RuntimeError("parse completo não deveria ser chamado com índice morno")

    monkeypatch.setattr(Path, "read_text", _explode)
    morno = cache_patches.prepare_session_replay_data(
        str(log_dir), sid, offset=45, limit=20, state_cache_dir=cache_dir)
    assert morno["error"] is None
    assert morno["window"]["session_index"]["hit"] is True
    assert len(morno["events"]) > 0


def test_paridade_janela_profunda_com_e_sem_indice(cache_patches, tmp_path):
    log_dir = tmp_path / "cap"
    log_dir.mkdir()
    sid = _gerar_sessao(str(log_dir), 120, deterministicos=(50,), reconexoes=(10, 30))
    cache_dir = str(tmp_path / "cache")

    # popula índice + estados com uma janela rasa
    cache_patches.prepare_session_replay_data(
        str(log_dir), sid, offset=45, limit=20, state_cache_dir=cache_dir)
    com_indice = cache_patches.prepare_session_replay_data(
        str(log_dir), sid, offset=85, limit=20, state_cache_dir=cache_dir)
    assert com_indice["window"]["session_index"]["hit"] is True
    assert com_indice["window"]["state_cache"]["hit"] is True

    referencia = cache_patches.prepare_session_replay_data(
        str(log_dir), sid, offset=85, limit=20, state_cache_dir=str(tmp_path / "cache-vazio"))
    assert referencia["window"]["session_index"]["hit"] is False

    assert _sumario(com_indice) == _sumario(referencia)


def test_alteracao_na_captura_invalida_indice(cache_patches, tmp_path):
    log_dir = tmp_path / "cap"
    log_dir.mkdir()
    sid = _gerar_sessao(str(log_dir), 120)
    cache_dir = str(tmp_path / "cache")

    cache_patches.prepare_session_replay_data(
        str(log_dir), sid, offset=45, limit=20, state_cache_dir=cache_dir)
    with open(log_dir / "audit-000001.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps({
            "type": "bytes", "session_id": sid, "seq_global": 9999,
            "seq_session": 9999, "ts_ms": 99999, "dir": "out",
            "data_b64": base64.b64encode(b"extra\r\n").decode(), "n": 7,
        }) + "\n")

    depois = cache_patches.prepare_session_replay_data(
        str(log_dir), sid, offset=45, limit=20, state_cache_dir=cache_dir)
    assert depois["window"]["session_index"]["hit"] is False
    assert depois["error"] is None


def test_indice_corrompido_cai_para_parse_completo(cache_patches, tmp_path):
    log_dir = tmp_path / "cap"
    log_dir.mkdir()
    sid = _gerar_sessao(str(log_dir), 120)
    cache_dir = str(tmp_path / "cache")

    cache_patches.prepare_session_replay_data(
        str(log_dir), sid, offset=45, limit=20, state_cache_dir=cache_dir)
    sig = replay_state_cache.capture_signature(log_dir)
    idx_path = sic.index_path(cache_dir, sig, sid)
    assert idx_path.exists()
    idx_path.write_bytes(b"isto-nao-e-gzip")

    depois = cache_patches.prepare_session_replay_data(
        str(log_dir), sid, offset=45, limit=20, state_cache_dir=cache_dir)
    assert depois["error"] is None
    assert depois["window"]["session_index"]["hit"] is False
    assert len(depois["events"]) > 0


def test_kill_switch_desliga_indice(cache_patches, tmp_path, monkeypatch):
    monkeypatch.setattr(svc, "SESSION_INDEX_ENABLED", False)
    log_dir = tmp_path / "cap"
    log_dir.mkdir()
    sid = _gerar_sessao(str(log_dir), 120)
    cache_dir = str(tmp_path / "cache")

    resultado = cache_patches.prepare_session_replay_data(
        str(log_dir), sid, offset=45, limit=20, state_cache_dir=cache_dir)
    assert resultado["error"] is None
    assert resultado["window"]["session_index"]["enabled"] is False
    sig = replay_state_cache.capture_signature(log_dir)
    assert not sic.index_path(cache_dir, sig, sid).exists()


def test_sessao_pequena_nao_gera_indice(cache_patches, tmp_path):
    """Sessões abaixo de MAX_FULL_REPLAY_EVENTS não precisam de índice."""
    log_dir = tmp_path / "cap"
    log_dir.mkdir()
    sid = _gerar_sessao(str(log_dir), 30)
    cache_dir = str(tmp_path / "cache")

    resultado = cache_patches.prepare_session_replay_data(str(log_dir), sid, state_cache_dir=cache_dir)
    assert resultado["error"] is None
    sig = replay_state_cache.capture_signature(log_dir)
    assert not sic.index_path(cache_dir, sig, sid).exists()

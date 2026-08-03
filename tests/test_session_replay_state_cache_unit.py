"""Testes de paridade do replay com cache de estado em disco (dívida X6).

Contexto: sem cache, uma janela profunda (offset grande) reprocessa o stream
desde o evento 0 — rolagem profunda fica quadrática. Com o cache, a engine
retoma do estado persistido mais próximo. O payload da janela precisa ser
idêntico ao de uma execução sem cache (paridade frio × morno), com exceção
documentada: checkpoints anteriores ao ponto de retomada não são regerados.

Contrato coberto:
  1. execução fria popula o cache; execução morna retoma (hit) do maior
     índice <= offset;
  2. eventos/timeline/playback/final_snapshot/canonical_signatures da janela
     são idênticos entre frio e morno;
  3. checkpoints dentro da janela são idênticos;
  4. deterministic_input é materializado apenas dentro da janela (contrato
     de paginação) e não impede a retomada; pares session_end/session_start
     intermediários (reconexões com o mesmo session_id) também não — a fase
     de skip faz só o bookkeeping dos campos do payload;
  5. alteração no arquivo de captura invalida o cache.

Run:
  PYTHONPATH=gateway python3 -m pytest tests/test_session_replay_state_cache_unit.py -v
"""
from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from control.services import session_replay_service as svc


def _gerar_sessao(tmpdir: str, n_eventos: int, *, deterministicos: tuple[int, ...] = (), reconexoes: tuple[int, ...] = ()) -> str:
    """Gera sessão sintética (2/3 OUT, 1/3 IN) com clear-screen periódico.

    deterministicos: índices (no espaço de eventos bytes) após os quais um
    evento deterministic_input é inserido.
    reconexoes: índices após os quais um par session_end/session_start é
    inserido (capturas com reconexão reutilizam o session_id).
    """
    sid = "sessao-cache-x6"
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
    """Projeção comparável do payload: tudo que deve ser idêntico entre
    execução fria e morna, exceto checkpoints fora da janela."""
    return {
        "events": payload["events"],
        "timeline": list(payload["timeline"]),
        "playback": list(payload["playback"]),
        "final_snapshot": payload["final_snapshot"],
        "canonical_signatures": payload["canonical_signatures"],
        "deterministic_events": payload["deterministic_events"],
        "geometry": payload["geometry"],
        "window": {k: v for k, v in payload["window"].items() if k != "state_cache"},
    }


def _checkpoints_na_janela(payload: dict, seq_min: int) -> list[dict]:
    return [cp for cp in payload["checkpoints"] if int(cp.get("seq_global") or 0) >= seq_min]


@pytest.fixture
def cache_patches(monkeypatch):
    monkeypatch.setattr(svc, "MAX_FULL_REPLAY_EVENTS", 50)
    monkeypatch.setattr(svc, "STATE_CACHE_INTERVAL", 20)
    monkeypatch.setattr(svc, "STATE_CACHE_ENABLED", True)
    return svc


def test_frio_popula_cache_e_morno_retoma_com_paridade(cache_patches, tmp_path):
    log_dir = tmp_path / "cap"
    log_dir.mkdir()
    sid = _gerar_sessao(str(log_dir), 120)
    cache_dir = str(tmp_path / "cache")

    frio = cache_patches.prepare_session_replay_data(
        str(log_dir), sid, offset=45, limit=20, state_cache_dir=cache_dir)
    assert frio["error"] is None
    assert frio["window"]["state_cache"]["hit"] is False
    assert frio["window"]["state_cache"]["stored"] >= 2  # índices 20 e 40

    morno = cache_patches.prepare_session_replay_data(
        str(log_dir), sid, offset=45, limit=20, state_cache_dir=cache_dir)
    assert morno["error"] is None
    assert morno["window"]["state_cache"]["hit"] is True
    assert morno["window"]["state_cache"]["resumed_from"] == 40

    assert _sumario(morno) == _sumario(frio)

    seq_primeiro_evento = morno["events"][0]["seq_global"]
    assert _checkpoints_na_janela(morno, seq_primeiro_evento) == \
        _checkpoints_na_janela(frio, seq_primeiro_evento)


def test_segunda_janela_mais_profunda_estende_cache(cache_patches, tmp_path):
    log_dir = tmp_path / "cap"
    log_dir.mkdir()
    sid = _gerar_sessao(str(log_dir), 120)
    cache_dir = str(tmp_path / "cache")

    cache_patches.prepare_session_replay_data(str(log_dir), sid, offset=45, limit=20, state_cache_dir=cache_dir)
    profundo = cache_patches.prepare_session_replay_data(str(log_dir), sid, offset=85, limit=20, state_cache_dir=cache_dir)
    assert profundo["window"]["state_cache"]["hit"] is True
    # primeira execução persistiu 20/40 (pré-janela) e 60 (dentro da janela)
    assert profundo["window"]["state_cache"]["resumed_from"] == 60

    referencia = cache_patches.prepare_session_replay_data(
        str(log_dir), sid, offset=85, limit=20, state_cache_dir=str(tmp_path / "cache-vazio"))
    assert _sumario(profundo) == _sumario(referencia)

    # a execução profunda estendeu o cache: a próxima retoma de 80
    mais_profundo = cache_patches.prepare_session_replay_data(
        str(log_dir), sid, offset=105, limit=15, state_cache_dir=cache_dir)
    assert mais_profundo["window"]["state_cache"]["resumed_from"] == 100


def test_deterministic_input_antes_do_resume_nao_bloqueia_retomada(cache_patches, tmp_path):
    """deterministic_input fora da janela não é materializado (contrato de
    paginação: eventos pertencem à sua janela) e não impede a retomada."""
    log_dir = tmp_path / "cap"
    log_dir.mkdir()
    sid = _gerar_sessao(str(log_dir), 120, deterministicos=(10, 50))
    cache_dir = str(tmp_path / "cache")

    cache_patches.prepare_session_replay_data(str(log_dir), sid, offset=45, limit=20, state_cache_dir=cache_dir)
    morno = cache_patches.prepare_session_replay_data(str(log_dir), sid, offset=45, limit=20, state_cache_dir=cache_dir)
    assert morno["window"]["state_cache"]["hit"] is True
    assert morno["window"]["state_cache"]["resumed_from"] == 40

    referencia = cache_patches.prepare_session_replay_data(
        str(log_dir), sid, offset=45, limit=20, state_cache_dir=str(tmp_path / "cache-vazio"))
    assert _sumario(morno) == _sumario(referencia)

    # apenas o deterministic_input da janela (índice 50) é materializado;
    # o do índice 10 (fora da janela) não aparece em nenhuma das execuções
    assert len(morno["deterministic_events"]) == 1
    assert len(referencia["deterministic_events"]) == 1
    assert morno["deterministic_events"][0]["seq_global"] == \
        referencia["deterministic_events"][0]["seq_global"]


def test_reconexoes_no_meio_do_stream_nao_bloqueiam_retomada(cache_patches, tmp_path):
    """Pares session_end/session_start intermediários (capturas com
    reconexão reutilizando o session_id, caso real da captura 20 do MIG24)
    não impedem a retomada: os efeitos na engine já estão no estado
    restaurado e a fase de skip faz só o bookkeeping dos campos do payload."""
    log_dir = tmp_path / "cap"
    log_dir.mkdir()
    sid = _gerar_sessao(str(log_dir), 120, reconexoes=(10, 30), deterministicos=(50,))
    cache_dir = str(tmp_path / "cache")

    cache_patches.prepare_session_replay_data(str(log_dir), sid, offset=45, limit=20, state_cache_dir=cache_dir)
    morno = cache_patches.prepare_session_replay_data(str(log_dir), sid, offset=45, limit=20, state_cache_dir=cache_dir)
    assert morno["window"]["state_cache"]["hit"] is True
    assert morno["window"]["state_cache"]["resumed_from"] == 40

    referencia = cache_patches.prepare_session_replay_data(
        str(log_dir), sid, offset=45, limit=20, state_cache_dir=str(tmp_path / "cache-vazio"))
    assert _sumario(morno) == _sumario(referencia)
    # bookkeeping dos limites de sessão preservado na retomada
    assert morno["session_start"] == referencia["session_start"]
    assert morno["session_end"] == referencia["session_end"]

    seq_primeiro_evento = morno["events"][0]["seq_global"]
    assert _checkpoints_na_janela(morno, seq_primeiro_evento) == \
        _checkpoints_na_janela(referencia, seq_primeiro_evento)


def test_alteracao_na_captura_invalida_cache(cache_patches, tmp_path):
    log_dir = tmp_path / "cap"
    log_dir.mkdir()
    sid = _gerar_sessao(str(log_dir), 120)
    cache_dir = str(tmp_path / "cache")

    cache_patches.prepare_session_replay_data(str(log_dir), sid, offset=45, limit=20, state_cache_dir=cache_dir)
    with open(log_dir / "audit-000001.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps({
            "type": "bytes", "session_id": sid, "seq_global": 9999,
            "seq_session": 9999, "ts_ms": 99999, "dir": "out",
            "data_b64": base64.b64encode(b"extra\r\n").decode(), "n": 7,
        }) + "\n")

    depois = cache_patches.prepare_session_replay_data(str(log_dir), sid, offset=45, limit=20, state_cache_dir=cache_dir)
    assert depois["window"]["state_cache"]["hit"] is False

"""Testes da política de checkpoints na janela de replay (dívida X6).

Contexto: em regiões esparsas da captura (eventos a mais de
CHECKPOINT_TIME_INTERVAL_MS de distância no relógio — caso real da captura
20 do MIG24), a regra de checkpoint por intervalo de tempo gerava um
checkpoint com render completo (encode_snapshot_compact) por evento OUT da
janela — ~6 s dos ~12 s remanescentes da janela profunda morna. Dentro da
janela materializada, esses checkpoints são redundantes: cada evento OUT já
carrega diff + assinaturas, e o tamanho da janela limita o custo de seek.

Contrato coberto:
  1. janela esparsa não gera checkpoints interval_time/interval_events —
     mantidos a âncora da janela (primeiro OUT materializado, base de seek
     direto em janela profunda) e os checkpoints semânticos
     (ris/clear/resize);
  2. eventos, diffs e snapshots da janela são preservados (paridade
     frio×morno);
  3. modo completo (sessão pequena, win_end=None) mantém a política de
     intervalos inalterada;
  4. o primeiro evento OUT da janela é checkpoint (âncora de seek).

Run:
  PYTHONPATH=gateway python3 -m pytest tests/test_replay_checkpoint_policy_unit.py -v
"""
from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from control.services import session_replay_service as svc


def _gerar_sessao_esparsa(tmpdir: str, n_eventos: int) -> str:
    """Sessão com eventos a 10 s de distância (dispara interval_time em
    todo OUT) e clear-screen a cada 7 OUTs (checkpoint semântico)."""
    sid = "sessao-checkpoint-x6"
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
                "seq_session": seq, "ts_ms": 1000 + i * 10000,
                "dir": "out" if out else "in",
                "data_b64": data,
                "n": len(base64.b64decode(data)),
            }) + "\n")
        seq += 1
        f.write(json.dumps({
            "type": "session_end", "session_id": sid, "seq_global": seq,
            "seq_session": seq, "ts_ms": 1000 + n_eventos * 10000,
        }) + "\n")
    return sid


@pytest.fixture
def patches(monkeypatch):
    monkeypatch.setattr(svc, "MAX_FULL_REPLAY_EVENTS", 50)
    monkeypatch.setattr(svc, "STATE_CACHE_INTERVAL", 20)
    monkeypatch.setattr(svc, "STATE_CACHE_ENABLED", True)
    return svc


def _checkpoints_da_janela(payload: dict, offset: int, limit: int) -> list[dict]:
    """Checkpoints cujo seq_global pertence aos eventos da janela."""
    seqs = {ev["seq_global"] for ev in payload["events"]}
    return [cp for cp in payload["checkpoints"] if int(cp.get("seq_global") or 0) in seqs]


def test_janela_esparsa_nao_gera_checkpoint_por_intervalo(patches, tmp_path):
    log_dir = tmp_path / "cap"
    log_dir.mkdir()
    sid = _gerar_sessao_esparsa(str(log_dir), 200)
    cache_dir = str(tmp_path / "cache")

    # aquece o cache de estado e executa a janela morna
    patches.prepare_session_replay_data(str(log_dir), sid, offset=45, limit=20, state_cache_dir=cache_dir)
    morno = patches.prepare_session_replay_data(str(log_dir), sid, offset=60, limit=40, state_cache_dir=cache_dir)
    assert morno["error"] is None
    assert morno["window"]["state_cache"]["hit"] is True

    cps = _checkpoints_da_janela(morno, 60, 40)
    reasons = [cp.get("reason") for cp in cps]
    # sem a otimização, todo OUT da janela esparsa viraria checkpoint
    # interval_time (~27 renders); com ela, sobram âncora + semânticos
    assert "interval_time" not in reasons
    assert "interval_events" not in reasons
    assert reasons.count("window_start") == 1
    assert all(r in ("window_start", "clear_screen", "ris", "resize") for r in reasons)
    assert len(cps) < 10  # era ~27 antes da política


def test_primeiro_out_da_janela_e_checkpoint_ancora(patches, tmp_path):
    log_dir = tmp_path / "cap"
    log_dir.mkdir()
    sid = _gerar_sessao_esparsa(str(log_dir), 200)
    cache_dir = str(tmp_path / "cache")

    patches.prepare_session_replay_data(str(log_dir), sid, offset=45, limit=20, state_cache_dir=cache_dir)
    morno = patches.prepare_session_replay_data(str(log_dir), sid, offset=60, limit=40, state_cache_dir=cache_dir)

    primeiro_out = next(ev for ev in morno["events"] if ev["direction"] == "out")
    assert primeiro_out.get("is_checkpoint") is True
    cps = _checkpoints_da_janela(morno, 60, 40)
    ancora = next(cp for cp in cps if cp.get("reason") == "window_start")
    assert int(ancora["seq_global"]) == primeiro_out["seq_global"]


def test_paridade_frio_morno_com_nova_politica(patches, tmp_path):
    log_dir = tmp_path / "cap"
    log_dir.mkdir()
    sid = _gerar_sessao_esparsa(str(log_dir), 200)
    cache_dir = str(tmp_path / "cache")

    frio = patches.prepare_session_replay_data(str(log_dir), sid, offset=60, limit=40, state_cache_dir=cache_dir)
    morno = patches.prepare_session_replay_data(str(log_dir), sid, offset=60, limit=40, state_cache_dir=cache_dir)
    assert morno["window"]["state_cache"]["hit"] is True

    assert morno["events"] == frio["events"]
    assert list(morno["timeline"]) == list(frio["timeline"])
    assert list(morno["playback"]) == list(frio["playback"])
    assert morno["final_snapshot"] == frio["final_snapshot"]
    assert morno["canonical_signatures"] == frio["canonical_signatures"]


def test_modo_completo_mantem_checkpoints_por_intervalo(patches, tmp_path):
    """Sessão pequena (modo completo, win_end=None): a política de
    intervalos é inalterada — os diffs não substituem os checkpoints porque
    a sessão inteira está materializada (seek até 20k eventos)."""
    log_dir = tmp_path / "cap"
    log_dir.mkdir()
    sid = _gerar_sessao_esparsa(str(log_dir), 30)

    completo = patches.prepare_session_replay_data(str(log_dir), sid, state_cache_dir=str(tmp_path / "cache"))
    assert completo["error"] is None
    assert completo["window"]["partial_state"] is False

    reasons = [cp.get("reason") for cp in completo["checkpoints"]]
    # eventos a 10 s de distância: interval_time deve aparecer
    assert "interval_time" in reasons

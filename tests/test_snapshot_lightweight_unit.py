"""Caminho leve de snapshot para o wait de checkpoint (sem cells).

Medido no AIX (captura 13, run 93): ``wait_compare_cpu_ms`` = 101,5s dos
122,4s de ``sync_wait_ms`` — a fatia dominante é o ``cells`` (2000
``to_dict`` por snapshot) que o wait NÃO consome (só assinaturas +
texto). ``engine.snapshot(include_cells=False)`` pula a montagem das
células com assinaturas byte-idênticas, e o ``canonical_snapshot_now``
reusa o texto já serializado em vez de serializar de novo.
"""
from __future__ import annotations

from dakota_gateway.replay import ReplayConfig, _TargetSession
from dakota_gateway.screen import TerminalScreenState, build_screen_snapshot
from dakota_terminal.engine import TerminalEngine

RAW = ("\x1b[2J\x1b[H" + "\r\n".join("LINHA %02d " % i + "X" * 60 for i in range(25))).encode()


def _engine() -> TerminalEngine:
    eng = TerminalEngine(rows=25, cols=80, encoding="utf-8")
    eng.feed_bytes(RAW)
    return eng


def test_snapshot_sem_cells_mantem_assinaturas_identicas():
    """include_cells=False NÃO pode mudar nenhuma assinatura (contrato)."""
    eng = _engine()
    full = eng.snapshot()
    light = eng.snapshot(include_cells=False)
    for key in ("text_sig", "visual_sig", "semantic_sig"):
        assert light[key] == full[key], key
    assert light["cells"] == []
    assert full["cells"], "o snapshot completo deve manter as células"


def test_snapshot_default_continua_com_cells():
    """O default (include_cells=True) é intocável: consumidores de diffs e
    do replay visual dependem das células."""
    eng = _engine()
    snap = eng.snapshot()
    assert len(snap["cells"]) == 25 * 80


def test_canonical_snapshot_now_rapido_mantem_mesmos_valores():
    """O caminho leve do _TargetSession produz exatamente as mesmas 4
    assinaturas + screen_text do caminho completo."""
    cfg = ReplayConfig(log_dir="/tmp", target_host="local")
    sess = _TargetSession(cfg, "s1")
    sess.screen_state.feed_bytes(RAW)
    snap = sess.canonical_snapshot_now()
    # Referência: os mesmos valores computados pelo caminho completo.
    text = sess.screen_state.text()
    legacy = build_screen_snapshot(text)
    full = sess.screen_state.engine.snapshot()
    assert snap["text_sig"] == full["text_sig"]
    assert snap["visual_sig"] == full["visual_sig"]
    assert snap["semantic_sig"] == (full.get("semantic_sig") or legacy.screen_sig)
    assert snap["screen_sig"] == legacy.screen_sig
    assert snap["screen_text"] == text

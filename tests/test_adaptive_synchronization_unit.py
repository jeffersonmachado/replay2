#!/usr/bin/env python3
"""Testes de sincronização: quiet point × convergência e evidência de
type-ahead humano (§7, §24, §16.11–12)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "gateway"))

from dakota_gateway.replay_control.synchronization import (
    extract_capture_typeahead_evidence,
    extract_typeahead_evidence,
    quiet_points,
)


def _ev(seq, ts, typ="bytes", dir_="in", sid="s1"):
    ev = {"seq_global": seq, "ts_ms": ts, "type": typ, "session_id": sid}
    if typ == "bytes":
        ev["dir"] = dir_
    return ev


class QuietPointTests(unittest.TestCase):
    def test_quiet_point_apos_burst(self):
        events = [
            _ev(1, 1000, dir_="out"),
            _ev(2, 1050, dir_="out"),
            _ev(3, 1300, dir_="out"),
        ]
        pts = quiet_points(events, stable_ms=150)
        # entre 1050→1300 há 250ms de silêncio: quiet point em 1050+150=1200
        self.assertIn((1050, 1200), pts)
        # gap de 50ms entre 1000→1050 NÃO é quiet point
        self.assertNotIn((1000, 1150), pts)

    def test_silencio_temporario_seguido_de_bytes_nao_e_convergencia(self):
        """§24: quiet != convergência — o quiet point é reportado, mas cabe ao
        chamador exigir estado esperado para decisões críticas."""
        events = [
            _ev(1, 1000, dir_="out"),
            _ev(2, 1400, dir_="out"),  # "aguarde..." → silêncio → continua
            _ev(3, 1500, dir_="out"),
        ]
        pts = quiet_points(events, stable_ms=150)
        self.assertIn((1000, 1150), pts)
        self.assertIn((1500, 1650), pts)


class TypeaheadEvidenceTests(unittest.TestCase):
    def test_typeahead_detectado(self):
        """Operador enviou B e C enquanto o terminal ainda produzia output."""
        events = [
            _ev(1, 1000, dir_="in"),    # send A
            _ev(2, 1040, dir_="out"),   # resposta começa
            _ev(3, 1060, dir_="in"),    # send B durante output
            _ev(4, 1090, dir_="out"),
            _ev(5, 1120, dir_="in"),    # send C durante output
            _ev(6, 1200, dir_="out"),   # terminal ainda produzindo após C
            _ev(7, 5000, typ="session_end"),
        ]
        ev = extract_typeahead_evidence(events, stable_ms=150)
        self.assertEqual(len(ev), 2)  # B e C foram type-ahead
        self.assertEqual(ev[0].input_seq, 3)
        self.assertEqual(ev[1].input_seq, 5)
        for e in ev:
            self.assertTrue(e.output_active)
            self.assertFalse(e.checkpoint_between)

    def test_sem_typeahead_quando_terminal_ocioso(self):
        events = [
            _ev(1, 1000, dir_="in"),
            _ev(2, 1010, dir_="out"),
            _ev(3, 2000, dir_="in"),  # muito depois do quiet point (1160)
            _ev(4, 5000, typ="session_end"),
        ]
        ev = extract_typeahead_evidence(events, stable_ms=150)
        self.assertEqual(ev, [])

    def test_checkpoint_entre_acoes_invalidate_seguranca(self):
        events = [
            _ev(1, 1000, dir_="in"),
            _ev(2, 1040, dir_="out"),
            _ev(3, 1060, dir_="in"),
            _ev(4, 1080, typ="checkpoint"),
            _ev(5, 1200, dir_="out"),
            _ev(6, 5000, typ="session_end"),
        ]
        ev = extract_typeahead_evidence(events, stable_ms=150)
        self.assertEqual(len(ev), 1)
        self.assertTrue(ev[0].checkpoint_between)
        self.assertFalse(ev[0].safe)

    def test_evidencia_segura_exige_convergencia(self):
        """Sessão sem resposta posterior / sem fim limpo não gera evidência segura."""
        events = [
            _ev(1, 1000, dir_="in"),
            _ev(2, 1040, dir_="out"),
            _ev(3, 1060, dir_="in"),
            # sessão morre sem output posterior e sem session_end
        ]
        ev = extract_typeahead_evidence(events, stable_ms=150)
        self.assertEqual(len(ev), 1)
        self.assertFalse(ev[0].converged)
        self.assertFalse(ev[0].safe)

    def test_evidencia_segura_completa(self):
        events = [
            _ev(1, 1000, dir_="in"),
            _ev(2, 1040, dir_="out"),
            _ev(3, 1060, dir_="in"),
            _ev(4, 1200, dir_="out"),  # sistema respondeu depois
            _ev(5, 5000, typ="session_end"),
        ]
        ev = extract_typeahead_evidence(events, stable_ms=150)
        self.assertEqual(len(ev), 1)
        self.assertTrue(ev[0].safe)
        d = ev[0].to_dict()
        for key in ("session_id", "input_seq", "prev_input_seq", "input_ts",
                    "quiet_point_ts", "output_active", "checkpoint_between",
                    "converged", "safe", "stable_ms"):
            self.assertIn(key, d)

    def test_sem_evidencia_para_sessao_desconhecida(self):
        ev = extract_typeahead_evidence([], stable_ms=150)
        self.assertEqual(ev, [])


class CaptureLevelEvidenceTests(unittest.TestCase):
    """extract_capture_typeahead_evidence: captura → params da run (§7)."""

    def _write_capture(self, tmp: str) -> None:
        import base64
        import json

        def b64(t: str) -> str:
            return base64.b64encode(t.encode()).decode()

        lines = [
            {"seq_global": 1, "ts_ms": 900, "type": "session_start",
             "session_id": "s1", "seq_session": 1, "rows": 25, "cols": 80},
            {"seq_global": 2, "ts_ms": 1000, "type": "bytes", "dir": "in",
             "session_id": "s1", "seq_session": 2, "data_b64": b64("A")},
            {"seq_global": 3, "ts_ms": 1040, "type": "bytes", "dir": "out",
             "session_id": "s1", "seq_session": 3, "data_b64": b64("echo")},
            {"seq_global": 4, "ts_ms": 1060, "type": "bytes", "dir": "in",
             "session_id": "s1", "seq_session": 4, "data_b64": b64("B")},
            {"seq_global": 5, "ts_ms": 1200, "type": "bytes", "dir": "out",
             "session_id": "s1", "seq_session": 5, "data_b64": b64("echo")},
            {"seq_global": 6, "ts_ms": 5000, "type": "session_end",
             "session_id": "s1", "seq_session": 6},
        ]
        from pathlib import Path as P
        (P(tmp) / "audit-h.part001.jsonl").write_text(
            "\n".join(json.dumps(ev) for ev in lines), encoding="utf-8",
        )

    def test_extrai_seqs_seguros_da_captura(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            self._write_capture(tmp)
            evidence = extract_capture_typeahead_evidence(tmp, {}, stable_ms=150)
        self.assertEqual(evidence, {"s1": [4]})

    def test_captura_sem_typeahead_gera_mapa_vazio(self):
        import base64
        import json
        import tempfile
        from pathlib import Path as P
        with tempfile.TemporaryDirectory() as tmp:
            lines = [
                {"seq_global": 1, "ts_ms": 900, "type": "session_start",
                 "session_id": "s1", "seq_session": 1, "rows": 25, "cols": 80},
                {"seq_global": 2, "ts_ms": 1000, "type": "bytes", "dir": "in",
                 "session_id": "s1", "seq_session": 2,
                 "data_b64": base64.b64encode(b"A").decode()},
                {"seq_global": 3, "ts_ms": 1010, "type": "bytes", "dir": "out",
                 "session_id": "s1", "seq_session": 3,
                 "data_b64": base64.b64encode(b"e").decode()},
                {"seq_global": 4, "ts_ms": 3000, "type": "bytes", "dir": "in",
                 "session_id": "s1", "seq_session": 4,
                 "data_b64": base64.b64encode(b"B").decode()},
                {"seq_global": 5, "ts_ms": 5000, "type": "session_end",
                 "session_id": "s1", "seq_session": 5},
            ]
            (P(tmp) / "audit-h.part001.jsonl").write_text(
                "\n".join(json.dumps(ev) for ev in lines), encoding="utf-8",
            )
            evidence = extract_capture_typeahead_evidence(tmp, {}, stable_ms=150)
        self.assertEqual(evidence, {})


if __name__ == "__main__":
    unittest.main()

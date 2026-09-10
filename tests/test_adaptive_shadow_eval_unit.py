"""Testes da avaliação offline de shadow mode sobre capturas (§19–§20).

``replay_control.shadow_eval.evaluate_capture`` reusa ShadowTracker +
AdaptiveScheduler + extract_typeahead_evidence sobre os audit-*.jsonl de
uma captura e produz o relatório de critério de promoção, verificando em
dados reais: equivalência de bytes do batching (§16.7), inviolabilidade do
checkpoint e ``false_safe_decisions == 0``.
"""
from __future__ import annotations

import base64
import json
import tempfile
import unittest
from pathlib import Path

from dakota_gateway.replay_control.shadow_eval import (
    evaluate_capture,
    evaluate_captures,
)


def _b64(text: str) -> str:
    return base64.b64encode(text.encode()).decode()


def _ev(seq, ts, typ, sid, **kw):
    ev = {"seq_global": seq, "ts_ms": ts, "type": typ, "session_id": sid,
          "seq_session": seq}
    ev.update(kw)
    return ev


def _write_capture(root: Path) -> None:
    """Captura com 4 sessões: type-ahead seguro, sem evidência, split por
    checkpoint e sessão não convergente."""
    lines = [
        # s1 — type-ahead humano comprovado: B e C enviados durante output
        _ev(1, 900, "session_start", "s1", rows=25, cols=80),
        _ev(2, 1000, "bytes", "s1", dir="in", data_b64=_b64("A")),
        _ev(3, 1010, "bytes", "s1", dir="out", data_b64=_b64("e")),
        _ev(4, 1040, "bytes", "s1", dir="in", data_b64=_b64("B")),
        _ev(5, 1050, "bytes", "s1", dir="out", data_b64=_b64("e")),
        _ev(6, 1060, "bytes", "s1", dir="in", data_b64=_b64("C")),
        _ev(7, 1070, "bytes", "s1", dir="out", data_b64=_b64("e")),
        _ev(8, 5000, "session_end", "s1"),
        # s2 — mesma sequência imprimível, mas DEPOIS do quiet point:
        # candidato a batch, porém sem evidência → rejeitado
        _ev(10, 900, "session_start", "s2", rows=25, cols=80),
        _ev(11, 1000, "bytes", "s2", dir="in", data_b64=_b64("X")),
        _ev(12, 1010, "bytes", "s2", dir="out", data_b64=_b64("e")),
        _ev(13, 3000, "bytes", "s2", dir="in", data_b64=_b64("Y")),
        _ev(14, 3010, "bytes", "s2", dir="out", data_b64=_b64("e")),
        _ev(15, 5000, "bytes", "s2", dir="in", data_b64=_b64("Z")),
        _ev(16, 5010, "bytes", "s2", dir="out", data_b64=_b64("e")),
        _ev(17, 9000, "session_end", "s2"),
        # s3 — checkpoint corta a run imprimível (barreira respeitada)
        _ev(20, 900, "session_start", "s3", rows=25, cols=80),
        _ev(21, 1000, "bytes", "s3", dir="in", data_b64=_b64("1")),
        _ev(22, 1100, "checkpoint", "s3", screen_sig="sig1"),
        _ev(23, 1200, "bytes", "s3", dir="in", data_b64=_b64("2")),
        _ev(24, 2000, "session_end", "s3"),
        # s4 — parece type-ahead mas a sessão NÃO convergiu (sem
        # session_end): evidência proibida + divergência marcada
        _ev(30, 900, "session_start", "s4", rows=25, cols=80),
        _ev(31, 1000, "bytes", "s4", dir="in", data_b64=_b64("P")),
        _ev(32, 1010, "bytes", "s4", dir="out", data_b64=_b64("e")),
        _ev(33, 1040, "bytes", "s4", dir="in", data_b64=_b64("Q")),
    ]
    (root / "audit-h.part001.jsonl").write_text(
        "\n".join(json.dumps(ev) for ev in lines), encoding="utf-8",
    )


class EvaluateCaptureTests(unittest.TestCase):
    def test_relatorio_de_promocao_sobre_captura(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_capture(Path(tmp))
            rep = evaluate_capture(tmp, {}, stable_ms=150, speed=1.0)

        self.assertEqual(rep["sessions"], 4)
        self.assertEqual(rep["total_actions"], 10)
        self.assertEqual(rep["batch_candidates"], 3)      # s1, s2 e s4
        self.assertEqual(rep["safe_candidates"], 1)       # só s1 (evidência)
        self.assertEqual(rep["rejected_candidates"], 2)   # s2 e s4
        self.assertEqual(rep["potential_saved_ms"], 60)   # 40 + 20 da s1
        self.assertEqual(rep["checkpoint_run_splits"], 1)  # s3
        self.assertEqual(rep["checkpoint_violations"], 0)
        self.assertEqual(rep["checkpoint_crossing_attempts"], 0)
        self.assertEqual(rep["divergences"], 1)           # s4 sem session_end
        self.assertEqual(rep["false_safe_decisions"], 0)
        self.assertEqual(rep["writes_before"], 10)
        self.assertEqual(rep["writes_after"], 8)          # s1 funde 3→1
        self.assertTrue(rep["bytes_equivalent"])
        self.assertEqual(rep["evidence_sessions"], ["s1"])

    def test_sem_checkpoint_nao_ha_split(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lines = [
                _ev(1, 900, "session_start", "s1", rows=25, cols=80),
                _ev(2, 1000, "bytes", "s1", dir="in", data_b64=_b64("A")),
                _ev(3, 5000, "session_end", "s1"),
            ]
            (root / "audit-h.part001.jsonl").write_text(
                "\n".join(json.dumps(ev) for ev in lines), encoding="utf-8",
            )
            rep = evaluate_capture(tmp, {}, stable_ms=150)
        self.assertEqual(rep["checkpoint_run_splits"], 0)
        self.assertEqual(rep["batch_candidates"], 0)
        self.assertEqual(rep["false_safe_decisions"], 0)
        self.assertTrue(rep["bytes_equivalent"])

    def test_speed_ajusta_pacing_pago(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_capture(Path(tmp))
            rep = evaluate_capture(tmp, {}, stable_ms=150, speed=2.0)
        # pacing pago cai pela metade; a economia prevista acompanha
        self.assertEqual(rep["potential_saved_ms"], 30)
        self.assertEqual(rep["safe_candidates"], 1)


class EvaluateCapturesTests(unittest.TestCase):
    def test_agrega_capturas_e_tolera_linhas_malformadas(self):
        # linhas JSONL quebradas são ignoradas pela janela (comportamento
        # de index_session_events) — a captura segue válida
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cap_ok = root / "cap-ok"
            cap_ok.mkdir()
            _write_capture(cap_ok)
            cap_bad = root / "cap-bad"
            cap_bad.mkdir()
            (cap_bad / "audit-x.part001.jsonl").write_text(
                "{linha quebrada\n", encoding="utf-8",
            )
            vazio = root / "cap-vazia"  # sem audit-*.jsonl: ignorada
            vazio.mkdir()
            rep = evaluate_captures(str(root), stable_ms=150)

        self.assertEqual(rep["totals"]["captures_ok"], 2)
        self.assertEqual(rep["totals"]["captures_error"], 0)
        self.assertEqual(rep["totals"]["total_actions"], 10)
        self.assertEqual(rep["totals"]["false_safe_decisions"], 0)
        self.assertEqual(rep["totals"]["checkpoint_violations"], 0)
        self.assertTrue(rep["totals"]["bytes_equivalent"])
        self.assertTrue(rep["totals"]["promotion_criteria_met"])

    def test_falha_numa_captura_nao_derruba_as_demais(self):
        from unittest import mock

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cap_ok = root / "cap-ok"
            cap_ok.mkdir()
            _write_capture(cap_ok)
            with mock.patch(
                "dakota_gateway.replay_control.shadow_eval.evaluate_capture",
                side_effect=RuntimeError("boom"),
            ):
                rep = evaluate_captures(str(root), stable_ms=150)
        self.assertEqual(rep["totals"]["captures_ok"], 0)
        self.assertEqual(rep["totals"]["captures_error"], 1)
        self.assertIn("boom", rep["captures"][0]["error"])


if __name__ == "__main__":
    unittest.main()


class ShadowEvalCliLocaleTest(unittest.TestCase):
    """Regressão AIX: o resumo do CLI crashava com UnicodeEncodeError quando o
    stdout tinha encoding latin-1 (locale POSIX do AIX), por causa do '→' na
    linha de writes — o relatório JSON era gravado, mas o script saía com
    traceback e exit code != 0."""

    def test_resumo_nao_crasha_com_stdout_latin1(self):
        import importlib.util
        import io
        import sys
        from unittest import mock

        script = (
            Path(__file__).resolve().parent.parent
            / "scripts" / "shadow_eval_adaptive_replay.py"
        )
        spec = importlib.util.spec_from_file_location("shadow_eval_cli", script)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "captures"
            cap = root / "cap-1"
            cap.mkdir(parents=True)
            _write_capture(cap)
            out = Path(tmp) / "rel.json"
            buf = io.BytesIO()
            latin1_stdout = io.TextIOWrapper(buf, encoding="latin-1")
            argv = [
                "shadow_eval_adaptive_replay.py",
                "--captures-dir", str(root),
                "--out", str(out),
            ]
            with mock.patch.object(sys, "argv", argv), \
                    mock.patch.object(sys, "stdout", latin1_stdout):
                rc = mod.main()
            latin1_stdout.flush()
            texto = buf.getvalue().decode("latin-1")
            totals = json.loads(out.read_text(encoding="utf-8"))["totals"]

        self.assertEqual(rc, 0)
        self.assertIn("false_safe_decisions=0", texto)
        self.assertIn("promotion_criteria_met=True", texto)
        self.assertTrue(totals)

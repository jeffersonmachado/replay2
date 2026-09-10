#!/usr/bin/env python3
"""Testes do adaptive scheduler + shadow mode (§6, §8, §18–20, §16.6–13, 17)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "gateway"))

from dakota_gateway.replay_control.adaptive_scheduler import (
    AdaptiveScheduler,
    SchedulerOp,
    ShadowTracker,
)
from dakota_gateway.replay_control.safety_guard import SafetyGuard


def _in(seq, ts, text, key_kind="printable"):
    import base64
    return {
        "seq_global": seq, "ts_ms": ts, "type": "bytes", "dir": "in",
        "session_id": "s1", "data_b64": base64.b64encode(text.encode()).decode(),
        "key_kind": key_kind, "n": len(text.encode()),
    }


def _cp(seq, ts):
    return {"seq_global": seq, "ts_ms": ts, "type": "checkpoint", "session_id": "s1",
            "screen_sig": "abc"}


def _out(seq, ts):
    return {"seq_global": seq, "ts_ms": ts, "type": "bytes", "dir": "out", "session_id": "s1"}


class PlanEventsTests(unittest.TestCase):
    def test_sequencia_imprimivel_vira_batch(self):
        """§16.6: chars imprimíveis contíguos, sem barreira, viram BATCH."""
        sch = AdaptiveScheduler()
        events = [_in(i, 1000, c) for i, c in enumerate("ABC", start=1)]
        decisions = sch.plan_events(events)
        self.assertEqual(len(decisions), 1)
        self.assertEqual(decisions[0].op, SchedulerOp.BATCH)
        self.assertEqual(decisions[0].reason_code, "CONTIGUOUS_PRINTABLE_INPUT")
        self.assertEqual(decisions[0].action_count, 3)

    def test_batch_preserva_bytes_exatos(self):
        """§16.7: aplicar as decisões produz exatamente os mesmos bytes."""
        import base64
        sch = AdaptiveScheduler()
        events = [_in(i, 1000, c) for i, c in enumerate("HELLO", start=1)]
        decisions = sch.plan_events(events)
        merged = sch.apply_decisions(events, decisions)
        original = b"".join(
            base64.b64decode(ev["data_b64"]) for ev in events
        )
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0][0], original)

    def test_checkpoint_impede_batch_atraves_fronteira(self):
        """§16.8: checkpoint é fronteira dura de batching."""
        sch = AdaptiveScheduler()
        events = [_in(1, 1000, "A"), _in(2, 1000, "B"), _cp(3, 1100),
                  _in(4, 1200, "C"), _in(5, 1200, "D")]
        decisions = sch.plan_events(events)
        ops = [d.op for d in decisions]
        self.assertEqual(ops.count(SchedulerOp.BATCH), 2)
        self.assertIn(SchedulerOp.WAIT_FOR_STATE, ops)
        cp_decision = [d for d in decisions if d.op == SchedulerOp.WAIT_FOR_STATE][0]
        self.assertEqual(cp_decision.reason_code, "CHECKPOINT_REQUIRED")
        for d in decisions:
            if d.op == SchedulerOp.BATCH:
                self.assertEqual(d.action_count, 2)

    def test_function_key_cria_barrier(self):
        """§16.9: teclas críticas são barreiras, nunca entram em batch."""
        sch = AdaptiveScheduler()
        events = [_in(1, 1000, "A"), _in(2, 1000, "B"),
                  _in(3, 1000, "\x1b[21~", key_kind="function_key"),
                  _in(4, 1100, "C")]
        decisions = sch.plan_events(events)
        barrier = [d for d in decisions if d.op == SchedulerOp.BARRIER]
        self.assertEqual(len(barrier), 1)
        self.assertEqual(barrier[0].reason_code, "FUNCTION_KEY_BARRIER")

    def test_enter_e_esc_sao_barreiras(self):
        sch = AdaptiveScheduler()
        events = [_in(1, 1000, "A"), _in(2, 1000, "\r", key_kind="enter"),
                  _in(3, 1100, "\x1b", key_kind="esc")]
        decisions = sch.plan_events(events)
        reasons = [d.reason_code for d in decisions if d.op == SchedulerOp.BARRIER]
        self.assertEqual(len(reasons), 2)

    def test_unknown_usa_fallback_conservador(self):
        """§16.10: ação desconhecida → FALLBACK_CONSERVATIVE, nunca batch."""
        sch = AdaptiveScheduler()
        events = [_in(1, 1000, "A"), _in(2, 1000, "\x01"),
                  _in(3, 1000, "B")]
        decisions = sch.plan_events(events)
        fb = [d for d in decisions if d.op == SchedulerOp.FALLBACK_CONSERVATIVE]
        self.assertTrue(fb)
        self.assertEqual(fb[0].reason_code, "UNKNOWN_ACTION")
        self.assertFalse(any(d.op == SchedulerOp.BATCH for d in decisions))

    def test_wait_explicito_e_barreira_com_reason(self):
        sch = AdaptiveScheduler()
        events = [_in(1, 1000, "A"), _in(2, 1250, "", key_kind="wait"),
                  _in(3, 1250, "B")]
        decisions = sch.plan_events(events)
        wait = [d for d in decisions if d.reason_code == "EXPLICIT_WAIT"]
        self.assertEqual(len(wait), 1)
        self.assertEqual(wait[0].op, SchedulerOp.BARRIER)

    def test_collapse_de_delta_positivo_exige_evidencia(self):
        """§16.11–12: fundir ações separadas por pacing > 0 só com evidência."""
        events = [_in(1, 1000, "A"), _in(2, 1500, "B"), _in(3, 2000, "C")]
        sem_evidencia = AdaptiveScheduler().plan_events(events)
        self.assertFalse(any(d.op == SchedulerOp.BATCH for d in sem_evidencia))
        self.assertTrue(any(
            d.op == SchedulerOp.FALLBACK_CONSERVATIVE
            and d.reason_code in ("INSUFFICIENT_EVIDENCE", "NO_EVIDENCE")
            for d in sem_evidencia
        ))
        com_evidencia = AdaptiveScheduler(evidence_seqs={2, 3}).plan_events(events)
        batch = [d for d in com_evidencia if d.op == SchedulerOp.BATCH]
        self.assertTrue(batch)
        self.assertEqual(batch[0].reason_code, "HUMAN_TYPEAHEAD_EVIDENCE")
        self.assertEqual(batch[0].predicted_saved_ms, 1000)

    def test_trilha_sintetica_dispensa_evidencia_de_typeahead(self):
        """Deltas de trilha sintética são artificiais por construção — o
        colapso não destrói informação humana."""
        events = [_in(1, 1000, "A"), _in(2, 1050, "B"), _in(3, 1100, "C")]
        decisions = AdaptiveScheduler(synthetic_trail=True).plan_events(events)
        batch = [d for d in decisions if d.op == SchedulerOp.BATCH]
        self.assertTrue(batch)
        self.assertEqual(batch[0].predicted_saved_ms, 100)

    def test_divergencia_cancela_otimizacao(self):
        """§16.13: após divergência, tudo cai em FALLBACK_CONSERVATIVE."""
        guard = SafetyGuard()
        sch = AdaptiveScheduler(guard=guard, synthetic_trail=True)
        events = [_in(1, 1000, "A"), _in(2, 1000, "B")]
        self.assertTrue(any(d.op == SchedulerOp.BATCH for d in sch.plan_events(events)))
        guard.record_divergence("checkpoint mismatch seq=7")
        decisions = sch.plan_events(events)
        self.assertTrue(all(
            d.op == SchedulerOp.FALLBACK_CONSERVATIVE for d in decisions
        ))
        self.assertEqual(decisions[0].reason_code, "DIVERGED_STATE")

    def test_decisoes_auditaveis(self):
        sch = AdaptiveScheduler()
        events = [_in(1, 1000, "A"), _in(2, 1000, "B")]
        d = sch.plan_events(events)[0].to_dict()
        for key in ("op", "reason_code", "seq_start", "seq_end", "action_count",
                    "predicted_saved_ms", "evidence"):
            self.assertIn(key, d)

    def test_politica_identica_entre_ambientes(self):
        """§16.17: a decisão não depende do ambiente — só de ações/estado."""
        events = [_in(1, 1000, "A"), _in(2, 1000, "B"), _cp(3, 1100)]
        d_aix = AdaptiveScheduler(environment_id="aix-mig24").plan_events(events)
        d_linux = AdaptiveScheduler(environment_id="linux-x86").plan_events(events)
        self.assertEqual(
            [(d.op, d.reason_code) for d in d_aix],
            [(d.op, d.reason_code) for d in d_linux],
        )

    def test_eventos_out_nao_afetam_plano(self):
        sch = AdaptiveScheduler()
        events = [_in(1, 1000, "A"), _out(2, 1010), _in(3, 1000, "B")]
        decisions = sch.plan_events(events)
        self.assertTrue(any(d.op == SchedulerOp.BATCH for d in decisions))


class ShadowTrackerTests(unittest.TestCase):
    """§19–20: shadow mode registra o que FARIA sem alterar execução."""

    def test_shadow_conta_candidatos_e_economia(self):
        tr = ShadowTracker(synthetic_trail=True)
        # execução conservadora: 3 chars com sleeps de 50ms entre eles
        tr.observe(_in(1, 1000, "A"), paced_sleep_ms=0)
        tr.observe(_in(2, 1050, "B"), paced_sleep_ms=50)
        tr.observe(_in(3, 1100, "C"), paced_sleep_ms=50)
        rep = tr.report()
        self.assertEqual(rep["total_actions"], 3)
        self.assertEqual(rep["batch_candidates"], 1)
        self.assertEqual(rep["safe_candidates"], 1)
        self.assertEqual(rep["potential_saved_ms"], 100)
        self.assertEqual(rep["false_safe_decisions"], 0)

    def test_shadow_nao_conta_batch_inseguro(self):
        tr = ShadowTracker()  # sem evidência, trilha real
        tr.observe(_in(1, 1000, "A"), paced_sleep_ms=0)
        tr.observe(_in(2, 1500, "B"), paced_sleep_ms=500)
        rep = tr.report()
        self.assertEqual(rep["batch_candidates"], 1)   # candidato identificado
        self.assertEqual(rep["safe_candidates"], 0)    # mas rejeitado
        self.assertEqual(rep["rejected_candidates"], 1)
        self.assertEqual(rep["potential_saved_ms"], 0)

    def test_shadow_checkpoint_nunca_atravessado(self):
        tr = ShadowTracker(synthetic_trail=True)
        tr.observe(_in(1, 1000, "A"), paced_sleep_ms=0)
        tr.observe(_cp(2, 1100), paced_sleep_ms=0)
        tr.observe(_in(3, 1200, "B"), paced_sleep_ms=0)
        rep = tr.report()
        self.assertEqual(rep["batch_candidates"], 0)
        self.assertEqual(rep["checkpoint_crossing_attempts"], 0)

    def test_divergencia_marca_false_safe(self):
        """§20: critério de promoção — false_safe_decisions deve ser 0."""
        tr = ShadowTracker(synthetic_trail=True)
        tr.observe(_in(1, 1000, "A"), paced_sleep_ms=0)
        tr.observe(_in(2, 1050, "B"), paced_sleep_ms=50)
        tr.note_divergence(2)  # divergência exatamente na região do candidato
        rep = tr.report()
        self.assertEqual(rep["divergences"], 1)
        self.assertEqual(rep["false_safe_decisions"], 1)

    def test_divergencia_fora_da_regiao_nao_e_false_safe(self):
        tr = ShadowTracker(synthetic_trail=True)
        tr.observe(_in(1, 1000, "A"), paced_sleep_ms=0)
        tr.observe(_in(2, 1050, "B"), paced_sleep_ms=50)
        tr.observe(_in(3, 5000, "\r", key_kind="enter"), paced_sleep_ms=0)
        tr.note_divergence(3)
        rep = tr.report()
        self.assertEqual(rep["false_safe_decisions"], 0)


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""Testes da telemetria de execução (§3, §15, §16.14–15).

Definições matemáticas (sem dupla contagem):

- ``journey_total_ms``: soma do tempo de parede das sessões, do primeiro ao
  último evento processado de cada sessão.
- ``erp_response_ms``: soma, sobre os pontos de sincronização, do tempo entre
  o fim do envio que disparou resposta e o último byte de resposta recebido
  antes do estado esperado (tempo atribuível ao ERP/rede).
- ``replay_overhead_ms``: todo o resto — pacing + espera explícita + espera
  de quiet imposta + envio + comparação + residual. Por construção:

      journey_total_ms == erp_response_ms + replay_overhead_ms
      replay_overhead_ms == pacing_ms + explicit_wait_ms + sync_wait_ms
                          + send_ms + compare_ms + other_ms

- ``replay_overhead_ratio = replay_overhead_ms / journey_total_ms`` (0..1).
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "gateway"))

from dakota_gateway.replay_control.execution_telemetry import (
    RunTelemetry,
    SessionTelemetry,
    TelemetryBucket,
)


class SessionTelemetryTests(unittest.TestCase):
    def test_buckets_sao_disjuntos_e_somam_o_total(self):
        t = SessionTelemetry(session_id="s1")
        t.begin_session(1000.0)
        t.record(TelemetryBucket.PACING, 100.0)
        t.record(TelemetryBucket.EXPLICIT_WAIT, 50.0)
        t.record(TelemetryBucket.SEND, 5.0)
        t.record(TelemetryBucket.CHECKPOINT_WAIT, 300.0, erp_ms=250.0)
        t.record(TelemetryBucket.COMPARE, 10.0)
        t.end_session(2000.0)
        snap = t.snapshot()
        self.assertEqual(snap["journey_total_ms"], 1000.0)
        # checkpoint_wait = erp (250) + sync/quiet (50)
        self.assertEqual(snap["checkpoint_wait_ms"], 300.0)
        self.assertEqual(snap["erp_response_ms"], 250.0)
        # sync_wait inclui a porção quiet do checkpoint wait
        self.assertEqual(snap["sync_wait_ms"], 50.0)
        overhead = snap["replay_overhead_ms"]
        self.assertEqual(overhead, 1000.0 - 250.0)
        partes = (
            snap["pacing_ms"] + snap["explicit_wait_ms"] + snap["sync_wait_ms"]
            + snap["send_ms"] + snap["compare_ms"] + snap["other_ms"]
        )
        self.assertAlmostEqual(overhead, partes)

    def test_sem_dupla_contagem_ratio_entre_zero_e_um(self):
        t = SessionTelemetry(session_id="s1")
        t.begin_session(0.0)
        t.record(TelemetryBucket.PACING, 200.0)
        t.record(TelemetryBucket.CHECKPOINT_WAIT, 800.0, erp_ms=700.0)
        t.end_session(1000.0)
        snap = t.snapshot()
        self.assertGreaterEqual(snap["replay_overhead_ratio"], 0.0)
        self.assertLessEqual(snap["replay_overhead_ratio"], 1.0)
        self.assertAlmostEqual(snap["replay_overhead_ratio"], 0.3)

    def test_total_zero_nao_divide_por_zero(self):
        t = SessionTelemetry(session_id="s1")
        t.begin_session(100.0)
        t.end_session(100.0)
        self.assertEqual(t.snapshot()["replay_overhead_ratio"], 0.0)

    def test_wait_compare_cpu_informativo_sem_dupla_contagem(self):
        """wait_compare_cpu_ms é sub-porção INFORMATIVA do checkpoint wait
        (CPU de compare/predicado dentro do wait): acumula, aparece no
        snapshot e NÃO entra no overhead — senão dupla contagem com
        sync_wait/erp (§16.14)."""
        t = SessionTelemetry(session_id="s1")
        t.begin_session(1000.0)
        t.record(TelemetryBucket.CHECKPOINT_WAIT, 500.0, erp_ms=100.0)
        t.record_wait_compare(320.0)
        t.record_wait_compare(30.0)
        t.end_session(1500.0)
        snap = t.snapshot()
        self.assertEqual(snap["wait_compare_cpu_ms"], 350.0)
        self.assertEqual(snap["compare_ms"], 0.0)
        # Invariante intacta: overhead = total - erp (o compare dentro do
        # wait já está em checkpoint_wait/sync_wait — não soma de novo).
        self.assertAlmostEqual(snap["replay_overhead_ms"], 400.0)
        self.assertAlmostEqual(
            snap["replay_overhead_ms"],
            snap["journey_total_ms"] - snap["erp_response_ms"],
        )

    def test_wait_compare_cpu_default_zero_no_snapshot(self):
        t = SessionTelemetry(session_id="s1")
        t.begin_session(0.0)
        t.end_session(10.0)
        self.assertEqual(t.snapshot()["wait_compare_cpu_ms"], 0.0)

    def test_contadores_de_batch(self):
        t = SessionTelemetry(session_id="s1")
        t.begin_session(0.0)
        t.record_batch(action_count=5, saved_ms=200.0)
        t.record_barrier()
        t.record_adaptive_wait()
        t.record_conservative_fallback()
        t.end_session(10.0)
        snap = t.snapshot()
        self.assertEqual(snap["batch_count"], 1)
        self.assertEqual(snap["batched_action_count"], 5)
        self.assertEqual(snap["barrier_count"], 1)
        self.assertEqual(snap["adaptive_wait_count"], 1)
        self.assertEqual(snap["conservative_fallback_count"], 1)
        self.assertEqual(snap["batching_saved_ms"], 200.0)

    def test_ttfb_tts_registrados(self):
        t = SessionTelemetry(session_id="s1")
        t.begin_session(0.0)
        t.record_response_timing(
            time_to_first_byte_ms=30.0,
            time_to_last_byte_ms=120.0,
            time_to_expected_state_ms=110.0,
        )
        t.end_session(500.0)
        snap = t.snapshot()
        self.assertEqual(snap["time_to_first_byte_ms"], 30.0)
        self.assertEqual(snap["time_to_last_byte_ms"], 120.0)
        self.assertEqual(snap["time_to_expected_state_ms"], 110.0)


class RunTelemetryTests(unittest.TestCase):
    def test_agrega_sessoes_thread_safe(self):
        run = RunTelemetry()
        for i in range(3):
            s = run.session(f"s{i}")
            s.begin_session(0.0)
            s.record(TelemetryBucket.PACING, 10.0 * (i + 1))
            s.end_session(100.0)
        snap = run.snapshot()
        self.assertEqual(snap["journey_total_ms"], 300.0)
        self.assertEqual(snap["pacing_ms"], 60.0)
        self.assertEqual(snap["sessions"], 3)

    def test_snapshot_tem_todas_as_metricas_do_contrato(self):
        run = RunTelemetry()
        s = run.session("s")
        s.begin_session(0.0)
        s.end_session(1.0)
        snap = run.snapshot()
        for key in (
            "journey_total_ms", "erp_response_ms", "replay_overhead_ms",
            "pacing_ms", "sync_wait_ms", "checkpoint_wait_ms",
            "wait_compare_cpu_ms",
            "explicit_wait_ms", "send_ms", "compare_ms",
            "time_to_first_byte_ms", "time_to_last_byte_ms",
            "time_to_expected_state_ms",
            "batch_count", "batched_action_count", "barrier_count",
            "adaptive_wait_count", "conservative_fallback_count",
            "batching_saved_ms", "replay_overhead_ratio",
        ):
            self.assertIn(key, snap, key)


if __name__ == "__main__":
    unittest.main()

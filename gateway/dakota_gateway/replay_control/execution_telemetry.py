"""Telemetria de execução do motor de replay (§3, §15).

Separa, por sessão e por run, quanto do tempo de parede pertence a cada
responsabilidade — sem dupla contagem. Os buckets são segmentos disjuntos do
relógio; o invariante é:

    journey_total_ms == erp_response_ms + replay_overhead_ms
    replay_overhead_ms == pacing_ms + explicit_wait_ms + sync_wait_ms
                        + send_ms + compare_ms + other_ms

Definições:

- ``journey_total_ms``: tempo de parede da sessão, do primeiro ao último
  evento processado (soma das sessões no agregado da run);
- ``erp_response_ms``: porção dos pontos de sincronização atribuível ao
  ERP/rede — do fim do envio até o último byte de resposta antes do estado
  esperado (o restante do checkpoint wait é carência de quiet, que é
  política do Replay2 → ``sync_wait_ms``). Em waits que terminam em
  mismatch, a porção ERP vem da anotação ``wait_erp_ms`` do
  ``wait_for_signature_match`` (tempo até o último byte observado; zero se
  nenhum byte chegou durante a espera) — antes da 0.9.9 o wait inteiro de
  um mismatch era atribuído ao ERP, inflando este bucket com carência que
  é política do Replay2;
- ``pacing_ms``: sleeps de cadência por delta de ``ts_ms`` (política);
- ``explicit_wait_ms``: waits declarados pela jornada (``{WAIT:ms}``);
- ``sync_wait_ms``: esperas de quiet/dreno impostas pelo Replay2;
- ``checkpoint_wait_ms``: tempo total dentro dos waits de checkpoint
  (= porção erp + porção sync, mantida para visibilidade — não soma de novo
  no overhead);
- ``send_ms``/``compare_ms``: custo de escrita no PTY e de comparação de
  assinaturas fora dos waits;
- ``other_ms``: residual (total - erp - buckets), sempre >= 0 por construção;
- ``batching_saved_ms``: sleeps de pacing que o batching deixou de pagar
  (medido em adaptive) ou economia prevista (shadow);
- ``convergence_pacing_skip_count``/``convergence_pacing_saved_ms``:
  pacing pulado (política adaptive) porque o wait de checkpoint anterior
  convergiu — estado conhecido e estável, cadência seria artificial.

``replay_overhead_ratio = replay_overhead_ms / journey_total_ms``.
"""
from __future__ import annotations

import threading
from enum import Enum


class TelemetryBucket(str, Enum):
    PACING = "pacing_ms"
    EXPLICIT_WAIT = "explicit_wait_ms"
    SYNC_WAIT = "sync_wait_ms"
    CHECKPOINT_WAIT = "checkpoint_wait_ms"
    SEND = "send_ms"
    COMPARE = "compare_ms"


class SessionTelemetry:
    """Coletor por sessão; relógios injetados (testável, determinístico)."""

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self._buckets: dict[TelemetryBucket, float] = {}
        self._checkpoint_wait_ms = 0.0
        self._erp_ms = 0.0
        self._wait_compare_cpu_ms = 0.0
        self._start: float | None = None
        self._end: float | None = None
        self.batch_count = 0
        self.batched_action_count = 0
        self.barrier_count = 0
        self.adaptive_wait_count = 0
        self.conservative_fallback_count = 0
        self.batching_saved_ms = 0.0
        self.convergence_pacing_skip_count = 0
        self.convergence_pacing_saved_ms = 0.0
        self._ttfb: list[float] = []
        self._ttlb: list[float] = []
        self._tts: list[float] = []
        self.shadow_report: dict | None = None

    def begin_session(self, now: float) -> None:
        self._start = now

    def end_session(self, now: float) -> None:
        self._end = now

    def record(self, bucket: TelemetryBucket, elapsed_ms: float, *, erp_ms: float = 0.0) -> None:
        """Registra um segmento de tempo num bucket.

        ``CHECKPOINT_WAIT`` não soma no overhead diretamente: é decomposto em
        porção ERP (``erp_ms`` → ``erp_response_ms``) e carência quiet
        (restante → ``sync_wait_ms``), e o total fica visível em
        ``checkpoint_wait_ms`` (informativo; nunca somado de novo).
        """
        if bucket is TelemetryBucket.CHECKPOINT_WAIT:
            erp = min(max(0.0, erp_ms), max(0.0, elapsed_ms))
            self._checkpoint_wait_ms += max(0.0, elapsed_ms)
            self._erp_ms += erp
            self._buckets[TelemetryBucket.SYNC_WAIT] = (
                self._buckets.get(TelemetryBucket.SYNC_WAIT, 0.0)
                + max(0.0, elapsed_ms) - erp
            )
            return
        self._buckets[bucket] = self._buckets.get(bucket, 0.0) + max(0.0, elapsed_ms)

    def record_batch(self, *, action_count: int, saved_ms: float) -> None:
        self.batch_count += 1
        self.batched_action_count += action_count
        self.batching_saved_ms += max(0.0, saved_ms)

    def record_barrier(self) -> None:
        self.barrier_count += 1

    def record_adaptive_wait(self) -> None:
        self.adaptive_wait_count += 1

    def record_conservative_fallback(self) -> None:
        self.conservative_fallback_count += 1

    def record_convergence_pacing_skip(self, saved_ms: float) -> None:
        """Pacing pulado porque o wait anterior convergiu (§12/§24): o
        estado é conhecido e estável, então a cadência por delta de ts_ms
        seria espera artificial do Replay2 — não sincronização."""
        self.convergence_pacing_skip_count += 1
        self.convergence_pacing_saved_ms += max(0.0, saved_ms)

    def record_wait_compare(self, cpu_ms: float) -> None:
        """CPU de compare+predicado DENTRO dos waits de checkpoint
        (``wait_compare_cpu_ms`` do ``wait_for_signature_match``).

        Sub-porção informativa do checkpoint wait — nunca somada no
        overhead (o tempo já está em ``checkpoint_wait_ms``/``sync_wait_ms``,
        somar de novo seria dupla contagem, §16.14)."""
        self._wait_compare_cpu_ms += max(0.0, cpu_ms)

    def record_response_timing(
        self,
        *,
        time_to_first_byte_ms: float,
        time_to_last_byte_ms: float,
        time_to_expected_state_ms: float,
    ) -> None:
        self._ttfb.append(time_to_first_byte_ms)
        self._ttlb.append(time_to_last_byte_ms)
        self._tts.append(time_to_expected_state_ms)

    def attach_shadow(self, report: dict) -> None:
        """Anexa o relatório do shadow mode (§19) à sessão."""
        self.shadow_report = dict(report)

    @staticmethod
    def _sum(values: list[float]) -> float:
        return float(sum(values))

    def snapshot(self) -> dict:
        total = 0.0
        if self._start is not None and self._end is not None:
            total = max(0.0, (self._end - self._start))
        pacing = self._buckets.get(TelemetryBucket.PACING, 0.0)
        explicit = self._buckets.get(TelemetryBucket.EXPLICIT_WAIT, 0.0)
        sync = self._buckets.get(TelemetryBucket.SYNC_WAIT, 0.0)
        send = self._buckets.get(TelemetryBucket.SEND, 0.0)
        compare = self._buckets.get(TelemetryBucket.COMPARE, 0.0)
        erp = self._erp_ms
        known_overhead = pacing + explicit + sync + send + compare
        other = max(0.0, total - erp - known_overhead)
        overhead = known_overhead + other
        snap = {
            "session_id": self.session_id,
            "journey_total_ms": total,
            "erp_response_ms": erp,
            "replay_overhead_ms": overhead,
            "pacing_ms": pacing,
            "explicit_wait_ms": explicit,
            "sync_wait_ms": sync,
            "checkpoint_wait_ms": self._checkpoint_wait_ms,
            "wait_compare_cpu_ms": self._wait_compare_cpu_ms,
            "send_ms": send,
            "compare_ms": compare,
            "other_ms": other,
            "time_to_first_byte_ms": self._sum(self._ttfb),
            "time_to_last_byte_ms": self._sum(self._ttlb),
            "time_to_expected_state_ms": self._sum(self._tts),
            "batch_count": self.batch_count,
            "batched_action_count": self.batched_action_count,
            "barrier_count": self.barrier_count,
            "adaptive_wait_count": self.adaptive_wait_count,
            "conservative_fallback_count": self.conservative_fallback_count,
            "batching_saved_ms": self.batching_saved_ms,
            "convergence_pacing_skip_count": self.convergence_pacing_skip_count,
            "convergence_pacing_saved_ms": self.convergence_pacing_saved_ms,
            "replay_overhead_ratio": (overhead / total) if total > 0 else 0.0,
        }
        if self.shadow_report is not None:
            snap["shadow"] = dict(self.shadow_report)
        return snap


class RunTelemetry:
    """Agregador thread-safe das sessões de uma run."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sessions: list[SessionTelemetry] = []

    def session(self, session_id: str) -> SessionTelemetry:
        s = SessionTelemetry(session_id)
        with self._lock:
            self._sessions.append(s)
        return s

    def snapshot(self) -> dict:
        with self._lock:
            snaps = [s.snapshot() for s in self._sessions]
        agg: dict = {"sessions": len(snaps)}
        totals: dict[str, float] = {}
        shadow: dict[str, float] = {}
        for snap in snaps:
            for key, value in snap.items():
                if key == "session_id":
                    continue
                if key == "shadow" and isinstance(value, dict):
                    for skey, svalue in value.items():
                        if isinstance(svalue, (int, float)):
                            shadow[skey] = shadow.get(skey, 0.0) + svalue
                    continue
                if isinstance(value, (int, float)):
                    totals[key] = totals.get(key, 0.0) + value
        agg.update(totals)
        if shadow:
            agg["shadow"] = shadow
        total = agg.get("journey_total_ms", 0.0)
        agg["replay_overhead_ratio"] = (
            agg.get("replay_overhead_ms", 0.0) / total if total > 0 else 0.0
        )
        return agg

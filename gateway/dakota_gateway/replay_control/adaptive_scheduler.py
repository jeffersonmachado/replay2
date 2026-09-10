"""Adaptive scheduler do motor de replay (§6, §8, §18–20).

Decide, para cada ação de uma sessão, a operação de execução:

- ``CONTINUE`` — envia ação individual, sem pacing extra;
- ``BATCH`` — funde ações imprimíveis contíguas num único write;
- ``WAIT_FOR_OUTPUT`` — aguarda qualquer resposta (reservado);
- ``WAIT_FOR_QUIET`` — aguarda quiet point (fraco; não é convergência);
- ``WAIT_FOR_STATE`` — aguarda estado esperado (checkpoint — autoridade);
- ``BARRIER`` — fronteira dura: ENTER/ESC/F-key/TAB/navegação/WAIT explícito;
- ``FALLBACK_CONSERVATIVE`` — execução evento a evento, política original.

Toda decisão carrega ``reason_code`` e ``evidence`` — auditável por
construção. A política de decisão NÃO depende do ambiente (§14): AIX e
Linux recebem o mesmo plano; a espera termina antes em quem responde antes.

Fase 1 (conservadora): só ``PRINTABLE_INPUT`` contíguo é elegível a batch.
Colapsar deltas de pacing positivos exige evidência (type-ahead humano da
captura — :mod:`synchronization` — ou trilha sintética, cujos deltas são
artificiais por construção).
"""
from __future__ import annotations

import base64
from dataclasses import dataclass, field
from enum import Enum

from .action_classifier import ActionClass, classify_bytes
from .safety_guard import SafetyGuard


class SchedulerOp(str, Enum):
    CONTINUE = "CONTINUE"
    BATCH = "BATCH"
    WAIT_FOR_OUTPUT = "WAIT_FOR_OUTPUT"
    WAIT_FOR_QUIET = "WAIT_FOR_QUIET"
    WAIT_FOR_STATE = "WAIT_FOR_STATE"
    BARRIER = "BARRIER"
    FALLBACK_CONSERVATIVE = "FALLBACK_CONSERVATIVE"


#: Políticas de execução (rollout seguro, §18).
POLICY_CONSERVATIVE = "conservative"
POLICY_ADAPTIVE = "adaptive"
POLICY_ADAPTIVE_SHADOW = "adaptive_shadow"
EXECUTION_POLICIES = frozenset({
    POLICY_CONSERVATIVE, POLICY_ADAPTIVE, POLICY_ADAPTIVE_SHADOW,
})


def normalize_execution_policy(value: str) -> str:
    policy = str(value or "").strip().lower()
    return policy if policy in EXECUTION_POLICIES else POLICY_CONSERVATIVE


@dataclass(frozen=True)
class SchedulerDecision:
    op: SchedulerOp
    reason_code: str
    seq_start: int
    seq_end: int
    action_count: int = 1
    predicted_saved_ms: int = 0
    evidence: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "op": self.op.value,
            "reason_code": self.reason_code,
            "seq_start": self.seq_start,
            "seq_end": self.seq_end,
            "action_count": self.action_count,
            "predicted_saved_ms": self.predicted_saved_ms,
            "evidence": dict(self.evidence),
        }


def _is_input(ev: dict) -> bool:
    typ = str(ev.get("type") or "")
    return (typ == "bytes" and ev.get("dir") == "in") or typ == "deterministic_input"


def _decode(ev: dict) -> bytes:
    raw = ev.get("data_b64")
    if raw is None and ev.get("type") == "deterministic_input":
        raw = ev.get("key_b64")
    if not raw:
        return b""
    try:
        return base64.b64decode(raw)
    except Exception:
        return b""


class AdaptiveScheduler:
    """Planeja decisões de execução sobre o fluxo de eventos de uma sessão.

    A política é puramente função das ações/estado — ``environment_id`` é
    apenas rótulo de auditoria (registrado na evidência), nunca altera a
    decisão (§14, §16.17).
    """

    def __init__(
        self,
        *,
        guard: SafetyGuard | None = None,
        synthetic_trail: bool = False,
        evidence_seqs: frozenset[int] | set[int] = frozenset(),
        environment_id: str = "",
    ) -> None:
        self.guard = guard or SafetyGuard()
        self.synthetic_trail = bool(synthetic_trail)
        self.evidence_seqs = {int(s) for s in evidence_seqs}
        self.environment_id = str(environment_id or "")

    # ------------------------------------------------------------------
    # Planejamento offline (por sessão)
    # ------------------------------------------------------------------

    def _boundary_collapsible(self, delta_ms: int, next_seq: int) -> tuple[bool, str]:
        """Um boundary com delta>0 só colapsa com evidência (§7, §16.12)."""
        if delta_ms <= 0:
            return True, "delta_zero"
        if self.synthetic_trail:
            return True, "synthetic_trail"
        if next_seq in self.evidence_seqs:
            return True, "safe_typeahead_evidence"
        return False, "sem evidencia de type-ahead"

    def _plan_run(self, run: list[dict]) -> list[SchedulerDecision]:
        """Planeja uma run de ações imprimíveis contíguas."""
        seqs = [int(ev.get("seq_global") or 0) for ev in run]
        if len(run) == 1:
            return [SchedulerDecision(
                SchedulerOp.CONTINUE, "SINGLE_ACTION", seqs[0], seqs[0],
                evidence=self._evidence_meta(),
            )]
        if self.guard.diverged:
            return [
                SchedulerDecision(
                    SchedulerOp.FALLBACK_CONSERVATIVE, "DIVERGED_STATE",
                    seq, seq, evidence=self._evidence_meta(),
                )
                for seq in seqs
            ]
        classes = [ActionClass.PRINTABLE_INPUT] * len(run)
        deltas = [
            max(0, int(run[i].get("ts_ms") or 0) - int(run[i - 1].get("ts_ms") or 0))
            for i in range(1, len(run))
        ]
        collapsible = True
        evidence_used = False
        block_reason = ""
        for i, delta in enumerate(deltas, start=1):
            ok, why = self._boundary_collapsible(delta, seqs[i])
            if not ok:
                collapsible = False
                block_reason = why
                break
            if why == "safe_typeahead_evidence":
                evidence_used = True
        decision = self.guard.evaluate_batch(
            action_classes=classes,
            checkpoint_pending=False,  # runs nunca atravessam checkpoints
            delta_positive=any(d > 0 for d in deltas),
            has_evidence=self.synthetic_trail or evidence_used
            or not any(d > 0 for d in deltas),
        )
        if collapsible and decision.allowed:
            reason = (
                "HUMAN_TYPEAHEAD_EVIDENCE" if any(d > 0 for d in deltas)
                else "CONTIGUOUS_PRINTABLE_INPUT"
            )
            return [SchedulerDecision(
                SchedulerOp.BATCH, reason, seqs[0], seqs[-1],
                action_count=len(run), predicted_saved_ms=sum(deltas),
                evidence={
                    **self._evidence_meta(),
                    "boundaries": len(deltas),
                    "typeahead_evidence": evidence_used,
                },
            )]
        reason = (
            decision.reason_code if not decision.allowed
            else "INSUFFICIENT_EVIDENCE"
        )
        if not collapsible and decision.allowed:
            reason = "INSUFFICIENT_EVIDENCE"
        return [
            SchedulerDecision(
                SchedulerOp.FALLBACK_CONSERVATIVE, reason, seq, seq,
                evidence={**self._evidence_meta(), "detail": block_reason},
            )
            for seq in seqs
        ]

    def _evidence_meta(self) -> dict:
        meta = {"synthetic_trail": self.synthetic_trail}
        if self.environment_id:
            meta["environment_id"] = self.environment_id
        return meta

    _BARRIER_REASONS = {
        ActionClass.ENTER: "SCREEN_TRANSITION",
        ActionClass.ESC: "SCREEN_TRANSITION",
        ActionClass.TAB: "SCREEN_TRANSITION",
        ActionClass.NAVIGATION: "SCREEN_TRANSITION",
        ActionClass.FUNCTION_KEY: "FUNCTION_KEY_BARRIER",
        ActionClass.SUBMIT: "SCREEN_TRANSITION",
        ActionClass.QUERY: "STATE_DEPENDENCY",
        ActionClass.SCREEN_TRANSITION: "SCREEN_TRANSITION",
        ActionClass.EXPLICIT_WAIT: "EXPLICIT_WAIT",
    }

    def plan_events(self, events: list[dict]) -> list[SchedulerDecision]:
        """Planeja as decisões de execução para os eventos de uma sessão."""
        decisions: list[SchedulerDecision] = []
        run: list[dict] = []

        def flush_run() -> None:
            nonlocal run
            if run:
                decisions.extend(self._plan_run(run))
                run = []

        for ev in events:
            typ = str(ev.get("type") or "")
            if typ == "checkpoint":
                flush_run()
                seq = int(ev.get("seq_global") or 0)
                decisions.append(SchedulerDecision(
                    SchedulerOp.WAIT_FOR_STATE, "CHECKPOINT_REQUIRED", seq, seq,
                    evidence=self._evidence_meta(),
                ))
                continue
            if not _is_input(ev):
                continue  # bytes dir=out e demais tipos não são ações
            data = _decode(ev)
            classified = classify_bytes(data, key_kind=ev.get("key_kind"))
            seq = int(ev.get("seq_global") or 0)
            if classified.action_class is ActionClass.PRINTABLE_INPUT:
                run.append(ev)
                continue
            flush_run()
            if classified.action_class is ActionClass.UNKNOWN:
                decisions.append(SchedulerDecision(
                    SchedulerOp.FALLBACK_CONSERVATIVE, "UNKNOWN_ACTION", seq, seq,
                    evidence={**self._evidence_meta(), "classifier": classified.reason},
                ))
            elif classified.action_class is ActionClass.FIELD_EDIT:
                decisions.append(SchedulerDecision(
                    SchedulerOp.CONTINUE, "FIELD_EDIT", seq, seq,
                    evidence=self._evidence_meta(),
                ))
            else:
                decisions.append(SchedulerDecision(
                    SchedulerOp.BARRIER,
                    self._BARRIER_REASONS.get(
                        classified.action_class, "STATE_DEPENDENCY",
                    ),
                    seq, seq,
                    evidence={
                        **self._evidence_meta(),
                        "action_class": classified.action_class.value,
                    },
                ))
        flush_run()
        return decisions

    # ------------------------------------------------------------------
    # Aplicação (prova de equivalência de bytes — §16.7)
    # ------------------------------------------------------------------

    @staticmethod
    def apply_decisions(
        events: list[dict],
        decisions: list[SchedulerDecision],
    ) -> list[tuple[bytes, list[int]]]:
        """Funde os bytes conforme as decisões.

        Retorna [(bytes_a_enviar, [seqs cobertos])] apenas para eventos de
        input; checkpoints não produzem bytes. Invariante: a concatenação
        dos bytes retornados é idêntica à concatenação dos bytes dos
        eventos de input originais, na mesma ordem — BARRIER emite os
        próprios bytes como write individual (exatamente o que o executor
        faz: flush + send isolado); marcadores WAIT têm payload vazio e
        não emitem bytes.
        """
        by_seq = {
            int(ev.get("seq_global") or 0): ev for ev in events if _is_input(ev)
        }
        merged: list[tuple[bytes, list[int]]] = []
        for dec in decisions:
            seqs = [
                seq for seq in sorted(by_seq)
                if dec.seq_start <= seq <= dec.seq_end
            ]
            seqs = [s for s in seqs if s >= dec.seq_start and s <= dec.seq_end]
            data = b"".join(_decode(by_seq[s]) for s in seqs)
            if dec.op is SchedulerOp.BATCH:
                if data:
                    merged.append((data, seqs))
            elif dec.op in (
                SchedulerOp.CONTINUE,
                SchedulerOp.FALLBACK_CONSERVATIVE,
                SchedulerOp.BARRIER,
            ):
                if data:
                    merged.append((data, seqs))
            # WAIT_FOR_STATE/WAIT_FOR_* não emitem bytes (checkpoint/espera)
        return merged


@dataclass
class AdaptiveRuntime:
    """Bundle de execução adaptativa compartilhado runner ↔ executors.

    Criado pelo Runner a partir dos params da run (``execution_policy``,
    ``synthetic``, ``typeahead_evidence``, ``environment_id``). ``None`` nos
    executors significa comportamento histórico idêntico (conservador sem
    telemetria).
    """

    policy: str = POLICY_CONSERVATIVE
    telemetry: object | None = None        # execution_telemetry.RunTelemetry
    latency_profile: object | None = None  # latency_profile.LatencyProfile
    environment_id: str = ""
    synthetic_trail: bool = False
    typeahead_evidence: dict = field(default_factory=dict)  # session_id → {seqs}

    def evidence_for(self, session_id: str) -> set[int]:
        raw = (self.typeahead_evidence or {}).get(session_id) or []
        return {int(s) for s in raw}


class ShadowTracker:
    """Shadow mode online (§19): registra o que o scheduler FARIA, sem
    alterar a execução conservadora em curso.

    Uso no executor: para cada evento processado no caminho conservador,
    chamar :meth:`observe` com o sleep de pacing efetivamente pago; em
    divergências, :meth:`note_divergence`. O :meth:`report` alimenta o
    critério de promoção (§20): ``false_safe_decisions`` deve ser 0.
    """

    def __init__(
        self,
        *,
        synthetic_trail: bool = False,
        evidence_seqs: frozenset[int] | set[int] = frozenset(),
    ) -> None:
        self.synthetic_trail = bool(synthetic_trail)
        self.evidence_seqs = {int(s) for s in evidence_seqs}
        self._run: list[tuple[int, int]] = []  # (seq_global, paced_sleep_ms)
        self._safe_runs: list[tuple[int, int, int]] = []  # (seq_ini, seq_fim, saved)
        self._false_safe_marks: set[tuple[int, int]] = set()
        self._total_actions = 0
        self._batch_candidates = 0
        self._safe_candidates = 0
        self._rejected_candidates = 0
        self._fallbacks = 0
        self._potential_saved_ms = 0
        self._divergences = 0
        self._divergence_seqs: list[int] = []

    def _boundary_collapsible(self, seq: int, paced_sleep_ms: int) -> bool:
        if paced_sleep_ms <= 0:
            return True
        if self.synthetic_trail:
            return True
        return seq in self.evidence_seqs

    def _flush_run(self) -> None:
        run, self._run = self._run, []
        if len(run) < 2:
            return
        self._batch_candidates += 1
        boundaries_ok = all(
            self._boundary_collapsible(seq, sleep_ms)
            for seq, sleep_ms in run[1:]
        )
        if boundaries_ok:
            self._safe_candidates += 1
            saved = sum(sleep_ms for _, sleep_ms in run[1:])
            self._potential_saved_ms += saved
            self._safe_runs.append((run[0][0], run[-1][0], saved))
        else:
            self._rejected_candidates += 1

    def observe(self, ev: dict, *, paced_sleep_ms: int = 0) -> None:
        typ = str(ev.get("type") or "")
        if typ == "checkpoint":
            self._flush_run()
            return
        if not _is_input(ev):
            return
        self._total_actions += 1
        data = _decode(ev)
        classified = classify_bytes(data, key_kind=ev.get("key_kind"))
        seq = int(ev.get("seq_global") or 0)
        if classified.action_class is ActionClass.PRINTABLE_INPUT:
            self._run.append((seq, int(paced_sleep_ms)))
            return
        self._flush_run()
        if classified.action_class is ActionClass.UNKNOWN:
            self._fallbacks += 1

    def note_divergence(self, seq_global: int) -> None:
        self._divergences += 1
        self._divergence_seqs.append(int(seq_global))

    def report(self) -> dict:
        self._flush_run()
        false_safe = 0
        for seq in self._divergence_seqs:
            for run_start, run_end, _saved in self._safe_runs:
                if run_start < seq <= run_end and (run_start, run_end) not in self._false_safe_marks:
                    self._false_safe_marks.add((run_start, run_end))
                    false_safe += 1
        return {
            "total_actions": self._total_actions,
            "batch_candidates": self._batch_candidates,
            "safe_candidates": self._safe_candidates,
            "rejected_candidates": self._rejected_candidates,
            "fallbacks": self._fallbacks,
            "checkpoint_crossing_attempts": 0,  # o desenho nunca atravessa checkpoint
            "divergences": self._divergences,
            "false_safe_decisions": false_safe,
            "potential_saved_ms": self._potential_saved_ms,
        }

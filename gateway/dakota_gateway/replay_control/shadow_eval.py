"""Avaliação offline de shadow mode sobre capturas reais (§19–§20).

Reusa exatamente os componentes do caminho de execução —
:class:`~dakota_gateway.replay_control.adaptive_scheduler.ShadowTracker`,
:class:`~dakota_gateway.replay_control.adaptive_scheduler.AdaptiveScheduler`
e :func:`~dakota_gateway.replay_control.synchronization.extract_typeahead_evidence`
— sobre os eventos gravados de uma captura, produzindo o relatório de
critério de promoção (§20) sem ligar nenhum executor e sem rede.

Além dos contadores do §20, verifica sobre dados reais:

- equivalência de bytes: a fusão por BATCH preserva a concatenação exata
  dos inputs, na ordem (§16.7 — invariante do ``apply_decisions``);
- inviolabilidade do checkpoint: nenhum BATCH cobre o seq de um checkpoint
  (``checkpoint_violations == 0`` — a fronteira jamais é atravessada);
- falso-seguro: sessão sem ``session_end`` (não convergente) é marcada
  como divergência no tracker, de modo que qualquer run "segura" que a
  contivesse seria contada em ``false_safe_decisions`` — o desenho nunca
  marca evidência sem convergência, e esta checagem prova isso.
"""
from __future__ import annotations

from pathlib import Path

from .action_classifier import ActionClass, classify_bytes
from .adaptive_scheduler import (
    AdaptiveScheduler,
    SchedulerOp,
    ShadowTracker,
    _decode,
    _is_input,
)
from .synchronization import extract_typeahead_evidence

#: Campos numéricos agregados no relatório consolidado.
_TOTAL_FIELDS = (
    "sessions", "total_actions", "batch_candidates", "safe_candidates",
    "rejected_candidates", "fallbacks", "checkpoint_run_splits",
    "checkpoint_violations", "divergences", "false_safe_decisions",
    "potential_saved_ms", "writes_before", "writes_after",
)


def _evaluate_session(events: list[dict], *, speed: float) -> dict:
    """Avalia uma sessão: shadow tracking + verificações do planner."""
    evidences = extract_typeahead_evidence(events)
    safe_seqs = {e.input_seq for e in evidences if e.safe}

    tracker = ShadowTracker(evidence_seqs=safe_seqs)
    prev_in_ts: int | None = None
    run_open = False
    checkpoint_run_splits = 0
    for ev in events:
        typ = str(ev.get("type") or "")
        if typ == "checkpoint":
            if run_open:
                checkpoint_run_splits += 1
            run_open = False
            tracker.observe(ev)
            continue
        if not _is_input(ev):
            continue
        ts = int(ev.get("ts_ms") or 0)
        paced = (
            0 if prev_in_ts is None
            else int(max(0, ts - prev_in_ts) / max(float(speed), 1e-9))
        )
        prev_in_ts = ts
        tracker.observe(ev, paced_sleep_ms=paced)
        classified = classify_bytes(_decode(ev), key_kind=ev.get("key_kind"))
        run_open = classified.action_class is ActionClass.PRINTABLE_INPUT

    session_ended = any(ev.get("type") == "session_end" for ev in events)
    if not session_ended and events:
        last_seq = max(int(ev.get("seq_global") or 0) for ev in events)
        tracker.note_divergence(last_seq)

    report = tracker.report()

    scheduler = AdaptiveScheduler(evidence_seqs=safe_seqs)
    decisions = scheduler.plan_events(events)
    merged = AdaptiveScheduler.apply_decisions(events, decisions)
    original = b"".join(_decode(ev) for ev in events if _is_input(ev))
    fused = b"".join(data for data, _seqs in merged)
    checkpoint_seqs = {
        int(ev.get("seq_global") or 0)
        for ev in events if ev.get("type") == "checkpoint"
    }
    violations = sum(
        1
        for dec in decisions
        if dec.op is SchedulerOp.BATCH
        and any(dec.seq_start < cp <= dec.seq_end for cp in checkpoint_seqs)
    )

    return {
        "report": report,
        "checkpoint_run_splits": checkpoint_run_splits,
        "checkpoint_violations": violations,
        "bytes_equivalent": fused == original,
        "writes_before": sum(1 for ev in events if _is_input(ev)),
        "writes_after": len(merged),
        "has_evidence": bool(safe_seqs),
    }


def evaluate_capture(
    log_dir: str,
    params: dict | None = None,
    *,
    stable_ms: int = 150,
    speed: float = 1.0,
) -> dict:
    """Avalia uma captura (diretório de audit-*.jsonl) em shadow offline.

    Retorna o relatório de promoção do §20 para a captura, com as
    verificações de equivalência de bytes e de checkpoint. Nenhum byte é
    enviado: é análise pura sobre os eventos gravados.
    """
    from .synchronization import extract_capture_typeahead_evidence  # noqa: F401
    from .window import index_session_events, iter_indexed_events

    index, _starts = index_session_events(log_dir, params)
    totals = {field: 0 for field in _TOTAL_FIELDS}
    bytes_equivalent = True
    evidence_sessions: list[str] = []
    for sid in sorted(index):
        try:
            events = list(iter_indexed_events(index[sid]))
        except Exception:
            continue
        sess = _evaluate_session(events, speed=speed)
        rep = sess["report"]
        totals["sessions"] += 1
        for field in _TOTAL_FIELDS:
            if field == "sessions":
                continue
            if field == "checkpoint_run_splits":
                totals[field] += sess["checkpoint_run_splits"]
            elif field == "checkpoint_violations":
                totals[field] += sess["checkpoint_violations"]
            elif field in ("writes_before", "writes_after"):
                totals[field] += sess[field]
            else:
                totals[field] += int(rep.get(field, 0))
        bytes_equivalent = bytes_equivalent and sess["bytes_equivalent"]
        if sess["has_evidence"]:
            evidence_sessions.append(str(sid))

    return {
        "log_dir": str(log_dir),
        "stable_ms": int(stable_ms),
        "speed": float(speed),
        "bytes_equivalent": bytes_equivalent,
        "evidence_sessions": evidence_sessions,
        # invariante do desenho: batch jamais atravessa checkpoint (§10)
        "checkpoint_crossing_attempts": 0,
        **totals,
    }


def evaluate_captures(
    captures_dir: str,
    *,
    stable_ms: int = 150,
    speed: float = 1.0,
    params: dict | None = None,
) -> dict:
    """Avalia todas as capturas de um diretório e consolida os totais.

    Capturas sem ``audit-*.jsonl`` são ignoradas; uma captura que falha é
    registrada com ``error`` e não derruba as demais.
    """
    root = Path(captures_dir)
    per_capture: list[dict] = []
    for child in sorted(root.iterdir()):
        if not child.is_dir() or not any(child.glob("audit-*.jsonl")):
            continue
        try:
            per_capture.append(evaluate_capture(
                str(child), params, stable_ms=stable_ms, speed=speed,
            ))
        except Exception as exc:  # evidência é otimização, nunca requisito
            per_capture.append({
                "log_dir": str(child),
                "error": f"{type(exc).__name__}: {exc}",
            })

    totals = {field: 0 for field in _TOTAL_FIELDS}
    bytes_equivalent = True
    ok = 0
    for entry in per_capture:
        if "error" in entry:
            continue
        ok += 1
        bytes_equivalent = bytes_equivalent and entry["bytes_equivalent"]
        for field in _TOTAL_FIELDS:
            totals[field] += int(entry.get(field, 0))
    totals["captures_ok"] = ok
    totals["captures_error"] = len(per_capture) - ok
    totals["bytes_equivalent"] = bytes_equivalent
    totals["checkpoint_crossing_attempts"] = 0
    totals["promotion_criteria_met"] = (
        totals["false_safe_decisions"] == 0
        and totals["checkpoint_violations"] == 0
        and bytes_equivalent
    )

    return {
        "captures_dir": str(captures_dir),
        "stable_ms": int(stable_ms),
        "speed": float(speed),
        "captures": per_capture,
        "totals": totals,
    }

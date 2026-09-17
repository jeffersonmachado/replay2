#!/usr/bin/env python3
"""Shadow mode (§19) no executor strict-global.

O strict-global aceita ``adaptive=`` e registra telemetria, mas ignorava
silenciosamente a política ``adaptive_shadow``: a run executava conservador
sem registrar NENHUMA decisão shadow nas métricas (metrics_json.adaptive sem
a chave ``shadow``). Estes testes travam o contrato: strict-global +
adaptive_shadow produz o relatório shadow por sessão, sem alterar um byte da
execução.

Decisão de design: a cadência do strict-global é dirigida por checkpoint
(não há pacing por delta de ts_ms a economizar), então o shadow observa com
``paced_sleep_ms=0`` — o valor da métrica aqui é a cobertura/segurança do
batching (total_actions/batch_candidates/false_safe_decisions, o critério de
promoção do §20), não a economia de tempo.
"""
from __future__ import annotations

import base64
import json
import threading
from pathlib import Path
from unittest import mock

from dakota_gateway.replay import ReplayConfig
from dakota_gateway.replay_control import executors as executors_mod
from dakota_gateway.replay_control.adaptive_scheduler import (
    AdaptiveRuntime,
    POLICY_ADAPTIVE_SHADOW,
    POLICY_CONSERVATIVE,
)
from dakota_gateway.replay_control.execution_telemetry import RunTelemetry
from dakota_gateway.replay_control.executors import replay_strict_global_controlled


class _FakeSelector:
    def register(self, *args, **kwargs):
        return None

    def select(self, timeout=None):
        return []

    def close(self):
        return None


class _FakeSession:
    lock = threading.Lock()
    writes: dict[str, list[bytes]] = {}

    @classmethod
    def reset(cls):
        with cls.lock:
            cls.writes = {}

    def __init__(self, cfg, sid, target_user_override=None):
        self.session_id = sid
        self.master_fd = 0
        self.last_out_ms = 0
        self.screen_state = object()

    def canonical_snapshot_now(self):
        return {"text_sig": "", "visual_sig": "", "semantic_sig": "", "screen_sig": ""}

    def read_out(self):
        return b""

    def write_in(self, data: bytes):
        with type(self).lock:
            type(self).writes.setdefault(self.session_id, []).append(bytes(data))

    def close(self):
        return None


def _patch_sessions():
    return (
        mock.patch.object(executors_mod, "_TargetSession", _FakeSession),
        mock.patch.object(executors_mod.selectors, "DefaultSelector", _FakeSelector),
    )


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _write_capture(log_dir: Path, sessions: dict[str, list[dict]]) -> None:
    lines: list[str] = []
    seq = 0
    for sid, events in sessions.items():
        seq += 1
        lines.append(json.dumps({
            "type": "session_start", "session_id": sid,
            "seq_global": seq, "seq_session": 1, "rows": 25, "cols": 80,
        }))
        for i, ev in enumerate(events):
            seq += 1
            ev = dict(ev)
            ev["session_id"] = sid
            ev["seq_global"] = seq
            ev["seq_session"] = i + 2
            lines.append(json.dumps(ev))
    (log_dir / "audit-t.part001.jsonl").write_text("\n".join(lines), encoding="utf-8")


def _det_inputs(text: str, ts0: int = 1000) -> list[dict]:
    """deterministic_input imprimível com assinatura esperada (exige compare)."""
    return [
        {"type": "deterministic_input", "ts_ms": ts0 + i * 100,
         "key_b64": _b64(ch.encode()), "key_kind": "printable",
         "screen_sig": f"sig_{ch}"}
        for i, ch in enumerate(text)
    ]


def _run_strict(tmp_path: Path, sessions, *, policy: str, wait_results=None,
                params=None, telemetry=None):
    """Roda o strict-global com sessões fake; wait_results controla o match
    por chamada (default: sempre matched)."""
    _write_capture(tmp_path, sessions)
    _FakeSession.reset()
    results_iter = iter(wait_results or [])

    def fake_wait(*args, **kwargs):
        try:
            return next(results_iter)
        except StopIteration:
            return True, {"matched": True}, {}

    rt = AdaptiveRuntime(policy=policy, telemetry=telemetry)
    failures: list = []
    p1, p2 = _patch_sessions()
    with p1, p2, \
         mock.patch.object(executors_mod, "wait_for_signature_match", fake_wait):
        replay_strict_global_controlled(
            ReplayConfig(log_dir=str(tmp_path), target_host="local",
                         checkpoint_quiet_ms=0),
            params=params or {},
            should_pause_or_cancel=lambda: None,
            on_progress=lambda *a: None,
            on_failure=lambda f: failures.append(f),
            adaptive=rt,
        )
    return failures


def test_shadow_strict_global_registra_decisoes(tmp_path):
    """§19: strict-global + adaptive_shadow produz metrics.adaptive.shadow
    com ações/candidatos coerentes (3 imprimíveis contíguos = 1 candidato,
    quebrado pelo checkpoint; pacing 0 → candidato seguro, economia 0)."""
    events = _det_inputs("ABC") + [
        {"type": "checkpoint", "ts_ms": 1500, "screen_sig": "sig_menu"},
    ]
    tel = RunTelemetry()
    _run_strict(
        tmp_path, {"s0": events}, policy=POLICY_ADAPTIVE_SHADOW,
        params={"input_mode": "deterministic",
                "on_deterministic_mismatch": "send-anyway"},
        telemetry=tel,
    )
    snap = tel.snapshot()
    assert "shadow" in snap, snap
    shadow = snap["shadow"]
    assert shadow["total_actions"] == 3, shadow
    assert shadow["batch_candidates"] == 1, shadow
    assert shadow["safe_candidates"] == 1, shadow
    assert shadow["false_safe_decisions"] == 0, shadow
    assert shadow["checkpoint_crossing_attempts"] == 0, shadow


def test_conservative_nao_produz_chave_shadow(tmp_path):
    """Pin: política conservative (default) nunca anexa relatório shadow."""
    tel = RunTelemetry()
    _run_strict(
        tmp_path, {"s0": _det_inputs("ABC")}, policy=POLICY_CONSERVATIVE,
        params={"input_mode": "deterministic",
                "on_deterministic_mismatch": "send-anyway"},
        telemetry=tel,
    )
    assert "shadow" not in tel.snapshot(), tel.snapshot()


def test_divergencia_dentro_de_run_candidata_marca_false_safe(tmp_path):
    """Divergência no 2º input de uma run candidata (seq dentro do intervalo
    seguro) incrementa false_safe_decisions — o critério de promoção (§20)."""
    events = _det_inputs("ABC")
    tel = RunTelemetry()
    failures = _run_strict(
        tmp_path, {"s0": events}, policy=POLICY_ADAPTIVE_SHADOW,
        wait_results=[
            (True, {"matched": True}, {}),
            (False, {"matched": False}, {}),
            (True, {"matched": True}, {}),
        ],
        params={"input_mode": "deterministic",
                "on_deterministic_mismatch": "send-anyway"},
        telemetry=tel,
    )
    assert len(failures) == 1, failures
    shadow = tel.snapshot()["shadow"]
    assert shadow["total_actions"] == 3, shadow
    assert shadow["divergences"] == 1, shadow
    assert shadow["false_safe_decisions"] == 1, shadow


def test_shadow_nao_altera_nenhum_byte_enviado(tmp_path):
    """A política é só observação: os bytes do strict-global conservative e
    do adaptive_shadow têm de ser idênticos, write a write."""
    events = [
        {"type": "bytes", "dir": "in", "ts_ms": 1000 + i * 100,
         "data_b64": _b64(d)}
        for i, d in enumerate([b"A", b"B", b"\r", b"C", b"D"])
    ]

    writes_por_policy: dict[str, list] = {}
    for policy in (POLICY_CONSERVATIVE, POLICY_ADAPTIVE_SHADOW):
        sub = tmp_path / policy
        sub.mkdir()
        _run_strict(sub, {"s0": [dict(ev) for ev in events]}, policy=policy,
                    params={}, telemetry=RunTelemetry())
        writes_por_policy[policy] = list(_FakeSession.writes.get("s0") or [])
    assert writes_por_policy[POLICY_CONSERVATIVE]
    assert (
        writes_por_policy[POLICY_ADAPTIVE_SHADOW]
        == writes_por_policy[POLICY_CONSERVATIVE]
        == [b"A", b"B", b"\r", b"C", b"D"]
    )

#!/usr/bin/env python3
"""Benchmark do executor de replay: ANTES × DEPOIS do motor adaptativo.

Mede, com sessões fake (sem SSH/rede — 100% offline e reproduzível), o tempo
que o **Replay2** impõe à jornada (pacing, writes, overhead), isolando o ERP
(que aqui responde instantaneamente — logo tudo o que se mede é Replay2):

- ANTES: trilha no formato legado (cadência artificial de 50 ms por evento,
  ENTER implícito por input) executada com ``execution_policy=conservative``
  (comportamento histórico: um write por evento + sleep de pacing por delta);
- DEPOIS (materialização): trilha nova (timing semântico — chars do campo
  dividem ts, WAIT preservado) com política conservadora;
- DEPOIS (adaptive): trilha nova com ``execution_policy=adaptive`` (batching
  conservador de imprimíveis contíguos).

Uso:

    python3 scripts/benchmark_adaptive_replay.py [--json out.json]

Saída: métricas antes/depois/delta/% para tempo total, pacing, overhead,
writes e throughput (chars/s). Exit code 0 sempre (é medição, não gate).
"""
from __future__ import annotations

import argparse
import base64
import json
import sqlite3
import sys
import tempfile
import time
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "gateway"))

from dakota_gateway.replay import ReplayConfig
from dakota_gateway.replay_control import executors as executors_mod
from dakota_gateway.replay_control.adaptive_scheduler import (
    AdaptiveRuntime,
    POLICY_ADAPTIVE,
    POLICY_CONSERVATIVE,
)
from dakota_gateway.replay_control.execution_telemetry import RunTelemetry
from dakota_gateway.replay_control.executors import (
    LoadTestParams,
    replay_parallel_sessions_concurrent_controlled,
)
from dakota_gateway.synthetic.journey import JourneyDefinition, JourneyStep
from dakota_gateway.synthetic.journey_builder import JourneyBuilder
from dakota_gateway.synthetic.replay_adapter import ReplayAdapter

HMAC_KEY = b"benchmark_adaptive_hmac_key__"

# Cenário (§23-A/B): campos longos, vários campos, algumas sessões
SESSIONS = 3
FIELDS = 4
FIELD_LEN = 12
LEGACY_CADENCE_MS = 50  # política histórica: ts = sess_ts + seq * 50


class _FakeSelector:
    def register(self, *args, **kwargs):
        return None

    def select(self, timeout=None):
        return []

    def close(self):
        return None


class _FakeSession:
    """Sessão fake: write instantâneo (ERP ideal) — mede só o Replay2."""

    writes = 0

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
        type(self).writes += 1

    def close(self):
        return None


def _journey() -> JourneyDefinition:
    fields = "\\n".join(f"{{{{cliente.campo{i}}}}}" for i in range(FIELDS))
    return JourneyDefinition(
        journey_id="bench_adaptive",
        name="Benchmark adaptativo",
        category="bench",
        steps=[
            JourneyStep(step_order=0, screen_id="menu", action="navigate", trigger="3"),
            JourneyStep(step_order=1, screen_id="form", action="input",
                        input_template=fields.replace("\\n", "\n")),
            JourneyStep(step_order=2, screen_id="form", action="submit", trigger="F10"),
        ],
    )


def _build_new_trail(out: Path) -> None:
    ReplayAdapter().generate_synthetic_jsonl(
        _journey(), SESSIONS, 42, str(out), hmac_key=HMAC_KEY,
    )


def _build_legacy_trail(out: Path) -> None:
    """Reproduz o formato histórico: chars em 50 ms/evento + ENTER por input."""
    tmp = Path(tempfile.mkdtemp())
    _build_new_trail(tmp)  # mesma massa/seed → mesmos dados
    # reescreve os ts_ms na cadência antiga (50 ms por evento) — é exatamente
    # o que o adapter legado gravava (sess_ts + seq_session * 50)
    for src in sorted(tmp.glob("audit-*.jsonl")):
        lines = []
        for line in src.read_text(encoding="utf-8").splitlines():
            ev = json.loads(line)
            if ev.get("key_kind") == "wait":
                continue  # o adapter legado descartava WAITs
            ev.pop("key_kind", None)
            lines.append(ev)
        sess_ts = lines[0]["ts_ms"]
        for ev in lines:
            ev["ts_ms"] = sess_ts + int(ev.get("seq_session") or 0) * LEGACY_CADENCE_MS
        (out / src.name).write_text(
            "\n".join(json.dumps(ev) for ev in lines), encoding="utf-8",
        )


def _run(log_dir: Path, policy: str, *, synthetic: bool) -> dict:
    _FakeSession.writes = 0
    telemetry = RunTelemetry()
    rt = AdaptiveRuntime(policy=policy, telemetry=telemetry, synthetic_trail=synthetic)
    started = time.monotonic()
    with (
        mock.patch.object(executors_mod, "_TargetSession", _FakeSession),
        mock.patch.object(executors_mod.selectors, "DefaultSelector", _FakeSelector),
    ):
        replay_parallel_sessions_concurrent_controlled(
            ReplayConfig(log_dir=str(log_dir), target_host="bench", checkpoint_quiet_ms=0),
            LoadTestParams(concurrency=SESSIONS, ramp_up_per_sec=0, speed=1.0),
            window_params={},
            should_pause_or_cancel=lambda: None,
            on_progress=lambda *a: None,
            on_session_result=lambda *a: None,
            on_failure=lambda f: None,
            adaptive=rt,
        )
    wall_ms = (time.monotonic() - started) * 1000.0
    snap = telemetry.snapshot()
    snap["wall_ms"] = wall_ms
    snap["writes"] = _FakeSession.writes
    return snap


def _pct(before: float, after: float) -> float:
    if before <= 0:
        return 0.0
    return round((before - after) / before * 100.0, 1)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", default="", help="grava resultado em JSON")
    args = parser.parse_args()

    tmp = Path(tempfile.mkdtemp(prefix="bench-adaptive-"))
    legacy_dir = tmp / "legacy"
    new_dir = tmp / "new"
    legacy_dir.mkdir(parents=True)
    new_dir.mkdir(parents=True)
    _build_legacy_trail(legacy_dir)
    _build_new_trail(new_dir)

    antes = _run(legacy_dir, POLICY_CONSERVATIVE, synthetic=True)
    depois_mat = _run(new_dir, POLICY_CONSERVATIVE, synthetic=True)
    depois_adp = _run(new_dir, POLICY_ADAPTIVE, synthetic=True)

    report = {
        "cenario": {
            "sessions": SESSIONS, "fields": FIELDS, "field_len": FIELD_LEN,
            "legacy_cadence_ms": LEGACY_CADENCE_MS, "speed": 1.0,
            "nota": "ERP ideal (resposta instantânea): tudo medido é Replay2",
        },
        "antes": antes,
        "depois_materializacao": depois_mat,
        "depois_adaptive": depois_adp,
        "delta_adaptive_vs_antes": {
            "journey_total_ms": round(antes["journey_total_ms"] - depois_adp["journey_total_ms"], 1),
            "pacing_ms": round(antes["pacing_ms"] - depois_adp["pacing_ms"], 1),
            "replay_overhead_ms": round(antes["replay_overhead_ms"] - depois_adp["replay_overhead_ms"], 1),
            "writes": antes["writes"] - depois_adp["writes"],
            "journey_total_improvement_pct": _pct(antes["journey_total_ms"], depois_adp["journey_total_ms"]),
            "pacing_improvement_pct": _pct(antes["pacing_ms"], depois_adp["pacing_ms"]),
            "overhead_improvement_pct": _pct(antes["replay_overhead_ms"], depois_adp["replay_overhead_ms"]),
        },
    }
    text = json.dumps(report, indent=2, ensure_ascii=False)
    print(text)
    if args.json:
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json).write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

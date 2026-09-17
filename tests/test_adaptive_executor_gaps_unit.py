#!/usr/bin/env python3
"""Revisão de riscos do executor adaptativo (pause em batch, fail-fast,
jitter × skip de convergência, política no parallel-sessions simples).

Usa sessões fake (sem SSH/PTY real), no padrão de
tests/test_adaptive_executor_integration.py e tests/test_replay_concurrency_unit.py.
"""
from __future__ import annotations

import base64
import json
import threading
import time
import types
from pathlib import Path
from unittest import mock

import pytest

from dakota_gateway import auth
from dakota_gateway.db.connection import connect as db_connect
from dakota_gateway.replay import ReplayConfig, ReplayError
from dakota_gateway.replay_control import executors as executors_mod
from dakota_gateway.replay_control import runner as runner_mod
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
from dakota_gateway.replay_control.runner import Runner, cancel_run, create_run
from dakota_gateway.state_db import exec1, init_db, now_ms, query_one


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
    write_hook = None

    @classmethod
    def reset(cls, write_hook=None):
        with cls.lock:
            cls.writes = {}
            cls.write_hook = write_hook

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
        cls = type(self)
        hook = cls.write_hook
        if hook is not None:
            hook(self.session_id, data)
        with cls.lock:
            cls.writes.setdefault(self.session_id, []).append(bytes(data))

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
    """Grava capture com eventos por sessão (dicts já prontos)."""
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
            ev.setdefault("type", "bytes")
            ev.setdefault("dir", "in")
            ev["session_id"] = sid
            ev["seq_global"] = seq
            ev["seq_session"] = i + 2
            lines.append(json.dumps(ev))
    (log_dir / "audit-t.part001.jsonl").write_text("\n".join(lines), encoding="utf-8")


def _printables(text: str, ts0: int = 1000, delta: int = 0) -> list[dict]:
    return [
        {"ts_ms": ts0 + i * delta, "data_b64": _b64(ch.encode()), "key_kind": "printable"}
        for i, ch in enumerate(text)
    ]


def _cfg(log_dir: Path) -> ReplayConfig:
    return ReplayConfig(log_dir=str(log_dir), target_host="local", checkpoint_quiet_ms=0)


# ---------------------------------------------------------------------------
# Gap 1 — pause/resume DENTRO de um batch adaptativo
#
# O batch é atômico por design: os bytes acumulam em ``pending`` e o único
# envio é o write único de ``flush_pending``, precedido de
# ``should_pause_or_cancel``. Pause bloqueia dentro do check (runner), então
# nunca há write com a run pausada e o resume completa o batch intacto;
# cancel descarta o batch sem write parcial. Estes testes travam o contrato.
# ---------------------------------------------------------------------------

def test_pause_dentro_de_batch_nao_escreve_e_resume_completa_intacto(tmp_path):
    """Pause no meio da acumulação: nenhum write enquanto pausado; no resume
    o batch sai inteiro em UM write (tudo-ou-nada), na ordem da captura."""
    _write_capture(tmp_path, {"s0": _printables("ABCDE", delta=0)})

    blocked = threading.Event()
    release = threading.Event()
    calls = {"n": 0}
    calls_lock = threading.Lock()

    def spc():
        # Só contam os checks do worker (a thread principal também chama o
        # spc na submissão/espera de futures — contar só o worker torna o
        # ponto de pausa determinístico).
        if not threading.current_thread().name.startswith("replay-vu"):
            return
        with calls_lock:
            calls["n"] += 1
            n = calls["n"]
        # 4º check do worker = topo do loop do 2º input: pending já tem "A".
        if n == 4:
            blocked.set()
            release.wait(timeout=10)

    _FakeSession.reset()
    p1, p2 = _patch_sessions()
    rt = AdaptiveRuntime(policy=POLICY_ADAPTIVE, telemetry=RunTelemetry())
    errors: list = []
    results: list = []

    def target():
        try:
            replay_parallel_sessions_concurrent_controlled(
                _cfg(tmp_path),
                LoadTestParams(concurrency=1, ramp_up_per_sec=0, speed=1.0),
                window_params={},
                should_pause_or_cancel=spc,
                on_progress=lambda *a: None,
                on_session_result=lambda sid, st, msg: results.append((sid, st)),
                on_failure=lambda f: None,
                adaptive=rt,
            )
        except BaseException as exc:  # noqa: BLE001 — o teste quer qualquer falha
            errors.append(exc)

    with p1, p2:
        t = threading.Thread(target=target)
        t.start()
        assert blocked.wait(timeout=5), "worker não chegou ao ponto de pausa"
        time.sleep(0.3)
        # batch parcialmente acumulado, run "pausada": nada pode ter saído
        assert _FakeSession.writes.get("s0") in (None, []), _FakeSession.writes
        release.set()
        t.join(timeout=10)
    assert not errors, errors
    # resume: o batch completo sai como UM write atômico, ordem preservada
    assert _FakeSession.writes["s0"] == [b"ABCDE"]
    assert results == [("s0", "success")], results


def test_cancel_dentro_de_batch_descarta_sem_write_parcial(tmp_path):
    """Cancel no meio da acumulação: o batch pendente é descartado inteiro —
    nenhum byte é enviado depois do ponto de cancelamento."""
    _write_capture(tmp_path, {"s0": _printables("ABCDE", delta=0)})

    paused = threading.Event()
    release = threading.Event()
    cancel = threading.Event()
    calls = {"n": 0}
    calls_lock = threading.Lock()

    def spc():
        if cancel.is_set():
            raise ReplayError("cancelled")
        if not threading.current_thread().name.startswith("replay-vu"):
            return
        with calls_lock:
            calls["n"] += 1
            n = calls["n"]
        # Estaciona o worker com o batch parcialmente acumulado; o teste
        # cancela a run e libera — o próximo check levanta o cancelamento.
        if n == 4:
            paused.set()
            release.wait(timeout=10)
            if cancel.is_set():
                raise ReplayError("cancelled")

    _FakeSession.reset()
    p1, p2 = _patch_sessions()
    rt = AdaptiveRuntime(policy=POLICY_ADAPTIVE, telemetry=RunTelemetry())
    results: list = []
    errors: list = []

    def target():
        try:
            # fail-fast: o cancel observado pelo worker sinaliza stop_all e o
            # executor levanta ReplayError de forma determinística (em modo
            # continue a propagação depende de qual thread vê o cancel
            # primeiro — risco listado no relatório da revisão).
            replay_parallel_sessions_concurrent_controlled(
                _cfg(tmp_path),
                LoadTestParams(concurrency=1, ramp_up_per_sec=0, speed=1.0,
                               on_checkpoint_mismatch="fail-fast"),
                window_params={},
                should_pause_or_cancel=spc,
                on_progress=lambda *a: None,
                on_session_result=lambda sid, st, msg: results.append((sid, st)),
                on_failure=lambda f: None,
                adaptive=rt,
            )
        except BaseException as exc:  # noqa: BLE001 — o teste quer qualquer falha
            errors.append(exc)

    with p1, p2:
        t = threading.Thread(target=target)
        t.start()
        assert paused.wait(timeout=5), "worker não chegou ao batch parcial"
        cancel.set()
        release.set()
        t.join(timeout=10)
    assert errors and isinstance(errors[0], ReplayError), errors
    assert _FakeSession.writes.get("s0") in (None, []), _FakeSession.writes
    assert results == [("s0", "failed")], results


# ---------------------------------------------------------------------------
# Gap 2 — flush de batch em fail-fast
#
# Na sessão que falha, todo ponto de falha (checkpoint/comparação) é
# precedido de flush_pending: o batch sai inteiro ANTES da falha e nenhum
# byte é enviado depois. No fail-fast disparado por OUTRA sessão (stop_all),
# o batch em voo é concluído atomicamente antes da parada — eventos ainda
# não processados nunca são enviados.
# ---------------------------------------------------------------------------

def _mismatch_wait(*args, **kwargs):
    return False, {"matched": False}, {}


def _mock_failure_path():
    return (
        mock.patch.object(executors_mod, "_wait_for_expected_observed", _mismatch_wait),
        mock.patch.object(executors_mod, "expected_screen_text_from_event", lambda *a, **kw: ""),
        mock.patch.object(executors_mod, "observed_screen_text_from_session", lambda *a, **kw: ""),
    )


def test_falha_failfast_envia_batch_inteiro_antes_e_nada_depois(tmp_path):
    """Checkpoint que falha em fail-fast: batch pendente já foi flushed
    (write único, ordem preservada); nenhum byte após o ponto de falha."""
    events = _printables("AB", delta=0) + [
        {"type": "checkpoint", "ts_ms": 1100, "screen_sig": "sig_menu"},
    ]
    _write_capture(tmp_path, {"s0": events})

    _FakeSession.reset()
    p1, p2 = _patch_sessions()
    m1, m2, m3 = _mock_failure_path()
    rt = AdaptiveRuntime(policy=POLICY_ADAPTIVE, telemetry=RunTelemetry())
    results: list = []
    failures: list = []
    lp = LoadTestParams(
        concurrency=1, ramp_up_per_sec=0, speed=1.0,
        on_checkpoint_mismatch="fail-fast",
    )
    with p1, p2, m1, m2, m3:
        with pytest.raises(ReplayError):
            replay_parallel_sessions_concurrent_controlled(
                _cfg(tmp_path),
                lp,
                window_params={},
                should_pause_or_cancel=lambda: None,
                on_progress=lambda *a: None,
                on_session_result=lambda sid, st, msg: results.append((sid, st)),
                on_failure=lambda f: failures.append(f),
                adaptive=rt,
            )
    # o batch "AB" saiu inteiro ANTES da falha; nada depois
    assert _FakeSession.writes["s0"] == [b"AB"], _FakeSession.writes
    assert results == [("s0", "failed")], results
    assert len(failures) == 1


def test_failfast_de_outra_sessao_conclui_batch_em_voo_e_para(tmp_path):
    """Stop global (fail-fast de s0) com batch em voo em s1: o batch é
    concluído atomicamente (tudo-ou-nada) e a sessão para como "stopped";
    o evento ainda não processado nunca é enviado."""
    _write_capture(tmp_path, {
        "s0": [{"type": "checkpoint", "ts_ms": 1000, "screen_sig": "sig_menu"}],
        "s1": _printables("XYZ", delta=0),
    })

    s1_ready = threading.Event()  # s1 acumulou X,Y e está prestes a pedir Z
    s1_gate = threading.Event()   # liberado quando s0 falhou
    real_iter = executors_mod.iter_indexed_events

    def iter_wrapper(entries):
        it = real_iter(entries)

        def gen():
            first = next(it, None)
            if first is None:
                return
            sid = str(first.get("session_id") or "")
            yield first
            if sid != "s1":
                yield from it
                return
            # X e Y são entregues (o worker acumula os dois em ``pending``);
            # o gerador estaciona antes de Z até o fail-fast de s0 acontecer.
            for _ in range(2):
                ev = next(it, None)
                if ev is None:
                    return
                yield ev
            s1_ready.set()
            s1_gate.wait(timeout=10)
            yield from it

        return gen()

    def wait_s0(*args, **kwargs):
        # o checkpoint de s0 só diverge depois que s1 acumulou o batch
        assert s1_ready.wait(timeout=10)
        return False, {"matched": False}, {}

    def on_result(sid, st, msg):
        results.append((sid, st))
        if sid == "s0" and st == "failed":
            s1_gate.set()

    _FakeSession.reset()
    results: list = []
    p1, p2 = _patch_sessions()
    rt = AdaptiveRuntime(policy=POLICY_ADAPTIVE, telemetry=RunTelemetry())
    lp = LoadTestParams(
        concurrency=2, ramp_up_per_sec=0, speed=1.0,
        on_checkpoint_mismatch="fail-fast",
    )
    with p1, p2, \
         mock.patch.object(executors_mod, "iter_indexed_events", iter_wrapper), \
         mock.patch.object(executors_mod, "_wait_for_expected_observed", wait_s0), \
         mock.patch.object(executors_mod, "expected_screen_text_from_event", lambda *a, **kw: ""), \
         mock.patch.object(executors_mod, "observed_screen_text_from_session", lambda *a, **kw: ""):
        with pytest.raises(ReplayError):
            replay_parallel_sessions_concurrent_controlled(
                _cfg(tmp_path),
                lp,
                window_params={},
                should_pause_or_cancel=lambda: None,
                on_progress=lambda *a: None,
                on_session_result=on_result,
                on_failure=lambda f: None,
                adaptive=rt,
            )
    # batch em voo concluído atomicamente; "Z" (não processado) nunca enviado
    assert _FakeSession.writes["s1"] == [b"XY"], _FakeSession.writes
    assert dict(results) == {"s0": "failed", "s1": "stopped"}, results


# ---------------------------------------------------------------------------
# Gap 3 — jitter_ms × skip de pacing por convergência
#
# jitter_ms é configuração explícita do operador (variação de carga). O skip
# de convergência da política adaptive remove a cadência GRAVADA (delta de
# ts_ms), que é artificial; o jitter configurado continua aplicado.
# ---------------------------------------------------------------------------

def _det_events(n: int, *, ts0: int = 1000, delta: int = 400) -> list[dict]:
    return [
        {"type": "deterministic_input", "ts_ms": ts0 + i * delta,
         "key_b64": _b64(str(i).encode()), "key_kind": "printable",
         "screen_sig": f"sig{i}"}
        for i in range(n)
    ]


def _run_det_convergido(tmp_path, *, policy: str, jitter_ms: int, telemetry):
    _write_capture(tmp_path, {"s0": _det_events(3)})
    _FakeSession.reset()
    p1, p2 = _patch_sessions()
    rt = AdaptiveRuntime(policy=policy, telemetry=telemetry)
    lp = LoadTestParams(
        concurrency=1, ramp_up_per_sec=0, speed=1.0, jitter_ms=jitter_ms,
        input_mode="deterministic", on_deterministic_mismatch="send-anyway",
    )
    with p1, p2, \
         mock.patch.object(executors_mod, "_wait_for_expected_observed",
                           lambda *a, **kw: (True, {"matched": True}, {})), \
         mock.patch.object(executors_mod, "random",
                           types.SimpleNamespace(randint=lambda a, b: 30)):
        replay_parallel_sessions_concurrent_controlled(
            _cfg(tmp_path),
            lp,
            window_params={},
            should_pause_or_cancel=lambda: None,
            on_progress=lambda *a: None,
            on_session_result=lambda *a: None,
            on_failure=lambda f: None,
            adaptive=rt,
        )


def test_jitter_configurado_sobrevive_ao_skip_de_convergencia(tmp_path):
    """Adaptive + convergência: o delta de ts_ms é pulado, mas o jitter
    configurado (30ms fixos via mock) continua aplicado por input."""
    tel = RunTelemetry()
    _run_det_convergido(tmp_path, policy=POLICY_ADAPTIVE, jitter_ms=50, telemetry=tel)
    snap = tel.snapshot()
    # skip da cadência gravada continua valendo (2× o delta de 400ms)
    assert snap["convergence_pacing_skip_count"] == 2, snap
    assert snap["convergence_pacing_saved_ms"] >= 800, snap
    # mas o jitter explícito do operador não pode ser zerado pelo skip
    assert snap["pacing_ms"] >= 50, snap["pacing_ms"]


def test_jitter_conservador_aplicado_entre_inputs(tmp_path):
    """Conservador: delta gravado + jitter são pagos integralmente (pin)."""
    tel = RunTelemetry()
    _run_det_convergido(tmp_path, policy=POLICY_CONSERVATIVE, jitter_ms=50, telemetry=tel)
    snap = tel.snapshot()
    assert snap["pacing_ms"] >= 800, snap["pacing_ms"]
    assert snap.get("convergence_pacing_skip_count", 0) == 0


# ---------------------------------------------------------------------------
# Gap 4 — execution_policy=adaptive no parallel-sessions SIMPLES
#
# O executor simples (concurrency<=1) não recebe o AdaptiveRuntime — a
# política pedida seria ignorada em silêncio com as métricas dizendo
# "adaptive". Decisão (c): a run segue conservadora (comportamento seguro —
# a cadência ali já é dirigida por checkpoint, sem pacing/batching a
# otimizar) e o runner registra aviso estruturado
# (policy_effective=policy_warning em metrics_json["adaptive"] + evento).
# ---------------------------------------------------------------------------

def _mk_run_com_policy(tmp_path, params: dict):
    log_dir = tmp_path / "cap"
    log_dir.mkdir()
    _write_capture(log_dir, {"s0": _printables("AB", delta=0)})
    con = db_connect(str(tmp_path / "t.db"))
    init_db(con)
    ph = auth.pbkdf2_hash_password("admin123")
    user_id = int(con.execute(
        "INSERT INTO users(username,password_hash,role,created_at_ms) VALUES(?,?,?,?)",
        ("admin", ph, "admin", now_ms()),
    ).lastrowid)
    rid = create_run(con, user_id, str(log_dir), "h", "u", "", "parallel-sessions")
    exec1(con, "UPDATE replay_runs SET params_json=? WHERE id=?",
          (json.dumps(params), rid))
    return con, rid


def test_runner_avisa_quando_adaptive_cai_no_parallel_simples(tmp_path):
    """concurrency ausente/1 + adaptive: aviso estruturado, run conservadora."""
    con, rid = _mk_run_com_policy(tmp_path, {"execution_policy": "adaptive"})
    try:
        runner = Runner(str(tmp_path / "t.db"), b"k" * 32)
        with mock.patch.object(runner_mod, "verify_log", lambda *a, **k: None), \
             mock.patch.object(runner_mod, "replay_parallel_sessions_controlled",
                               lambda *a, **k: None):
            runner.run_foreground(rid)
        row = query_one(con, "SELECT status, metrics_json FROM replay_runs WHERE id=?", (rid,))
        assert row["status"] == "success", row["status"]
        adaptive = json.loads(row["metrics_json"]).get("adaptive") or {}
        assert adaptive.get("execution_policy") == "adaptive", adaptive
        assert adaptive.get("policy_effective") == "conservative", adaptive
        assert adaptive.get("policy_warning"), adaptive
        ev = query_one(
            con,
            "SELECT kind, message FROM replay_run_events WHERE run_id=? AND kind='warning'",
            (rid,),
        )
        assert ev and "execution_policy" in str(ev["message"]), ev
    finally:
        con.close()


def test_runner_concorrente_aplica_policy_sem_aviso(tmp_path):
    """concurrency>1 + adaptive: o motor adaptativo é entregue ao executor
    concurrent e nenhum aviso de política é registrado."""
    con, rid = _mk_run_com_policy(
        tmp_path, {"execution_policy": "adaptive", "concurrency": 2},
    )
    try:
        captured: dict = {}
        runner = Runner(str(tmp_path / "t.db"), b"k" * 32)
        with mock.patch.object(runner_mod, "verify_log", lambda *a, **k: None), \
             mock.patch.object(
                 runner_mod, "replay_parallel_sessions_concurrent_controlled",
                 lambda *a, **k: captured.update(k)):
            runner.run_foreground(rid)
        assert getattr(captured.get("adaptive"), "policy", None) == "adaptive", captured
        row = query_one(con, "SELECT metrics_json FROM replay_runs WHERE id=?", (rid,))
        adaptive = json.loads(row["metrics_json"]).get("adaptive") or {}
        assert "policy_warning" not in adaptive, adaptive
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Corrida de cancel em modo "continue"
#
# Cenário: o operador cancela a run; o cancel é observado apenas pelo WORKER
# (que o trata como falha de sessão em modo continue — sem stop_all) e a
# thread principal do executor sai do loop de espera sem rechecar; o executor
# retorna normalmente. Antes da correção o runner marcava a run como
# "success", sobrescrevendo o "cancelled" gravado pelo cancel_run. Uma run
# cancelada pelo operador NUNCA pode terminar "success": o runner re-checa o
# controle (sem o TTL do cache) antes de marcar sucesso.
# ---------------------------------------------------------------------------

def test_cancel_visto_so_pelo_worker_nunca_termina_success(tmp_path):
    """Executor retorna normalmente com a run cancelada no banco durante a
    execução (corrida do modo continue) — o runner deve terminar 'cancelled'.

    O _RunControlState cacheia o status (TTL de 1s) antes da execução: o
    cancel cai DENTRO da janela do cache, então um check() comum não o veria
    — a reprodução exige releitura forçada, que é a correção.
    """
    con, rid = _mk_run_com_policy(tmp_path, {"concurrency": 2})
    try:
        exec1(con, "UPDATE replay_runs SET status='running' WHERE id=?", (rid,))

        def executor_engole_cancel(*a, **k):
            # o worker vê o cancel e o trata como falha de sessão (modo
            # continue): o executor retorna SEM levantar ReplayError
            cancel_run(con, rid)

        runner = Runner(str(tmp_path / "t.db"), b"k" * 32)
        with mock.patch.object(runner_mod, "verify_log", lambda *a, **k: None), \
             mock.patch.object(
                 runner_mod, "replay_parallel_sessions_concurrent_controlled",
                 executor_engole_cancel):
            runner.run_foreground(rid)
        row = query_one(con, "SELECT status FROM replay_runs WHERE id=?", (rid,))
        assert row["status"] == "cancelled", row["status"]
    finally:
        con.close()


def test_run_sem_cancel_termina_success_com_recheque(tmp_path):
    """Pin da mudança: run normal (sem cancel) continua terminando success."""
    con, rid = _mk_run_com_policy(tmp_path, {"concurrency": 2})
    try:
        runner = Runner(str(tmp_path / "t.db"), b"k" * 32)
        with mock.patch.object(runner_mod, "verify_log", lambda *a, **k: None), \
             mock.patch.object(
                 runner_mod, "replay_parallel_sessions_concurrent_controlled",
                 lambda *a, **k: None):
            runner.run_foreground(rid)
        row = query_one(con, "SELECT status FROM replay_runs WHERE id=?", (rid,))
        assert row["status"] == "success", row["status"]
    finally:
        con.close()

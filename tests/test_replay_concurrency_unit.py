"""Testes de concorrência real e escala dos executores de replay (Fase 8).

Cobre: paralelismo real entre sessões, limite de workers/threads, ordem
preservada dentro de cada sessão, pausa/cancelamento responsivos, consumo de
memória limitado (sem materialização integral do capture) e o controle de
run com cache de status (sem consulta ao SQLite por evento).
"""
from __future__ import annotations

import base64
import json
import threading
import time
import tracemalloc
from pathlib import Path
from threading import Lock
from unittest import mock

import pytest

from dakota_gateway import auth
from dakota_gateway import replay_control
from dakota_gateway.db.connection import connect as db_connect
from dakota_gateway.replay import ReplayConfig, ReplayError
from dakota_gateway.replay_control import executors as executors_mod
from dakota_gateway.replay_control import runner as runner_mod
from dakota_gateway.replay_control import window as window_mod
from dakota_gateway.replay_control.executors import (
    LoadTestParams,
    replay_parallel_sessions_concurrent_controlled,
    replay_parallel_sessions_controlled,
)
from dakota_gateway.replay_control.runner import Runner, _RunControlState, cancel_run, create_run, pause_run, resume_run
from dakota_gateway.replay_control.window import index_session_events, scan_capture_metadata
from dakota_gateway.state_db import exec1, init_db, now_ms, query_one


class _FakeSelector:
    def register(self, *args, **kwargs):
        return None

    def select(self, timeout=None):
        return []

    def close(self):
        return None


class _FakeSession:
    """Sessão fake instrumentada: conta ativas, registra writes por sessão."""

    lock = threading.Lock()
    active = 0
    max_active = 0
    max_vu_threads = 0
    writes: dict[str, list[bytes]] = {}
    write_hook = None  # callable(session_id, data)
    keep_writes = True  # False em testes de memória (não reter payloads)

    @classmethod
    def reset(cls, write_hook=None):
        with cls.lock:
            cls.active = 0
            cls.max_active = 0
            cls.max_vu_threads = 0
            cls.writes = {}
            cls.write_hook = write_hook
            cls.keep_writes = True

    def __init__(self, cfg, sid, target_user_override=None):
        cls = type(self)
        with cls.lock:
            cls.active += 1
            cls.max_active = max(cls.max_active, cls.active)
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
        if not cls.keep_writes:
            return
        with cls.lock:
            cls.writes.setdefault(self.session_id, []).append(bytes(data))

    def close(self):
        cls = type(self)
        with cls.lock:
            cls.active -= 1


def _write_capture(log_dir: Path, sessions: int, inputs_per_session: int, payload: int = 16) -> list[str]:
    """Gera capture sintética (não assinada) com N sessões × M inputs raw."""
    data = (b"0123456789" * (payload // 10 + 1))[:payload]
    data_b64 = base64.b64encode(data).decode("ascii")
    lines: list[str] = []
    seq = 0
    sids = []
    for s in range(sessions):
        sid = f"s{s:04d}"
        sids.append(sid)
        seq += 1
        lines.append(json.dumps({
            "type": "session_start", "session_id": sid,
            "seq_global": seq, "seq_session": 1, "rows": 25, "cols": 80,
        }))
        for i in range(inputs_per_session):
            seq += 1
            lines.append(json.dumps({
                "type": "bytes", "dir": "in", "session_id": sid,
                "seq_global": seq, "seq_session": i + 2,
                "ts_ms": 1000 + i * 10, "data_b64": data_b64,
            }))
    (log_dir / "audit-load.part001.jsonl").write_text("\n".join(lines), encoding="utf-8")
    return sids


def _cfg(log_dir: str) -> ReplayConfig:
    return ReplayConfig(log_dir=log_dir, target_host="local", checkpoint_quiet_ms=0)


def _run_catching(fn, bucket: list) -> None:
    try:
        fn()
    except BaseException as exc:  # noqa: BLE001 — teste quer capturar qualquer falha
        bucket.append(exc)


def _patch_sessions():
    return (
        mock.patch.object(executors_mod, "_TargetSession", _FakeSession),
        mock.patch.object(executors_mod.selectors, "DefaultSelector", _FakeSelector),
    )


# ---------------------------------------------------------------------------
# Paralelismo real / limite de workers
# ---------------------------------------------------------------------------

def test_parallel_sessions_executa_em_paralelo_de_verdade(tmp_path):
    """3 sessões com barreira de 3: sequencial travaria no timeout da barreira."""
    _write_capture(tmp_path, sessions=3, inputs_per_session=2)
    barrier = threading.Barrier(3, timeout=5)
    seen: set[str] = set()
    seen_lock = Lock()

    def hook(sid, data):
        with seen_lock:
            first = sid not in seen
            seen.add(sid)
        if first:
            barrier.wait()

    _FakeSession.reset(write_hook=hook)
    p1, p2 = _patch_sessions()
    with p1, p2:
        replay_parallel_sessions_controlled(
            _cfg(str(tmp_path)),
            params={},
            should_pause_or_cancel=lambda: None,
            on_progress=lambda *a: None,
            on_failure=lambda f: None,
        )
    assert len(_FakeSession.writes) == 3


def test_concurrent_respeita_limite_de_workers(tmp_path):
    """100 sessões com concurrency=5: nunca mais de 5 sessões ativas."""
    _write_capture(tmp_path, sessions=100, inputs_per_session=3)

    def hook(sid, data):
        time.sleep(0.002)

    _FakeSession.reset(write_hook=hook)
    results: list[tuple[str, str]] = []
    p1, p2 = _patch_sessions()
    with p1, p2:
        replay_parallel_sessions_concurrent_controlled(
            _cfg(str(tmp_path)),
            LoadTestParams(concurrency=5, ramp_up_per_sec=0, speed=0),
            window_params={},
            should_pause_or_cancel=lambda: None,
            on_progress=lambda *a: None,
            on_session_result=lambda sid, st, msg: results.append((sid, st)),
            on_failure=lambda f: None,
        )
    assert _FakeSession.max_active <= 5
    assert _FakeSession.max_active >= 2  # houve paralelismo real
    assert sum(1 for _, st in results if st == "success") == 100


def test_sem_explosao_de_threads_com_1000_sessoes(tmp_path):
    """1000 sessões com concurrency=10: pool fixo, sem 1-thread-por-sessão."""
    _write_capture(tmp_path, sessions=1000, inputs_per_session=2)
    observed = {"max": 0}
    obs_lock = Lock()

    def hook(sid, data):
        n = sum(1 for t in threading.enumerate() if t.name.startswith("replay-vu"))
        with obs_lock:
            observed["max"] = max(observed["max"], n)

    _FakeSession.reset(write_hook=hook)
    results: list[str] = []
    p1, p2 = _patch_sessions()
    with p1, p2:
        replay_parallel_sessions_concurrent_controlled(
            _cfg(str(tmp_path)),
            LoadTestParams(concurrency=10, ramp_up_per_sec=0, speed=0),
            window_params={},
            should_pause_or_cancel=lambda: None,
            on_progress=lambda *a: None,
            on_session_result=lambda sid, st, msg: results.append(st),
            on_failure=lambda f: None,
        )
    assert _FakeSession.max_active <= 10
    assert observed["max"] <= 10
    assert sum(1 for st in results if st == "success") == 1000


def test_ordem_preservada_dentro_de_cada_sessao(tmp_path):
    """Writes de cada sessão saem na ordem dos seqs, mesmo sob concorrência."""
    sessions, inputs = 30, 40
    lines: list[str] = []
    seq = 0
    for s in range(sessions):
        sid = f"s{s:04d}"
        seq += 1
        lines.append(json.dumps({
            "type": "session_start", "session_id": sid,
            "seq_global": seq, "seq_session": 1, "rows": 25, "cols": 80,
        }))
        for i in range(inputs):
            seq += 1
            lines.append(json.dumps({
                "type": "bytes", "dir": "in", "session_id": sid,
                "seq_global": seq, "seq_session": i + 2, "ts_ms": 1000 + i,
                "data_b64": base64.b64encode(f"{i:04d}".encode()).decode("ascii"),
            }))
    (tmp_path / "audit-ord.part001.jsonl").write_text("\n".join(lines), encoding="utf-8")

    _FakeSession.reset()
    p1, p2 = _patch_sessions()
    with p1, p2:
        replay_parallel_sessions_concurrent_controlled(
            _cfg(str(tmp_path)),
            LoadTestParams(concurrency=8, ramp_up_per_sec=0, speed=0),
            window_params={},
            should_pause_or_cancel=lambda: None,
            on_progress=lambda *a: None,
            on_session_result=lambda *a: None,
            on_failure=lambda f: None,
        )
    expected = [f"{i:04d}".encode() for i in range(inputs)]
    assert len(_FakeSession.writes) == sessions
    for sid, got in _FakeSession.writes.items():
        assert got == expected, f"ordem quebrada na sessão {sid}"


# ---------------------------------------------------------------------------
# Pausa / cancelamento responsivos
# ---------------------------------------------------------------------------

def test_cancelamento_para_em_menos_de_2s(tmp_path):
    """Cancelar run com muitas sessões em voo encerra o executor em < 2s."""
    _write_capture(tmp_path, sessions=500, inputs_per_session=100)
    cancel = threading.Event()
    progressed = {"n": 0}
    prog_lock = Lock()

    def spc():
        if cancel.is_set():
            raise ReplayError("cancelled")

    def on_progress(*a):
        with prog_lock:
            progressed["n"] += 1

    _FakeSession.reset()
    errors: list = []
    p1, p2 = _patch_sessions()
    with p1, p2:
        t = threading.Thread(
            target=_run_catching,
            args=(lambda: replay_parallel_sessions_concurrent_controlled(
                _cfg(str(tmp_path)),
                LoadTestParams(concurrency=10, ramp_up_per_sec=0, speed=0),
                window_params={},
                should_pause_or_cancel=spc,
                on_progress=on_progress,
                on_session_result=lambda *a: None,
                on_failure=lambda f: None,
            ), errors),
            daemon=True,
        )
        t.start()
        deadline = time.monotonic() + 10
        while progressed["n"] < 20 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert progressed["n"] >= 20
        t0 = time.monotonic()
        cancel.set()
        t.join(timeout=10)
        elapsed = time.monotonic() - t0
    assert not t.is_alive()
    assert elapsed < 2.0, f"cancelamento demorou {elapsed:.2f}s"
    assert errors and isinstance(errors[0], ReplayError) and str(errors[0]) == "cancelled"


def test_pausa_bloqueia_e_resume_retoma(tmp_path):
    """Com a run pausada o progresso estaciona; ao resumir, tudo completa."""
    _write_capture(tmp_path, sessions=10, inputs_per_session=100)
    paused = threading.Event()
    progressed = {"n": 0}
    prog_lock = Lock()

    def spc():
        while paused.is_set():
            time.sleep(0.02)

    def on_progress(*a):
        with prog_lock:
            progressed["n"] += 1

    results: list[str] = []
    _FakeSession.reset()
    p1, p2 = _patch_sessions()
    with p1, p2:
        t = threading.Thread(
            target=_run_catching,
            args=(lambda: replay_parallel_sessions_concurrent_controlled(
                _cfg(str(tmp_path)),
                LoadTestParams(concurrency=4, ramp_up_per_sec=0, speed=0),
                window_params={},
                should_pause_or_cancel=spc,
                on_progress=on_progress,
                on_session_result=lambda sid, st, msg: results.append(st),
                on_failure=lambda f: None,
            ), []),
            daemon=True,
        )
        t.start()
        deadline = time.monotonic() + 10
        while progressed["n"] < 20 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert progressed["n"] >= 20
        paused.set()
        time.sleep(0.3)  # dá tempo de todos os workers estacionarem no spc
        with prog_lock:
            snapshot = progressed["n"]
        time.sleep(0.6)
        with prog_lock:
            assert progressed["n"] == snapshot, "progresso avançou com a run pausada"
        paused.clear()
        t.join(timeout=15)
    assert not t.is_alive()
    assert sum(1 for st in results if st == "success") == 10


# ---------------------------------------------------------------------------
# Controle de run com cache (sem consulta ao SQLite por evento)
# ---------------------------------------------------------------------------

def _mk_run(db_path: Path):
    con = db_connect(str(db_path))
    init_db(con)
    ph = auth.pbkdf2_hash_password("admin123")
    user_id = int(con.execute(
        "INSERT INTO users(username,password_hash,role,created_at_ms) VALUES(?,?,?,?)",
        ("admin", ph, "admin", now_ms()),
    ).lastrowid)
    log_dir = db_path.parent / "cap"
    log_dir.mkdir()
    rid = create_run(con, user_id, str(log_dir), "h", "u", "", "strict-global")
    exec1(con, "UPDATE replay_runs SET status='running' WHERE id=?", (rid,))
    return con, rid


def test_run_control_nao_consulta_db_a_cada_check(tmp_path):
    con, rid = _mk_run(tmp_path / "t.db")
    try:
        control = _RunControlState(con, rid, Lock(), poll_interval_s=60.0)
        calls = {"n": 0}
        original = runner_mod.get_run

        def counting(*a, **k):
            calls["n"] += 1
            return original(*a, **k)

        with mock.patch.object(runner_mod, "get_run", counting):
            for _ in range(500):
                control.check()
        assert calls["n"] == 0, f"status relido {calls['n']}x dentro do TTL"
    finally:
        con.close()


def test_run_control_reage_ao_cancel_apos_ttl(tmp_path):
    con, rid = _mk_run(tmp_path / "t.db")
    try:
        control = _RunControlState(con, rid, Lock(), poll_interval_s=0.2)
        control.check()  # running: passa
        cancel_run(con, rid)
        t0 = time.monotonic()
        with pytest.raises(ReplayError, match="cancelled"):
            while True:
                control.check()
                time.sleep(0.01)
                if time.monotonic() - t0 > 3:
                    break
        assert time.monotonic() - t0 < 2.0
    finally:
        con.close()


def test_run_control_pausa_bloqueia_e_resume_libera(tmp_path):
    con, rid = _mk_run(tmp_path / "t.db")
    try:
        pause_run(con, rid)  # pausa ANTES do controle: a leitura inicial já vê paused
        control = _RunControlState(con, rid, Lock(), poll_interval_s=0.1)
        timer = threading.Timer(0.3, lambda: resume_run(con, rid))
        timer.start()
        t0 = time.monotonic()
        control.check()
        elapsed = time.monotonic() - t0
        timer.join()
        assert elapsed >= 0.25, "check() não bloqueou enquanto pausado"
        assert elapsed < 3.0
    finally:
        con.close()


def test_runner_faz_uma_unica_varredura_de_metadados(tmp_path):
    """Runner calcula sessions_total/seq_end com uma passagem cacheada por run."""
    log_dir = tmp_path / "cap"
    log_dir.mkdir()
    sids = _write_capture(log_dir, sessions=3, inputs_per_session=9)
    con = db_connect(str(tmp_path / "t.db"))
    init_db(con)
    ph = auth.pbkdf2_hash_password("admin123")
    user_id = int(con.execute(
        "INSERT INTO users(username,password_hash,role,created_at_ms) VALUES(?,?,?,?)",
        ("admin", ph, "admin", now_ms()),
    ).lastrowid)
    rid = create_run(con, user_id, str(log_dir), "h", "u", "", "strict-global")

    scans = {"n": 0}
    original_scan = window_mod.scan_capture_metadata

    def counting_scan(*a, **k):
        scans["n"] += 1
        return original_scan(*a, **k)

    runner = Runner(str(tmp_path / "t.db"), b"k" * 32)
    with mock.patch.object(runner_mod, "verify_log", lambda *a, **k: None), \
         mock.patch.object(runner_mod, "replay_strict_global_controlled", lambda *a, **k: None), \
         mock.patch.object(runner_mod, "scan_capture_metadata", counting_scan):
        runner.run_foreground(rid)
    assert scans["n"] == 1, f"metadados varridos {scans['n']}x na mesma run"
    row = query_one(con, "SELECT status, last_seq_global_applied FROM replay_runs WHERE id=?", (rid,))
    assert row["status"] == "success"
    assert row["last_seq_global_applied"] == 30  # 3 session_start + 27 inputs
    con.close()


# ---------------------------------------------------------------------------
# Metadados / índice de offsets / memória
# ---------------------------------------------------------------------------

def test_scan_capture_metadata_resumem_janela(tmp_path):
    sids = _write_capture(tmp_path, sessions=5, inputs_per_session=10)
    meta = scan_capture_metadata(str(tmp_path), {})
    assert meta["sessions_total"] == 5
    assert meta["seq_end"] == 55  # 5 starts + 50 inputs
    meta_win = scan_capture_metadata(str(tmp_path), {"replay_from_seq_global": 50})
    assert meta_win["seq_end"] == 55
    assert meta_win["sessions_total"] == 1  # só a última sessão entra na janela


def test_indice_de_offsets_respeita_janela_e_ordem(tmp_path):
    sids = _write_capture(tmp_path, sessions=4, inputs_per_session=3)
    index, starts = window_mod.index_session_events(str(tmp_path), {})
    assert sorted(index.keys()) == sids
    assert set(starts.keys()) == set(sids)
    assert all(st.get("type") == "session_start" for st in starts.values())
    for sid in sids:
        seqs = [ev.get("seq_global") for ev in window_mod.iter_indexed_events(index[sid])]
        assert seqs == sorted(seqs)
        assert len(seqs) == 4  # 1 session_start + 3 inputs
    # janela por sessão: só s0002
    index2, _ = window_mod.index_session_events(str(tmp_path), {"replay_session_id": "s0002"})
    assert sorted(index2.keys()) == ["s0002"]


@pytest.fixture(scope="module")
def big_capture(tmp_path_factory):
    """Capture ~20MB: 25 sessões × 600 inputs com payload de 1KB."""
    d = tmp_path_factory.mktemp("bigcap")
    _write_capture(d, sessions=25, inputs_per_session=600, payload=1000)
    return str(d)


def test_indice_nao_materializa_eventos(big_capture):
    """Construir o índice de offsets não retém os eventos em memória."""
    size_mb = sum(f.stat().st_size for f in Path(big_capture).glob("audit-*.jsonl")) / 1e6
    assert size_mb > 15, f"fixture pequena demais ({size_mb:.1f}MB) para discriminar"
    tracemalloc.start()
    try:
        index, starts = index_session_events(big_capture, {})
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert len(index) == 25
    assert peak < 12e6, f"índice reteve {peak / 1e6:.1f}MB — materialização integral?"


def test_replay_concorrente_nao_materializa_capture(big_capture):
    """Replay concorrente sobre capture grande mantém pico de memória limitado."""
    _FakeSession.reset()
    _FakeSession.keep_writes = False  # o mock não pode reter os payloads
    results: list[str] = []
    p1, p2 = _patch_sessions()
    tracemalloc.start()
    try:
        with p1, p2:
            replay_parallel_sessions_concurrent_controlled(
                _cfg(big_capture),
                LoadTestParams(concurrency=4, ramp_up_per_sec=0, speed=0),
                window_params={},
                should_pause_or_cancel=lambda: None,
                on_progress=lambda *a: None,
                on_session_result=lambda sid, st, msg: results.append(st),
                on_failure=lambda f: None,
            )
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert sum(1 for st in results if st == "success") == 25
    assert peak < 15e6, f"replay reteve {peak / 1e6:.1f}MB — capture materializado"

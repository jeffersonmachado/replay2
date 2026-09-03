"""Testes do job assíncrono de replay sintético em 1 clique.

Regressão do achado da captura 81 (AIX, v0.9.2): a rota
POST /api/captures/{id}/synthetic-replay era síncrona e a síntese levava
mais de 5 min no AIX — o request HTTP morria por timeout sem criar a run.
A rota passa a disparar um job em thread daemon e a UI consulta o andamento
por GET .../synthetic-replay-jobs/{job_id}.
"""
from __future__ import annotations

import threading
import time

import pytest

from control.services import capture_synthesis_service as mod


class _FakePool:
    """Pool mínimo: conta acquire/release para provar a liberação."""

    def __init__(self):
        self.acquired = 0
        self.released = 0

    def acquire(self):
        self.acquired += 1
        return object()

    def release(self, con):
        self.released += 1


def _wait_job(job_id: str, timeout: float = 5.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = mod.get_synthetic_replay_job(job_id)
        assert job is not None, f"job {job_id} sumiu do registro"
        if job["status"] in {"done", "error"}:
            return job
        time.sleep(0.02)
    raise AssertionError(f"job {job_id} não terminou em {timeout}s — status {job['status']}")


def _start(pool, **kwargs):
    params = dict(
        capture_id=81,
        created_by=1,
        source_dir="/dakota11/prg",
        runner=object(),
        hmac_key=b"k",
    )
    params.update(kwargs)
    return mod.start_synthetic_replay_job(pool, **params)


def test_job_conclui_com_resultado_e_fases(monkeypatch):
    def fake_sync(con, capture_id, **kwargs):
        progress = kwargs.get("progress")
        assert callable(progress), "o job precisa repassar o callback de progresso"
        progress("sintetizando dados a partir da captura")
        progress("construindo trilha sintética")
        return {"ok": True, "run_id": 123, "substitutions_count": 3}

    monkeypatch.setattr(mod, "start_synthetic_replay", fake_sync)
    pool = _FakePool()
    out = _start(pool)
    assert out["ok"] is True
    assert out["status"] == "queued"
    assert out["job_id"]
    assert out["capture_id"] == 81

    job = _wait_job(out["job_id"])
    assert job["status"] == "done"
    assert job["error"] is None
    assert job["result"]["run_id"] == 123
    assert job["phases"] == [
        "sintetizando dados a partir da captura",
        "construindo trilha sintética",
    ]
    assert job["finished_ms"] >= job["started_ms"]
    assert pool.acquired == 1 and pool.released == 1


def test_job_registra_erro_e_libera_conexao(monkeypatch):
    def fake_sync(con, capture_id, **kwargs):
        raise ValueError("captura não encontrada")

    monkeypatch.setattr(mod, "start_synthetic_replay", fake_sync)
    pool = _FakePool()
    out = _start(pool)
    job = _wait_job(out["job_id"])
    assert job["status"] == "error"
    assert "captura não encontrada" in job["error"]
    assert job["result"] is None
    assert pool.acquired == 1 and pool.released == 1


def test_job_desconhecido_retorna_none():
    assert mod.get_synthetic_replay_job("job-inexistente") is None


def test_start_synthetic_replay_emite_fases_de_progresso(monkeypatch, tmp_path):
    """A função síncrona reporta as 3 fases quando recebe o callback."""
    dataset = tmp_path / "dataset.jsonl"
    dataset.write_text('{"rede": "1"}\n', encoding="utf-8")

    monkeypatch.setattr(mod, "synthesize_capture", lambda *a, **k: {
        "artifacts": {"dataset": str(dataset)},
        "output_dir": str(tmp_path),
        "screen_mappings": [],
        "capture_jsonl": "audit-000001.jsonl",
        "journey_id": "",
        "warnings": [],
        "key_fields": [],
        "stored_skip_fields": [],
        "skip_fields": [],
        "lookup_counts": {},
    })
    monkeypatch.setattr(mod, "build_synthetic_trail", lambda *a, **k: {
        "events": 10,
        "applied": [],
        "applied_detail": [],
        "dropped_banner": 0,
        "dropped_entry": 0,
        "warnings": [],
        "entry": None,
    })

    import control.services.run_service as run_service
    monkeypatch.setattr(
        run_service, "create_run_request_payload", lambda *a, **k: {"id": 7}
    )

    class _Runner:
        def __init__(self):
            self.started = []

        def start_run_async(self, run_id):
            self.started.append(run_id)

    runner = _Runner()
    phases: list[str] = []
    payload = mod.start_synthetic_replay(
        object(),
        81,
        created_by=1,
        source_dir="/dakota11/prg",
        auto_entry=False,
        runner=runner,
        hmac_key=b"k",
        progress=phases.append,
    )
    assert payload["ok"] is True
    assert payload["run_id"] == 7
    assert runner.started == [7]
    assert phases == [
        "sintetizando dados a partir da captura",
        "construindo trilha sintética",
        "criando run de replay",
    ]


def test_start_synthetic_replay_sem_progress_segue_compativel(monkeypatch, tmp_path):
    """Back-compat: chamadores antigos sem o callback continuam funcionando."""
    dataset = tmp_path / "dataset.jsonl"
    dataset.write_text('{"rede": "1"}\n', encoding="utf-8")
    monkeypatch.setattr(mod, "synthesize_capture", lambda *a, **k: {
        "artifacts": {"dataset": str(dataset)},
        "output_dir": str(tmp_path),
        "screen_mappings": [],
        "capture_jsonl": "audit-000001.jsonl",
        "journey_id": "",
        "warnings": [],
        "key_fields": [],
        "stored_skip_fields": [],
        "skip_fields": [],
        "lookup_counts": {},
    })
    monkeypatch.setattr(mod, "build_synthetic_trail", lambda *a, **k: {
        "events": 1, "applied": [], "applied_detail": [],
        "dropped_banner": 0, "dropped_entry": 0, "warnings": [], "entry": None,
    })
    import control.services.run_service as run_service
    monkeypatch.setattr(
        run_service, "create_run_request_payload", lambda *a, **k: {"id": 8}
    )
    payload = mod.start_synthetic_replay(
        object(), 81, created_by=1, source_dir="/x", auto_entry=False,
        runner=type("R", (), {"start_run_async": lambda self, rid: None})(),
        hmac_key=b"k",
    )
    assert payload["run_id"] == 8



# ---------------------------------------------------------------------------
# Nível de rota: o default do POST passou a ser assíncrono (202 + job_id) e o
# GET .../synthetic-replay-jobs/{job_id} expõe o andamento para a UI.
# ---------------------------------------------------------------------------

import json
import tempfile
import unittest
from pathlib import Path


class _FakeWFile:
    def __init__(self):
        self.data = b""

    def write(self, data):
        self.data += data


class _RouteFakeHandler:
    def __init__(self, server, body=None):
        self.server = server
        self._body = body or {}
        self.status_code = 200
        self.wfile = _FakeWFile()

    def _require(self, roles=None):
        return {"id": 1, "username": "admin", "role": "admin"}

    def send_response(self, code):
        self.status_code = code

    def send_header(self, *args, **kwargs):
        return None

    def end_headers(self):
        return None

    def json(self):
        return json.loads(self.wfile.data.decode("utf-8"))


class _RouteFakeRunner:
    hmac_key = b"k" * 32

    def __init__(self):
        self.started = []

    def start_run_async(self, run_id):
        self.started.append(run_id)


class _RouteFakeServer:
    def __init__(self, pool, runner):
        self.db_pool = pool
        self.runner = runner


class _Parsed:
    def __init__(self, path):
        self.path = path
        self.query = ""


class SyntheticReplayAsyncRouteTests(unittest.TestCase):
    def test_post_default_retorna_job_e_get_expoe_andamento(self):
        from dakota_gateway.state_db import ConnectionPool
        from control.routes.capture_routes import (
            handle_capture_get_route,
            handle_capture_post_route,
        )

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db_path = str(Path(tmp.name) / "t.db")
        pool = ConnectionPool(db_path, min_size=1, max_size=4)
        runner = _RouteFakeRunner()
        server = _RouteFakeServer(pool, runner)

        import control.services.capture_synthesis_service as svc

        def fake_sync(con, capture_id, **kwargs):
            progress = kwargs.get("progress")
            if callable(progress):
                progress("sintetizando dados a partir da captura")
            return {"ok": True, "run_id": 42}

        orig = svc.start_synthetic_replay
        svc.start_synthetic_replay = fake_sync
        self.addCleanup(setattr, svc, "start_synthetic_replay", orig)

        handler = _RouteFakeHandler(server, body={"source_dir": "/dakota11/prg"})
        handled = handle_capture_post_route(
            handler, _Parsed("/api/captures/81/synthetic-replay"), handler._body,
            now_ms_fn=lambda: 456,
            log_dir_base=str(Path(tmp.name) / "captures"),
        )
        self.assertTrue(handled)
        self.assertEqual(handler.status_code, 202)
        data = handler.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["status"], "queued")
        job_id = data["job_id"]

        # GET consulta o job até concluir — sem timeout de HTTP no meio.
        for _ in range(500):
            get_handler = _RouteFakeHandler(server)
            handled = handle_capture_get_route(
                get_handler,
                _Parsed(f"/api/captures/81/synthetic-replay-jobs/{job_id}"),
                read_gateway_monitor_fn=None,
            )
            self.assertTrue(handled)
            self.assertEqual(get_handler.status_code, 200)
            job = get_handler.json()["job"]
            if job["status"] in {"done", "error"}:
                break
            time.sleep(0.02)
        self.assertEqual(job["status"], "done")
        self.assertEqual(job["result"]["run_id"], 42)
        self.assertEqual(job["phases"], ["sintetizando dados a partir da captura"])

        # job de outra captura não vaza
        other = _RouteFakeHandler(server)
        handle_capture_get_route(
            other,
            _Parsed(f"/api/captures/999/synthetic-replay-jobs/{job_id}"),
            read_gateway_monitor_fn=None,
        )
        self.assertEqual(other.status_code, 404)

        # id inexistente → 404
        missing = _RouteFakeHandler(server)
        handle_capture_get_route(
            missing,
            _Parsed("/api/captures/81/synthetic-replay-jobs/zzz"),
            read_gateway_monitor_fn=None,
        )
        self.assertEqual(missing.status_code, 404)


if __name__ == "__main__":
    unittest.main()

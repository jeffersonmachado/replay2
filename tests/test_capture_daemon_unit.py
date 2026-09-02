"""Testes unitários do capture-daemon e do AuditClient (sink remoto).

Cobre o contrato do serviço privilegiado de captura (AF_UNIX):
- protocolo resolve/append/ping;
- assinatura (hash-chain + HMAC) feita pelo daemon e válida pelo verifier;
- continuidade de seq_global após restart do daemon (self-healing);
- appends concorrentes em capturas distintas;
- comportamento de fallback quando o socket não existe.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path

import pytest

from dakota_gateway.audit_client import (
    AuditClient,
    AuditClientError,
    daemon_request,
    daemon_resolve,
)
from dakota_gateway.capture_daemon import (
    CaptureDaemonServer,
    default_socket_path,
    event_from_dict,
    resolve_capture,
)
from dakota_gateway.schema import AuditEvent
from dakota_gateway.verifier import verify_log

HMAC_KEY = b"chave-de-teste-do-daemon"


def _make_db(tmp_path: Path, *, active: bool = True) -> tuple[str, str, str]:
    """Cria replay.db mínimo com uma capture_session; retorna (db, log_dir, uuid)."""
    db_path = str(tmp_path / "state" / "replay.db")
    log_dir = str(tmp_path / "state" / "captures" / "cap-1")
    session_uuid = uuid.uuid4().hex
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    # setup usa sqlite3 cru (sem PRAGMA foreign_keys) para não precisar de users
    con = sqlite3.connect(db_path)
    try:
        con.execute(
            """
            CREATE TABLE capture_sessions (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              session_uuid TEXT NOT NULL UNIQUE,
              status TEXT NOT NULL DEFAULT 'active',
              created_by INTEGER NOT NULL,
              created_by_username TEXT NOT NULL,
              started_at_ms INTEGER NOT NULL,
              ended_at_ms INTEGER,
              log_dir TEXT NOT NULL
            )
            """
        )
        con.execute(
            "INSERT INTO capture_sessions (session_uuid, status, created_by, created_by_username, started_at_ms, log_dir)"
            " VALUES (?, ?, 1, 'tester', 1, ?)",
            (session_uuid, "active" if active else "finished", log_dir),
        )
        con.commit()
    finally:
        con.close()
    return db_path, log_dir, session_uuid


def _event(ev_type: str = "bytes", **kw) -> AuditEvent:
    base = dict(
        v="",
        seq_global=0,
        ts_ms=int(time.time() * 1000),
        type=ev_type,
        actor="tester",
        session_id="sess-1",
        seq_session=1,
    )
    if ev_type == "bytes":
        base.update(dir="out", data_b64="aGVsbG8=", n=5)
    base.update(kw)
    return AuditEvent(**base)


class DaemonFixture:
    """Sobe um CaptureDaemonServer em thread e expõe socket/db."""

    def __init__(self, tmp_path: Path, db_path: str):
        self.socket_path = str(tmp_path / "state" / "daemon" / "capture.sock")
        self.server = CaptureDaemonServer(self.socket_path, db_path, HMAC_KEY)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.server.cleanup()
        self.thread.join(timeout=5)


@pytest.fixture
def daemon(tmp_path):
    db_path, log_dir, session_uuid = _make_db(tmp_path)
    fx = DaemonFixture(tmp_path, db_path)
    yield fx, db_path, log_dir, session_uuid
    fx.stop()


# -- protocolo ------------------------------------------------------------


def test_ping(daemon):
    fx, _db, _log_dir, _uuid = daemon
    resp = daemon_request(fx.socket_path, {"op": "ping"})
    assert resp == {"ok": True, "pong": True}


def test_resolve_captura_ativa(daemon):
    fx, db_path, log_dir, session_uuid = daemon
    resp = daemon_request(fx.socket_path, {"op": "resolve", "capture_id": 0})
    assert resp["ok"] is True
    assert resp["session_uuid"] == session_uuid
    assert resp["log_dir"] == log_dir
    assert Path(log_dir).is_dir()


def test_resolve_sem_captura_ativa(tmp_path):
    db_path, _log_dir, _uuid = _make_db(tmp_path, active=False)
    fx = DaemonFixture(tmp_path, db_path)
    try:
        resp = daemon_request(fx.socket_path, {"op": "resolve", "capture_id": 0})
        assert resp["ok"] is False
        assert resp["error"] == "no_active_capture"
        assert daemon_resolve(fx.socket_path) is None
    finally:
        fx.stop()


def test_resolve_direto_na_funcao(daemon):
    _fx, db_path, log_dir, session_uuid = daemon
    capture = resolve_capture(db_path, 0)
    assert capture is not None
    assert capture["session_uuid"] == session_uuid
    assert capture["log_dir"] == log_dir


def test_socket_inexistente_retorna_none(tmp_path):
    assert daemon_resolve(str(tmp_path / "nada" / "capture.sock")) is None
    assert daemon_request(str(tmp_path / "nada" / "capture.sock"), {"op": "ping"}) is None


def test_default_socket_path_ao_lado_do_db(tmp_path):
    db = str(tmp_path / "state" / "replay.db")
    assert default_socket_path(db) == str(tmp_path / "state" / "daemon" / "capture.sock")


# -- append / integridade ---------------------------------------------------


def test_append_assina_e_verifica(daemon):
    fx, _db, log_dir, session_uuid = daemon
    client = AuditClient(fx.socket_path, log_dir)
    try:
        ev1 = client.append(_event("session_start"))
        ev2 = client.append(_event("bytes"))
        ev3 = client.append(_event("session_end", type="session_end"))
    finally:
        client.close()

    # campos assinados aplicados de volta no evento local (por referência)
    assert ev1.seq_global == 1
    assert ev2.seq_global == 2
    assert ev3.seq_global == 3
    assert ev1.hash and ev1.hmac
    assert ev2.prev_hash == ev1.hash
    assert ev3.prev_hash == ev2.hash

    # trilha gravada pelo daemon passa na verificação oficial
    verify_log(log_dir, HMAC_KEY)


def test_integridade_do_cliente_e_ignorada(daemon):
    """Cliente não forja seq/hash/hmac: o daemon recalcula tudo."""
    fx, _db, log_dir, _uuid = daemon
    client = AuditClient(fx.socket_path, log_dir)
    try:
        ev = _event("session_start")
        ev.seq_global = 999
        ev.hash = "f" * 64
        ev.hmac = "f" * 64
        signed = client.append(ev)
    finally:
        client.close()
    assert signed.seq_global == 1
    assert signed.hash != "f" * 64
    verify_log(log_dir, HMAC_KEY)


def test_event_from_dict_filtra_campos():
    ev = event_from_dict(
        {
            "v": "v2",
            "seq_global": 42,
            "ts_ms": 1,
            "type": "bytes",
            "actor": "a",
            "session_id": "s",
            "seq_session": 1,
            "hash": "x",
            "hmac": "y",
            "campo_invasor": "drop",
        }
    )
    assert ev.seq_global == 0
    assert ev.hash == "" and ev.hmac == "" and ev.prev_hash == ""
    assert not hasattr(ev, "campo_invasor")


def test_restart_do_daemon_preserva_cadeia(tmp_path):
    """Daemon reiniciado no meio da captura: self-healing retoma seq/hash."""
    db_path, log_dir, _uuid = _make_db(tmp_path)

    fx1 = DaemonFixture(tmp_path, db_path)
    client = AuditClient(fx1.socket_path, log_dir)
    ev1 = client.append(_event("session_start"))
    client.close()
    fx1.stop()

    fx2 = DaemonFixture(tmp_path, db_path)
    try:
        client = AuditClient(fx2.socket_path, log_dir)
        ev2 = client.append(_event("bytes"))
        client.close()
    finally:
        fx2.stop()

    assert ev2.seq_global == ev1.seq_global + 1
    assert ev2.prev_hash == ev1.hash
    verify_log(log_dir, HMAC_KEY)


def test_appends_concorrentes_em_capturas_distintas(daemon, tmp_path):
    fx, _db, log_dir_a, _uuid = daemon
    log_dir_b = str(tmp_path / "state" / "captures" / "cap-2")
    erros: list[Exception] = []

    def worker(log_dir: str, n: int) -> None:
        try:
            client = AuditClient(fx.socket_path, log_dir)
            try:
                for i in range(n):
                    client.append(_event("bytes", seq_session=i + 1))
            finally:
                client.close()
        except Exception as exc:  # pragma: no cover - só em falha
            erros.append(exc)

    threads = [
        threading.Thread(target=worker, args=(log_dir_a, 20)),
        threading.Thread(target=worker, args=(log_dir_a, 20)),
        threading.Thread(target=worker, args=(log_dir_b, 20)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert not erros
    verify_log(log_dir_a, HMAC_KEY)
    verify_log(log_dir_b, HMAC_KEY)

    # 40 eventos na captura A, 20 na B — cadeias independentes
    def _count(log_dir: str) -> int:
        total = 0
        for f in Path(log_dir).glob("audit-*.jsonl"):
            total += sum(1 for ln in f.read_text().splitlines() if ln.strip())
        return total

    assert _count(log_dir_a) == 40
    assert _count(log_dir_b) == 20


def test_append_recusado_sem_log_dir(daemon):
    fx, _db, _log_dir, _uuid = daemon
    resp = daemon_request(fx.socket_path, {"op": "append", "log_dir": "", "event": {}})
    assert resp["ok"] is False
    assert resp["error"] == "invalid_append"


def test_operacao_desconhecida(daemon):
    fx, _db, _log_dir, _uuid = daemon
    resp = daemon_request(fx.socket_path, {"op": "drop-table"})
    assert resp["ok"] is False
    assert resp["error"] == "unknown_op"


def test_client_erro_quando_daemon_cai(tmp_path):
    db_path, log_dir, _uuid = _make_db(tmp_path)
    fx = DaemonFixture(tmp_path, db_path)
    client = AuditClient(fx.socket_path, log_dir)
    fx.stop()
    with pytest.raises((AuditClientError, OSError)):
        client.append(_event("bytes"))


# -- append_many (FASE 7: lotes + fila single-writer por captura) --------------


def _event_dict(ev_type: str = "bytes", **kw) -> dict:
    from dataclasses import asdict

    return asdict(_event(ev_type, **kw))


def test_append_many_assina_e_verifica(daemon):
    fx, _db, log_dir, _uuid = daemon
    client = AuditClient(fx.socket_path, log_dir)
    try:
        batch = [_event("bytes", seq_session=i + 1) for i in range(5)]
        signed = client.append_many(batch)
    finally:
        client.close()

    assert signed == batch  # confirmação individual, mesmos objetos
    for i, ev in enumerate(signed, start=1):
        assert ev.seq_global == i
        assert ev.hash and ev.hmac
        if i > 1:
            assert ev.prev_hash == signed[i - 2].hash
    verify_log(log_dir, HMAC_KEY)


def test_append_many_preserva_ordem_do_lote(daemon):
    """Eventos de um mesmo lote ficam contíguos e na ordem enviada."""
    fx, _db, log_dir, _uuid = daemon
    resp = daemon_request(
        fx.socket_path,
        {
            "op": "append_many",
            "log_dir": log_dir,
            "events": [_event_dict(seq_session=i + 1, session_id="s-ord") for i in range(7)],
        },
    )
    assert resp["ok"] is True
    assert [e["seq_global"] for e in resp["events"]] == list(range(1, 8))
    assert [e["seq_session"] for e in resp["events"]] == list(range(1, 8))
    verify_log(log_dir, HMAC_KEY)


def test_append_many_vazio_rejeitado(daemon):
    fx, _db, log_dir, _uuid = daemon
    resp = daemon_request(fx.socket_path, {"op": "append_many", "log_dir": log_dir, "events": []})
    assert resp["ok"] is False
    assert resp["error"] == "invalid_append"


def test_append_many_evento_malformado_nao_grava_nada(daemon):
    """Tudo-ou-nada por requisição: um evento malformado no meio do lote
    rejeita o lote inteiro e não consome seq_global."""
    fx, _db, log_dir, _uuid = daemon
    resp = daemon_request(
        fx.socket_path,
        {
            "op": "append_many",
            "log_dir": log_dir,
            "events": [_event_dict(), "nao-e-dict", _event_dict()],
        },
    )
    assert resp["ok"] is False

    # nada gravado: o próximo append válido começa do seq 1, sem buraco
    resp2 = daemon_request(
        fx.socket_path, {"op": "append", "log_dir": log_dir, "event": _event_dict()}
    )
    assert resp2["ok"] is True
    assert resp2["event"]["seq_global"] == 1
    verify_log(log_dir, HMAC_KEY)


def test_append_many_acima_do_limite_rejeitado(daemon):
    fx, _db, log_dir, _uuid = daemon
    resp = daemon_request(
        fx.socket_path,
        {"op": "append_many", "log_dir": log_dir, "events": [_event_dict() for _ in range(600)]},
    )
    assert resp["ok"] is False
    assert resp["error"] == "too_many_events"


def test_append_many_concorrente_mesma_captura(daemon):
    """Vários clientes mandando lotes ao mesmo tempo: a fila single-writer
    por captura serializa sem buracos de seq nem quebra de cadeia."""
    fx, _db, log_dir, _uuid = daemon
    erros: list[Exception] = []

    def worker(sid: str, n_lotes: int) -> None:
        try:
            client = AuditClient(fx.socket_path, log_dir)
            try:
                for lote in range(n_lotes):
                    client.append_many(
                        [_event("bytes", session_id=sid, seq_session=lote * 5 + i + 1) for i in range(5)]
                    )
            finally:
                client.close()
        except Exception as exc:  # pragma: no cover - só em falha
            erros.append(exc)

    threads = [threading.Thread(target=worker, args=(f"sess-{t}", 10)) for t in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)

    assert not erros
    verify_log(log_dir, HMAC_KEY)
    total = sum(
        1 for f in Path(log_dir).glob("audit-*.jsonl") for ln in f.read_text().splitlines() if ln.strip()
    )
    assert total == 8 * 10 * 5


def test_client_append_many_erro_quando_daemon_cai(tmp_path):
    db_path, log_dir, _uuid = _make_db(tmp_path)
    fx = DaemonFixture(tmp_path, db_path)
    client = AuditClient(fx.socket_path, log_dir)
    fx.stop()
    with pytest.raises((AuditClientError, OSError)):
        client.append_many([_event("bytes")])

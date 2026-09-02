"""Testes da escrita em lotes do AuditWriter (FASE 7).

Cobre o contrato novo do writer auditável sem alterar o formato da trilha
(JSONL + audit.state + manifests continuam idênticos, verificáveis pelo
verifier oficial):

- ``append_many``: ordem global determinística, seq_global contíguo,
  seq_session preservado, hash-chain encadeada no lote, HMAC por evento;
- segurança entre threads do mesmo processo (lock interno) e entre
  processos (flock), sem buracos de seq;
- recuperação de estado pela cauda do log (state ausente/defasado);
- rotação no meio de um lote;
- lote parcialmente inválido: tudo-ou-nada, nada é gravado e o seq não
  é consumido;
- ciclo de vida do descritor (close idempotente, append após close falha);
- checkpoint do audit.state uma vez por lote;
- política de fsync configurável.
"""

from __future__ import annotations

import json
import multiprocessing
import os
import sys
import threading
import time
from pathlib import Path

import pytest

# allow running tests from repo root
GATEWAY_DIR = str(Path(__file__).resolve().parents[1])
if GATEWAY_DIR not in sys.path:
    sys.path.insert(0, GATEWAY_DIR)

from dakota_gateway.audit_writer import AuditWriter
from dakota_gateway.schema import AuditEvent
from dakota_gateway.verifier import verify_log

HMAC_KEY = b"segredo-do-teste-fase7"


def _ev(session_id: str = "s1", seq_session: int = 1, ev_type: str = "bytes", **kw) -> AuditEvent:
    base = dict(
        v="",
        seq_global=0,
        ts_ms=int(time.time() * 1000),
        type=ev_type,
        actor="tester",
        session_id=session_id,
        seq_session=seq_session,
    )
    if ev_type == "bytes":
        base.update(dir="out", data_b64="aGVsbG8=", n=5)
    base.update(kw)
    return AuditEvent(**base)


def _events(log_dir: str) -> list[dict]:
    out = []
    for f in sorted(Path(log_dir).glob("audit-*.jsonl")):
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def _state(log_dir: str) -> dict:
    d = {}
    for ln in (Path(log_dir) / "audit.state").read_text(encoding="utf-8").splitlines():
        if "=" in ln:
            k, v = ln.split("=", 1)
            d[k] = v
    return d


# -- append_many: contrato básico -------------------------------------------


def test_append_many_ordem_seq_e_cadeia(tmp_path):
    d = str(tmp_path)
    w = AuditWriter(d, HMAC_KEY)
    batch = [_ev("s1", i + 1) for i in range(10)]
    signed = w.append_many(batch)
    w.close()

    assert signed == batch  # confirmação individual: mesmos objetos, na ordem
    for i, ev in enumerate(signed, start=1):
        assert ev.seq_global == i
        assert ev.hash and ev.hmac
        if i > 1:
            assert ev.prev_hash == signed[i - 2].hash
    verify_log(d, HMAC_KEY)


def test_append_many_preserva_seq_session(tmp_path):
    d = str(tmp_path)
    w = AuditWriter(d, HMAC_KEY)
    batch = [_ev("s1", 7), _ev("s2", 3), _ev("s1", 8)]
    w.append_many(batch)
    w.close()
    events = _events(d)
    assert [(e["session_id"], e["seq_session"]) for e in events] == [
        ("s1", 7),
        ("s2", 3),
        ("s1", 8),
    ]
    verify_log(d, HMAC_KEY)


def test_append_equivale_a_append_many_unitario(tmp_path):
    d = str(tmp_path)
    w = AuditWriter(d, HMAC_KEY)
    ev = w.append(_ev("s1", 1, "session_start"))
    assert ev.seq_global == 1 and ev.hash and ev.hmac
    w.close()
    verify_log(d, HMAC_KEY)


def test_append_many_lote_vazio_rejeitado(tmp_path):
    w = AuditWriter(str(tmp_path), HMAC_KEY)
    with pytest.raises(ValueError):
        w.append_many([])
    w.close()


# -- falha parcial: tudo-ou-nada por lote ------------------------------------


def test_lote_com_evento_malformado_nao_grava_nada(tmp_path):
    d = str(tmp_path)
    w = AuditWriter(d, HMAC_KEY)
    w.append(_ev("s1", 1, "session_start"))

    antes = _events(d)
    st_antes = _state(d)
    batch = [_ev("s1", 2), "nao-e-evento", _ev("s1", 3)]
    with pytest.raises((TypeError, ValueError)):
        w.append_many(batch)

    # nada gravado, seq_global não consumido, state intacto
    assert _events(d) == antes
    assert _state(d) == st_antes
    # writer continua saudável: o próximo evento usa o seq seguinte sem buraco
    ev = w.append(_ev("s1", 2))
    assert ev.seq_global == 2
    w.close()
    verify_log(d, HMAC_KEY)


def test_lote_com_campo_nao_serializavel_nao_grava_nada(tmp_path):
    d = str(tmp_path)
    w = AuditWriter(d, HMAC_KEY)
    bom = _ev("s1", 1)
    ruim = _ev("s1", 2)
    ruim.screen_sample = b"bytes nao serializam em JSON"  # type: ignore[assignment]
    with pytest.raises((TypeError, ValueError)):
        w.append_many([bom, ruim])
    assert _events(d) == []
    ev = w.append(_ev("s1", 1))
    assert ev.seq_global == 1
    w.close()
    verify_log(d, HMAC_KEY)


# -- concorrência: threads no mesmo processo ---------------------------------


def test_threads_mesmo_writer_sem_buracos(tmp_path):
    """Lock interno: threads do mesmo processo não podem corromper a cadeia.

    Antes da FASE 7 o writer dependia só do flock, que não serializa threads
    do mesmo processo (mesmo open file description) — corridas no
    audit.state/.tmp quebravam o append (FileNotFoundError) ou duplicavam
    seq_global.
    """
    d = str(tmp_path)
    w = AuditWriter(d, HMAC_KEY)
    erros: list[BaseException] = []

    def worker(sid: str, n: int) -> None:
        try:
            for i in range(1, n + 1):
                w.append(_ev(sid, i))
        except BaseException as exc:  # noqa: BLE001 - reportar qualquer falha
            erros.append(exc)

    threads = [threading.Thread(target=worker, args=(f"s{t}", 50)) for t in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    w.close()

    assert not erros
    assert len(_events(d)) == 8 * 50
    verify_log(d, HMAC_KEY)


def test_threads_mesmo_writer_append_many(tmp_path):
    d = str(tmp_path)
    w = AuditWriter(d, HMAC_KEY)
    erros: list[BaseException] = []

    def worker(sid: str, n_lotes: int) -> None:
        try:
            for lote in range(n_lotes):
                w.append_many([_ev(sid, lote * 5 + i + 1) for i in range(5)])
        except BaseException as exc:  # noqa: BLE001
            erros.append(exc)

    threads = [threading.Thread(target=worker, args=(f"s{t}", 10)) for t in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    w.close()

    assert not erros
    assert len(_events(d)) == 8 * 50
    verify_log(d, HMAC_KEY)


def test_dois_writers_alternando_mesmo_diretorio(tmp_path):
    """Duas instâncias vivas no mesmo processo (ex.: daemon + fallback local):
    cada uma deve perceber a escrita da outra e continuar a cadeia."""
    d = str(tmp_path)
    w1 = AuditWriter(d, HMAC_KEY)
    w2 = AuditWriter(d, HMAC_KEY)
    for i in range(20):
        (w1 if i % 2 == 0 else w2).append(_ev("s1", i + 1))
    w1.close()
    w2.close()
    assert len(_events(d)) == 20
    verify_log(d, HMAC_KEY)


# -- concorrência: processos distintos ---------------------------------------

_RESULT_KEY = "erro"


def _proc_worker(log_dir: str, proc_idx: int, n: int, queue) -> None:
    try:
        w = AuditWriter(log_dir, HMAC_KEY)
        sid = f"proc-{proc_idx}"
        for i in range(1, n + 1):
            w.append(_ev(sid, i))
        w.close()
    except BaseException as exc:  # noqa: BLE001 - propagar para o pai
        queue.put({_RESULT_KEY: f"proc {proc_idx}: {exc!r}"})


def test_processos_gravando_na_mesma_captura(tmp_path):
    d = str(tmp_path)
    ctx = multiprocessing.get_context("fork")
    queue = ctx.Queue()
    n_proc, n_ev = 4, 25
    procs = [
        ctx.Process(target=_proc_worker, args=(d, p, n_ev, queue))
        for p in range(n_proc)
    ]
    for p in procs:
        p.start()
    for p in procs:
        p.join(timeout=120)

    erros = []
    while not queue.empty():
        erros.append(queue.get())
    assert not erros, erros
    assert all(p.exitcode == 0 for p in procs)

    events = _events(d)
    assert len(events) == n_proc * n_ev
    # ordem global contígua, sem buracos nem duplicados
    assert [e["seq_global"] for e in events] == list(range(1, n_proc * n_ev + 1))
    # seq_session por sessão (processo) contíguo
    por_sessao: dict[str, list[int]] = {}
    for e in events:
        por_sessao.setdefault(e["session_id"], []).append(e["seq_session"])
    for seqs in por_sessao.values():
        assert seqs == list(range(1, n_ev + 1))
    verify_log(d, HMAC_KEY)


# -- reinicialização e recuperação de estado ----------------------------------


def test_reabertura_recupera_seq_e_prev_hash(tmp_path):
    d = str(tmp_path)
    w1 = AuditWriter(d, HMAC_KEY)
    ev1 = w1.append(_ev("s1", 1))
    w1.close()

    w2 = AuditWriter(d, HMAC_KEY)
    ev2 = w2.append(_ev("s1", 2))
    w2.close()

    assert ev2.seq_global == ev1.seq_global + 1
    assert ev2.prev_hash == ev1.hash
    verify_log(d, HMAC_KEY)


def test_state_ausente_recupera_pela_cauda_do_log(tmp_path):
    """Queda que apagou/corrompeu o audit.state: o log append-only é a fonte
    da verdade; o writer novo retoma seq/prev_hash pela cauda do último log."""
    d = str(tmp_path)
    w1 = AuditWriter(d, HMAC_KEY)
    for i in range(1, 6):
        w1.append(_ev("s1", i))
    w1.close()

    os.remove(Path(d) / "audit.state")

    w2 = AuditWriter(d, HMAC_KEY)
    ev = w2.append(_ev("s1", 6))
    w2.close()

    assert ev.seq_global == 6
    verify_log(d, HMAC_KEY)


def test_state_defasado_recupera_pela_cauda(tmp_path):
    """Queda entre append e checkpoint do state: o JSONL foi gravado mas o
    audit.state ficou para trás — a retomada não pode duplicar nem corromper."""
    d = str(tmp_path)
    w1 = AuditWriter(d, HMAC_KEY)
    for i in range(1, 6):
        w1.append(_ev("s1", i))
    w1.close()

    # simula state gravado após o 2º evento (crash antes do checkpoint do 3º)
    events = _events(d)
    state_path = Path(d) / "audit.state"
    st = _state(d)
    state_path.write_text(
        f"seq_global=2\nprev_hash={events[1]['hash']}\n"
        f"current_log={st['current_log']}\npart={st['part']}\n",
        encoding="utf-8",
    )
    # state mais antigo que o log (a cura só roda quando o log está à frente)
    log_mtime = os.stat(st["current_log"]).st_mtime_ns
    os.utime(state_path, ns=(log_mtime - 1_000_000, log_mtime - 1_000_000))

    w2 = AuditWriter(d, HMAC_KEY)
    ev = w2.append(_ev("s1", 6))
    w2.close()

    assert ev.seq_global == 6
    assert ev.prev_hash == events[4]["hash"]
    verify_log(d, HMAC_KEY)


def test_recuperacao_usa_cauda_limitada(tmp_path, monkeypatch):
    """A recuperação de estado não pode varrer o log inteiro: lê só a cauda."""
    import dakota_gateway.audit_writer as aw

    d = str(tmp_path)
    w1 = AuditWriter(d, HMAC_KEY)
    for i in range(1, 4):
        w1.append(_ev("s1", i, data_b64="eA==" * 100, n=300))
    w1.close()
    os.remove(Path(d) / "audit.state")

    # espia _last_log_entry: a recuperação consulta a cauda (seek + read
    # limitado), não uma varredura completa
    orig_last = aw._last_log_entry
    chamadas = []

    def _spy(path):
        chamadas.append(path)
        return orig_last(path)

    monkeypatch.setattr(aw, "_last_log_entry", _spy)
    w2 = AuditWriter(d, HMAC_KEY)
    ev = w2.append(_ev("s1", 4))
    w2.close()

    assert ev.seq_global == 4
    assert chamadas, "recuperação deveria consultar a cauda do log"
    verify_log(d, HMAC_KEY)


# -- rotação ------------------------------------------------------------------


def test_rotacao_no_meio_do_lote(tmp_path):
    d = str(tmp_path)
    # linhas ~200 bytes: rotaciona a cada ~3 eventos
    w = AuditWriter(d, HMAC_KEY, rotate_bytes=700)
    batch = [_ev("s1", i + 1, data_b64="eA==" * 30, n=120) for i in range(10)]
    signed = w.append_many(batch)
    w.close()

    parts = sorted(Path(d).glob("audit-*.jsonl"))
    assert len(parts) >= 3, f"esperava rotação, achou {[p.name for p in parts]}"
    # parts fechados têm manifest válido (verifier confere)
    manifests = sorted(Path(d).glob("audit-*.jsonl.manifest.json"))
    assert len(manifests) == len(parts) - 1
    assert [e.seq_global for e in signed] == list(range(1, 11))
    verify_log(d, HMAC_KEY)


# -- checkpoint de estado por lote --------------------------------------------


def test_checkpoint_do_state_uma_vez_por_lote(tmp_path, monkeypatch):
    d = str(tmp_path)
    w = AuditWriter(d, HMAC_KEY)
    salvamentos = 0
    orig = w._save_state_locked

    def _conta(st):
        nonlocal salvamentos
        salvamentos += 1
        return orig(st)

    monkeypatch.setattr(w, "_save_state_locked", _conta)
    w.append_many([_ev("s1", i + 1) for i in range(20)])
    assert salvamentos == 1
    w.append(_ev("s1", 21))
    assert salvamentos == 2
    w.close()
    verify_log(d, HMAC_KEY)


# -- ciclo de vida do descritor / fsync ----------------------------------------


def test_close_idempotente_e_append_apos_close_falha(tmp_path):
    w = AuditWriter(str(tmp_path), HMAC_KEY)
    w.append(_ev("s1", 1))
    w.close()
    w.close()
    with pytest.raises(RuntimeError):
        w.append(_ev("s1", 2))
    with pytest.raises(RuntimeError):
        w.append_many([_ev("s1", 2)])


def test_fsync_configuravel(tmp_path):
    d = str(tmp_path)
    w = AuditWriter(d, HMAC_KEY, fsync=True)
    assert w.fsync is True
    w.append_many([_ev("s1", i + 1) for i in range(5)])
    w.close()
    verify_log(d, HMAC_KEY)

    w2 = AuditWriter(d, HMAC_KEY, fsync=False)
    assert w2.fsync is False
    w2.close()


def test_fsync_por_env(tmp_path, monkeypatch):
    monkeypatch.setenv("DAKOTA_AUDIT_FSYNC", "1")
    w = AuditWriter(str(tmp_path), HMAC_KEY)
    assert w.fsync is True
    w.close()

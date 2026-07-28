import os
import stat
import sys
import tempfile
import time
from pathlib import Path

# allow running tests from repo root
GATEWAY_DIR = str(Path(__file__).resolve().parents[1])
if GATEWAY_DIR not in sys.path:
    sys.path.insert(0, GATEWAY_DIR)

from dakota_gateway.audit_writer import AuditWriter
from dakota_gateway.schema import AuditEvent
from dakota_gateway.verifier import verify_log, VerificationError


def test_writer_and_verify_ok():
    with tempfile.TemporaryDirectory() as d:
        w = AuditWriter(d, b"secret", rotate_bytes=0)
        sid = "s1"
        actor = "u"
        w.append(AuditEvent(v="v1", seq_global=0, ts_ms=int(time.time() * 1000), type="session_start", actor=actor, session_id=sid, seq_session=1))
        w.append(AuditEvent(v="v1", seq_global=0, ts_ms=int(time.time() * 1000), type="bytes", actor=actor, session_id=sid, seq_session=2, dir="in", data_b64="AA==", n=1))
        w.append(AuditEvent(v="v1", seq_global=0, ts_ms=int(time.time() * 1000), type="session_end", actor=actor, session_id=sid, seq_session=3))
        w.close()
        verify_log(d, b"secret")


def test_verify_detects_tamper():
    with tempfile.TemporaryDirectory() as d:
        w = AuditWriter(d, b"secret", rotate_bytes=0)
        sid = "s1"
        actor = "u"
        w.append(AuditEvent(v="v1", seq_global=0, ts_ms=1, type="session_start", actor=actor, session_id=sid, seq_session=1))
        w.close()
        # tamper with log file
        files = [p for p in os.listdir(d) if p.endswith(".jsonl")]
        assert files
        p = os.path.join(d, files[0])
        txt = open(p, "r", encoding="utf-8").read()
        open(p, "w", encoding="utf-8").write(txt.replace("session_start", "session_starT"))
        try:
            verify_log(d, b"secret")
        except VerificationError:
            return
        raise AssertionError("expected VerificationError")


def test_writer_shared_modes():
    """Arquivos da trilha compartilhada devem ser gravaveis pelo grupo.

    Cenario do incidente: uma sessao privilegiada (root) criava
    audit.lock/audit-*.jsonl como 0644 root:root e as demais sessoes da
    captura (results/ferblo) morriam com PermissionError ao abrir o lock,
    abortando o login (sessao de 0 minutos). O writer deve criar os arquivos
    como 0660 e o log_dir com setgid, independente do umask do processo.
    """
    old_umask = os.umask(0o022)
    try:
        with tempfile.TemporaryDirectory() as d:
            w = AuditWriter(d, b"secret", rotate_bytes=0)
            w.append(AuditEvent(v="v1", seq_global=0, ts_ms=1, type="session_start", actor="u", session_id="s1", seq_session=1))
            w.close()

            dir_mode = stat.S_IMODE(os.stat(d).st_mode)
            assert dir_mode == 0o2770, f"log_dir sem setgid/grupo: {oct(dir_mode)}"

            lock_mode = stat.S_IMODE(os.stat(os.path.join(d, "audit.lock")).st_mode)
            assert lock_mode == 0o660, f"audit.lock nao compartilhado: {oct(lock_mode)}"

            files = [p for p in os.listdir(d) if p.endswith(".jsonl")]
            assert files
            log_mode = stat.S_IMODE(os.stat(os.path.join(d, files[0])).st_mode)
            assert log_mode == 0o660, f"audit log nao compartilhado: {oct(log_mode)}"
    finally:
        os.umask(old_umask)


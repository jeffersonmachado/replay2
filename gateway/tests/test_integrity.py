import os
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


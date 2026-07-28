"""Testes do Port22CaptureSampler: markers fora da trilha verificável.

Os eventos do sampler são v1 não assinados; se ficarem no diretório raiz do
log_dir quebram o `verify` (diretório misto). Devem ir para
`<log_dir>/supervision/`, fora do glob não-recursivo do verifier/replay.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from dakota_gateway.audit_writer import AuditWriter
from dakota_gateway.schema import AuditEvent
from dakota_gateway.verifier import verify_log

from control.runtime_supervision import Port22CaptureSampler

HMAC_KEY = b"chave-teste-sampler"


def _run_cmd_ok(cmd):
    return 0, ""


def _capture(tmp_path: Path) -> dict:
    return {
        "id": 1,
        "session_uuid": "uuid-teste",
        "log_dir": str(tmp_path / "captures" / "cap-1"),
    }


def test_sampler_escreve_em_subdir_supervision(tmp_path):
    sampler = Port22CaptureSampler(run_cmd=_run_cmd_ok)
    capture = _capture(tmp_path)
    try:
        result = sampler.start(capture)
        assert result["started"] is True
        file_path = Path(result["file"])
        assert file_path.parent.name == "supervision"
        assert file_path.parent.parent == Path(capture["log_dir"])
        # nenhum audit-*.jsonl no diretório raiz do log_dir
        assert list(Path(capture["log_dir"]).glob("audit-*.jsonl")) == []
    finally:
        sampler.stop()

    # eventos gravados (session_start + session_end) no arquivo do sampler
    lines = [json.loads(ln) for ln in file_path.read_text().splitlines() if ln.strip()]
    assert [e["type"] for e in lines] == ["session_start", "session_end"]
    assert all(e["actor"] == "gateway" for e in lines)
    # permissões fechadas (0660), não world-writable
    assert (file_path.stat().st_mode & 0o777) == 0o660


def test_log_dir_com_sampler_passa_no_verify(tmp_path):
    """log_dir com trilha assinada + markers do sampler deve verificar OK."""
    capture = _capture(tmp_path)
    log_dir = capture["log_dir"]

    # trilha assinada pelo caminho oficial (daemon/AuditWriter)
    writer = AuditWriter(log_dir, HMAC_KEY)
    try:
        writer.append(AuditEvent(
            v="", seq_global=0, ts_ms=int(time.time() * 1000),
            type="session_start", actor="tester", session_id="uuid-teste",
            seq_session=1,
        ))
        writer.append(AuditEvent(
            v="", seq_global=0, ts_ms=int(time.time() * 1000),
            type="session_end", actor="tester", session_id="uuid-teste",
            seq_session=2,
        ))
    finally:
        writer.close()

    sampler = Port22CaptureSampler(run_cmd=_run_cmd_ok)
    try:
        sampler.start(capture)
    finally:
        sampler.stop()

    # verify usa glob não-recursivo: markers em supervision/ não interferem
    verify_log(log_dir, HMAC_KEY)

    # e a observabilidade (rglob) continua enxergando os markers
    via_rglob = sorted(Path(log_dir).rglob("audit-*.jsonl"))
    assert len(via_rglob) == 2
    assert any(p.parent.name == "supervision" for p in via_rglob)

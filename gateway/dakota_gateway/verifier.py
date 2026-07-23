from __future__ import annotations

import hmac
import json
from pathlib import Path

from .canonical import payload_for_event
from .crypto import sha256_hex, hmac_sha256_hex
from .schema import AuditEvent


class VerificationError(Exception):
    pass


def _iter_jsonl_files(log_dir: str) -> list[Path]:
    p = Path(log_dir)
    files = sorted(p.glob("audit-*.jsonl"))
    return files


def _as_int(value, *, field: str, where: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as e:
        raise VerificationError(f"{where}: {field} inválido: {value!r}") from e


def verify_log(log_dir: str, hmac_key: bytes) -> None:
    """
    Verifies:
    - seq_global monotonic without gaps (across all files)
    - prev_hash chain matches
    - hash and hmac recompute matches (payload v1 legado ou v2 conforme ev.v)
    - seq_session monotonic per session without gaps (best effort)
    - manifests (*.manifest.json), quando presentes
    """
    prev_hash = ""
    expected_seq_global = 1
    per_session_next = {}

    for f in _iter_jsonl_files(log_dir):
        with open(f, "r", encoding="utf-8", errors="replace") as fh:
            for ln_no, line in enumerate(fh, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except Exception as e:
                    raise VerificationError(f"{f}:{ln_no}: invalid JSON: {e}") from e
                if not isinstance(d, dict):
                    raise VerificationError(f"{f}:{ln_no}: JSON not object")

                ev = AuditEvent(**{k: d.get(k) for k in AuditEvent.__dataclass_fields__.keys() if k in d})
                where = f"{f}:{ln_no}"
                # required fields
                if ev.v not in ("v1", "v2"):
                    raise VerificationError(f"{where}: unexpected v={ev.v}")
                seq_global = _as_int(ev.seq_global, field="seq_global", where=where)
                if seq_global != expected_seq_global:
                    raise VerificationError(f"{where}: seq_global gap: got {ev.seq_global} expected {expected_seq_global}")
                expected_seq_global += 1

                if ev.prev_hash != prev_hash:
                    raise VerificationError(f"{where}: prev_hash mismatch: got {ev.prev_hash} expected {prev_hash}")

                payload = payload_for_event(ev).encode("utf-8")
                want_hash = sha256_hex(payload)
                want_hmac = hmac_sha256_hex(hmac_key, payload)
                if not hmac.compare_digest(ev.hash or "", want_hash):
                    raise VerificationError(f"{where}: hash mismatch")
                if not hmac.compare_digest(ev.hmac or "", want_hmac):
                    raise VerificationError(f"{where}: hmac mismatch")

                prev_hash = ev.hash

                # per-session sequence (best effort: divergência realinha a
                # expectativa sem abortar a verificação da cadeia global)
                sid = ev.session_id
                if sid:
                    seq_session = _as_int(ev.seq_session, field="seq_session", where=where)
                    nxt = per_session_next.get(sid, 1)
                    per_session_next[sid] = (seq_session if seq_session != nxt else nxt) + 1

    verify_manifests(log_dir)


def verify_manifests(log_dir: str) -> None:
    """
    Verifica os manifests gerados por audit_writer.write_manifest, quando
    presentes: file_sha256, bytes, seq_start/seq_end e first/last hash do
    arquivo JSONL correspondente.
    """
    p = Path(log_dir)
    for m in sorted(p.glob("audit-*.jsonl.manifest.json")):
        try:
            data = json.loads(m.read_text(encoding="utf-8"))
        except Exception as e:
            raise VerificationError(f"{m}: manifest inválido: {e}") from e
        if not isinstance(data, dict):
            raise VerificationError(f"{m}: manifest não é objeto JSON")

        jsonl = m.parent / m.name[: -len(".manifest.json")]
        if not jsonl.exists():
            raise VerificationError(f"{m}: arquivo de log ausente para o manifest")

        raw = jsonl.read_bytes()
        if not hmac.compare_digest(str(data.get("file_sha256") or ""), sha256_hex(raw)):
            raise VerificationError(f"{m}: file_sha256 diverge do conteúdo do log")
        if _as_int(data.get("bytes"), field="bytes", where=str(m)) != len(raw):
            raise VerificationError(f"{m}: bytes diverge do tamanho do log")

        seq_start = 0
        seq_end = 0
        first_hash = ""
        last_hash = ""
        with open(jsonl, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except Exception:
                    continue
                if not isinstance(ev, dict):
                    continue
                sg = _as_int(ev.get("seq_global") or 0, field="seq_global", where=str(m))
                h = ev.get("hash") or ""
                if seq_start == 0 and sg:
                    seq_start = sg
                    first_hash = h
                if sg:
                    seq_end = sg
                    last_hash = h

        if _as_int(data.get("seq_start") or 0, field="seq_start", where=str(m)) != seq_start:
            raise VerificationError(f"{m}: seq_start diverge do log")
        if _as_int(data.get("seq_end") or 0, field="seq_end", where=str(m)) != seq_end:
            raise VerificationError(f"{m}: seq_end diverge do log")
        if not hmac.compare_digest(str(data.get("first_hash") or ""), first_hash):
            raise VerificationError(f"{m}: first_hash diverge do log")
        if not hmac.compare_digest(str(data.get("last_hash") or ""), last_hash):
            raise VerificationError(f"{m}: last_hash diverge do log")

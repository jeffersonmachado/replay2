from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path

from .canonical import payload_for_event
from .crypto import sha256_hex, hmac_sha256_hex
from .schema import AuditEvent

# Chunk de streaming da passagem única: hash-chain/HMAC/sequências e as
# estatísticas do manifest (file_sha256, bytes, seq_start/seq_end,
# first/last hash) são acumulados na MESMA leitura — nunca read_bytes() em
# logs grandes (capturas chegam a centenas de MB).
_CHUNK_BYTES = 1024 * 1024


class VerificationError(Exception):
    pass


class _ArquivoStats:
    """Estatísticas de um audit-*.jsonl coletadas durante a passagem única.

    Espelha exatamente o que audit_writer.write_manifest grava no manifest:
    file_sha256/bytes do bruto e seq_start/seq_end/first_hash/last_hash dos
    eventos com seq_global não nulo.
    """

    __slots__ = ("sha", "nbytes", "seq_start", "seq_end", "first_hash", "last_hash")

    def __init__(self) -> None:
        self.sha = hashlib.sha256()
        self.nbytes = 0
        self.seq_start = 0
        self.seq_end = 0
        self.first_hash = ""
        self.last_hash = ""

    def registra_evento(self, seq_global: int, ev_hash: str) -> None:
        if seq_global:
            if self.seq_start == 0:
                self.seq_start = seq_global
                self.first_hash = ev_hash
            self.seq_end = seq_global
            self.last_hash = ev_hash


def _varrer_arquivo(path: Path, on_linha) -> _ArquivoStats:
    """Uma passagem de streaming: acumula sha256/bytes do bruto e chama
    on_linha(texto, stats) para cada linha decodificada (utf-8/replace)."""
    stats = _ArquivoStats()
    with open(path, "rb") as fh:
        buf = b""
        while True:
            chunk = fh.read(_CHUNK_BYTES)
            if not chunk:
                break
            stats.sha.update(chunk)
            stats.nbytes += len(chunk)
            buf += chunk
            partes = buf.split(b"\n")
            buf = partes.pop()
            for raw in partes:
                on_linha(raw.decode("utf-8", errors="replace").strip(), stats)
        if buf.strip():
            on_linha(buf.decode("utf-8", errors="replace").strip(), stats)
    return stats


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
    Verifies (em UMA passagem de streaming por arquivo):
    - seq_global monotonic without gaps (across all files)
    - prev_hash chain matches
    - hash and hmac recompute matches (payload v1 legado ou v2 conforme ev.v)
    - seq_session monotonic per session without gaps (best effort)
    - manifests (*.manifest.json), quando presentes — conferidos com as
      estatísticas acumuladas na mesma passagem (sem reler o JSONL)
    """
    prev_hash = ""
    expected_seq_global = 1
    per_session_next = {}
    stats_por_arquivo: dict[str, _ArquivoStats] = {}

    for f in _iter_jsonl_files(log_dir):
        estado = {"ln_no": 0}

        def _checa_linha(line: str, stats: _ArquivoStats, *, _f=f, _estado=estado) -> None:
            nonlocal prev_hash, expected_seq_global
            _estado["ln_no"] += 1
            ln_no = _estado["ln_no"]
            if not line:
                return
            try:
                d = json.loads(line)
            except Exception as e:
                raise VerificationError(f"{_f}:{ln_no}: invalid JSON: {e}") from e
            if not isinstance(d, dict):
                raise VerificationError(f"{_f}:{ln_no}: JSON not object")

            ev = AuditEvent(**{k: d.get(k) for k in AuditEvent.__dataclass_fields__.keys() if k in d})
            where = f"{_f}:{ln_no}"
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
            stats.registra_evento(seq_global, ev.hash or "")

            # per-session sequence (best effort: divergência realinha a
            # expectativa sem abortar a verificação da cadeia global)
            sid = ev.session_id
            if sid:
                seq_session = _as_int(ev.seq_session, field="seq_session", where=where)
                nxt = per_session_next.get(sid, 1)
                per_session_next[sid] = (seq_session if seq_session != nxt else nxt) + 1

        stats_por_arquivo[f.name] = _varrer_arquivo(f, _checa_linha)

    verify_manifests(log_dir, stats_por_arquivo=stats_por_arquivo)


def _stats_tolerantes(jsonl: Path) -> _ArquivoStats:
    """Estatísticas do arquivo com parse tolerante (linhas inválidas são
    ignoradas) — mesmo comportamento do scanner antigo do manifest, usado
    quando verify_manifests é chamado de forma independente."""

    def _conta(line: str, stats: _ArquivoStats) -> None:
        if not line:
            return
        try:
            ev = json.loads(line)
        except Exception:
            return
        if not isinstance(ev, dict):
            return
        try:
            sg = int(ev.get("seq_global") or 0)
        except (TypeError, ValueError):
            return
        stats.registra_evento(sg, str(ev.get("hash") or ""))

    return _varrer_arquivo(jsonl, _conta)


def verify_manifests(log_dir: str, *, stats_por_arquivo: dict | None = None) -> None:
    """
    Verifica os manifests gerados por audit_writer.write_manifest, quando
    presentes: file_sha256, bytes, seq_start/seq_end e first/last hash do
    arquivo JSONL correspondente.

    Com stats_por_arquivo (chamada via verify_log) nenhum JSONL é relido —
    as estatísticas vieram da mesma passagem que verificou a cadeia. Sem
    elas, cada arquivo é varrido em streaming (nunca read_bytes).
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

        if stats_por_arquivo is not None and jsonl.name in stats_por_arquivo:
            stats = stats_por_arquivo[jsonl.name]
        else:
            stats = _stats_tolerantes(jsonl)

        if not hmac.compare_digest(str(data.get("file_sha256") or ""), stats.sha.hexdigest()):
            raise VerificationError(f"{m}: file_sha256 diverge do conteúdo do log")
        if _as_int(data.get("bytes"), field="bytes", where=str(m)) != stats.nbytes:
            raise VerificationError(f"{m}: bytes diverge do tamanho do log")
        if _as_int(data.get("seq_start") or 0, field="seq_start", where=str(m)) != stats.seq_start:
            raise VerificationError(f"{m}: seq_start diverge do log")
        if _as_int(data.get("seq_end") or 0, field="seq_end", where=str(m)) != stats.seq_end:
            raise VerificationError(f"{m}: seq_end diverge do log")
        if not hmac.compare_digest(str(data.get("first_hash") or ""), stats.first_hash):
            raise VerificationError(f"{m}: first_hash diverge do log")
        if not hmac.compare_digest(str(data.get("last_hash") or ""), stats.last_hash):
            raise VerificationError(f"{m}: last_hash diverge do log")

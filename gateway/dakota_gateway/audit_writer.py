from __future__ import annotations

import base64
import json
import os
import threading
import time
from dataclasses import asdict
from pathlib import Path
from typing import Optional

import fcntl

from .canonical import canonical_string_v2
from .crypto import sha256_hex, hmac_sha256_hex
from .schema import AuditEvent

# Valores aceitos como "ligado" na env DAKOTA_AUDIT_FSYNC.
_FSYNC_ON = {"1", "true", "yes", "on"}


class AuditWriter:
    """Global append-only writer with:
    - global total order (seq_global) across all processes
    - hash-chain + HMAC
    - optional rotation + manifest

    Concorrência (FASE 7): o writer é seguro para threads do mesmo processo
    (``threading.Lock`` interno) e entre processos (flock em ``audit.lock``).
    A ordem global é determinística: cada lote adquire as duas travas, assina
    e grava atomicamente em relação aos demais escritores.

    Escrita em lotes (``append_many``):
    - tudo-ou-nada por lote para erros de validação/serialização: o lote
      inteiro é assinado em memória ANTES de qualquer escrita; se algum
      evento for inválido, nada é gravado e o ``seq_global`` não é consumido;
    - falha de I/O no meio da escrita propaga a exceção; o estado em memória
      é invalidado e a próxima operação retoma pela cauda do log (o
      ``audit.state`` só avança depois do flush completo do lote), sem
      duplicar nem corromper a cadeia;
    - o ``audit.state`` (checkpoint) é gravado UMA vez por lote;
    - cada evento recebe confirmação individual (o objeto é assinado por
      referência e devolvido na lista, como ``append`` sempre fez).

    Durabilidade: o descritor do JSONL fica aberto entre lotes; cada lote
    termina com ``flush()`` (buffer Python → SO). ``fsync()`` por lote é
    opcional (``fsync=True`` ou env ``DAKOTA_AUDIT_FSYNC=1``, default off).
    Janela de perda sem fsync: um crash de SO/energia pode perder os lotes
    ainda só no page cache; como o ``audit.state`` só avança após o flush, a
    retomada detecta a defasagem pela cauda do log. Com fsync a janela se
    reduz ao lote em voo no momento da queda.
    """

    def __init__(self, log_dir: str, hmac_key: bytes, rotate_bytes: int = 0, fsync: Optional[bool] = None):
        self.log_dir = Path(log_dir)
        self.hmac_key = hmac_key
        self.rotate_bytes = int(rotate_bytes)
        if fsync is None:
            fsync = os.environ.get("DAKOTA_AUDIT_FSYNC", "").strip().lower() in _FSYNC_ON
        self.fsync = bool(fsync)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        # setgid no dir: arquivos criados aqui herdam o grupo do diretorio.
        # Sem isso, uma sessao privilegiada (ex.: root) cria arquivos com o
        # grupo primario dela e impede o append das demais sessoes da captura.
        try:
            os.chmod(self.log_dir, 0o2770)
        except OSError:
            pass

        self.lock_path = self.log_dir / "audit.lock"
        self.state_path = self.log_dir / "audit.state"

        self._lock_fd = open(self.lock_path, "a+", encoding="utf-8")
        # lock/log sao compartilhados entre todos os usuarios da captura:
        # 0660 + setgid no dir garante append por qualquer membro do grupo.
        try:
            os.fchmod(self._lock_fd.fileno(), 0o660)
        except OSError:
            pass

        # --- estado em memória (FASE 7) ---
        # Trava intra-processo: o flock NÃO serializa threads que dividem o
        # mesmo open file description, então toda operação passa antes por
        # este mutex (ordem: mutex → flock, nunca o contrário).
        self._mutex = threading.Lock()
        self._closed = False
        # dict {seq_global, prev_hash, current_log, part} ou None até a 1ª
        # operação; recarregado quando outro processo grava. A detecção é por
        # CONTEÚDO do audit.state (não por mtime: a granularidade de
        # timestamp de tmpfs/ext4 pode repetir entre gravações rápidas).
        self._st: dict | None = None
        self._state_raw: str | None = None
        # descritor do JSONL corrente, mantido aberto entre lotes
        self._log_fh = None
        self._log_path: Path | None = None
        self._log_size = 0

    # -- ciclo de vida ------------------------------------------------------

    def close(self):
        """Fecha o descritor do log e o lock. Idempotente; após ``close``,
        ``append``/``append_many`` falham com ``RuntimeError``."""
        with self._mutex:
            if self._closed:
                return
            self._closed = True
            self._close_log_locked()
            try:
                self._lock_fd.close()
            except Exception:
                pass

    def _check_open(self) -> None:
        if self._closed:
            raise RuntimeError("AuditWriter fechado")

    # -- estado (memória + audit.state + cura pela cauda) --------------------

    def _load_state_locked(self) -> dict:
        if not self.state_path.exists():
            d = {"seq_global": 0, "prev_hash": "", "current_log": "", "part": 0}
            self._recover_state_from_logs_locked(d)
            return d
        d = {"seq_global": 0, "prev_hash": "", "current_log": "", "part": 0}
        for ln in self.state_path.read_text(encoding="utf-8", errors="replace").splitlines():
            ln = ln.strip()
            if not ln or "=" not in ln:
                continue
            k, v = ln.split("=", 1)
            if k == "seq_global":
                d[k] = int(v or "0")
            elif k == "part":
                d[k] = int(v or "0")
            else:
                d[k] = v
        self._heal_state_from_log_locked(d)
        return d

    def _read_state_raw_locked(self) -> str | None:
        try:
            return self.state_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None

    def _ensure_state_locked(self) -> dict:
        """Garante estado válido em memória (chamado já sob mutex + flock).

        Mantém seq_global/prev_hash em memória enquanto o audit.state não
        muda em disco (leitura de ~100 bytes por lote). Se outro processo
        gravou (conteúdo diverge), recarrega — o flock garante que ninguém
        está escrevendo neste instante.
        """
        raw = self._read_state_raw_locked()
        if self._st is not None and raw == self._state_raw:
            return self._st
        # estado ausente ou escrito por outro processo: (re)carrega e solta o
        # descritor do log, pois o arquivo corrente pode ter rotacionado.
        self._close_log_locked()
        self._st = self._load_state_locked()
        self._state_raw = raw
        return self._st

    def _invalidate_state_locked(self) -> None:
        """Descarta o estado em memória após falha de I/O na escrita.

        O JSONL pode ter recebido parte do lote enquanto o audit.state ficou
        para trás; a próxima operação recarrega e cura pela cauda do log.
        """
        self._close_log_locked()
        self._st = None
        self._state_raw = None

    def _recover_state_from_logs_locked(self, st: dict) -> None:
        """Retoma seq_global/prev_hash pela cauda do log mais recente.

        Usada quando o audit.state não existe (perdido/corrompido numa queda).
        O log append-only é a fonte da verdade. Se o arquivo mais recente já
        tem manifest (part fechada), continua a cadeia numa part nova para
        não invalidar o manifest existente.
        """
        logs = sorted(self.log_dir.glob("audit-*.jsonl"))
        if not logs:
            return
        last = logs[-1]
        entry = _last_log_entry(last)
        if not entry:
            return
        st["seq_global"] = int(entry.get("seq_global") or 0)
        st["prev_hash"] = str(entry.get("hash") or "")
        part = _part_from_name(last.name)
        if last.with_suffix(last.suffix + ".manifest.json").exists():
            part += 1
            prefix = last.name.split(".part")[0]
            st["current_log"] = str(self.log_dir / f"{prefix}.part{part:03d}.jsonl")
        else:
            st["current_log"] = str(last)
        st["part"] = part

    def _heal_state_from_log_locked(self, st: dict) -> None:
        """Deriva seq_global/prev_hash do log quando ele está à frente do state.

        A gravação do JSONL ocorre antes da persistência do audit.state; se o
        processo morrer entre as duas, o state fica defasado e o próximo append
        corromperia a cadeia. O log append-only é a fonte da verdade.
        """
        cur = st.get("current_log") or ""
        if not cur:
            return
        log_path = Path(cur)
        try:
            # Heurística barata: o state é salvo logo após o append, então só
            # há defasagem quando o log foi modificado depois do state.
            if log_path.stat().st_mtime_ns <= self.state_path.stat().st_mtime_ns:
                return
        except OSError:
            return
        last = _last_log_entry(log_path)
        if not last:
            return
        log_seq = int(last.get("seq_global") or 0)
        if log_seq >= int(st.get("seq_global") or 0):
            st["seq_global"] = log_seq
            st["prev_hash"] = str(last.get("hash") or "")

    def _save_state_locked(self, st: dict) -> None:
        tmp = self.state_path.with_suffix(".tmp")
        content = (
            f"seq_global={st.get('seq_global', 0)}\n"
            f"prev_hash={st.get('prev_hash', '')}\n"
            f"current_log={st.get('current_log', '')}\n"
            f"part={st.get('part', 0)}\n"
        )
        if self.fsync:
            with open(tmp, "w", encoding="utf-8") as fh:
                fh.write(content)
                fh.flush()
                os.fsync(fh.fileno())
        else:
            tmp.write_text(content, encoding="utf-8")
        os.replace(tmp, self.state_path)
        if self.fsync:
            # fsync do diretório: torna o rename durável junto com o conteúdo.
            try:
                dir_fd = os.open(self.log_dir, os.O_RDONLY)
                try:
                    os.fsync(dir_fd)
                finally:
                    os.close(dir_fd)
            except OSError:
                pass
        self._state_raw = content

    # -- arquivo de log corrente (descritor aberto) ---------------------------

    def _current_log_path_locked(self, st: dict) -> Path:
        cur = st.get("current_log") or ""
        if cur:
            return Path(cur)
        ts = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
        st["part"] = 1
        cur = str(self.log_dir / f"audit-{ts}.part{st['part']:03d}.jsonl")
        st["current_log"] = cur
        return Path(cur)

    def _open_log_locked(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        log_created = not path.exists()
        self._log_fh = open(path, "a", encoding="utf-8")
        if log_created:
            # arquivo novo da cadeia: modo compartilhado (ver __init__)
            try:
                os.fchmod(self._log_fh.fileno(), 0o660)
            except OSError:
                pass
        self._log_path = path
        try:
            self._log_size = path.stat().st_size
        except OSError:
            self._log_size = 0

    def _close_log_locked(self) -> None:
        fh = self._log_fh
        self._log_fh = None
        self._log_path = None
        self._log_size = 0
        if fh is not None:
            try:
                fh.flush()
                fh.close()
            except Exception:
                pass

    def _maybe_rotate_locked(self, st: dict) -> None:
        if self.rotate_bytes <= 0:
            return
        if self._log_fh is None or self._log_size < self.rotate_bytes:
            return

        path = self._log_path
        # fecha o descritor ANTES de gerar o manifest (o hash é do arquivo
        # completo em disco) e só então abre a part seguinte.
        self._close_log_locked()
        write_manifest(str(path))

        base = path.name
        prefix = base.split(".part")[0]
        st["part"] = int(st.get("part") or 0) + 1
        st["current_log"] = str(self.log_dir / f"{prefix}.part{st['part']:03d}.jsonl")

    # -- escrita ---------------------------------------------------------------

    def append(self, ev: AuditEvent) -> AuditEvent:
        """Assina e grava um evento (equivale a ``append_many([ev])[0]``)."""
        return self.append_many([ev])[0]

    def append_many(self, events: list[AuditEvent]) -> list[AuditEvent]:
        """Assina e grava um lote de eventos atomicamente.

        Contrato (ver docstring da classe): ordem global determinística,
        ``seq_global`` contíguo, ``seq_session`` preservado, hash-chain
        encadeada dentro do lote, HMAC por evento, checkpoint do state uma
        vez por lote, confirmação individual (objetos assinados por
        referência, devolvidos na ordem).
        """
        events = list(events)
        if not events:
            raise ValueError("append_many exige ao menos um evento")
        for ev in events:
            if not isinstance(ev, AuditEvent):
                raise TypeError(f"evento não é AuditEvent: {type(ev).__name__}")

        with self._mutex:
            self._check_open()
            fcntl.flock(self._lock_fd.fileno(), fcntl.LOCK_EX)
            try:
                st = self._ensure_state_locked()

                # 1) valida e assina o lote INTEIRO em memória, antes de
                #    escrever qualquer linha: erro aqui = nada gravado,
                #    seq/prev_hash restaurados (tudo-ou-nada).
                saved_seq = int(st.get("seq_global", 0))
                saved_prev = st.get("prev_hash", "") or ""
                try:
                    for ev in events:
                        ev.v = "v2"
                        if ev.timestamp_ms is None:
                            ev.timestamp_ms = int(ev.ts_ms or 0)
                        st["seq_global"] = int(st.get("seq_global", 0)) + 1
                        ev.seq_global = st["seq_global"]
                        ev.prev_hash = st.get("prev_hash", "") or ""
                        payload = canonical_string_v2(ev).encode("utf-8")
                        ev.hash = sha256_hex(payload)
                        ev.hmac = hmac_sha256_hex(self.hmac_key, payload)
                        st["prev_hash"] = ev.hash
                        # serialização também é validação: falha aqui aborta
                        # o lote antes de qualquer escrita
                        line = json.dumps(asdict(ev), ensure_ascii=False) + "\n"
                        ev._audit_line = line  # type: ignore[attr-defined]
                except Exception:
                    st["seq_global"] = saved_seq
                    st["prev_hash"] = saved_prev
                    for ev in events:
                        ev.__dict__.pop("_audit_line", None)
                    raise

                # 2) escreve o lote (rotacionando no meio se preciso), flush
                #    ao final; falha de I/O invalida o estado em memória —
                #    a retomada cura pela cauda do log.
                try:
                    for ev in events:
                        if self._log_fh is None:
                            self._open_log_locked(self._current_log_path_locked(st))
                        self._maybe_rotate_locked(st)
                        if self._log_fh is None:
                            self._open_log_locked(self._current_log_path_locked(st))
                        line = ev.__dict__.pop("_audit_line")
                        self._log_fh.write(line)
                        self._log_size += len(line.encode("utf-8"))
                    self._log_fh.flush()
                    if self.fsync:
                        os.fsync(self._log_fh.fileno())
                except Exception:
                    self._invalidate_state_locked()
                    raise

                # 3) checkpoint do state UMA vez por lote.
                self._save_state_locked(st)
                return events
            finally:
                fcntl.flock(self._lock_fd.fileno(), fcntl.LOCK_UN)


def _part_from_name(name: str) -> int:
    """Extrai o número da part de ``audit-<ts>.partNNN.jsonl`` (0 se ausente)."""
    try:
        return int(name.split(".part", 1)[1].split(".", 1)[0])
    except (IndexError, ValueError):
        return 0


def _last_log_entry(path: Path) -> dict:
    """Retorna o último evento JSON válido do log (best effort).

    Lê apenas a cauda do arquivo; se a última linha for maior que a janela,
    faz varredura completa como fallback.
    """
    try:
        size = path.stat().st_size
    except OSError:
        return {}
    if size <= 0:
        return {}

    def _scan_lines(lines) -> dict:
        for line in reversed(lines):
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except Exception:
                # cauda pode começar no meio de uma linha; ignorar truncadas
                continue
            if isinstance(ev, dict):
                return ev
        return {}

    try:
        with open(path, "rb") as fh:
            fh.seek(max(0, size - 1024 * 1024))
            tail = fh.read()
    except OSError:
        return {}
    found = _scan_lines(tail.decode("utf-8", errors="replace").splitlines())
    if found:
        return found
    try:
        return _scan_lines(path.read_text(encoding="utf-8", errors="replace").splitlines())
    except OSError:
        return {}


def write_manifest(jsonl_path: str) -> None:
    p = Path(jsonl_path)
    if not p.exists():
        return

    file_sha = sha256_hex(p.read_bytes())

    seq_start = 0
    seq_end = 0
    first_hash = ""
    last_hash = ""
    with open(p, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except Exception:
                continue
            if not isinstance(ev, dict):
                continue
            sg = int(ev.get("seq_global") or 0)
            h = ev.get("hash") or ""
            if seq_start == 0 and sg:
                seq_start = sg
                first_hash = h
            if sg:
                seq_end = sg
                last_hash = h

    manifest = {
        "path": p.name,
        "bytes": p.stat().st_size,
        "seq_start": seq_start,
        "seq_end": seq_end,
        "first_hash": first_hash,
        "last_hash": last_hash,
        "file_sha256": file_sha,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S.%fZ", time.gmtime()),
    }
    (p.with_suffix(p.suffix + ".manifest.json")).write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")

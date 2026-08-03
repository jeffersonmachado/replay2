"""Índice em disco de eventos de sessão para replay (dívida X6).

Mesmo com o cache de estado da TerminalEngine (replay_state_cache), cada
request de replay em sessão enorme relia e reparseava todos os
audit-*.jsonl (ex.: 314 MB / 116k linhas da captura 20 do MIG24) para
extrair os eventos da sessão, calcular os totais de playback e localizar a
janela — o custo dominante remanescente do endpoint.

Este módulo persiste, por (capture_sig, session_id), um mapa tipado dos
eventos relevantes (session_start, session_end, bytes, deterministic_input):

    types:  string com um código por evento ("s"/"e"/"b"/"d"), em ordem de
            stream (ordem de seq_global — capturas fora de ordem não são
            indexadas);
    seq:    seq_global de cada evento;
    file:   índice do arquivo (em "files") de cada evento;
    off:    offset em bytes da linha de cada evento no arquivo;
    bpos:   posição (no stream acima) do n-ésimo evento "bytes";
    bdir:   direção ("i"/"o"/"?") do n-ésimo evento "bytes";
    blen:   tamanho decodificado (base64) do n-ésimo evento "bytes".

Com ele, o replay materializa a janela por seek (sem varrer o arquivo
inteiro) e obtém os totais de playback sem decodificar base64 de novo.

Layout:
    <cache_dir>/<capture_sig>/<session_id>/index.json.gz

Onde capture_sig é a mesma assinatura do replay_state_cache (nome+size+
mtime dos audit-*.jsonl) — qualquer alteração no arquivo invalida o índice.

Tudo aqui é fail-safe: qualquer erro de leitura/validação vira miss e o
chamador faz o parse completo, como antes.
"""
from __future__ import annotations

import gzip
import json
import os
import re
from pathlib import Path

INDEX_VERSION = 1

_TYPE_CODES = {"session_start": "s", "session_end": "e", "bytes": "b", "deterministic_input": "d"}
CODE_TYPES = {v: k for k, v in _TYPE_CODES.items()}


def type_code(ev_type: str) -> str | None:
    """Código de stream do tipo de evento, ou None se não indexável."""
    return _TYPE_CODES.get(ev_type)


def _session_dir(cache_dir: str | Path, capture_sig: str, session_id: str) -> Path:
    safe_sid = re.sub(r"[^A-Za-z0-9_.-]", "_", str(session_id or ""))
    return Path(cache_dir) / str(capture_sig) / safe_sid


def index_path(cache_dir: str | Path, capture_sig: str, session_id: str) -> Path:
    return _session_dir(cache_dir, capture_sig, session_id) / "index.json.gz"


def store_index(cache_dir: str | Path, index: dict) -> bool:
    """Persiste o índice de forma atômica. Falha silenciosa (miss depois)."""
    try:
        target = index_path(cache_dir, index["capture_sig"], index["session_id"])
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".tmp")
        with gzip.open(tmp, "wt", encoding="utf-8") as f:
            json.dump(index, f, separators=(",", ":"))
        os.replace(tmp, target)
        return True
    except Exception:
        return False


def load_index(cache_dir: str | Path, capture_sig: str, session_id: str) -> dict | None:
    """Carrega e valida o índice; qualquer inconsistência vira miss (None)."""
    try:
        with gzip.open(index_path(cache_dir, capture_sig, session_id), "rt", encoding="utf-8") as f:
            index = json.load(f)
    except Exception:
        return None
    try:
        if not isinstance(index, dict):
            return None
        if int(index.get("v") or 0) != INDEX_VERSION:
            return None
        if str(index.get("capture_sig") or "") != str(capture_sig):
            return None
        if str(index.get("session_id") or "") != str(session_id):
            return None
        n = len(index["types"])
        if len(index["seq"]) != n or len(index["file"]) != n or len(index["off"]) != n:
            return None
        n_bytes = int(index["n_bytes"])
        if len(index["bpos"]) != n_bytes or len(index["bdir"]) != n_bytes or len(index["blen"]) != n_bytes:
            return None
        if len(index["files"]) != len(set(index["files"])) and not index["files"]:
            return None
        return index
    except (KeyError, TypeError, ValueError):
        return None


class IndexBuilder:
    """Acumula entradas durante o parse completo para construir o índice.

    Registra apenas eventos indexáveis (s/e/b/d) cuja seq_global seja
    monotônica não-decrescente; uma violação de ordem marca o índice como
    inválido e ele não é persistido (o replay segue pelo parse completo).
    """

    def __init__(self) -> None:
        self.types: list[str] = []
        self.seq: list[int] = []
        self.file: list[int] = []
        self.off: list[int] = []
        self.bpos: list[int] = []
        self.bdir: list[str] = []
        self.blen: list[int] = []
        self.total_in = 0
        self.total_out = 0
        self.monotonic = True
        self._last_seq = -1

    def add(self, code: str, seq_global: int, file_idx: int, offset: int, *,
            direction: str = "", decoded_len: int = 0) -> None:
        if seq_global < self._last_seq:
            self.monotonic = False
        self._last_seq = seq_global
        pos = len(self.types)
        self.types.append(code)
        self.seq.append(seq_global)
        self.file.append(file_idx)
        self.off.append(offset)
        if code == "b":
            self.bpos.append(pos)
            if direction in ("i", "in"):
                d = "i"
            elif direction in ("o", "out"):
                d = "o"
            else:
                d = "?"
            self.bdir.append(d)
            self.blen.append(int(decoded_len))
            if d == "i":
                self.total_in += int(decoded_len)
            elif d == "o":
                self.total_out += int(decoded_len)

    def build(self, *, capture_sig: str, session_id: str, files: list[str]) -> dict | None:
        """Fecha o índice, ou None se a ordem de seq_global foi violada."""
        if not self.monotonic:
            return None
        return {
            "v": INDEX_VERSION,
            "capture_sig": capture_sig,
            "session_id": session_id,
            "files": list(files),
            "types": "".join(self.types),
            "seq": self.seq,
            "file": self.file,
            "off": self.off,
            "bpos": self.bpos,
            "bdir": "".join(self.bdir),
            "blen": self.blen,
            "n_bytes": len(self.bpos),
            "total_in": self.total_in,
            "total_out": self.total_out,
        }


def materialize_events(
    log_dir: str | Path,
    index: dict,
    start_pos: int,
    end_pos: int,
) -> list[dict] | None:
    """Lê por seek os eventos do stream nas posições [start_pos, end_pos].

    Retorna a lista de eventos (dicts) em ordem de stream, ou None em
    qualquer falha de leitura/parse — o chamador cai para o parse completo.
    """
    types = index["types"]
    end_pos = min(end_pos, len(types) - 1)
    if start_pos > end_pos:
        return []
    log_path = Path(log_dir)
    files = index["files"]
    # Agrupa offsets por arquivo para ler cada arquivo uma única vez.
    por_arquivo: dict[int, list[int]] = {}
    for pos in range(start_pos, end_pos + 1):
        por_arquivo.setdefault(index["file"][pos], []).append(index["off"][pos])
    linhas: dict[int, bytes] = {}
    try:
        for file_idx, offsets in por_arquivo.items():
            caminho = log_path / files[file_idx]
            with open(caminho, "rb") as fh:
                for off in offsets:
                    fh.seek(off)
                    linhas[off] = fh.readline()
    except (OSError, IndexError):
        return None
    eventos: list[dict] = []
    for pos in range(start_pos, end_pos + 1):
        raw = linhas.get(index["off"][pos])
        if raw is None:
            return None
        try:
            item = json.loads(raw.decode("utf-8", errors="replace").strip())
        except Exception:
            return None
        if not isinstance(item, dict):
            return None
        eventos.append(item)
    return eventos


def event_at(log_dir: str | Path, index: dict, pos: int) -> dict | None:
    """Lê um único evento do stream por seek (ou None em falha)."""
    eventos = materialize_events(log_dir, index, pos, pos)
    if not eventos:
        return None
    return eventos[0]


def find_last_pos(index: dict, codes: str, before_pos: int) -> int:
    """Maior posição de stream < before_pos cujo tipo está em codes (-1 se não houver)."""
    types = index["types"]
    limite = min(before_pos, len(types))
    for pos in range(limite - 1, -1, -1):
        if types[pos] in codes:
            return pos
    return -1


def find_first_pos(index: dict, codes: str) -> int:
    """Menor posição de stream cujo tipo está em codes (-1 se não houver)."""
    types = index["types"]
    for pos in range(len(types)):
        if types[pos] in codes:
            return pos
    return -1

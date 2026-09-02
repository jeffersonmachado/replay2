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
import hashlib
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
        if len(index["files"]) != len(set(index["files"])):
            # nomes de arquivo duplicados tornam os offsets ambíguos — miss.
            # (a condição original tinha "and not files": logicamente
            # impossível, nunca rejeitava duplicata)
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


# ── Índice GLOBAL por captura (FASE 9) ───────────────────────────────────
# O índice por sessão acima nasceu para o replay; observabilidade (monitor,
# detalhe de sessão) precisava varrer todos os audit-*.jsonl a cada request.
# Este índice cobre a captura inteira em UMA passagem de streaming:
# arquivos (nome/size/mtime/bytes indexados), primeiro/último evento e
# seq_global, tipos, contagens, timestamps, bytes por direção, checkpoints e,
# por sessão, as posições (arquivo, offset) de todos os seus eventos.
#
# Invalidação: mtime+size por arquivo. Atualização incremental: se a captura
# só cresceu (append no último arquivo e/ou arquivos novos no fim), processa
# APENAS o delta a partir dos offsets conhecidos; qualquer outra alteração
# (arquivo do meio, shrink, mtime sem crescimento) força rebuild completo.
#
# Layout (chave estável pelo caminho do log_dir — o conteúdo muda com o
# append e a validação é por arquivo, então não usamos capture_sig aqui):
#     <cache_dir>/capturas/<sha256(log_dir)[:16]>/capture-index.json.gz
#
# Kill-switch: REPLAY_SESSION_INDEX=0 (o mesmo do índice de sessão).
# Tudo fail-safe: erro de leitura/validação vira miss (None) e o chamador
# faz a varredura completa, como antes.

CAPTURE_INDEX_VERSION = 1


def capture_index_path(cache_dir: str | Path, log_dir: str | Path) -> Path:
    chave = hashlib.sha256(str(Path(log_dir).resolve()).encode("utf-8")).hexdigest()[:16]
    return Path(cache_dir) / "capturas" / chave / "capture-index.json.gz"


def _novo_indice_capture(log_dir: str | Path) -> dict:
    return {
        "v": CAPTURE_INDEX_VERSION,
        "log_dir": str(Path(log_dir).resolve()),
        "files": [],
        "total_events": 0,
        "first_seq_global": 0,
        "last_seq_global": 0,
        "first_ts_ms": 0,
        "last_ts_ms": 0,
        "type_counts": {},
        "checkpoints": 0,
        "sessions": {},
    }


def _decoded_len_b64(data_b64: str) -> int:
    """Tamanho decodificado do base64 sem decodificar (padding incluso)."""
    n = len(data_b64)
    if not n:
        return 0
    pad = 2 if data_b64.endswith("==") else (1 if data_b64.endswith("=") else 0)
    return max(0, (n // 4) * 3 - pad)


def _registrar_evento_capture(index: dict, file_idx: int, offset: int, raw: bytes) -> None:
    """Registra um evento (linha bruta) no índice. Parse tolerante: linha
    inválida é ignorada (índice é best effort; a verificação formal é do
    verifier)."""
    try:
        ev = json.loads(raw.decode("utf-8", errors="replace").strip())
    except Exception:
        return
    if not isinstance(ev, dict):
        return
    index["total_events"] += 1
    typ = str(ev.get("type") or "unknown").strip() or "unknown"
    type_counts = index["type_counts"]
    type_counts[typ] = type_counts.get(typ, 0) + 1
    if typ == "checkpoint":
        index["checkpoints"] += 1
    try:
        sg = int(ev.get("seq_global") or 0)
    except (TypeError, ValueError):
        sg = 0
    if sg:
        if not index["first_seq_global"]:
            index["first_seq_global"] = sg
        index["last_seq_global"] = sg
    try:
        ts = int(ev.get("ts_ms") or 0)
    except (TypeError, ValueError):
        ts = 0
    if ts:
        if not index["first_ts_ms"]:
            index["first_ts_ms"] = ts
        index["last_ts_ms"] = ts

    sid = str(ev.get("session_id") or "").strip()
    if not sid:
        return
    sess = index["sessions"].get(sid)
    if sess is None:
        sess = {
            "count": 0, "first_seq_global": 0, "last_seq_global": 0,
            "first_ts_ms": 0, "last_ts_ms": 0,
            "bytes_in": 0, "bytes_out": 0, "checkpoints": 0,
            "types": {}, "locs": [],
        }
        index["sessions"][sid] = sess
    sess["count"] += 1
    sess["types"][typ] = sess["types"].get(typ, 0) + 1
    if sg:
        if not sess["first_seq_global"]:
            sess["first_seq_global"] = sg
        sess["last_seq_global"] = sg
    if ts:
        if not sess["first_ts_ms"]:
            sess["first_ts_ms"] = ts
        sess["last_ts_ms"] = ts
    if typ == "checkpoint":
        sess["checkpoints"] += 1
    elif typ == "bytes":
        try:
            n = int(ev.get("n"))
        except (TypeError, ValueError):
            n = _decoded_len_b64(str(ev.get("data_b64") or ""))
        direction = str(ev.get("dir") or "").strip().lower()
        if direction in ("i", "in"):
            sess["bytes_in"] += n
        elif direction in ("o", "out"):
            sess["bytes_out"] += n
    sess["locs"].append([file_idx, offset])


def _scan_arquivo_capture(index: dict, log_path: Path, file_idx: int, start_offset: int) -> bool:
    """Varre um arquivo a partir de start_offset (sempre fronteira de linha),
    registrando eventos e atualizando files[file_idx]. Linha parcial no fim
    (arquivo em gravação) não é indexada — o delta seguinte a retoma."""
    info = index["files"][file_idx]
    caminho = log_path / info["name"]
    try:
        st = caminho.stat()
    except OSError:
        return False
    offset = start_offset
    try:
        with open(caminho, "rb") as fh:
            fh.seek(start_offset)
            while True:
                pos = fh.tell()
                raw = fh.readline()
                if not raw or not raw.endswith(b"\n"):
                    break
                offset = fh.tell()
                _registrar_evento_capture(index, file_idx, pos, raw)
    except OSError:
        return False
    info["indexed_bytes"] = offset
    info["size"] = st.st_size
    info["mtime_ns"] = st.st_mtime_ns
    return True


def build_capture_index(log_dir: str | Path) -> dict | None:
    """Constrói o índice da captura em UMA passagem (cada arquivo aberto uma
    única vez). None se o diretório não puder ser lido."""
    log_path = Path(log_dir)
    try:
        arquivos = sorted(log_path.glob("audit-*.jsonl"))
    except OSError:
        return None
    index = _novo_indice_capture(log_dir)
    for caminho in arquivos:
        try:
            st = caminho.stat()
        except OSError:
            continue
        index["files"].append({
            "name": caminho.name, "size": st.st_size,
            "mtime_ns": st.st_mtime_ns, "indexed_bytes": 0,
        })
        if not _scan_arquivo_capture(index, log_path, len(index["files"]) - 1, 0):
            return None
    return index


def store_capture_index(cache_dir: str | Path, index: dict) -> bool:
    """Persiste o índice de captura de forma atômica. Falha silenciosa."""
    try:
        target = capture_index_path(cache_dir, index["log_dir"])
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".tmp")
        with gzip.open(tmp, "wt", encoding="utf-8") as f:
            json.dump(index, f, separators=(",", ":"))
        os.replace(tmp, target)
        return True
    except Exception:
        return False


def load_capture_index(cache_dir: str | Path, log_dir: str | Path) -> dict | None:
    """Carrega e valida a estrutura do índice; qualquer inconsistência → None."""
    try:
        with gzip.open(capture_index_path(cache_dir, log_dir), "rt", encoding="utf-8") as f:
            index = json.load(f)
    except Exception:
        return None
    try:
        if not isinstance(index, dict):
            return None
        if int(index.get("v") or 0) != CAPTURE_INDEX_VERSION:
            return None
        if str(index.get("log_dir") or "") != str(Path(log_dir).resolve()):
            return None
        files = index["files"]
        if not isinstance(files, list):
            return None
        nomes = []
        for info in files:
            nomes.append(str(info["name"]))
            int(info["size"])
            int(info["mtime_ns"])
            int(info["indexed_bytes"])
        if len(nomes) != len(set(nomes)):
            return None
        if not isinstance(index["sessions"], dict):
            return None
        for sess in index["sessions"].values():
            if not isinstance(sess.get("locs"), list):
                return None
        return index
    except (KeyError, TypeError, ValueError, AttributeError):
        return None


def _estado_arquivos(log_path: Path) -> list[tuple[str, int, int]] | None:
    """(nome, size, mtime_ns) dos audit-*.jsonl atuais, ou None em erro."""
    try:
        arquivos = sorted(log_path.glob("audit-*.jsonl"))
        estado = []
        for caminho in arquivos:
            st = caminho.stat()
            estado.append((caminho.name, st.st_size, st.st_mtime_ns))
        return estado
    except OSError:
        return None


def _classificar_delta(files_idx: list[dict], atual: list[tuple[str, int, int]]) -> str:
    """Classifica a situação do índice frente aos arquivos atuais:
    'hit' (nada mudou), 'delta' (só append/arquivos novos) ou 'rebuild'."""
    n_idx = len(files_idx)
    if n_idx == 0:
        return "rebuild" if atual else "hit"
    if len(atual) < n_idx:
        return "rebuild"
    for i in range(n_idx - 1):
        nome, size, mtime = atual[i]
        info = files_idx[i]
        if nome != info["name"] or size != int(info["size"]) or mtime != int(info["mtime_ns"]):
            return "rebuild"
    nome, size, mtime = atual[n_idx - 1]
    ultimo = files_idx[n_idx - 1]
    if nome != ultimo["name"]:
        return "rebuild"
    indexado = int(ultimo["indexed_bytes"])
    if size < indexado:
        return "rebuild"
    if size == indexado and mtime != int(ultimo["mtime_ns"]):
        # mesmo tamanho mas mtime mudou: conteúdo pode ter sido reescrito
        return "rebuild"
    if size == indexado and len(atual) == n_idx:
        return "hit"
    return "delta"


def get_capture_index(
    cache_dir: str | Path,
    log_dir: str | Path,
    *,
    enabled: bool | None = None,
    min_total_bytes: int = 0,
    store: bool = True,
) -> dict | None:
    """Índice global da captura, fresco: hit de disco, delta incremental ou
    rebuild completo conforme o estado dos arquivos (mtime+size por arquivo).

    enabled=None resolve pelo kill-switch REPLAY_SESSION_INDEX (0 desliga).
    min_total_bytes evita construir índice para capturas pequenas (a
    varredura direta já é barata); só se aplica quando NÃO há índice em
    disco. Falhas viram None (chamador faz a varredura completa).
    """
    if enabled is None:
        enabled = os.environ.get("REPLAY_SESSION_INDEX", "1") != "0"
    if not enabled:
        return None
    log_path = Path(log_dir)
    atual = _estado_arquivos(log_path)
    if atual is None:
        return None

    index = load_capture_index(cache_dir, log_dir)
    if index is None:
        if sum(size for _nome, size, _mtime in atual) < int(min_total_bytes or 0):
            return None
        index = build_capture_index(log_path)
        if index is None:
            return None
        if store:
            store_capture_index(cache_dir, index)
        return index

    situacao = _classificar_delta(index["files"], atual)
    if situacao == "hit":
        return index
    if situacao == "rebuild":
        index = build_capture_index(log_path)
        if index is None:
            return None
        if store:
            store_capture_index(cache_dir, index)
        return index

    # delta: reindexa só o que cresceu — último arquivo a partir do offset
    # conhecido + arquivos novos no fim.
    n_idx = len(index["files"])
    if not _scan_arquivo_capture(index, log_path, n_idx - 1, int(index["files"][n_idx - 1]["indexed_bytes"])):
        return None
    for nome, size, mtime in atual[n_idx:]:
        index["files"].append({"name": nome, "size": size, "mtime_ns": mtime, "indexed_bytes": 0})
        if not _scan_arquivo_capture(index, log_path, len(index["files"]) - 1, 0):
            return None
    if store:
        store_capture_index(cache_dir, index)
    return index


def session_event_locations(index: dict, session_id: str) -> list | None:
    """Posições [file_idx, offset] dos eventos da sessão, ou None se ausente."""
    try:
        sess = index["sessions"].get(str(session_id or "").strip())
    except (KeyError, AttributeError):
        return None
    if sess is None:
        return None
    return sess["locs"]

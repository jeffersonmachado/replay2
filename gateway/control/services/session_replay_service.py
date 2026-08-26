"""Preparacao de dados de replay de sessoes capturadas.

Extraido de gateway_observability_service.py para separar
a logica de replay (dominio de execucao) da observabilidade
(dominio de monitoramento).

v0.3.19+: TerminalEngine Python como fonte oficial de snapshots,
diffs, checkpoints e assinaturas. O JS terminal nao interpreta
mais ANSI no fluxo de producao.
"""
from __future__ import annotations

import base64
import codecs
import json
import os
import re
from pathlib import Path

from dakota_gateway.terminal_config import is_supported_encoding, normalize_encoding, validate_terminal_geometry
from dakota_terminal import (
    TerminalEngine,
    snapshot_from_engine,
    encode_render_snapshot,
    encode_snapshot_compact,
    create_diff,
    apply_diff,
    validate_diff,
    compare_signatures,
)

from control.services import replay_state_cache
from control.services import session_index_cache

# Janela de replay (dívida X6): sessões enormes (ex.: 116k eventos)
# derrubavam o control plane — o endpoint materializava eventos/timeline/
# playback completos e chamava snapshot_from_engine (1920 células) por
# evento OUT. Sem janela explícita, sessões acima de MAX_FULL_REPLAY_EVENTS
# retornam apenas a fatia inicial, marcada como truncada.
DEFAULT_REPLAY_WINDOW_LIMIT = 1000
MAX_FULL_REPLAY_EVENTS = 20000
# Teto de checkpoints por replay (X6): capturas legadas redesenham a tela
# inteira com clear-screen a cada página — sem teto, quase todo evento OUT
# gera checkpoint com snapshot completo (foi o que inflou o RSS para 4 GB).
MAX_REPLAY_CHECKPOINTS = 2000
# Cache de estado em disco (X6): sem ele, uma janela profunda reprocessa o
# stream desde o evento 0 e a rolagem profunda fica quadrática. Em sessões
# enormes, o estado completo da TerminalEngine é persistido a cada
# STATE_CACHE_INTERVAL eventos "bytes" (pontos limpos) e uma janela com
# offset grande retoma do estado mais próximo. REPLAY_STATE_CACHE=0 desliga.
STATE_CACHE_INTERVAL = 1000
STATE_CACHE_ENABLED = os.environ.get("REPLAY_STATE_CACHE", "1") != "0"
# Índice de sessão em disco (X6): sem ele, cada request de replay relê e
# reparseia todos os audit-*.jsonl (314 MB / 116k linhas na captura 20 do
# MIG24) para extrair a sessão, totalizar o playback e localizar a janela.
# O índice persiste o mapa tipado dos eventos (tipo/seq/arquivo/offset +
# direção/tamanho decodificado dos "bytes"); a janela é materializada por
# seek e os totais saem de somas de arrays. REPLAY_SESSION_INDEX=0 desliga.
SESSION_INDEX_ENABLED = os.environ.get("REPLAY_SESSION_INDEX", "1") != "0"


def build_reference_payload(
    *,
    initial_snapshot: dict,
    events: list[dict],
    checkpoints: list[dict],
    final_snapshot: dict,
) -> dict:
    """Build a replay payload with exactly one full event collection.

    Timeline and playback carry stable references only. This keeps each full
    diff/checkpoint serialized once while preserving the legacy consumers'
    ability to resolve by id.
    """
    event_refs = [str(ev.get("event_id") or ev.get("id") or ev.get("seq_global") or ev.get("seq") or idx) for idx, ev in enumerate(events)]
    checkpoint_refs = [
        str(cp.get("checkpoint_id") or cp.get("id") or cp.get("seq_global") or idx)
        for idx, cp in enumerate(checkpoints)
    ]
    return {
        "initial_snapshot": initial_snapshot,
        "events": events,
        "checkpoints": checkpoints,
        "timeline": {"event_refs": event_refs, "checkpoint_refs": checkpoint_refs},
        "playback": {"event_refs": event_refs, "checkpoint_refs": checkpoint_refs},
        "final_snapshot": final_snapshot,
    }


def _render_snapshot_payload(snapshot: dict) -> dict:
    """Usa formato compacto (run-length) para reduzir payload de transporte."""
    return encode_snapshot_compact(snapshot)


def _attach_render_snapshot(target: dict, snapshot: dict) -> None:
    payload = _render_snapshot_payload(snapshot)
    target["snapshot_compact"] = payload


class ReferenceView(dict):
    """Dict JSON contract with in-process legacy list access.

    Flask/json serialization sees only the dict keys. Existing Python callers
    that still index/iterate the timeline during migration see resolved items.
    """

    def __init__(self, *, event_refs: list[str], checkpoint_refs: list[str], items: list[dict]):
        super().__init__(event_refs=event_refs, checkpoint_refs=checkpoint_refs)
        self._items = items

    def __len__(self) -> int:
        return len(self._items)

    def __iter__(self):
        return iter(self._items)

    def __getitem__(self, key):
        if isinstance(key, int):
            return self._items[key]
        if isinstance(key, slice):
            return self._items[key]
        return super().__getitem__(key)


class PlaybackReferenceView(ReferenceView):
    def __init__(self, *, event_refs: list[str], checkpoint_refs: list[str], items: list[dict], meta: dict):
        super().__init__(event_refs=event_refs, checkpoint_refs=checkpoint_refs, items=items)
        for key, value in meta.items():
            self[key] = value

    def get(self, key, default=None):
        if key == "events":
            return self._items
        return super().get(key, default)


def _event_direction(ev: dict) -> str:
    """Retorna a direcao do evento: 'in', 'out' ou '' (desconhecida)."""
    return str(ev.get("direction") or ev.get("dir") or "").strip()


def _empty_reference_view() -> ReferenceView:
    return ReferenceView(event_refs=[], checkpoint_refs=[], items=[])


def _detect_encoding(events: list[dict], session_start: dict | None = None) -> str:
    """Detecta encoding a partir de metadados.

    Prioridade:
    1. Metadados do session_start (campo 'encoding')
    2. Fallback: utf-8

    Encodings suportados: utf-8, cp850, cp437, iso-8859-1, windows-1252, latin1, ascii
    """
    if session_start:
        enc = normalize_encoding(session_start.get("encoding") or "")
        if enc:
            return enc
    return "utf-8"


def _encoding_resolution(session_start: dict | None) -> dict:
    requested = str((session_start or {}).get("encoding") or "").strip()
    if not requested:
        return {"encoding": "utf-8", "encoding_source": "default"}
    encoding = normalize_encoding(requested)
    if is_supported_encoding(requested):
        return {"encoding": encoding, "encoding_source": "session_metadata"}
    return {
        "encoding": "utf-8",
        "encoding_source": "fallback",
        "encoding_warning": {
            "requested_encoding": requested,
            "resolved_encoding": "utf-8",
            "message": "encoding nao suportado; usando utf-8",
        },
    }


def _session_start_geometry_ok(session_start: dict | None) -> bool:
    """True quando os metadados do session_start definem a geometria.

    Espelha a prioridade 1 de _detect_geometry: sem metadados válidos, a
    detecção varre todos os eventos OUT atrás de resize e o caminho
    indexado (X6) precisa cair para o parse completo.
    """
    if not session_start:
        return False
    try:
        validate_terminal_geometry(session_start.get("rows"), session_start.get("cols"))
        return True
    except Exception:
        return False


def _detect_geometry(events: list[dict], session_start: dict | None = None) -> dict:
    """Detecta geometria a partir de metadados (prioridade) ou resize explicito.

    Ordem de resolucao:
    1. Metadados do session_start (rows, cols, term, encoding)
    2. Resize via CSI 8;rows;cols t (apenas eventos OUT)
    3. Variaveis de ambiente LINES/COLUMNS do session_start
    4. Fallback legado 25x80

    Retorna dict com: rows, cols, term, encoding, geometry_source
    """
    # Prioridade 1: metadados do session_start
    if session_start:
        s_rows = session_start.get("rows")
        s_cols = session_start.get("cols")
        try:
            geom = validate_terminal_geometry(s_rows, s_cols)
            s_term = str(session_start.get("term") or "xterm")
            enc_info = _encoding_resolution(session_start)
            src = str(session_start.get("geometry_source") or "session_metadata").strip()
            if src not in {"explicit", "session_metadata", "tty", "environment", "resize_event", "legacy_fallback"}:
                src = "session_metadata"
            return {
                "rows": geom.rows, "cols": geom.cols,
                "term": s_term,
                **enc_info,
                "geometry_source": src,
            }
        except Exception:
            pass

    # Encoding: metadados ou fallback utf-8
    enc_info = _encoding_resolution(session_start)
    encoding = enc_info["encoding"]
    term = str(session_start.get("term") or "xterm") if session_start else "xterm"

    # Prioridade 2: resize via CSI 8;rows;cols t (apenas eventos OUT)
    rows = None
    cols = None
    for ev in events:
        if _event_direction(ev) != "out":
            continue  # apenas eventos OUT podem alterar geometria
        data = ev.get("data_b64") or ""
        if not data:
            continue
        try:
            raw = base64.b64decode(data)
        except Exception:
            continue
        for match in re.finditer(rb'\x1b\[8;(\d+);(\d+)t', raw):
            r = int(match.group(1))
            c = int(match.group(2))
            try:
                geom = validate_terminal_geometry(r, c)
                rows = geom.rows
                cols = geom.cols
            except Exception:
                continue
    if rows and cols:
        return {"rows": rows, "cols": cols, "term": term, **enc_info, "geometry_source": "resize_event"}
    return {"rows": 25, "cols": 80, "term": term, **enc_info, "geometry_source": "legacy_fallback"}


def _resolve_encoding_from_session(session_start: dict | None) -> str:
    """Resolve encoding a partir de metadados da sessao.

    Mesma logica de _detect_encoding, mas usada internamente por _detect_geometry
    para evitar dependencia circular.
    """
    if not session_start:
        return "utf-8"
    return _encoding_resolution(session_start)["encoding"]


def _decode_event_bytes(data_b64: str, declared_n: int | None) -> tuple[bytes, dict | None]:
    try:
        raw = base64.b64decode(data_b64, validate=True) if data_b64 else b""
    except Exception:
        return b"", {
            "declared_bytes": declared_n,
            "actual_bytes": 0,
            "integrity_error": "invalid_base64",
        }
    actual = len(raw)
    if declared_n is not None and declared_n != actual:
        return raw, {
            "declared_bytes": declared_n,
            "actual_bytes": actual,
            "integrity_error": "byte_count_mismatch",
        }
    return raw, None


def prepare_session_replay_data(
    log_dir: str,
    session_id: str,
    *,
    offset: int | None = None,
    limit: int | None = None,
    state_cache_dir: str | None = None,
    abort_check=None,
    _allow_index: bool = True,
) -> dict:
    """
    Prepara dados de replay de uma sessao.
    Retorna eventos bytes (in/out) estruturados para visualizacao
    e replay da interacao capturada.

    offset/limit (dívida X6): restringem a fatia de eventos "bytes"
    materializada em events/timeline/playback. Sem janela explícita,
    sessões com mais de MAX_FULL_REPLAY_EVENTS eventos retornam só os
    primeiros DEFAULT_REPLAY_WINDOW_LIMIT, com window.truncated=True.
    O estado do terminal (snapshots/sigs) é sempre calculado sobre o
    prefixo completo, então a fatia é consistente com o modo completo.

    abort_check (dívida X6): callable opcional → True para abortar (a rota
    passa uma sonda do socket do cliente). Consultado periodicamente nos
    loops de parse e de processamento; requests abandonados pelo cliente
    não queimam CPU até o fim.
    """
    clean_dir = str(log_dir or "").strip()
    clean_sid = str(session_id or "").strip()

    def _aborted() -> bool:
        if abort_check is None:
            return False
        try:
            return bool(abort_check())
        except Exception:
            return False

    def _abort_result() -> dict:
        return {
            "error": {"code": "client_aborted", "message": "cliente desconectou durante o processamento"},
            "events": [],
            "timeline": _empty_reference_view(),
            "playback": None,
            "aborted": True,
        }

    if not clean_dir or not clean_sid:
        return {
            "error": {"code": "invalid_params", "message": "log_dir e session_id sao obrigatorios"},
            "events": [],
            "timeline": _empty_reference_view(),
            "playback": None,
        }

    log_path = Path(clean_dir)
    if not log_path.exists():
        return {
            "error": {"code": "log_dir_not_found", "message": f"diretorio de log nao encontrado: {clean_dir}"},
            "events": [],
            "timeline": _empty_reference_view(),
            "playback": None,
        }

    files = sorted(log_path.glob("audit-*.jsonl"))

    if not files:
        return {
            "error": {"code": "no_audit_files", "message": f"nenhum arquivo audit-*.jsonl encontrado em: {clean_dir}"},
            "events": [],
            "timeline": _empty_reference_view(),
            "playback": None,
        }
    # ── Índice de sessão em disco (X6) ──────────────────────────────────
    # Sem ele, cada request relê e reparseia todos os audit-*.jsonl para
    # extrair a sessão, totalizar o playback e localizar a janela (314 MB /
    # 116k linhas na captura 20 do MIG24 — custo dominante remanescente do
    # endpoint). Com o índice, a janela é materializada por seek e os
    # totais saem de somas de arrays.
    cache_dir_resolved = state_cache_dir or str(log_path.parent / "replay_state_cache")
    capture_sig = replay_state_cache.capture_signature(log_path)
    index_info: dict = {"enabled": bool(SESSION_INDEX_ENABLED), "hit": False, "stored": False}
    index = (
        session_index_cache.load_index(cache_dir_resolved, capture_sig, clean_sid)
        if SESSION_INDEX_ENABLED and _allow_index
        else None
    )
    indexed = index is not None

    events: list[dict] = []
    builder = None if indexed or not SESSION_INDEX_ENABLED else session_index_cache.IndexBuilder()
    session_start = None
    session_end = None
    resume_from = 0
    resume_envelope = None
    pre_resume_session_starts: list[dict] = []
    start_pos = 0
    end_pos = -1

    if indexed:
        first_s_pos = session_index_cache.find_first_pos(index, "s")
        session_start = session_index_cache.event_at(log_path, index, first_s_pos) if first_s_pos >= 0 else None
        first_e_pos = session_index_cache.find_first_pos(index, "e")
        session_end = session_index_cache.event_at(log_path, index, first_e_pos) if first_e_pos >= 0 else None
        if _session_start_geometry_ok(session_start):
            index_info["hit"] = True
        else:
            # Sem geometria de metadados, _detect_geometry varre todos os
            # eventos OUT atrás de resize — exige o parse completo.
            indexed = False
            index = None
            index_info["reason"] = "geometry_requires_full_scan"
            builder = session_index_cache.IndexBuilder() if SESSION_INDEX_ENABLED else None

    if not indexed:
        _linhas_lidas = 0
        for file_idx, file_path in enumerate(files):
            try:
                with open(file_path, "rb") as fh:
                    file_offset = 0
                    while True:
                        raw_line = fh.readline()
                        if not raw_line:
                            break
                        _linhas_lidas += 1
                        # Abort de request abandonada (X6): a sonda é uma
                        # syscall recv(MSG_PEEK) — custo desprezível frente
                        # ao parse da linha; cadência curta dá abort
                        # responsivo em sessões enormes.
                        if _linhas_lidas % 64 == 0 and _aborted():
                            return _abort_result()
                        line_offset = file_offset
                        file_offset += len(raw_line)
                        line = raw_line.decode("utf-8", errors="replace").strip()
                        if not line:
                            continue
                        try:
                            item = json.loads(line)
                        except Exception:
                            continue
                        if not isinstance(item, dict):
                            continue
                        if str(item.get("session_id") or "").strip() != clean_sid:
                            continue
                        events.append(item)
                        if builder is not None:
                            code = session_index_cache.type_code(str(item.get("type") or "").strip())
                            if code is not None:
                                direction = ""
                                decoded_len = 0
                                if code == "b":
                                    _raw, _warn = _decode_event_bytes(
                                        str(item.get("data_b64") or "").strip(),
                                        int(item["n"]) if item.get("n") is not None else None,
                                    )
                                    direction = str(item.get("dir") or "").strip()
                                    decoded_len = len(_raw)
                                builder.add(
                                    code,
                                    int(item.get("seq_global") or 0),
                                    file_idx,
                                    line_offset,
                                    direction=direction,
                                    decoded_len=decoded_len,
                                )
            except OSError as exc:
                return {"error": f"erro ao ler arquivo: {exc}", "events": [], "timeline": _empty_reference_view(), "playback": None}

        events.sort(key=lambda x: int(x.get("seq_global") or 0))

        # Verifica se a sessao existe nos logs
        if not events:
            return {
                "error": {"code": "session_not_found", "message": f"session_id nao encontrado: {clean_sid}"},
                "events": [],
                "timeline": _empty_reference_view(),
                "playback": None,
            }

        # Extrai session_start antes da deteccao de geometria
        for ev in events:
            ev_type = str(ev.get("type") or "").strip()
            if ev_type == "session_start" and session_start is None:
                session_start = ev
            elif ev_type == "session_end" and session_end is None:
                session_end = ev

    geometry = _detect_geometry(events, session_start)
    detected_encoding = _detect_encoding(events, session_start)

    # ── Janela de materialização (X6) ───────────────────────────────────
    if indexed:
        total_bytes_events = int(index["n_bytes"])
    else:
        total_bytes_events = sum(1 for ev in events if str(ev.get("type") or "").strip() == "bytes")
    explicit_window = offset is not None or limit is not None
    if explicit_window:
        win_start = max(0, int(offset or 0))
        win_limit = max(1, int(limit or DEFAULT_REPLAY_WINDOW_LIMIT))
        win_end = win_start + win_limit
    elif total_bytes_events > MAX_FULL_REPLAY_EVENTS:
        # Sessão enorme sem janela explícita: nunca materializa tudo.
        win_start = 0
        win_limit = DEFAULT_REPLAY_WINDOW_LIMIT
        win_end = win_limit
    else:
        win_start = 0
        win_limit = total_bytes_events
        win_end = None  # modo completo (sessão pequena)

    # Índice (no espaço de eventos "bytes") do último OUT antes da janela:
    # é a base de diff do primeiro evento OUT materializado.
    window_base_out_index = -1
    if win_end is not None and win_start > 0:
        if indexed:
            _bdir = index["bdir"]
            for _k in range(min(win_start, len(_bdir)) - 1, -1, -1):
                if _bdir[_k] == "o":
                    window_base_out_index = _k
                    break
        else:
            _idx = 0
            for _ev in events:
                if str(_ev.get("type") or "").strip() != "bytes":
                    continue
                if _idx >= win_start:
                    break
                if str(_ev.get("dir") or "").strip() == "out":
                    window_base_out_index = _idx
                _idx += 1

    # Totais de bytes reais (decodificados) da sessão inteira — calculados
    # upfront porque em modo parcial o loop para no fim da janela (X6). Com
    # o índice (ou o builder do parse corrente), saem de somas de arrays,
    # sem decodificar base64 de novo.
    if indexed:
        total_bytes_in_actual = int(index["total_in"])
        total_bytes_out_actual = int(index["total_out"])
    elif builder is not None:
        total_bytes_in_actual = builder.total_in
        total_bytes_out_actual = builder.total_out
    else:
        total_bytes_in_actual = 0
        total_bytes_out_actual = 0
        for _ev in events:
            if str(_ev.get("type") or "").strip() != "bytes":
                continue
            _raw, _warn = _decode_event_bytes(
                str(_ev.get("data_b64") or "").strip(),
                int(_ev["n"]) if _ev.get("n") is not None else None,
            )
            _dir = str(_ev.get("dir") or "").strip()
            if _dir == "in":
                total_bytes_in_actual += len(_raw)
            elif _dir == "out":
                total_bytes_out_actual += len(_raw)

    # Persiste o índice de sessão para os próximos requests (X6) — apenas
    # sessões enormes, mesmo critério do cache de estado.
    if builder is not None and total_bytes_events > MAX_FULL_REPLAY_EVENTS:
        _novo_indice = builder.build(
            capture_sig=capture_sig, session_id=clean_sid,
            files=[f.name for f in files],
        )
        if _novo_indice is not None and session_index_cache.store_index(cache_dir_resolved, _novo_indice):
            index_info["stored"] = True

    # ── Cache de estado em disco (X6): decisão de retomada ──────────────
    # O lookup é feito aqui (fase A) porque o caminho indexado precisa do
    # ponto de retomada para delimitar a materialização por seek; a
    # aplicação do estado na engine acontece na fase B, junto ao parse
    # completo, para manter um único loop principal nos dois caminhos.
    cache_enabled = bool(STATE_CACHE_ENABLED) and total_bytes_events > MAX_FULL_REPLAY_EVENTS
    cache_info: dict = {"enabled": cache_enabled, "hit": False, "resumed_from": None, "stored": 0}
    state_hit = None
    if cache_enabled and win_start > 0:
        state_hit = replay_state_cache.load_nearest_state(
            cache_dir_resolved, capture_sig, clean_sid, win_start,
            rows=geometry["rows"], cols=geometry["cols"],
            term=geometry.get("term", "xterm"), encoding=detected_encoding,
        )

    # ── TerminalEngine Python: fonte oficial ────────────────────────────
    engine = TerminalEngine(
        rows=geometry["rows"],
        cols=geometry["cols"],
        term=geometry.get("term", "xterm"),
        encoding=detected_encoding,
        session_id=clean_sid,
    )

    if indexed:
        # Delimita a fatia do stream a materializar: do ponto de retomada
        # (ou do início) até o último evento processado pelo loop — o evento
        # "bytes" de ordinal win_end-1, ou o fim do stream quando a janela
        # cobre (ou ultrapassa) o fim da sessão.
        n_stream = len(index["types"])
        if index["n_bytes"] and win_end is not None and win_end <= int(index["n_bytes"]):
            end_pos = index["bpos"][win_end - 1]
        else:
            end_pos = n_stream - 1
        if state_hit is not None:
            candidate_idx, candidate_envelope = state_hit
            # Valida o estado numa engine descartável: a engine real só é
            # carregada na fase B, DEPOIS do initial_snapshot (tela em
            # branco) — carregá-la aqui poluiria o checkpoint inicial.
            try:
                _probe = TerminalEngine(
                    rows=geometry["rows"], cols=geometry["cols"],
                    term=geometry.get("term", "xterm"), encoding=detected_encoding,
                    session_id=clean_sid,
                )
                _probe.load_state(candidate_envelope["engine"])
                resume_envelope = candidate_envelope
                resume_from = candidate_idx
            except (ValueError, KeyError, TypeError):
                cache_info["reason"] = "state_load_failed"
                resume_from = 0
        if resume_from > 0:
            # A fase de skip do parse completo consome os eventos "bytes" de
            # ordinal < resume_from e faz bookkeeping dos session_start/end
            # intercalados; o processamento normal começa logo após o bytes
            # de ordinal resume_from-1.
            start_pos = index["bpos"][resume_from - 1] + 1
            # session_start(s) antes do primeiro evento bytes — reprodução
            # do checkpoint inicial na aplicação do estado (fase B).
            first_b_pos = index["bpos"][0]
            for _pos in range(first_b_pos):
                if index["types"][_pos] == "s":
                    _ev = session_index_cache.event_at(log_path, index, _pos)
                    if _ev is not None:
                        pre_resume_session_starts.append(_ev)
            # Bookkeeping dos limites de sessão: último session_start/end
            # antes do ponto de retomada; sem predecessor, valem os
            # primeiros do stream (já atribuídos acima).
            _s_pos = session_index_cache.find_last_pos(index, "s", start_pos)
            if _s_pos >= 0:
                session_start = session_index_cache.event_at(log_path, index, _s_pos)
            _e_pos = session_index_cache.find_last_pos(index, "e", start_pos)
            if _e_pos >= 0:
                session_end = session_index_cache.event_at(log_path, index, _e_pos)
        materialized = session_index_cache.materialize_events(log_path, index, start_pos, end_pos)
        if materialized is None:
            # Falha de leitura via índice — cai para o parse completo.
            return prepare_session_replay_data(
                log_dir, session_id,
                offset=offset, limit=limit, state_cache_dir=state_cache_dir,
                _allow_index=False,
            )
        events = materialized

    event_items = []
    deterministic_events = []
    timeline = []
    decoders: dict[str, codecs.IncrementalDecoder] = {}

    # Cache do encoding compacto por snapshot (dívida de performance do
    # replay): o mesmo snapshot é anexado ao checkpoint + event_item +
    # timeline_item e o encode_snapshot_compact (rows*cols células) era
    # refeito 3x — era o custo dominante do endpoint em sessões com muitos
    # checkpoints. A referência ao snapshot é mantida no valor para que o
    # id() não seja reutilizado por outro dict enquanto o cache vive.
    compact_cache: dict[int, tuple[dict, dict]] = {}

    def _compact_cached(snapshot: dict) -> dict:
        key = id(snapshot)
        entry = compact_cache.get(key)
        if entry is None or entry[0] is not snapshot:
            entry = (snapshot, _render_snapshot_payload(snapshot))
            compact_cache[key] = entry
        return entry[1]

    def _attach_cached(target: dict, snapshot: dict) -> None:
        target["snapshot_compact"] = _compact_cached(snapshot)

    # ── Snapshots, diffs, checkpoints ───────────────────────────────────
    initial_snapshot = snapshot_from_engine(engine)
    checkpoints: list[dict] = []
    current_snapshot = initial_snapshot
    last_out_snapshot = initial_snapshot
    last_snapshot = initial_snapshot
    last_out_seq_global = 0  # seq_global do ultimo evento OUT
    out_event_count = 0
    window_out_seen = False  # X6: algum OUT já materializado na janela (âncora)
    bytes_event_index = 0  # índice no espaço de eventos "bytes" (base da janela)
    last_rows = geometry["rows"]
    last_cols = geometry["cols"]
    last_checkpoint_time_ms = 0
    CHECKPOINT_EVENT_INTERVAL = 250   # snapshot completo a cada N eventos OUT
    CHECKPOINT_TIME_INTERVAL_MS = 3000  # ou a cada 3 segundos

    # Adiciona checkpoint inicial
    initial_checkpoint = {
        "session_id": clean_sid,
        "seq_global": 0,
        "timestamp_ms": 0,
        "text_sig": initial_snapshot.get("text_sig", ""),
        "visual_sig": initial_snapshot.get("visual_sig", ""),
        "semantic_sig": initial_snapshot.get("semantic_sig", ""),
        "rows": geometry["rows"],
        "cols": geometry["cols"],
        "term": geometry.get("term", "xterm"),
        "encoding": detected_encoding,
        "engine_version": engine.engine_version,
        "reason": "session_start",
    }
    _attach_cached(initial_checkpoint, initial_snapshot)
    checkpoints.append(initial_checkpoint)

    # ── Cache de estado em disco (X6): aplicação da retomada ────────────
    # O lookup foi feito na fase A (junto à delimitação da janela). Aqui o
    # estado é aplicado à engine e os contadores/checkpoints são
    # restaurados. Sem cache, uma janela com offset grande reprocessa o
    # stream desde o evento 0 (rolagem profunda quadrática). O estado
    # restaurado já reflete TODOS os efeitos dos eventos anteriores
    # (inclui engine.finish de session_end intermediários — capturas com
    # reconexão reutilizam o session_id e têm centenas de pares
    # session_start/session_end no meio do stream). Na fase de skip,
    # session_start/session_end atualizam só o bookkeeping (campos do
    # payload), sem reexecutar efeitos na engine, e deterministic_input
    # pré-janela não é materializado (contrato de paginação). O checkpoint
    # do session_start inicial é reproduzido com a tela em branco, igual à
    # execução sem cache. Exceção documentada de paridade: checkpoints
    # anteriores ao ponto de retomada (de qualquer tipo) não são regerados
    # (window.state_cache.hit=true sinaliza isso).
    state_applied = False
    if not indexed and state_hit is not None:
        candidate_idx, envelope = state_hit
        count = 0
        for ev in events:
            if count >= candidate_idx:
                break
            ev_type = str(ev.get("type") or "").strip()
            if ev_type == "bytes":
                count += 1
            elif ev_type == "session_start" and count == 0:
                pre_resume_session_starts.append(ev)
        try:
            engine.load_state(envelope["engine"])
            resume_envelope = envelope
            resume_from = candidate_idx
            state_applied = True
        except (ValueError, KeyError, TypeError):
            cache_info["reason"] = "state_load_failed"
            resume_from = 0
    elif indexed and resume_envelope is not None:
        # Estado validado na fase A (engine descartável); a carga real
        # acontece aqui, depois do initial_snapshot. Uma falha neste ponto
        # é anomalia — os eventos já foram materializados a partir do
        # ponto de retomada, então cai para o parse completo.
        try:
            engine.load_state(resume_envelope["engine"])
            state_applied = True
        except (ValueError, KeyError, TypeError):
            return prepare_session_replay_data(
                log_dir, session_id,
                offset=offset, limit=limit, state_cache_dir=state_cache_dir,
                _allow_index=False,
            )

    if state_applied and resume_envelope is not None:
        counters = resume_envelope.get("counters") or {}
        restored_snapshot = snapshot_from_engine(engine)
        current_snapshot = restored_snapshot
        last_out_snapshot = restored_snapshot
        last_snapshot = restored_snapshot
        out_event_count = int(counters.get("out_event_count") or 0)
        last_out_seq_global = int(counters.get("last_out_seq_global") or 0)
        last_rows = int(counters.get("last_rows") or engine.rows)
        last_cols = int(counters.get("last_cols") or engine.cols)
        last_checkpoint_time_ms = int(counters.get("last_checkpoint_time_ms") or 0)
        bytes_event_index = resume_from
        cache_info["hit"] = True
        cache_info["resumed_from"] = resume_from
        # Reproduz o checkpoint do session_start inicial (tela
        # em branco nesse ponto, igual à execução sem cache).
        for ss_ev in pre_resume_session_starts:
            checkpoint = {
                "session_id": clean_sid,
                "seq_global": int(ss_ev.get("seq_global") or 0),
                "timestamp_ms": int(ss_ev.get("ts_ms") or 0),
                "text_sig": initial_snapshot.get("text_sig", ""),
                "visual_sig": initial_snapshot.get("visual_sig", ""),
                "semantic_sig": initial_snapshot.get("semantic_sig", ""),
                "rows": geometry["rows"],
                "cols": geometry["cols"],
                "term": geometry.get("term", "xterm"),
                "encoding": detected_encoding,
                "engine_version": engine.engine_version,
                "reason": "session_start",
            }
            _attach_cached(checkpoint, initial_snapshot)
            checkpoints.append(checkpoint)

    # No caminho indexado, a materialização já começa no ponto de retomada
    # (o bookkeeping pré-janela foi feito na fase A) — não há o que pular.
    skip_remaining = 0 if indexed else resume_from

    _iter = 0
    for ev in events:
        _iter += 1
        # Abort de request abandonada (X6): sonda a cada 64 eventos — custo
        # desprezível frente ao processamento (renders/feed da engine).
        if _iter % 64 == 0 and _aborted():
            return _abort_result()
        ev_type = str(ev.get("type") or "").strip()

        # Retomada (X6): eventos anteriores ao ponto de retomada já estão
        # refletidos no estado restaurado. session_start/session_end fazem
        # só bookkeeping (campos do payload) — os efeitos na engine
        # (engine.finish do session_end) já estão no estado; o checkpoint
        # do session_start inicial já foi reproduzido; deterministic_input
        # pré-janela não é materializado (contrato de paginação).
        if skip_remaining > 0 and ev_type in ("bytes", "session_start", "session_end", "deterministic_input"):
            if ev_type == "bytes":
                skip_remaining -= 1
            elif ev_type == "session_start":
                session_start = ev
            elif ev_type == "session_end":
                session_end = ev
            continue

        # Modo parcial (X6): a janela acabou — interrompe o processamento
        # do stream. Estado final, checkpoints e canonical_signatures
        # refletem o fim da janela, não o fim da sessão (window.partial_state).
        if win_end is not None and bytes_event_index >= win_end:
            break

        if ev_type == "session_start":
            session_start = ev
            # Gera checkpoint apos session_start
            checkpoint = {
                "session_id": clean_sid,
                "seq_global": int(ev.get("seq_global") or 0),
                "timestamp_ms": int(ev.get("ts_ms") or 0),
                "text_sig": current_snapshot.get("text_sig", ""),
                "visual_sig": current_snapshot.get("visual_sig", ""),
                "semantic_sig": current_snapshot.get("semantic_sig", ""),
                "rows": engine.rows,
                "cols": engine.cols,
                "term": engine.term,
                "encoding": engine.encoding,
                "engine_version": engine.engine_version,
                "reason": "session_start",
            }
            _attach_cached(checkpoint, current_snapshot)
            checkpoints.append(checkpoint)
        elif ev_type == "session_end":
            session_end = ev
            engine.finish(seq_global=last_out_seq_global, direction="out", session_id=clean_sid)
            final_snapshot = snapshot_from_engine(engine)
            checkpoint = {
                "session_id": clean_sid,
                "seq_global": int(ev.get("seq_global") or 0),
                "timestamp_ms": int(ev.get("ts_ms") or 0),
                "text_sig": final_snapshot.get("text_sig", ""),
                "visual_sig": final_snapshot.get("visual_sig", ""),
                "semantic_sig": final_snapshot.get("semantic_sig", ""),
                "rows": engine.rows,
                "cols": engine.cols,
                "term": engine.term,
                "encoding": engine.encoding,
                "engine_version": engine.engine_version,
                "reason": "session_end",
            }
            _attach_cached(checkpoint, final_snapshot)
            checkpoints.append(checkpoint)
        elif ev_type == "bytes":
            data_b64 = str(ev.get("data_b64") or "").strip()
            direction = str(ev.get("dir") or "").strip()
            declared_n = int(ev["n"]) if ev.get("n") is not None else None
            seq_global = int(ev.get("seq_global") or 0)
            ts_ms = int(ev.get("ts_ms") or 0)

            data_raw, integrity_warning = _decode_event_bytes(data_b64, declared_n)
            actual_n = len(data_raw)

            in_window = win_end is None or (win_start <= bytes_event_index < win_end)
            generate_checkpoint = False
            diff = None

            # Alimenta TerminalEngine com bytes OUT
            if direction == "out" and data_raw:
                engine.feed_bytes(data_raw, seq_global=seq_global, direction=direction, session_id=clean_sid)
                out_event_count += 1

                # Detecta RIS e clear-screen para gerar checkpoint
                has_ris = b'\x1bc' in data_raw
                has_clear = b'\x1b[2J' in data_raw

                # Detecta resize que ocorreu (engine ja processou internamente)
                has_resize = (engine.rows != last_rows or engine.cols != last_cols)
                last_rows, last_cols = engine.rows, engine.cols

                # Gera checkpoint conforme politica
                time_since_checkpoint = ts_ms - last_checkpoint_time_ms
                # X6: dentro da janela materializada, checkpoints por
                # intervalo (eventos/tempo) são redundantes — cada evento
                # OUT da janela já carrega diff + assinaturas, e o tamanho
                # da janela limita o custo de seek. Em regiões esparsas
                # (captura 20 do MIG24: eventos a >3 s de distância), a
                # regra de intervalo gerava um checkpoint com render
                # completo por evento — o custo dominante do endpoint morno
                # (~6 s dos ~12 s da janela profunda). Mantidos: a âncora
                # da janela (primeiro OUT materializado, base de seek
                # direto em janela profunda) e os checkpoints semânticos
                # (RIS/clear/resize). No modo completo (win_end=None) a
                # política de intervalos é inalterada — a sessão inteira
                # está materializada e o seek não é limitado pela janela.
                first_out_in_window = win_end is not None and in_window and not window_out_seen
                generate_checkpoint = (
                    out_event_count == 1  # primeiro evento sempre gera checkpoint
                    or first_out_in_window
                    or (not (win_end is not None and in_window) and (
                        out_event_count % CHECKPOINT_EVENT_INTERVAL == 0
                        or time_since_checkpoint >= CHECKPOINT_TIME_INTERVAL_MS
                    ))
                    or has_ris
                    or has_resize
                    or has_clear
                )
                if in_window:
                    window_out_seen = True
                # X6: teto de checkpoints — sessões com redesenho constante
                # (clear-screen por página) gerariam um checkpoint com
                # snapshot completo por evento OUT.
                if generate_checkpoint and len(checkpoints) >= MAX_REPLAY_CHECKPOINTS:
                    generate_checkpoint = False

                # X6: snapshot_from_engine serializa rows*cols celulas e 3
                # assinaturas — só é calculado quando o evento está na
                # janela, gera checkpoint ou é a base de diff imediatamente
                # anterior à janela. Fora desses casos o estado da tela já
                # está correto na engine e o snapshot é dispensável.
                want_snapshot = (
                    in_window
                    or generate_checkpoint
                    or bytes_event_index == window_base_out_index
                )
                prev_out_snapshot = last_out_snapshot
                prev_out_seq_global = last_out_seq_global
                if want_snapshot:
                    current_snapshot = snapshot_from_engine(engine)
                    last_out_snapshot = current_snapshot
                    last_out_seq_global = seq_global
                    last_snapshot = current_snapshot
                else:
                    current_snapshot = last_out_snapshot

                if generate_checkpoint:
                    last_checkpoint_time_ms = ts_ms
                    reason = (
                        "ris" if has_ris else
                        "resize" if has_resize else
                        "clear_screen" if has_clear else
                        "session_start" if out_event_count == 1 else
                        "window_start" if first_out_in_window else
                        "interval_events" if out_event_count % CHECKPOINT_EVENT_INTERVAL == 0 else
                        "interval_time"
                    )
                    checkpoint = {
                        "session_id": clean_sid,
                        "seq_global": seq_global,
                        "timestamp_ms": ts_ms,
                        "text_sig": current_snapshot.get("text_sig", ""),
                        "visual_sig": current_snapshot.get("visual_sig", ""),
                        "semantic_sig": current_snapshot.get("semantic_sig", ""),
                        "rows": engine.rows,
                        "cols": engine.cols,
                        "term": engine.term,
                        "encoding": engine.encoding,
                        "engine_version": engine.engine_version,
                        "reason": reason,
                    }
                    _attach_cached(checkpoint, current_snapshot)
                    checkpoints.append(checkpoint)

                # Gera diff apenas para eventos materializados (janela)
                if in_window:
                    diff = create_diff(
                        prev_out_snapshot, current_snapshot,
                        base_seq=prev_out_seq_global,
                        seq=seq_global,
                        ts_ms=ts_ms,
                    )
            else:
                if data_raw:
                    # IN: nao altera tela, mas registra
                    pass
                current_snapshot = last_snapshot

            bytes_event_index += 1

            # Persiste o estado completo da engine em pontos limpos a cada
            # STATE_CACHE_INTERVAL eventos (X6) — base da retomada de
            # janelas profundas em sessões enormes.
            if (
                cache_enabled
                and bytes_event_index % STATE_CACHE_INTERVAL == 0
                and bytes_event_index != resume_from
                and engine.is_state_clean()
            ):
                envelope = {
                    "state_version": 1,
                    "capture_sig": capture_sig,
                    "session_id": clean_sid,
                    "bytes_index": bytes_event_index,
                    "rows": engine.rows,
                    "cols": engine.cols,
                    "term": engine.term,
                    "encoding": engine.encoding,
                    "engine": engine.state_dict(),
                    "counters": {
                        "out_event_count": out_event_count,
                        "last_out_seq_global": last_out_seq_global,
                        "last_rows": last_rows,
                        "last_cols": last_cols,
                        "last_checkpoint_time_ms": last_checkpoint_time_ms,
                    },
                }
                if replay_state_cache.store_state(
                    cache_dir_resolved, capture_sig, clean_sid, bytes_event_index, envelope,
                ):
                    cache_info["stored"] += 1

            if not in_window:
                # Fora da janela: engine já foi alimentada acima; não
                # materializa event_item/timeline_item (dívida X6).
                continue

            # Decodifica para legado
            if integrity_warning and integrity_warning.get("integrity_error") == "invalid_base64":
                data_str = "[base64 inválido]"
            else:
                try:
                    decoder_key = direction or "unknown"
                    decoder = decoders.get(decoder_key)
                    if decoder is None:
                        decoder = codecs.getincrementaldecoder(detected_encoding)(errors="replace")
                        decoders[decoder_key] = decoder
                    data_str = decoder.decode(data_raw, final=False)
                except Exception:
                    data_str = data_raw.hex()

            event_item = {
                "event_id": f"ev-{seq_global}",
                "seq_global": seq_global, "ts_ms": ts_ms, "type": "bytes",
                "direction": direction, "n_bytes": actual_n,
                "declared_bytes": declared_n, "actual_bytes": actual_n,
                "data_decoded": data_str, "data_b64": data_b64,
            }
            if integrity_warning:
                event_item["integrity_warning"] = integrity_warning
            # Eficiencia: apenas diff em eventos OUT normais
            if direction == "out":
                if diff:
                    event_item["diff"] = diff
                    event_item["text_sig"] = current_snapshot.get("text_sig", "")
                    event_item["visual_sig"] = current_snapshot.get("visual_sig", "")
                # Snapshot completo apenas em checkpoints
                if generate_checkpoint:
                    _attach_cached(event_item, current_snapshot)
                    event_item["is_checkpoint"] = True
            event_items.append(event_item)

            timeline_item = {
                "event_id": event_item["event_id"],
                "seq_global": seq_global, "ts_ms": ts_ms, "timestamp_ms": ts_ms,
                "type": "bytes", "direction": direction, "n_bytes": actual_n,
                "declared_bytes": declared_n, "actual_bytes": actual_n,
                "data_b64": data_b64, "data_decoded": data_str,
                "summary": data_str[:400],
            }
            if direction == "out" and diff:
                timeline_item["text_sig"] = current_snapshot.get("text_sig", "")
                timeline_item["visual_sig"] = current_snapshot.get("visual_sig", "")
                timeline_item["engine_version"] = engine.engine_version
                # snapshot_compact apenas em checkpoint (evita duplicacao)
                if generate_checkpoint:
                    _attach_cached(timeline_item, current_snapshot)
                    timeline_item["checkpoint_seq"] = True
            if integrity_warning:
                timeline_item["integrity_warning"] = integrity_warning
            timeline.append(timeline_item)
        elif ev_type == "deterministic_input":
            # X6: deterministic_input só é materializado dentro da janela —
            # contrato de paginação (cada janela carrega os seus eventos).
            # Capturas deterministicas podem ter dezenas de milhares desses
            # eventos (captura 20 do MIG24: 25k); calcular snapshot fresco
            # para cada um fora da janela era o custo dominante do modo
            # parcial nessas capturas.
            det_in_window = win_end is None or (win_start <= bytes_event_index < win_end)
            if not det_in_window:
                continue
            seq_global = int(ev.get("seq_global") or 0)
            ts_ms = int(ev.get("ts_ms") or 0)
            # Com a janela, current_snapshot pode estar defasado
            # (snapshots fora da janela são pulados); recalcula fresco.
            # No modo completo (win_end=None) o último snapshot já reflete o
            # estado atual — deterministic_input não alimenta a engine — e
            # recalcular (snapshot_from_engine + 3 assinaturas) era custo
            # puro por evento.
            if win_end is None and last_snapshot is not None:
                current_snapshot = last_snapshot
            else:
                current_snapshot = snapshot_from_engine(engine)
            deterministic_item = {
                "event_id": f"det-{seq_global}",
                "seq_global": seq_global, "ts_ms": ts_ms,
                "type": "deterministic_input",
                "screen_sig": str(ev.get("screen_sig") or ""),
                "screen_sample": str(ev.get("screen_sample") or ""),
                "norm_sha256": str(ev.get("norm_sha256") or ""),
                "norm_len": int(ev.get("norm_len") or 0),
                "key_kind": str(ev.get("key_kind") or ""),
                "key_text": str(ev.get("key_text") or ""),
                "key_b64": str(ev.get("key_b64") or ""),
                "input_len": int(ev.get("input_len") or 0),
                "contains_newline": bool(ev.get("contains_newline")),
                "contains_escape": bool(ev.get("contains_escape")),
                "is_probable_paste": bool(ev.get("is_probable_paste")),
                "is_probable_command": bool(ev.get("is_probable_command")),
                "logical_parts": int(ev.get("logical_parts") or 0),
                "screen_source": str(ev.get("screen_source") or ""),
                "screen_snapshot_ts_ms": int(ev.get("screen_snapshot_ts_ms") or 0) or None,
                "screen_snapshot_age_ms": int(ev.get("screen_snapshot_age_ms") or 0) or None,
                "source": str(ev.get("source") or ""),
                "expected_text_sig": current_snapshot.get("text_sig", "") if current_snapshot else "",
                "expected_visual_sig": current_snapshot.get("visual_sig", "") if current_snapshot else "",
                "expected_semantic_sig": current_snapshot.get("semantic_sig", "") if current_snapshot else "",
            }
            if current_snapshot:
                expected_snapshot = {
                    "text_sig": str(ev.get("expected_text_sig") or ev.get("text_sig") or ""),
                    "visual_sig": str(ev.get("expected_visual_sig") or ev.get("visual_sig") or ""),
                    "semantic_sig": str(ev.get("expected_semantic_sig") or ev.get("semantic_sig") or ""),
                    "screen_sig": str(ev.get("screen_sig") or ""),
                }
                deterministic_item["_comparison"] = compare_signatures(
                    expected_snapshot,
                    current_snapshot,
                    mode="hybrid",
                    legacy_expected_screen_sig=str(ev.get("screen_sig") or ""),
                    legacy_observed_screen_sig=str(current_snapshot.get("screen_sig") or ""),
                )
            deterministic_events.append(deterministic_item)
            timeline.append({
                "event_id": deterministic_item["event_id"],
                "seq_global": seq_global, "ts_ms": ts_ms, "timestamp_ms": ts_ms,
                "type": "deterministic_input",
                "screen_sig": deterministic_item["screen_sig"],
                "screen_sample": deterministic_item["screen_sample"],
                "key_kind": deterministic_item["key_kind"],
                "key_text": deterministic_item["key_text"],
                "screen_source": deterministic_item["screen_source"],
                "screen_snapshot_age_ms": deterministic_item["screen_snapshot_age_ms"],
                "contains_newline": deterministic_item["contains_newline"],
                "contains_escape": deterministic_item["contains_escape"],
                "is_probable_paste": deterministic_item["is_probable_paste"],
                "is_probable_command": deterministic_item["is_probable_command"],
                "expected_text_sig": deterministic_item["expected_text_sig"],
                "expected_visual_sig": deterministic_item["expected_visual_sig"],
                "summary": (
                    f"{deterministic_item['screen_sig'][:48]} "
                    f"[{deterministic_item['screen_source'] or 'unknown'}] -> "
                    f"{deterministic_item['key_text'] or deterministic_item['key_kind']}"
                ),
            })

    # Finaliza decoder e gera snapshot final
    if session_end is None:
        engine.finish(seq_global=last_out_seq_global, direction="out", session_id=clean_sid)
    final_snapshot = snapshot_from_engine(engine)

    # Adiciona decoder warnings ao resultado
    decoder_warnings = []
    for w in engine.decoder.warnings:
        decoder_warnings.append(w)

    sorted_timeline = sorted(timeline, key=lambda item: (int(item.get("seq_global") or 0), int(item.get("ts_ms") or 0)))
    reference_payload = build_reference_payload(
        initial_snapshot=_compact_cached(initial_snapshot),
        events=event_items,
        checkpoints=checkpoints,
        final_snapshot=_compact_cached(final_snapshot),
    )
    playback_meta = {
        # Totais de bytes decodificados reais da sessão inteira —
        # event_items contém apenas a janela materializada (X6).
        "total_bytes_in": total_bytes_in_actual,
        "total_bytes_out": total_bytes_out_actual,
        "event_count": total_bytes_events,
        "deterministic_event_count": len(deterministic_events),
        "available_input_modes": ["raw", "deterministic"],
        "comparison_modes": ["visual", "text", "semantic", "hybrid"],
        "default_comparison_mode": "visual",
        "legacy_comparison_mode": "hybrid",
        "engine_version": engine.engine_version,
    }
    playback_items = []
    for ev in event_items:
        item = {
            "event_id": ev.get("event_id"),
            "seq": ev["seq_global"],
            "seq_global": ev["seq_global"],
            "direction": ev["direction"],
            "bytes": ev["n_bytes"],
            "timestamp_ms": ev["ts_ms"],
        }
        if ev["direction"] == "out":
            if ev.get("diff"):
                item["diff"] = ev["diff"]
            if ev.get("text_sig"):
                item["text_sig"] = ev["text_sig"]
            if ev.get("visual_sig"):
                item["visual_sig"] = ev["visual_sig"]
            if ev.get("is_checkpoint") and ev.get("snapshot_compact"):
                item["snapshot_compact"] = ev["snapshot_compact"]
                item["render_snapshot"] = ev.get("render_snapshot", ev["snapshot_compact"])
                item["checkpoint"] = True
        playback_items.append(item)
    timeline_view = ReferenceView(
        # A timeline inclui TAMBÉM os deterministic_input (det-N): sem eles
        # nos refs, o filtro "Determinístico" da UI nunca listava nada no
        # contrato de referências (X6). O playback continua só com "bytes".
        event_refs=[
            str(item.get("event_id") or item.get("id") or item.get("seq_global") or idx)
            for idx, item in enumerate(sorted_timeline)
        ],
        checkpoint_refs=reference_payload["timeline"]["checkpoint_refs"],
        items=sorted_timeline,
    )
    playback_view = PlaybackReferenceView(
        event_refs=reference_payload["playback"]["event_refs"],
        checkpoint_refs=reference_payload["playback"]["checkpoint_refs"],
        items=playback_items,
        meta=playback_meta,
    )

    return {
        "error": None,
        "session_id": clean_sid,
        "session_start": session_start,
        "session_end": session_end,
        "geometry": geometry,
        "engine_version": engine.engine_version,
        "initial_snapshot": reference_payload["initial_snapshot"],
        "final_snapshot": reference_payload["final_snapshot"],
        "decoder_warnings": decoder_warnings,
        "checkpoints": reference_payload["checkpoints"],
        "events": reference_payload["events"],
        "deterministic_events": deterministic_events,
        "timeline": timeline_view,
        "timeline_items": sorted_timeline,
        "playback": playback_view,
        # Janela materializada (X6): truncated=True indica que há mais
        # eventos além do fim da fatia retornada.
        "window": {
            "offset": win_start,
            "limit": win_limit,
            "total_events": total_bytes_events,
            "truncated": win_end is not None and win_end < total_bytes_events,
            "explicit": explicit_window,
            # True quando o stream foi processado só até o fim da janela:
            # final_snapshot, checkpoints e canonical_signatures refletem
            # esse ponto, não o fim da sessão.
            "partial_state": win_end is not None and bytes_event_index < total_bytes_events,
            # Cache de estado em disco (X6): hit=True indica retomada a
            # partir de estado persistido — checkpoints anteriores ao ponto
            # de retomada não são regerados.
            "state_cache": cache_info,
            # Índice de sessão em disco (X6): hit=True indica que a janela
            # foi materializada por seek (sem reparsear os audit-*.jsonl)
            # e os totais vieram do índice.
            "session_index": index_info,
        },
        # True quando o teto MAX_REPLAY_CHECKPOINTS interrompeu novos
        # checkpoints (sessões com redesenho constante de tela).
        "checkpoints_capped": len(checkpoints) >= MAX_REPLAY_CHECKPOINTS,
        # Assinaturas canônicas persistidas para o gateway
        "canonical_signatures": {
            "text_sig": final_snapshot.get("text_sig", ""),
            "visual_sig": final_snapshot.get("visual_sig", ""),
            "semantic_sig": final_snapshot.get("semantic_sig", ""),
            "engine_version": engine.engine_version,
        },
    }

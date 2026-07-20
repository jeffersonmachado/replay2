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
import re
from pathlib import Path

from dakota_gateway.terminal_config import is_supported_encoding, normalize_encoding, validate_terminal_geometry
from dakota_terminal import (
    TerminalEngine,
    snapshot_from_engine,
    encode_render_snapshot,
    create_diff,
    apply_diff,
    validate_diff,
    compare_signatures,
)


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
    return encode_render_snapshot(snapshot)


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
) -> dict:
    """
    Prepara dados de replay de uma sessao.
    Retorna eventos bytes (in/out) estruturados para visualizacao
    e replay da interacao capturada.
    """
    clean_dir = str(log_dir or "").strip()
    clean_sid = str(session_id or "").strip()

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
    events: list[dict] = []

    for file_path in files:
        try:
            lines = file_path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError as exc:
            return {"error": f"erro ao ler arquivo: {exc}", "events": [], "timeline": _empty_reference_view(), "playback": None}

        for line in lines:
            line = line.strip()
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
    session_start = None
    session_end = None
    for ev in events:
        ev_type = str(ev.get("type") or "").strip()
        if ev_type == "session_start" and session_start is None:
            session_start = ev
        elif ev_type == "session_end" and session_end is None:
            session_end = ev

    geometry = _detect_geometry(events, session_start)
    detected_encoding = _detect_encoding(events, session_start)

    # ── TerminalEngine Python: fonte oficial ────────────────────────────
    engine = TerminalEngine(
        rows=geometry["rows"],
        cols=geometry["cols"],
        term=geometry.get("term", "xterm"),
        encoding=detected_encoding,
        session_id=clean_sid,
    )

    event_items = []
    deterministic_events = []
    timeline = []
    decoders: dict[str, codecs.IncrementalDecoder] = {}

    # ── Snapshots, diffs, checkpoints ───────────────────────────────────
    initial_snapshot = snapshot_from_engine(engine)
    checkpoints: list[dict] = []
    current_snapshot = initial_snapshot
    last_out_snapshot = initial_snapshot
    last_snapshot = initial_snapshot
    last_out_seq_global = 0  # seq_global do ultimo evento OUT
    out_event_count = 0
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
    _attach_render_snapshot(initial_checkpoint, initial_snapshot)
    checkpoints.append(initial_checkpoint)

    for ev in events:
        ev_type = str(ev.get("type") or "").strip()

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
            _attach_render_snapshot(checkpoint, current_snapshot)
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
            _attach_render_snapshot(checkpoint, final_snapshot)
            checkpoints.append(checkpoint)
        elif ev_type == "bytes":
            data_b64 = str(ev.get("data_b64") or "").strip()
            direction = str(ev.get("dir") or "").strip()
            declared_n = int(ev["n"]) if ev.get("n") is not None else None
            seq_global = int(ev.get("seq_global") or 0)
            ts_ms = int(ev.get("ts_ms") or 0)

            data_raw, integrity_warning = _decode_event_bytes(data_b64, declared_n)
            actual_n = len(data_raw)

            generate_checkpoint = False

            # Alimenta TerminalEngine com bytes OUT
            if direction == "out" and data_raw:
                engine.feed_bytes(data_raw, seq_global=seq_global, direction=direction, session_id=clean_sid)
                out_event_count += 1
                current_snapshot = snapshot_from_engine(engine)

                # Detecta RIS e clear-screen para gerar checkpoint
                has_ris = b'\x1bc' in data_raw
                has_clear = b'\x1b[2J' in data_raw

                # Detecta resize que ocorreu (engine ja processou internamente)
                has_resize = (current_snapshot["rows"] != last_snapshot["rows"] or
                              current_snapshot["cols"] != last_snapshot["cols"])

                # Gera checkpoint conforme politica
                time_since_checkpoint = ts_ms - last_checkpoint_time_ms
                generate_checkpoint = (
                    out_event_count == 1  # primeiro evento sempre gera checkpoint
                    or out_event_count % CHECKPOINT_EVENT_INTERVAL == 0
                    or time_since_checkpoint >= CHECKPOINT_TIME_INTERVAL_MS
                    or has_ris
                    or has_resize
                    or has_clear
                )

                if generate_checkpoint:
                    last_checkpoint_time_ms = ts_ms
                    reason = (
                        "ris" if has_ris else
                        "resize" if has_resize else
                        "clear_screen" if has_clear else
                        "session_start" if out_event_count == 1 else
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
                    _attach_render_snapshot(checkpoint, current_snapshot)
                    checkpoints.append(checkpoint)

                # Gera diff entre ultimo snapshot OUT e atual (com identidade sequencial)
                diff = create_diff(
                    last_out_snapshot, current_snapshot,
                    base_seq=last_out_seq_global,
                    seq=seq_global,
                    ts_ms=ts_ms,
                )
                last_out_snapshot = current_snapshot
                last_out_seq_global = seq_global
                last_snapshot = current_snapshot
            else:
                if data_raw:
                    # IN: nao altera tela, mas registra
                    pass
                current_snapshot = last_snapshot
                diff = None

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
                    _attach_render_snapshot(event_item, current_snapshot)
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
                    _attach_render_snapshot(timeline_item, current_snapshot)
                    timeline_item["checkpoint_seq"] = True
            if integrity_warning:
                timeline_item["integrity_warning"] = integrity_warning
            timeline.append(timeline_item)
        elif ev_type == "deterministic_input":
            seq_global = int(ev.get("seq_global") or 0)
            ts_ms = int(ev.get("ts_ms") or 0)
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
        initial_snapshot=_render_snapshot_payload(initial_snapshot),
        events=event_items,
        checkpoints=checkpoints,
        final_snapshot=_render_snapshot_payload(final_snapshot),
    )
    playback_meta = {
        "total_bytes_in": sum(e["n_bytes"] for e in event_items if e["direction"] == "in"),
        "total_bytes_out": sum(e["n_bytes"] for e in event_items if e["direction"] == "out"),
        "event_count": len(event_items),
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
        event_refs=reference_payload["timeline"]["event_refs"],
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
        # Assinaturas canônicas persistidas para o gateway
        "canonical_signatures": {
            "text_sig": final_snapshot.get("text_sig", ""),
            "visual_sig": final_snapshot.get("visual_sig", ""),
            "semantic_sig": final_snapshot.get("semantic_sig", ""),
            "engine_version": engine.engine_version,
        },
    }

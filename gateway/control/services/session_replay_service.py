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
    encode_snapshot_compact,
    create_diff,
    apply_diff,
    validate_diff,
)


def _event_direction(ev: dict) -> str:
    """Retorna a direcao do evento: 'in', 'out' ou '' (desconhecida)."""
    return str(ev.get("direction") or ev.get("dir") or "").strip()


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
            "replay_events": [],
            "playback": None,
        }

    log_path = Path(clean_dir)
    if not log_path.exists():
        return {
            "error": {"code": "log_dir_not_found", "message": f"diretorio de log nao encontrado: {clean_dir}"},
            "replay_events": [],
            "playback": None,
        }

    files = sorted(log_path.glob("audit-*.jsonl"))

    if not files:
        return {
            "error": {"code": "no_audit_files", "message": f"nenhum arquivo audit-*.jsonl encontrado em: {clean_dir}"},
            "replay_events": [],
            "playback": None,
        }
    events: list[dict] = []

    for file_path in files:
        try:
            lines = file_path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError as exc:
            return {"error": f"erro ao ler arquivo: {exc}", "replay_events": [], "playback": None}

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
            "replay_events": [],
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
    )

    replay_events = []
    deterministic_events = []
    timeline = []
    decoders: dict[str, codecs.IncrementalDecoder] = {}

    # ── Snapshots, diffs, checkpoints ───────────────────────────────────
    initial_snapshot = snapshot_from_engine(engine)
    checkpoints: list[dict] = []
    current_snapshot = initial_snapshot
    last_out_snapshot = initial_snapshot
    last_snapshot = initial_snapshot
    out_event_count = 0
    CHECKPOINT_INTERVAL = 250   # snapshot completo a cada N eventos OUT

    # Adiciona checkpoint inicial
    checkpoints.append({
        "seq_global": 0,
        "snapshot": encode_snapshot_compact(initial_snapshot),
    })

    for ev in events:
        ev_type = str(ev.get("type") or "").strip()

        if ev_type == "session_start":
            session_start = ev
        elif ev_type == "session_end":
            session_end = ev
            engine.finish()
            final_snapshot = snapshot_from_engine(engine)
            checkpoints.append({
                "seq_global": int(ev.get("seq_global") or 0),
                "snapshot": encode_snapshot_compact(final_snapshot),
            })
        elif ev_type == "bytes":
            data_b64 = str(ev.get("data_b64") or "").strip()
            direction = str(ev.get("dir") or "").strip()
            declared_n = int(ev["n"]) if ev.get("n") is not None else None
            seq_global = int(ev.get("seq_global") or 0)
            ts_ms = int(ev.get("ts_ms") or 0)

            data_raw, integrity_warning = _decode_event_bytes(data_b64, declared_n)
            actual_n = len(data_raw)

            # Alimenta TerminalEngine com bytes OUT
            if direction == "out" and data_raw:
                engine.feed_bytes(data_raw)
                out_event_count += 1
                current_snapshot = snapshot_from_engine(engine)

                # Gera checkpoint a cada N eventos OUT
                if out_event_count % CHECKPOINT_INTERVAL == 0:
                    checkpoints.append({
                        "seq_global": seq_global,
                        "snapshot": encode_snapshot_compact(current_snapshot),
                    })

                # Gera diff entre ultimo snapshot OUT e atual
                diff = create_diff(last_out_snapshot, current_snapshot)
                last_out_snapshot = current_snapshot
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

            replay_item = {
                "seq_global": seq_global, "ts_ms": ts_ms, "type": "bytes",
                "direction": direction, "n_bytes": actual_n,
                "declared_bytes": declared_n, "actual_bytes": actual_n,
                "data_decoded": data_str, "data_b64": data_b64,
            }
            if integrity_warning:
                replay_item["integrity_warning"] = integrity_warning
            replay_events.append(replay_item)

            timeline_item = {
                "seq_global": seq_global, "ts_ms": ts_ms, "timestamp_ms": ts_ms,
                "type": "bytes", "direction": direction, "n_bytes": actual_n,
                "declared_bytes": declared_n, "actual_bytes": actual_n,
                "data_b64": data_b64, "data_decoded": data_str,
                "summary": data_str[:400],
            }
            if direction == "out" and diff:
                timeline_item["snapshot"] = current_snapshot
                timeline_item["snapshot_compact"] = encode_snapshot_compact(current_snapshot)
                timeline_item["diff"] = diff
                timeline_item["text_sig"] = current_snapshot.get("text_sig", "")
                timeline_item["visual_sig"] = current_snapshot.get("visual_sig", "")
                timeline_item["engine_version"] = engine.engine_version
            if integrity_warning:
                timeline_item["integrity_warning"] = integrity_warning
            timeline.append(timeline_item)
        elif ev_type == "deterministic_input":
            seq_global = int(ev.get("seq_global") or 0)
            ts_ms = int(ev.get("ts_ms") or 0)
            deterministic_item = {
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
            }
            deterministic_events.append(deterministic_item)
            timeline.append({
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

    # ── Playback script ─────────────────────────────────────────────────
    playback_script = []
    for ev in replay_events:
        playback_script.append({
            "seq": ev["seq_global"],
            "seq_global": ev["seq_global"],
            "direction": ev["direction"],
            "bytes": ev["n_bytes"],
            "data_b64": ev.get("data_b64", ""),
            "content": ev["data_decoded"],
            "timestamp_ms": ev["ts_ms"],
        })

    return {
        "error": None,
        "session_id": clean_sid,
        "session_start": session_start,
        "session_end": session_end,
        "geometry": geometry,
        "engine_version": engine.engine_version,
        "initial_snapshot": encode_snapshot_compact(initial_snapshot),
        "final_snapshot": encode_snapshot_compact(snapshot_from_engine(engine)),
        "checkpoints": checkpoints,
        "replay_events": replay_events,
        "deterministic_events": deterministic_events,
        "timeline": sorted(timeline, key=lambda item: (int(item.get("seq_global") or 0), int(item.get("ts_ms") or 0))),
        "playback": {
            "events": playback_script,
            "total_bytes_in": sum(e["n_bytes"] for e in replay_events if e["direction"] == "in"),
            "total_bytes_out": sum(e["n_bytes"] for e in replay_events if e["direction"] == "out"),
            "event_count": len(replay_events),
            "deterministic_event_count": len(deterministic_events),
            "available_input_modes": ["raw", "deterministic"],
            "comparison_modes": ["visual", "text", "semantic", "hybrid"],
        },
    }

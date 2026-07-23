from __future__ import annotations

import json
from dataclasses import asdict


# Ordem de campos do payload legado v1 (mantida byte a byte para verificar
# capturas antigas; eventos novos usam o payload v2).
_KEY_ORDER = [
    "v",
    "seq_global",
    "ts_ms",
    "type",
    "actor",
    "session_id",
    "seq_session",
    "capture_id",
    "capture_session_uuid",
    "dir",
    "n",
    "data_b64",
    "sig",
    "norm_sha256",
    "norm_len",
    "screen_sig",
    "screen_sample",
    "key_b64",
    "key_text",
    "key_kind",
    "input_len",
    "contains_newline",
    "contains_escape",
    "is_probable_paste",
    "is_probable_command",
    "logical_parts",
    "screen_raw_b64",
    "screen_source",
    "screen_snapshot_ts_ms",
    "screen_snapshot_age_ms",
    "source",
    "prev_hash",
    "rows",
    "cols",
    "term",
    "encoding",
    "geometry_source",
]

# Campos de integridade nunca entram no payload canônico.
_EXCLUDED_FROM_PAYLOAD = ("hash", "hmac")


def canonical_string(ev) -> str:
    """
    Payload determinístico legado (v1) usado para hash-chain e HMAC.

    Mantido byte a byte para que o verifier continue validando capturas
    antigas; cobre apenas um subconjunto dos campos e não é injetivo
    (valores com '\n' colidem). Eventos novos usam canonical_string_v2.
    Campos ausentes são codificados como string vazia.
    """
    d = asdict(ev)
    parts = []
    for k in _KEY_ORDER:
        v = d.get(k, "")
        if v is None:
            v = ""
        parts.append(f"{k}={v}\n")
    return "".join(parts)


def canonical_string_v2(ev) -> str:
    """
    Payload determinístico v2: evento completo (menos hash/hmac) serializado
    em JSON com chaves ordenadas.

    Cobre todos os campos de AuditEvent (via_gateway, entry_mode,
    gateway_session_id, source_*, text_sig/visual_sig/semantic_sig,
    expected_*, timestamp_ms etc.) e é injetivo, pois os valores são
    serializados em JSON (sem ambiguidade com quebras de linha).
    """
    d = asdict(ev)
    payload = {k: v for k, v in d.items() if k not in _EXCLUDED_FROM_PAYLOAD}
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def payload_for_event(ev) -> str:
    """Seleciona o payload canônico conforme a versão registrada no evento."""
    if getattr(ev, "v", "v1") == "v2":
        return canonical_string_v2(ev)
    return canonical_string(ev)

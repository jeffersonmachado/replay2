"""Materializa trilha auditável de replay a partir de captura real + dados
sintéticos (fluxo "replay sintético em 1 clique" da UI).

Dada uma captura real (``audit-*.jsonl``) e uma lista de substituições
``(original → sintético)`` na ordem em que aparecem na captura, gera uma
trilha derivada que:

- descarta o ruído de banner pré-sessão (eventos antes do primeiro
  ``deterministic_input`` com tela real — ex.: registro TeraTerm
  ``NOME = .../WINDOWS = ...`` que só aparece na 1ª conexão do terminal e
  poluiria o prompt do menu no replay);
- substitui os inputs mapeados pelos valores sintéticos — campos com
  máscara digitados dígito a dígito (ex.: CPF ``@R 999.999.999-99``) são
  trocados dígito a dígito, preservando 1 evento por tecla;
- renumera ``seq_global`` (o verifier rejeita gaps) e re-assina a cadeia
  (hash-chain + HMAC), então a trilha passa no ``verify`` e pode ser
  executada por um run real em modo determinístico.
"""
from __future__ import annotations

import base64
import json
from pathlib import Path

from ..audit_writer import b64
from ..canonical import payload_for_event
from ..crypto import hmac_sha256_hex, sha256_hex
from ..schema import AuditEvent

# Assinatura de tela "vazia" — marca o banner pré-sessão (registro de
# terminal) que não faz parte do fluxo da aplicação.
_EMPTY_SIGS = ("", "L=0;W=0")


def det_key(ev: dict) -> str:
    """Conteúdo digitado de um evento ``deterministic_input`` (key_b64 canônico)."""
    if ev.get("key_b64"):
        try:
            return base64.b64decode(str(ev["key_b64"])).decode("utf-8", "replace")
        except Exception:
            return ""
    return str(ev.get("key_text") or "")


def _drop_pre_session_banner(events: list[dict]) -> tuple[list[dict], int]:
    """Remove eventos anteriores ao primeiro input com tela real.

    Mantém sempre ``session_start`` (configuração da sessão). Retorna
    ``(eventos, removidos)``.
    """
    first_real = None
    for ev in events:
        if ev.get("type") == "deterministic_input" and str(ev.get("screen_sig") or "") not in _EMPTY_SIGS:
            first_real = int(ev.get("seq_global") or 0)
            break
    if first_real is None or first_real <= 1:
        return events, 0
    keep: list[dict] = []
    dropped = 0
    for ev in events:
        seq = int(ev.get("seq_global") or 0)
        if seq < first_real and ev.get("type") != "session_start":
            dropped += 1
            continue
        keep.append(ev)
    return keep, dropped


def _apply_substitutions(
    events: list[dict],
    substitutions: list[tuple[str, str]],
) -> tuple[list[str], list[str]]:
    """Aplica substituições em ordem; retorna (avisos, log de aplicações)."""
    warnings: list[str] = []
    applied: list[str] = []
    det_idx = [i for i, ev in enumerate(events) if ev.get("type") == "deterministic_input"]
    cursor = -1  # posição (em `events`) da última substituição aplicada

    for original, value in substitutions:
        if not original:
            continue
        # Campo com máscara digitado dígito a dígito: sequência de eventos
        # de 1 caractere cuja concatenação é o valor original.
        if len(original) > 1 and original.isdigit() and value.isdigit() and len(value) == len(original):
            run: list[int] = []
            found: list[int] | None = None
            for i in det_idx:
                if i <= cursor:
                    continue
                key = det_key(events[i])
                if len(key) == 1 and key.isdigit():
                    run.append(i)
                    digits = "".join(det_key(events[j]) for j in run)
                    if digits == original:
                        found = list(run)
                        break
                    if not original.startswith(digits):
                        run = []
                else:
                    run = []
            if found:
                for pos, digit in zip(found, value):
                    events[pos]["key_b64"] = b64(digit.encode("utf-8"))
                    events[pos]["key_text"] = digit
                cursor = found[-1]
                applied.append(f"{original}->{value} (dígitos, seq {events[found[0]].get('seq_global')}..{events[found[-1]].get('seq_global')})")
                continue
            warnings.append(f"sequência de dígitos {original!r} não encontrada; substituição pulada")
            continue
        # Substituição simples: primeiro evento igual ao original após o cursor.
        pos = next(
            (i for i in det_idx if i > cursor and det_key(events[i]) == original),
            None,
        )
        if pos is None:
            warnings.append(f"input {original!r} não encontrado após seq do cursor; substituição pulada")
            continue
        events[pos]["key_b64"] = b64(value.encode("utf-8"))
        events[pos]["key_text"] = value
        cursor = pos
        applied.append(f"{original}->{value} (seq {events[pos].get('seq_global')})")

    return warnings, applied


def build_synthetic_trail(
    capture_jsonl: str | Path,
    substitutions: list[tuple[str, str]],
    out_dir: str | Path,
    *,
    hmac_key: bytes,
    drop_banner: bool = True,
) -> dict:
    """Gera trilha auditável derivada da captura com dados sintéticos.

    ``substitutions``: pares ``(valor_original, valor_sintético)`` na ordem
    em que os inputs aparecem na captura. Retorna dict com ``out``,
    ``events``, ``dropped_banner``, ``applied`` e ``warnings``.
    """
    capture_path = Path(capture_jsonl)
    events = [
        json.loads(line)
        for line in capture_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    dropped = 0
    if drop_banner:
        events, dropped = _drop_pre_session_banner(events)

    warnings, applied = _apply_substitutions(events, list(substitutions))

    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    out_file = out_path / capture_path.name

    prev_hash = ""
    with open(out_file, "w", encoding="utf-8") as f:
        for new_seq, ev in enumerate(events, 1):
            ev["seq_global"] = new_seq
            schema_ev = AuditEvent(**{
                k: v for k, v in ev.items() if k in AuditEvent.__dataclass_fields__
            })
            schema_ev.prev_hash = prev_hash
            payload = payload_for_event(schema_ev).encode("utf-8")
            ev["prev_hash"] = prev_hash
            ev["hash"] = sha256_hex(payload)
            ev["hmac"] = hmac_sha256_hex(hmac_key, payload)
            prev_hash = ev["hash"]
            f.write(json.dumps(ev, ensure_ascii=False) + "\n")

    return {
        "out": str(out_file),
        "events": len(events),
        "dropped_banner": dropped,
        "applied": applied,
        "warnings": warnings,
    }

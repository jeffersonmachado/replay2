"""Preferências e aprendizados da síntese de dados, persistidos por captura.

Antes deste módulo, parte dos "aprendizados" da síntese vivia só no browser
(localStorage) ou era recomputada a cada replay:

- ``skip_fields`` (campos mantidos com o valor original da captura) ia apenas
  no body do POST e no localStorage — agora persiste na coluna
  ``capture_sessions.synthetic_prefs`` (JSON);
- o ``entry_point`` (preâmbulo login/shell detectado por
  ``detect_session_entry``) era recomputado a cada ``build_synthetic_trail`` —
  agora é cacheado nas prefs, versionado pela ``VERSION`` do código (caches
  de versões anteriores invalidam sozinhos);
- a knowledge base do fonte (``source_entities``/``screen_entity_bindings``)
  não registrava de qual diretório foi gerada — a tabela ``source_kb_meta``
  guarda o ``source_dir`` resolvido e o fingerprint dos fontes (nº de
  ``*.prg`` + mtime máximo), e a síntese avisa quando a KB está desatualizada
  ou veio de outro diretório;
- feedback loop: as falhas de validação das runs sintéticas anteriores da
  captura viram sugestão de campo para o ``skip_fields`` do próximo replay.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from dakota_gateway.state_db import now_ms

# Padrões de rejeição de validação do ERP na tela observada (a falha estrutural
# é screen_divergence, mas o conteúdo mostra o motivo funcional).
_REJECTION_RE = re.compile(
    r"difere do item|n[aã]o confere|inv[áa]lid|n[aã]o cadastrad|n[aã]o existe",
    re.IGNORECASE,
)
# Aviso benigno recorrente do ambiente (programa de faturamento ausente) —
# não é rejeição de dado sintético.
_BENIGN_RE = re.compile(r"arq\d+\.pcp nao encontrado", re.IGNORECASE)


def current_version() -> str:
    """Versão do código (arquivo VERSION na raiz do projeto); '' se ilegível."""
    try:
        return (Path(__file__).resolve().parents[3] / "VERSION").read_text(
            encoding="utf-8"
        ).strip()
    except OSError:
        return ""


# ---------------------------------------------------------------------------
# Preferências por captura (capture_sessions.synthetic_prefs)
# ---------------------------------------------------------------------------


def load_prefs(con, capture_id: int) -> dict:
    """Preferências da síntese da captura; ``{}`` quando ausente/corrompida."""
    row = con.execute(
        "SELECT synthetic_prefs FROM capture_sessions WHERE id=?",
        (int(capture_id),),
    ).fetchone()
    if not row or not row["synthetic_prefs"]:
        return {}
    try:
        data = json.loads(row["synthetic_prefs"])
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_prefs(con, capture_id: int, prefs: dict) -> dict:
    """Mescla ``prefs`` sobre o armazenado, grava e retorna o resultado."""
    merged = load_prefs(con, capture_id)
    merged.update(prefs or {})
    merged["updated_at_ms"] = now_ms()
    con.execute(
        "UPDATE capture_sessions SET synthetic_prefs=? WHERE id=?",
        (json.dumps(merged, ensure_ascii=False), int(capture_id)),
    )
    return merged


def _normalize_fields(fields) -> list[str]:
    """Lista de campos sem brancos/vazios, deduplicada (case-insensitive)."""
    out: list[str] = []
    seen: set[str] = set()
    for field in fields or []:
        name = str(field or "").strip()
        if name and name.lower() not in seen:
            seen.add(name.lower())
            out.append(name)
    return out


def resolve_skip_fields(
    prefs: dict,
    explicit,
    auto_fields,
) -> dict[str, Any]:
    """Resolve o skip_fields efetivo de uma síntese/replay.

    - ``explicit`` não-None (o body CONTÉM a chave ``skip_fields``, mesmo
      vazia) = seleção explícita do usuário: o armazenado é substituído
      (``persist=True``) e o efetivo é a união com os campos-âncora
      auto-detectados;
    - ``explicit`` None (chave omitida — chamadas CLI): o armazenado é usado
      como está e não é alterado.

    Retorna ``{"effective", "stored", "persist"}`` — ``effective`` ordenado
    em minúsculas (insumo do ``_extract_substitutions``).
    """
    auto = _normalize_fields(auto_fields)
    if explicit is not None:
        stored = _normalize_fields(explicit if isinstance(explicit, list) else [])
        persist = True
    else:
        stored = _normalize_fields((prefs or {}).get("skip_fields"))
        persist = False
    effective = sorted({f.lower() for f in auto + stored})
    return {"effective": effective, "stored": stored, "persist": persist}


def entry_point_from_prefs(prefs: dict) -> tuple[bool, dict | None]:
    """Cache do entry_point: ``(válido, entry)``.

    Só é válido quando gravado pela MESMA VERSION do código — caches de
    versões anteriores invalidam sozinhos (a detecção evolui entre releases,
    ex.: a correção do cwd no preâmbulo). ``entry`` pode ser ``None`` (a
    detecção rodou e não achou preâmbulo — também é cacheada).
    """
    prefs = prefs or {}
    if "entry_point" not in prefs:
        return False, None
    if str(prefs.get("entry_point_version") or "") != current_version():
        return False, None
    entry = prefs.get("entry_point")
    return True, entry if isinstance(entry, dict) else None


# ---------------------------------------------------------------------------
# Metadados da knowledge base do fonte (source_kb_meta)
# ---------------------------------------------------------------------------


def fingerprint_source_dir(source_dir: str) -> dict[str, int]:
    """Fingerprint dos fontes Recital: nº de ``*.prg`` e mtime máximo (ms)."""
    file_count = 0
    max_mtime_ms = 0
    stack = [str(source_dir)]
    while stack:
        base = stack.pop()
        try:
            with os.scandir(base) as it:
                for entry in it:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(entry.path)
                        elif entry.name.lower().endswith(".prg"):
                            file_count += 1
                            mtime_ms = int(entry.stat().st_mtime * 1000)
                            if mtime_ms > max_mtime_ms:
                                max_mtime_ms = mtime_ms
                    except OSError:
                        continue
        except OSError:
            continue
    return {"file_count": file_count, "max_mtime_ms": max_mtime_ms}


def load_kb_meta(con) -> dict | None:
    """Metadados da KB gravados pelo analyze-source; ``None`` se nunca rodou."""
    try:
        row = con.execute(
            "SELECT source_dir, analyzed_at_ms, file_count, max_mtime_ms"
            " FROM source_kb_meta WHERE id=1"
        ).fetchone()
    except Exception:
        return None
    return dict(row) if row else None


def save_kb_meta(con, source_dir: str) -> dict:
    """Grava o stamp da KB: diretório resolvido + fingerprint dos ``*.prg``."""
    resolved = str(Path(str(source_dir)).resolve())
    fp = fingerprint_source_dir(resolved)
    meta = {
        "source_dir": resolved,
        "analyzed_at_ms": now_ms(),
        "file_count": fp["file_count"],
        "max_mtime_ms": fp["max_mtime_ms"],
    }
    con.execute(
        "INSERT INTO source_kb_meta(id, source_dir, analyzed_at_ms, file_count, max_mtime_ms)"
        " VALUES(1, ?, ?, ?, ?)"
        " ON CONFLICT(id) DO UPDATE SET source_dir=excluded.source_dir,"
        " analyzed_at_ms=excluded.analyzed_at_ms, file_count=excluded.file_count,"
        " max_mtime_ms=excluded.max_mtime_ms",
        (meta["source_dir"], meta["analyzed_at_ms"], meta["file_count"], meta["max_mtime_ms"]),
    )
    return meta


def kb_status(con, source_dir: str) -> dict[str, Any]:
    """Situação da KB frente ao ``source_dir`` da síntese (só aviso, nunca bloqueia).

    ``stale=True`` quando a KB foi gerada de outro diretório ou os fontes
    mudaram desde a análise (fingerprint diferente). ``warning`` traz a
    mensagem pronta para o array ``warnings`` das respostas.
    """
    resolved = str(Path(str(source_dir)).resolve()) if str(source_dir or "").strip() else ""
    meta = load_kb_meta(con)
    status: dict[str, Any] = {
        "source_dir": resolved,
        "stored_source_dir": str(meta.get("source_dir") or "") if meta else "",
        "stale": False,
        "analyzed_at_ms": int(meta["analyzed_at_ms"]) if meta and meta.get("analyzed_at_ms") else None,
        "warning": "",
    }
    if not meta or not resolved:
        return status
    stored_dir = status["stored_source_dir"]
    if stored_dir and stored_dir != resolved:
        status["stale"] = True
        status["warning"] = (
            f"base de conhecimento gerada de outro diretório ({stored_dir}) — "
            "rode a análise do fonte para atualizar"
        )
        return status
    fp = fingerprint_source_dir(resolved)
    if (fp["file_count"] != int(meta.get("file_count") or 0)
            or fp["max_mtime_ms"] != int(meta.get("max_mtime_ms") or 0)):
        status["stale"] = True
        status["warning"] = (
            "base de conhecimento desatualizada (os fontes mudaram desde a "
            "análise) — rode a análise do fonte para atualizar"
        )
    return status


# ---------------------------------------------------------------------------
# Feedback loop: falhas das runs sintéticas → sugestão de skip_fields
# ---------------------------------------------------------------------------


def _failure_text(failure: dict) -> str:
    """Texto da falha onde a rejeição de validação aparece (tela observada)."""
    parts = [str(failure.get("message") or ""), str(failure.get("observed_value") or "")]
    try:
        evidence = json.loads(failure.get("evidence_json") or "{}")
    except (TypeError, ValueError):
        evidence = {}
    if isinstance(evidence, dict):
        parts.append(str(evidence.get("observed_screen") or ""))
    return "\n".join(parts)


def _is_validation_rejection(text: str) -> bool:
    """A tela observada mostra rejeição de validação do ERP (exclui o benigno)."""
    clean = _BENIGN_RE.sub("", text)
    return bool(_REJECTION_RE.search(clean))


def _substitution_before_from_applied(applied, failure_seq: int) -> str:
    """Campo da substituição mais próxima ANTES do seq da falha.

    ``applied`` é a lista estruturada gravada no ``de-para.json`` da trilha
    (seqs já nas coordenadas da trilha renumerada).
    """
    best_field = ""
    best_seq = -1
    for rec in applied or []:
        if not isinstance(rec, dict):
            continue
        field = str(rec.get("field") or "").strip()
        if not field:
            continue
        try:
            seq_end = int(rec.get("seq_end") or rec.get("seq_start") or 0)
        except (TypeError, ValueError):
            continue
        if 0 < seq_end < failure_seq and seq_end > best_seq:
            best_seq = seq_end
            best_field = field
    return best_field


def _trail_dir_of_run(log_dir: str) -> Path | None:
    base = Path(str(log_dir or "").strip())
    return base if base.is_dir() else None


def _substitution_before_from_trail(trail_dir: Path, depara: dict, failure_seq: int) -> str:
    """Fallback para trilhas antigas (sem ``applied`` estruturado): casa o
    valor sintético digitado nos eventos da trilha antes do seq da falha."""
    by_value: dict[str, str] = {}  # valor sintético → campo
    for screen in depara.get("screens") or []:
        for field in screen.get("fields") or []:
            name = str(field.get("field") or "").strip()
            synthetic = str(field.get("synthetic") or "")
            original = str(field.get("original") or "")
            if name and synthetic and synthetic != original and not field.get("kept"):
                by_value.setdefault(synthetic, name)
    if not by_value:
        return ""
    best_field = ""
    best_seq = -1
    for path in sorted(trail_dir.glob("*.jsonl")):
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            if not line.strip():
                continue
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            if ev.get("type") != "deterministic_input":
                continue
            try:
                seq = int(ev.get("seq_global") or 0)
            except (TypeError, ValueError):
                continue
            if not (0 < seq < failure_seq) or seq <= best_seq:
                continue
            key = ""
            if ev.get("key_b64"):
                import base64
                try:
                    key = base64.b64decode(str(ev["key_b64"])).decode("utf-8", "replace")
                except Exception:
                    key = ""
            elif ev.get("key_text"):
                key = str(ev.get("key_text") or "")
            if key in by_value:
                best_seq = seq
                best_field = by_value[key]
    return best_field


def _feedback_for_run_row(con, run_id: int, log_dir: str) -> list[dict]:
    """Sugestão de skip_fields para UMA run sintética (0 ou 1 item).

    Procura a falha de ``screen_divergence`` mais cedo cuja tela observada
    mostra rejeição de validação do ERP ("Codigo nao cadastrado", "não
    confere"...) e mapeia a substituição mais próxima ANTES do seq da falha
    — o campo cujo valor sintético provavelmente disparou a rejeição.
    """
    failures = con.execute(
        "SELECT seq_global, message, observed_value, evidence_json"
        " FROM replay_failures WHERE run_id=? AND failure_type='screen_divergence'"
        " ORDER BY seq_global ASC",
        (int(run_id),),
    ).fetchall()
    culprit = None
    for row in failures:
        failure = dict(row)
        text = _failure_text(failure)
        if _is_validation_rejection(text):
            culprit = failure
            break
    if culprit is None:
        return []
    try:
        failure_seq = int(culprit["seq_global"] or 0)
    except (TypeError, ValueError):
        return []

    trail_dir = _trail_dir_of_run(log_dir)
    field = ""
    if trail_dir is not None:
        manifest = trail_dir / "de-para.json"
        if manifest.exists():
            try:
                depara = json.loads(manifest.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                depara = {}
            field = _substitution_before_from_applied(depara.get("applied"), failure_seq)
            if not field:
                field = _substitution_before_from_trail(trail_dir, depara, failure_seq)
    if not field:
        return []
    message = str(culprit["message"] or "").strip()
    return [{
        "field": field,
        "run_id": int(run_id),
        "failure_seq": failure_seq,
        "message": message[:200],
    }]


def _synthetic_runs_of_capture(con, capture_id: int, *, limit: int = 10) -> list[dict]:
    """Runs sintéticas mais recentes da captura (params.synthetic + origem)."""
    rows = con.execute(
        "SELECT id, log_dir, params_json FROM replay_runs ORDER BY id DESC LIMIT 2000"
    ).fetchall()
    runs: list[dict] = []
    for row in rows:
        try:
            params = json.loads(row["params_json"] or "{}")
        except (TypeError, ValueError):
            continue
        if not isinstance(params, dict) or not params.get("synthetic"):
            continue
        try:
            source = int(params.get("source_capture_id") or 0)
        except (TypeError, ValueError):
            continue
        if source != int(capture_id):
            continue
        runs.append({"id": int(row["id"]), "log_dir": str(row["log_dir"] or "")})
        if len(runs) >= max(1, int(limit or 10)):
            break
    return runs


def feedback_for_capture(con, capture_id: int, *, limit: int = 10) -> list[dict]:
    """Sugestões de ``skip_fields`` a partir das runs sintéticas da captura.

    Varre as ``limit`` runs sintéticas mais recentes; para cada uma, a falha
    de validação mais cedo vira uma sugestão ``{field, run_id, failure_seq,
    message}`` — aceitar a sugestão (UI: "Manter campo X no próximo replay")
    adiciona o campo às prefs da captura.
    """
    suggestions: list[dict] = []
    for run in _synthetic_runs_of_capture(con, capture_id, limit=limit):
        suggestions.extend(_feedback_for_run_row(con, run["id"], run["log_dir"]))
    return suggestions


def feedback_for_run(con, run_id: int) -> list[dict]:
    """Sugestões para o detalhe de UMA run — vazio se a run não é sintética."""
    row = con.execute(
        "SELECT id, log_dir, params_json FROM replay_runs WHERE id=?",
        (int(run_id),),
    ).fetchone()
    if not row:
        return []
    try:
        params = json.loads(row["params_json"] or "{}")
    except (TypeError, ValueError):
        return []
    if not isinstance(params, dict) or not params.get("synthetic"):
        return []
    return _feedback_for_run_row(con, int(row["id"]), str(row["log_dir"] or ""))

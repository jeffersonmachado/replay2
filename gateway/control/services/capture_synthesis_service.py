"""Serviço capture-to-synthetic para sessões gravadas pela UI."""
from __future__ import annotations

import base64
import json
import re
from pathlib import Path
from typing import Any

from dakota_gateway.synthetic.journey_synthesizer import JourneySynthesizer
from dakota_gateway.synthetic.synthetic_trail import build_synthetic_trail
from dakota_gateway.source_analyzer.semantic_types import identifies_record

from control.services.capture_service import get_capture


def _slug(value: str, fallback: str = "capture") -> str:
    clean = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(value or "").strip()).strip("-._")
    return clean[:80] or fallback


def _find_capture_jsonl(log_dir: str) -> list[Path]:
    base = Path(str(log_dir or "").strip())
    if not base.exists() or not base.is_dir():
        return []
    audit_files = sorted(base.glob("audit-*.jsonl"))
    if audit_files:
        return audit_files
    return sorted(base.glob("*.jsonl"))


def _combine_jsonl(files: list[Path], destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8") as out:
        for path in files:
            with path.open("r", encoding="utf-8", errors="replace") as src:
                for line in src:
                    if line.strip():
                        out.write(line if line.endswith("\n") else line + "\n")
    return destination


def synthesize_capture(
    con,
    capture_id: int,
    *,
    source_dir: str,
    samples: int = 10,
    seed: int | None = None,
    name: str = "",
    out_dir: str = "",
    include_validation: bool = True,
    include_stress: bool = False,
    concurrency: int = 5,
) -> dict[str, Any]:
    """Transforma uma captura registrada em template + dataset + sessões sintéticas."""
    capture = get_capture(con, capture_id)
    if not capture:
        raise ValueError("captura não encontrada")

    source_path = Path(str(source_dir or "").strip())
    if not source_path.exists() or not source_path.is_dir():
        raise ValueError("source_dir inválido ou inexistente")

    log_dir = str(capture.get("log_dir") or "").strip()
    files = _find_capture_jsonl(log_dir)
    if not files:
        raise ValueError("nenhum arquivo .jsonl encontrado na captura")

    samples = max(1, min(int(samples or 10), 10000))
    concurrency = max(1, min(int(concurrency or 5), 500))
    run_name = _slug(name or f"capture-{capture_id}-synthetic")
    output_dir = Path(out_dir or Path(log_dir) / "synthetic" / run_name)
    output_dir.mkdir(parents=True, exist_ok=True)

    capture_jsonl = files[0] if len(files) == 1 else _combine_jsonl(files, output_dir / "capture_combined.jsonl")

    # Reusa a knowledge base persistida pelo analyze-source quando disponível
    # (entidades + bindings no banco) — re-parsear o fonte inteiro a cada
    # síntese custava ~25 min por captura no AIX (1.965 programas).
    from dakota_gateway.synthetic.engine import SyntheticEngine
    engine = SyntheticEngine(db_connection=con)
    entities = engine.load_entities()
    bindings = engine.load_bindings()
    kb = {"entities": entities, "bindings": bindings} if entities and bindings else {}

    synthesizer = JourneySynthesizer()
    template = synthesizer.from_capture(
        capture_jsonl, source_path, name=name or run_name, **kb)
    result = synthesizer.synthesize(template, samples=samples, out_dir=output_dir, seed=seed)

    validation = None
    if include_validation:
        validation = synthesizer.validate_sessions(Path(result.sessions_dir), template)

    stress = None
    if include_stress:
        stress = synthesizer.simulate_stress(Path(result.sessions_dir), concurrency=concurrency)

    report = {}
    report_path = Path(result.report_path)
    if report_path.exists():
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
        except Exception:
            report = {}

    return {
        "ok": True,
        "capture_id": capture_id,
        "capture": capture,
        "source_dir": str(source_path),
        "capture_files": [str(path) for path in files],
        "capture_jsonl": str(capture_jsonl),
        "output_dir": str(output_dir),
        "journey_id": result.journey_id,
        "name": result.name,
        "samples": result.samples,
        "generated_sessions": result.generated_sessions,
        "entities_involved": result.entities_involved,
        "mapped_inputs": result.mapped_inputs,
        "command_inputs": result.command_inputs,
        "unmapped_inputs": result.unmapped_inputs,
        "artifacts": {
            "template": result.template_path,
            "dataset": result.dataset_path,
            "sessions_dir": result.sessions_dir,
            "report": result.report_path,
        },
        "screen_mappings": result.screen_mappings,
        # Campos-âncora detectados na KB (chave de consulta) — mantidos com o
        # valor original no replay sintético, sem intervenção do usuário.
        "key_fields": suggest_key_fields(result.screen_mappings, entities),
        "warnings": result.warnings,
        "evidence": result.evidence,
        "validation": validation,
        "stress": stress,
        "report": report,
    }


# ---------------------------------------------------------------------------
# Replay sintético em 1 clique (captura → dados sintéticos → run real)
# ---------------------------------------------------------------------------

# Operações de busca cujos campos funcionam como chave de consulta.
_LOOKUP_OPS = {"seek", "locate", "dbseek", "find"}


def suggest_key_fields(screen_mappings: list[dict] | None, entities: list | None) -> list[str]:
    """Campos-âncora da navegação a manter com o valor original da captura.

    Generalista — vale para qualquer captura/entidade: um campo mapeado é
    âncora quando compõe índice da entidade, aparece em operação de busca
    (seek/locate/dbseek/find), é único, tem lookup_table (FK — o valor
    precisa existir na entidade referenciada), ou tem tipo semântico
    identificador de registro (`source_analyzer.semantic_types`).
    Substituir uma âncora por um valor sintético inexistente faz a consulta
    não encontrar o registro e desvia o fluxo (ex.: cair no cadastro em vez
    de seguir a jornada gravada).
    """
    by_entity = {str(getattr(e, "name", "") or "").upper(): e for e in (entities or [])}
    keys: list[str] = []
    seen: set[str] = set()
    for screen in screen_mappings or []:
        entity = by_entity.get(str(screen.get("entity_name") or "").upper())
        if entity is None:
            continue
        anchors: set[str] = set()
        for idx in getattr(entity, "indexes", None) or []:
            if isinstance(idx, dict) and str(idx.get("field") or "").strip():
                anchors.add(str(idx["field"]).upper())
        for op in getattr(entity, "operations", None) or []:
            if str(getattr(op, "operation_type", "") or "").lower() in _LOOKUP_OPS:
                anchors.update(
                    str(f).upper() for f in (getattr(op, "fields", None) or []) if str(f or "").strip()
                )
        for fld in getattr(entity, "fields", None) or []:
            if not str(getattr(fld, "name", "") or "").strip():
                continue
            datatype = str(getattr(fld, "datatype", "") or "").strip().lower()
            semantic = str(getattr(fld, "semantic_type", "") or "").strip().lower()
            if (
                getattr(fld, "unique_flag", False)
                or str(getattr(fld, "lookup_table", "") or "").strip()
                or identifies_record(datatype)
                or identifies_record(semantic)
            ):
                anchors.add(str(fld.name).upper())
        if not anchors:
            continue
        for inp in screen.get("inputs") or []:
            field = str(inp.get("field_name") or "").strip()
            if field and field.upper() in anchors and field.lower() not in seen:
                seen.add(field.lower())
                keys.append(field)
    return keys


def _format_synthetic_value(original: str, value: Any) -> str:
    """Alinha o valor sintético ao formato esperado pelo campo na tela.

    - float → decimal pt-BR com vírgula (o Recital usa vírgula: "229,9");
    - int → string direta;
    - string com máscara (original só dígitos, ex.: CPF) → só dígitos.
    """
    if isinstance(value, float):
        return f"{value:.2f}".replace(".", ",")
    if isinstance(value, int):
        return str(value)
    text = str(value)
    if original.isdigit():
        return re.sub(r"\D", "", text)
    return text


def _extract_substitutions(
    screen_mappings: list[dict],
    dataset_row: dict,
    skip_fields: set[str] | None = None,
) -> list[tuple[str, str]]:
    """Pares (original → sintético) na ordem da captura, a partir dos mappings.

    ``skip_fields``: nomes de campos a manter com o valor original da captura
    (ex.: chaves de consulta como ``cpf`` — um valor sintético novo desviaria
    o fluxo para o cadastro em vez de seguir a jornada gravada).
    """
    skip = {str(f).strip().lower() for f in (skip_fields or set()) if str(f).strip()}
    subs: list[tuple[str, str]] = []
    for screen in screen_mappings or []:
        for inp in screen.get("inputs") or []:
            placeholder = str(inp.get("placeholder") or "")
            original = str(inp.get("original") or "")
            if not placeholder or not original or "{KEY:" in original:
                continue
            field = str(inp.get("field_name") or "").strip()
            if not field:
                m = re.match(r"^\{\{[^.]+\.([^}]+)\}\}$", placeholder)
                field = m.group(1) if m else ""
            if not field or field not in dataset_row:
                continue
            if field.lower() in skip:
                # Substituição identidade: mantém o valor original na trilha,
                # mas avança o cursor posicional para a ocorrência certa —
                # sem isso, uma substituição posterior de valor ambíguo
                # (ex.: frete "1") casaria no menu ("1 - REDE LOJAS").
                subs.append((original, original))
                continue
            value = _format_synthetic_value(original, dataset_row[field])
            if value and value != original:
                subs.append((original, value))
    return subs


def _capture_user_from_trail(capture_jsonl: str) -> str:
    """Usuário operacional da sessão gravada (logname/actor do session_start)."""
    try:
        with open(capture_jsonl, "r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                ev = json.loads(line)
                if ev.get("type") == "session_start":
                    return str(ev.get("logname") or ev.get("actor") or "").strip()
    except Exception:
        pass
    return ""


def start_synthetic_replay(
    con,
    capture_id: int,
    *,
    created_by: int,
    source_dir: str,
    seed: int | None = None,
    target_host: str = "",
    target_user: str = "",
    term: str = "",
    skip_fields: list[str] | None = None,
    runner,
    hmac_key: bytes,
) -> dict[str, Any]:
    """Sintetiza dados a partir da captura e dispara um run real (1 clique).

    Encadeia: síntese (template+dataset) → substituição dos inputs mapeados
    na trilha real da captura (banner pré-sessão removido, cadeia HMAC
    re-assinada) → run determinístico ``send-anyway`` via replay_control.
    Retorna o payload da run criada + estatísticas da trilha.
    """
    from control.services.run_service import create_run_request_payload

    synth = synthesize_capture(
        con,
        capture_id,
        source_dir=source_dir,
        samples=1,
        seed=seed,
        name=f"capture-{capture_id}-replay",
        include_validation=False,
    )

    dataset_path = Path(synth["artifacts"]["dataset"])
    dataset_lines = [l for l in dataset_path.read_text(encoding="utf-8").splitlines() if l.strip()]
    dataset_row = json.loads(dataset_lines[0]) if dataset_lines else {}

    # Campos-âncora (chave de consulta detectada na KB) são mantidos com o
    # valor original automaticamente; o chamador pode adicionar outros via
    # skip_fields explícito.
    suggested_skip = [str(f) for f in (synth.get("key_fields") or [])]
    explicit_skip = [str(f).strip() for f in (skip_fields or []) if str(f).strip()]
    effective_skip = sorted({f.lower() for f in suggested_skip + explicit_skip})

    substitutions = _extract_substitutions(
        synth.get("screen_mappings"), dataset_row, skip_fields=set(effective_skip)
    )
    synth_warnings = list(synth.get("warnings") or [])
    auto_kept = [f for f in suggested_skip if f.lower() not in {e.lower() for e in explicit_skip}]
    if auto_kept:
        synth_warnings.append(
            "campos-âncora mantidos com o valor original (chave de consulta): " + ", ".join(auto_kept)
        )
    if not substitutions:
        # Sem campo mapeado, o 1-clique ainda cumpre o replay (dados
        # originais, banner removido) em vez de bloquear o usuário.
        synth_warnings.append(
            "nenhum campo mapeado para substituição — replay usará os dados originais da captura"
        )

    trail_dir = Path(synth["output_dir"]) / "trail"
    trail = build_synthetic_trail(
        synth["capture_jsonl"],
        substitutions,
        trail_dir,
        hmac_key=hmac_key,
    )

    resolved_host = str(target_host or "").strip() or "127.0.0.1"
    resolved_user = str(target_user or "").strip() or _capture_user_from_trail(synth["capture_jsonl"])
    # TERM da captura é o do terminal do usuário (ex.: dk100 do TeraTerm) —
    # termos com sequências de porta auxiliar (ESC[5i) travam a sessão de
    # replay headless. O TerminalEngine emula xterm, então o default do
    # replay é xterm (overridável pelo chamador).
    resolved_term = str(term or "").strip() or "xterm"

    run_body: dict[str, Any] = {
        "log_dir": str(trail_dir),
        "mode": "strict-global",
        "target_host": resolved_host,
        "target_user": resolved_user,
        "params": {
            "input_mode": "deterministic",
            "on_deterministic_mismatch": "send-anyway",
            "match_mode": "strict",
            "match_threshold": 0.92,
            "term": resolved_term,
            "synthetic": True,
            "source_capture_id": capture_id,
            "journey_id": synth.get("journey_id") or "",
        },
    }
    created = create_run_request_payload(con, created_by=created_by, body=run_body)
    run_id = int(created["id"])
    runner.start_run_async(run_id)

    return {
        "ok": True,
        "capture_id": capture_id,
        "run_id": run_id,
        "status": "queued",
        "target_host": resolved_host,
        "target_user": resolved_user,
        "substitutions": trail["applied"],
        "substitutions_count": len(trail["applied"]),
        "key_fields_suggested": suggested_skip,
        "skip_fields": effective_skip,
        "dropped_banner_events": trail["dropped_banner"],
        "trail_events": trail["events"],
        "trail_dir": str(trail_dir),
        "warnings": trail["warnings"] + synth_warnings,
    }

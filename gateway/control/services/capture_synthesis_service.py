"""Serviço capture-to-synthetic para sessões gravadas pela UI."""
from __future__ import annotations

import base64
import json
import os
import re
from pathlib import Path
from typing import Any

from dakota_gateway.state_db import now_ms
from dakota_gateway.synthetic.journey_synthesizer import JourneySynthesizer
from dakota_gateway.synthetic.synthetic_trail import build_synthetic_trail
from dakota_gateway.source_analyzer.semantic_types import identifies_record

from control.services.capture_service import get_capture, resolve_replay_log_dir


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
    variation: str = "synthetic",
) -> dict[str, Any]:
    """Transforma uma captura registrada em template + dataset + sessões sintéticas.

    ``variation``: "synthetic" (default) — sessões com dados diferentes;
    "equal" — todas as sessões com os mesmos dados (1ª linha do dataset).
    """
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

    # Enriquecimento por arquivos de índice (i<TABELA>.00N): a expressão da
    # chave em texto claro no primeiro bloco é a fonte mais confiável de
    # "qual campo é chave" — vale mesmo quando a KB não tem índices parseados
    # do fonte. Sem índice no diretório de dados, segue sem enriquecer.
    from dakota_gateway.source_analyzer.index_file_reader import (
        discover_data_dirs,
        enrich_entities_with_index_files,
    )
    data_dirs = discover_data_dirs(source_path)
    if data_dirs and entities:
        enrich_entities_with_index_files(entities, data_dirs)

    synthesizer = JourneySynthesizer()
    template = synthesizer.from_capture(
        capture_jsonl, source_path, name=name or run_name, **kb)
    result = synthesizer.synthesize(
        template, samples=samples, out_dir=output_dir, seed=seed, variation=variation)

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

    key_fields = suggest_key_fields(result.screen_mappings, entities)

    # De→para (original → sintético) da 1ª sessão gerada — exibido no modal
    # do detalhe da captura após o "Gerar". Em variation=equal todas as
    # sessões usam esta linha; em synthetic ela representa a sessão 1.
    depara_screens: list[dict] = []
    try:
        with open(result.dataset_path, encoding="utf-8") as fh:
            first_line = fh.readline().strip()
        dataset_row = json.loads(first_line) if first_line else {}
    except Exception:
        dataset_row = {}
    if dataset_row:
        depara_screens = _build_depara_screens(result.screen_mappings, dataset_row, key_fields)

    return {
        "ok": True,
        "capture_id": capture_id,
        "capture": capture,
        "source_dir": str(source_path),
        "data_dirs": data_dirs,
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
        "variation": "equal" if str(variation or "").strip().lower() == "equal" else "synthetic",
        # Campos-âncora detectados na KB (chave de consulta) — mantidos com o
        # valor original no replay sintético, sem intervenção do usuário.
        "key_fields": key_fields,
        "depara": {
            "session_index": 1,
            "sessions": result.generated_sessions,
            "screens": depara_screens,
        },
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


def _screen_display_name(screen: dict) -> str:
    """Nome amigável da tela para o de→para.

    O ``entity_name`` da KB pode ser espúrio/genérico (ex.: entidade "arq"
    descoberta de um alias ``arq.`` de outro programa) — exibi-lo como nome da
    tela confunde o usuário. Quando o título gravado tem a linha de código de
    menu do Recital ("| 3.6.1 PEDIDO E-COMMERCE"), usa-se "3.6.1 PEDIDO
    E-COMMERCE"; senão, cai para o entity_name.
    """
    title = str(screen.get("screen_title") or "")
    m = re.search(r"(\d+(?:\.\d+)+\s+[A-Z0-9][^\n|]{1,60})", title.upper())
    if m:
        return m.group(1).strip().title()
    return str(screen.get("entity_name") or "")


def _build_depara_screens(
    screen_mappings: list[dict],
    dataset_row: dict,
    skip_fields: set[str] | list[str],
) -> list[dict]:
    """De→para por tela: campo, valor original da captura, valor na trilha
    sintética e se foi mantido (chave de consulta ou igual ao original).

    Espelha a seleção de ``_extract_substitutions``: só entram inputs com
    placeholder resolvido para um campo presente no dataset. Inputs de dados
    SEM campo mapeado (opção de menu, campo fora da KB, texto sem match)
    entram na lista ``preserved`` — o usuário vê todos os dados digitados
    contabilizados, não só os substituídos.
    """
    skip = {str(f).strip().lower() for f in (skip_fields or set()) if str(f).strip()}
    screens: list[dict] = []
    for screen in screen_mappings or []:
        fields: list[dict] = []
        preserved: list[dict] = []
        for inp in screen.get("inputs") or []:
            placeholder = str(inp.get("placeholder") or "")
            original = str(inp.get("original") or "")
            method = str(inp.get("method") or "")
            if not original or "{KEY:" in original or method == "command":
                continue
            if not placeholder:
                # Dado digitado mantido com o valor original — explicado.
                if method == "kept_layout_field":
                    note = "campo fora da KB — original mantido"
                elif method in ("menu_option_kept", ""):
                    note = "opção/código — original mantido"
                else:
                    note = "sem match confiável — original mantido"
                preserved.append({
                    "field": str(inp.get("layout_field")
                                 or inp.get("field_name") or ""),
                    "original": original,
                    "note": note,
                    "method": method,
                })
                continue
            field = str(inp.get("field_name") or "").strip()
            if not field:
                m = re.match(r"^\{\{[^.]+\.([^}]+)\}\}$", placeholder)
                field = m.group(1) if m else ""
            if not field or field not in dataset_row:
                continue
            kept = False
            note = ""
            if field.lower() in skip:
                kept, note, synthetic = True, "chave de consulta", original
            else:
                synthetic = _format_synthetic_value(original, dataset_row[field])
                if synthetic == original:
                    kept, note = True, "igual ao original"
            fields.append({
                "field": field,
                "original": original,
                "synthetic": synthetic,
                "kept": kept,
                "note": note,
                "method": str(inp.get("method") or ""),
            })
        # Telas de menu/navegação (sem campo mapeado e sem GET de formulário
        # identificado pelo cursor) não entram: os dígitos de opção delas
        # virariam ruído no de→para. Preservados só aparecem em telas com
        # substituições ou com campo de formulário comprovado pelo layout.
        has_layout_kept = any(p["method"] == "kept_layout_field" for p in preserved)
        if fields or has_layout_kept:
            screens.append({
                "entity": str(screen.get("entity_name") or ""),
                "display_name": _screen_display_name(screen),
                "operation": str(screen.get("operation") or ""),
                "fields": fields,
                "preserved": preserved,
            })
    return screens


def synthetic_substitutions_payload(con, capture_id: int, *, log_dir: str) -> dict[str, Any]:
    """De→para (original → sintético) da trilha sintética de uma captura.

    Lê o manifest ``de-para.json`` gravado pelo replay 1-clique dentro do
    trail_dir; trilhas antigas (sem manifest) são reconstruídas de
    ``report.json`` + ``dataset.jsonl`` (irmãos do ``trail/``) recalculando
    os campos-âncora na knowledge base. ``log_dir`` é validado por
    ``resolve_replay_log_dir`` (só caminhos dentro do log_dir da captura).
    """
    capture = get_capture(con, capture_id)
    if not capture:
        raise FileNotFoundError("captura não encontrada")
    base_log_dir = str(capture.get("log_dir") or "").strip()
    trail_dir = Path(resolve_replay_log_dir(base_log_dir, log_dir))

    manifest = trail_dir / "de-para.json"
    if manifest.exists():
        data = json.loads(manifest.read_text(encoding="utf-8"))
        return {
            "ok": True,
            "source": "manifest",
            "capture_id": capture_id,
            "journey_id": str(data.get("journey_id") or ""),
            "key_fields": list(data.get("key_fields") or []),
            "screens": list(data.get("screens") or []),
        }

    work_dir = trail_dir.parent
    report_path = work_dir / "report.json"
    dataset_path = work_dir / "dataset.jsonl"
    if not report_path.exists() or not dataset_path.exists():
        raise FileNotFoundError(
            "trilha sem de→para: não há manifest (de-para.json) nem artefatos "
            "de síntese (report.json/dataset.jsonl) ao lado do trail/"
        )
    report = json.loads(report_path.read_text(encoding="utf-8"))
    dataset_lines = [l for l in dataset_path.read_text(encoding="utf-8").splitlines() if l.strip()]
    dataset_row = json.loads(dataset_lines[0]) if dataset_lines else {}
    screen_mappings = list(report.get("screen_mappings") or [])

    from dakota_gateway.synthetic.engine import SyntheticEngine
    entities = SyntheticEngine(db_connection=con).load_entities()
    key_fields = suggest_key_fields(screen_mappings, entities)

    return {
        "ok": True,
        "source": "rebuilt",
        "capture_id": capture_id,
        "journey_id": str(report.get("journey_id") or ""),
        "key_fields": key_fields,
        "screens": _build_depara_screens(screen_mappings, dataset_row, key_fields),
    }


def _latest_synthesis_report(log_dir: str) -> Path | None:
    """report.json mais recente de uma síntese anterior desta captura."""
    base = Path(str(log_dir or "").strip()) / "synthetic"
    if not base.is_dir():
        return None
    reports = [p for p in base.glob("*/report.json") if p.is_file()]
    if not reports:
        return None
    return max(reports, key=lambda p: p.stat().st_mtime)


def _screen_mappings_from_template(template) -> list[dict]:
    """Mesmo shape do ``screen_mappings`` do report.json, a partir do template."""
    return [
        {
            "screen_title": step.screen_title,
            "screen_signature": step.screen_signature,
            "entity_name": step.entity_name,
            "operation": step.operation,
            "inputs": [
                {
                    "original": i.original,
                    "placeholder": i.placeholder,
                    "field_name": i.field_name,
                    "method": i.method,
                    "layout_field": getattr(i, "layout_field", ""),
                }
                for i in step.inputs
            ],
        }
        for step in template.steps
    ]


def synthetic_fields_payload(con, capture_id: int, *, source_dir: str) -> dict[str, Any]:
    """Campos da trilha para o multi-select "Manter originais (replay)".

    Agrupa os inputs mapeados por tela (entidade/operação), marcando os
    campos-âncora (chave de consulta) detectados na knowledge base — esses já
    são mantidos automaticamente e vêm desabilitados no seletor. Reusa o
    ``report.json`` da síntese mais recente quando existe (``source=report``);
    senão parametriza a captura na hora com a KB persistida + índices
    (``source=computed``), sem re-parsear o fonte.
    """
    capture = get_capture(con, capture_id)
    if not capture:
        raise FileNotFoundError("captura não encontrada")

    from dakota_gateway.synthetic.engine import SyntheticEngine
    engine = SyntheticEngine(db_connection=con)
    entities = engine.load_entities()

    log_dir = str(capture.get("log_dir") or "").strip()
    screen_mappings: list[dict] = []
    source = ""
    report_path = _latest_synthesis_report(log_dir)
    if report_path is not None:
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
            screen_mappings = list(report.get("screen_mappings") or [])
        except Exception:
            screen_mappings = []
        if screen_mappings:
            source = "report"

    if not screen_mappings:
        source_dir_clean = str(source_dir or "").strip()
        source_path = Path(source_dir_clean) if source_dir_clean else None
        if source_path is None or not source_path.exists() or not source_path.is_dir():
            raise ValueError(
                "source_dir inválido ou inexistente — informe a pasta dos fontes "
                "Recital para mapear os campos da trilha"
            )
        files = _find_capture_jsonl(log_dir)
        if not files:
            raise ValueError("nenhum arquivo .jsonl encontrado na captura")
        bindings = engine.load_bindings()
        kb = {"entities": entities, "bindings": bindings} if entities and bindings else {}
        from dakota_gateway.source_analyzer.index_file_reader import (
            discover_data_dirs,
            enrich_entities_with_index_files,
        )
        data_dirs = discover_data_dirs(source_path)
        if data_dirs and entities:
            enrich_entities_with_index_files(entities, data_dirs)
        capture_jsonl = files[0] if len(files) == 1 else _combine_jsonl(
            files, Path(log_dir) / "synthetic" / "capture_combined.jsonl")
        template = JourneySynthesizer().from_capture(
            capture_jsonl, source_path, name=f"capture-{capture_id}-fields", **kb)
        screen_mappings = _screen_mappings_from_template(template)
        source = "computed"

    key_fields = suggest_key_fields(screen_mappings, entities)
    key_set = {f.lower() for f in key_fields}
    screens: list[dict] = []
    all_fields: list[str] = []
    seen_all: set[str] = set()
    for screen in screen_mappings:
        fields: list[dict] = []
        seen: set[str] = set()
        for inp in screen.get("inputs") or []:
            if str(inp.get("method") or "") == "command":
                continue  # teclas de navegação/menu não são dados substituíveis
            original = str(inp.get("original") or "")
            if not original or "{KEY:" in original:
                continue
            field = str(inp.get("field_name") or "").strip()
            if not field:
                placeholder = str(inp.get("placeholder") or "")
                m = re.match(r"^\{\{[^.]+\.([^}]+)\}\}$", placeholder)
                field = m.group(1) if m else ""
            if not field or field.lower() in seen:
                continue
            seen.add(field.lower())
            if field.lower() not in seen_all:
                seen_all.add(field.lower())
                all_fields.append(field)
            fields.append({
                "field": field,
                "original": original,
                "method": str(inp.get("method") or ""),
                "key": field.lower() in key_set,
            })
        if fields:
            screens.append({
                "entity": str(screen.get("entity_name") or ""),
                "operation": str(screen.get("operation") or ""),
                "screen_title": str(screen.get("screen_title") or ""),
                "fields": fields,
            })

    return {
        "ok": True,
        "source": source,
        "capture_id": capture_id,
        "screens": screens,
        "fields": all_fields,
        "key_fields": key_fields,
    }


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

    # Manifest do de→para (original → sintético por tela) — alimenta o modal
    # "De→para" da página de replay da sessão sintética sem reprocessar nada.
    depara = {
        "capture_id": capture_id,
        "journey_id": synth.get("journey_id") or "",
        "generated_at_ms": now_ms(),
        "key_fields": effective_skip,
        "screens": _build_depara_screens(
            synth.get("screen_mappings"), dataset_row, effective_skip
        ),
    }
    try:
        (trail_dir / "de-para.json").write_text(
            json.dumps(depara, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError:
        pass  # o manifest é facilitador da UI; não derruba o replay

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

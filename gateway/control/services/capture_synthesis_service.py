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
    lookup_values: dict[str, list] | None = None,
) -> dict[str, Any]:
    """Transforma uma captura registrada em template + dataset + sessões sintéticas.

    ``variation``: "synthetic" (default) — sessões com dados diferentes;
    "equal" — todas as sessões com os mesmos dados (1ª linha do dataset).

    ``lookup_values``: valores reais por entidade/tabela referenciada (FK),
    digitados pelo usuário (manual). São fundidos com os valores observados
    nas capturas anteriores deste servidor (harvest dos report.json) — campos
    de lookup cobertos por valores reais deixam de ser âncora e passam a
    variar dentro do cadastro (ex.: condição de pagamento).
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

    # Valores reais para campos de lookup (FK): harvest das capturas
    # anteriores do servidor + lista manual do usuário (manual estende).
    merged_lookup: dict[str, list] = _harvest_lookup_values(
        Path(log_dir).parent, exclude_log_dir=log_dir)
    for key, vals in (lookup_values or {}).items():
        k = str(key or "").strip().lower()
        if not k:
            continue
        bucket = merged_lookup.setdefault(k, [])
        for v in (vals if isinstance(vals, list) else [vals]):
            sv = str(v).strip()
            if sv and sv not in bucket:
                bucket.append(sv)

    result = synthesizer.synthesize(
        template, samples=samples, out_dir=output_dir, seed=seed,
        variation=variation,
        lookup_values={k: v for k, v in merged_lookup.items() if v})

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

    key_fields = suggest_key_fields(
        result.screen_mappings, entities,
        indexed_fields=_indexed_field_names(data_dirs),
        lookup_covered={k for k, v in merged_lookup.items() if v})

    # De→para (original → sintético) da 1ª sessão gerada — exibido no modal
    # do detalhe da captura após o "Gerar". Em variation=equal todas as
    # sessões usam esta linha; em synthetic ela representa a sessão 1.
    depara_screens: list[dict] = []
    dataset_row = _first_session_dataset_row(result.dataset_path)
    if dataset_row:
        depara_screens = _build_depara_screens(
            result.screen_mappings, dataset_row, key_fields,
            lookup_counts={k: len(v) for k, v in merged_lookup.items() if v})

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
        # Cobertura de lookup aplicada nesta síntese (tabela FK → nº de
        # valores reais disponíveis) — alimenta a nota do de→para e o
        # replay em 1 clique.
        "lookup_counts": {k: len(v) for k, v in merged_lookup.items() if v},
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


def _indexed_field_names(data_dirs: list[str] | None) -> set[str]:
    """Nomes de campos que compõem chaves de índice (i<TABELA>.00N).

    Varre as expressões de chave em texto claro dos arquivos de índice dos
    diretórios de dados descobertos — um campo indexado é, por definição,
    um código que o ERP consulta (seek), e substituí-lo por um valor
    sintético inexistente faz a validação falhar ("Codigo nao cadastrado")
    e o fluxo desviar. Vale inclusive para células de grade cuja entidade
    não tem campos na KB (est361/est366 da captura 62).
    """
    from dakota_gateway.source_analyzer.index_file_reader import scan_index_files

    names: set[str] = set()
    for data_dir in data_dirs or []:
        for table_keys in scan_index_files(data_dir).values():
            for fields in table_keys:
                names.update(f.lower() for f in fields if f)
    return names


def _matches_indexed(field: str, indexed: set[str]) -> bool:
    """Casa o nome do campo com um campo indexado.

    Exato ou prefixo (a grade abrevia o nome da coluna: ``comb`` ←
    ``combinacao``, ``tam`` ← ``tamanho`` — convenção dos vCampos do
    legado). Prefixo exige ≥3 caracteres para não ancorar demais.
    """
    f = field.lower()
    if f in indexed:
        return True
    if len(f) >= 3:
        for name in indexed:
            if name.startswith(f) or (len(name) >= 3 and f.startswith(name)):
                return True
    return False


def _harvest_lookup_values(
    captures_root: Path,
    *,
    exclude_log_dir: str = "",
    max_per_key: int = 200,
) -> dict[str, list[str]]:
    """Valores reais digitados em capturas anteriores, por entidade/tabela.

    Fonte primária dos ``lookup_values``: códigos observados nas trilhas já
    sintetizadas (ex.: condições de pagamento usadas em outras capturas do
    mesmo servidor). Chave = ``lookup_table`` (FK) ou ``entity_name`` do
    input, em minúsculas. A captura atual é excluída (ela é a referência, não
    fonte de variação).
    """
    exclude_name = Path(str(exclude_log_dir or "")).name
    values: dict[str, dict] = {}  # chave → {valor: None} (dedup ordenado)
    root = Path(captures_root)
    if not root.is_dir():
        return {}
    for report in sorted(root.glob("*/synthetic/*/report.json")):
        try:
            if exclude_name and report.parents[2].name == exclude_name:
                continue
            if report.stat().st_size > 10 * 1024 * 1024:
                continue
            data = json.loads(report.read_text(encoding="utf-8"))
        except (OSError, ValueError, IndexError):
            continue
        for screen in data.get("screen_mappings") or []:
            for inp in screen.get("inputs") or []:
                original = str(inp.get("original") or "").strip()
                if not original or "{KEY:" in original:
                    continue
                keys = (
                    str(inp.get("lookup_table") or "").strip().lower(),
                    str(inp.get("entity_name") or "").strip().lower(),
                    # Chave por nome de campo: permite variar códigos reais
                    # (ex.: EAN) mesmo quando a tabela FK é desconhecida ou
                    # o input foi mapeado para a entidade errada — a lista
                    # ``field:<campo>`` alimenta o dataset via lookup.
                    (f"field:{str(inp.get('field_name') or '').strip().lower()}"
                     if str(inp.get("field_name") or "").strip() else ""),
                )
                for key in keys:
                    if not key:
                        continue
                    bucket = values.setdefault(key, {})
                    if len(bucket) < max_per_key:
                        bucket.setdefault(original, None)
    return {key: list(bucket) for key, bucket in values.items()}


def suggest_key_fields(
    screen_mappings: list[dict] | None,
    entities: list | None,
    indexed_fields: set[str] | None = None,
    lookup_covered: set[str] | None = None,
) -> list[str]:
    """Campos-âncora da navegação a manter com o valor original da captura.

    Generalista — vale para qualquer captura/entidade: um campo mapeado é
    âncora quando compõe índice da entidade, aparece em operação de busca
    (seek/locate/dbseek/find), é único, tem lookup_table (FK — o valor
    precisa existir na entidade referenciada), tem tipo semântico
    identificador de registro (`source_analyzer.semantic_types`), ou compõe
    chave de algum índice dos diretórios de dados (``indexed_fields`` —
    cobre células de grade sem metadados na KB). Substituir uma âncora por
    um valor sintético inexistente faz a consulta não encontrar o registro
    e desvia o fluxo (ex.: "Codigo nao cadastrado" e a sessão cai fora da
    jornada gravada).

    ``lookup_covered``: tabelas FK com valores reais disponíveis
    (``lookup_values``). Campo cuja ÚNICA razão de âncora é o lookup e cuja
    tabela está coberta deixa de ser âncora — o valor gerado já vem da lista
    de valores reais, então variar é seguro (ex.: condição de pagamento).
    Índice/seek/único continuam ancorando: são consultas diretas.
    """
    covered = {str(t).strip().lower() for t in (lookup_covered or set()) if str(t).strip()}
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
            lookup_table = str(getattr(fld, "lookup_table", "") or "").strip()
            # FK coberta por valores reais (lookup_values) não precisa de
            # âncora — a variação sorteia um valor que existe no cadastro.
            lookup_anchor = bool(lookup_table) and lookup_table.lower() not in covered
            if (
                getattr(fld, "unique_flag", False)
                or lookup_anchor
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
    if indexed_fields:
        # Passada por índice: campo que compõe chave de algum i<TABELA>.00N
        # é código consultado pelo ERP — mantido mesmo sem entidade na KB
        # (células de grade est361/est366, captura 62).
        for screen in screen_mappings or []:
            for inp in screen.get("inputs") or []:
                field = str(inp.get("field_name") or "").strip()
                if not field or field.lower() in seen:
                    continue
                if str(inp.get("method") or "") == "command":
                    continue
                if _matches_indexed(field, indexed_fields):
                    seen.add(field.lower())
                    keys.append(field)
    # Passada por evidência no próprio input (não depende de metadados da
    # KB — cobre entidade espúria/incompleta, ex.: "arq" da captura 73):
    # - lookup_table do input (da KB ou do VALID do fonte via ``_lookup_of``)
    #   sem cobertura de valores reais → o valor PRECISA existir na tabela
    #   referenciada; gerar livre cai em "Codigo nao cadastrado" (cfop 9445
    #   da run 52);
    # - valor com cara de código de registro (8-14 dígitos puros: EAN, CPF,
    #   CNPJ, código de barras) sem valores reais observados para o campo →
    #   idem (EAN 7036643879947 inventado para a NF da captura 73). Cobertura
    #   por ``field:<campo>`` (harvest de capturas anteriores) libera a
    #   variação com códigos que existem de verdade.
    for screen in screen_mappings or []:
        for inp in screen.get("inputs") or []:
            field = str(inp.get("field_name") or "").strip()
            if not field or field.lower() in seen:
                continue
            if str(inp.get("method") or "") == "command":
                continue
            field_key = f"field:{field.lower()}"
            lookup_table = str(inp.get("lookup_table") or "").strip().lower()
            if (lookup_table and lookup_table not in covered
                    and field_key not in covered):
                seen.add(field.lower())
                keys.append(field)
                continue
            original = str(inp.get("original") or "").strip()
            if (re.fullmatch(r"\d{8,14}", original)
                    and field_key not in covered):
                seen.add(field.lower())
                keys.append(field)
    return keys


def _format_synthetic_value(original: str, value: Any) -> str:
    """Alinha o valor sintético ao formato esperado pelo campo na tela.

    - float → decimal pt-BR com vírgula (o Recital usa vírgula: "229,9"),
      preservando o Nº de casas decimais do original: um GET com PICTURE de
      2 casas não comita "229,9" no ENTER (fica aguardando o último dígito),
      mas comita "763,05" — gerar com 2 casas quando o original tinha 1 muda
      o estado do campo e desalinha a navegação do replay (captura 62, run 40:
      a grade de pagamento fechou um ESC antes e a sessão saiu do pedido sem
      finalizar);
    - int → string direta;
    - string com máscara (original só dígitos, ex.: CPF) → só dígitos.
    """
    if isinstance(value, float):
        m = re.fullmatch(r"\d+[,.](\d+)", original.strip())
        decimals = len(m.group(1)) if m else 2
        return f"{value:.{decimals}f}".replace(".", ",")
    if isinstance(value, int):
        return str(value)
    text = str(value)
    if original.isdigit():
        return re.sub(r"\D", "", text)
    return text


def _dataset_lookup(dataset_row: dict, entity: str, field: str) -> tuple[bool, Any]:
    """Valor do campo no dataset da sessão 1, preferindo a chave prefixada
    pela entidade efetiva do input (``est361.modelo``) — sem ela, um campo
    com mesmo nome em duas entidades (ex.: ``codigo`` do formulário e da
    grade) pegaria o valor da entidade errada. Cai para a chave bare quando
    o row não é multi-entidade (trilhas/datasets antigos)."""
    if entity:
        prefixed = f"{entity}.{field}"
        if prefixed in dataset_row:
            return True, dataset_row[prefixed]
    if field in dataset_row:
        return True, dataset_row[field]
    return False, None


def _parse_br_number(text: str) -> float | None:
    """Número pt-BR da tela ("1.609,30" / "229,9" / "2") → float, ou None."""
    t = re.sub(r"[^\d,.-]", "", str(text or "").strip())
    if not t or not re.search(r"\d", t):
        return None
    t = t.replace(".", "").replace(",", ".") if "," in t else t
    try:
        return float(t)
    except ValueError:
        return None


def _payment_total_overrides(
    screen_mappings: list[dict],
    dataset_row: dict,
    skip: set[str],
) -> dict[str, str]:
    """Valor da grade de pagamento → total sintético do pedido.

    O pedido do Recital (est361) só grava quando a soma da grade de
    pagamento (est366) é igual ao total do pedido (qtd × valor unitário):
    pagamento parcial dispara o aviso "Valor do pedido difere do valor dos
    pagamentos" e a sequência de ESCs seguinte abandona a tela sem gravar.
    A captura 62 só persistiu no 2º passe, quando o pagamento era o total
    cheio — e a run 41 mostrou que o replay não volta ao ERP depois da
    saída do 1º passe (typeahead de menu/shell dessincroniza). Como a qtd
    sintética muda o total, TODOS os valores da grade de pagamento são
    igualados ao total sintético (maior valor original × fator
    qtd_sintética/qtd_original): o replay confirma a inclusão já no 1º
    passe, sem depender do retorno ao ERP.

    Retorna ``{original: novo_valor}``; vazio quando a trilha não tem o
    padrão qtd (grade de itens) × valor (grade de pagamento) ou os valores
    não são numéricos.
    """
    qtd_ratio = None
    pagamentos: list[str] = []
    for screen in screen_mappings or []:
        for inp in screen.get("inputs") or []:
            if not (inp.get("is_grid") or str(inp.get("grid_source") or "").strip()):
                continue
            placeholder = str(inp.get("placeholder") or "")
            original = str(inp.get("original") or "")
            if not placeholder or not original or "{KEY:" in original:
                continue
            field = str(inp.get("field_name") or "").strip().lower()
            if not field:
                m = re.match(r"^\{\{[^.]+\.([^}]+)\}\}$", placeholder)
                field = m.group(1).lower() if m else ""
            if not field or field in skip:
                continue
            ent = str(inp.get("entity_name") or screen.get("entity_name") or "")
            found, raw_value = _dataset_lookup(dataset_row, ent, field)
            if not found:
                continue
            if field == "qtd" and qtd_ratio is None:
                orig_q = _parse_br_number(original)
                synth_q = _parse_br_number(_format_synthetic_value(original, raw_value))
                if orig_q and synth_q and orig_q > 0:
                    qtd_ratio = synth_q / orig_q
            elif field == "valor":
                if _parse_br_number(original):
                    pagamentos.append(original)
    if not qtd_ratio or not pagamentos:
        return {}
    total = max(_parse_br_number(p) for p in pagamentos) * qtd_ratio
    overrides: dict[str, str] = {}
    for original in pagamentos:
        m = re.fullmatch(r"\d+[,.](\d+)", original.strip())
        decimals = len(m.group(1)) if m else 0
        formatted = f"{total:.{decimals}f}"
        if "," in original:
            formatted = formatted.replace(".", ",")
        if formatted != original:
            overrides[original] = formatted
    return overrides


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
    pay_overrides = _payment_total_overrides(screen_mappings, dataset_row, skip)
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
            if not field:
                continue
            ent = str(inp.get("entity_name") or screen.get("entity_name") or "")
            found, raw_value = _dataset_lookup(dataset_row, ent, field)
            if not found:
                continue
            if field.lower() in skip:
                # Substituição identidade: mantém o valor original na trilha,
                # mas avança o cursor posicional para a ocorrência certa —
                # sem isso, uma substituição posterior de valor ambíguo
                # (ex.: frete "1") casaria no menu ("1 - REDE LOJAS").
                subs.append((original, original))
                continue
            value = _format_synthetic_value(original, raw_value)
            if field.lower() == "valor" and (
                inp.get("is_grid") or str(inp.get("grid_source") or "").strip()
            ):
                value = pay_overrides.get(original, value)
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


def _first_session_dataset_row(dataset_path) -> dict:
    """Primeira linha do dataset por entidade, mesclada — espelha o
    ``session_data`` da sessão 1 (que junta o 1º registro de CADA entidade).
    Necessário porque telas com grade dbedit geram entidades separadas
    (est361=itens, est366=pagamento), cada uma com seu registro no jsonl.
    """
    merged: dict = {}
    try:
        lines = [l for l in Path(dataset_path).read_text(encoding="utf-8").splitlines() if l.strip()]
    except OSError:
        return {}
    seen_entities: set[str] = set()
    for line in lines:
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        ent = str(rec.get("_entity") or "")
        if ent in seen_entities:
            continue
        seen_entities.add(ent)
        for key, val in rec.items():
            if key == "_entity":
                continue
            # Espelha o session_data da sessão 1: chave prefixada por
            # entidade (resolve {{est361.modelo}}) + bare (última entidade
            # vence em caso de colisão de nome entre entidades).
            if ent:
                merged[f"{ent}.{key}"] = val
            merged[key] = val
    return merged


def _build_depara_screens(
    screen_mappings: list[dict],
    dataset_row: dict,
    skip_fields: set[str] | list[str],
    lookup_counts: dict[str, int] | None = None,
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
    pay_overrides = _payment_total_overrides(screen_mappings, dataset_row, skip)
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
                    "origin": "grade" if inp.get("is_grid") else "formulario",
                    "grid_source": str(inp.get("grid_source") or ""),
                })
                continue
            field = str(inp.get("field_name") or "").strip()
            if not field:
                m = re.match(r"^\{\{[^.]+\.([^}]+)\}\}$", placeholder)
                field = m.group(1) if m else ""
            if not field:
                continue
            ent = str(inp.get("entity_name") or screen.get("entity_name") or "")
            found, raw_value = _dataset_lookup(dataset_row, ent, field)
            if not found:
                continue
            kept = False
            note = ""
            if field.lower() in skip:
                kept, note, synthetic = True, "chave de consulta", original
            else:
                synthetic = _format_synthetic_value(original, raw_value)
                if field.lower() == "valor" and (
                    inp.get("is_grid") or str(inp.get("grid_source") or "").strip()
                ) and original in pay_overrides:
                    synthetic = pay_overrides[original]
                    note = "ajustado ao total do pedido"
                if synthetic == original:
                    kept, note = True, "igual ao original"
            if not note:
                # FK coberta por valores reais: o sintético foi sorteado do
                # cadastro (lookup_values), não gerado livre.
                lt = str(inp.get("lookup_table") or "").strip().lower()
                if lt and lookup_counts and lt in lookup_counts:
                    note = f"valor real do cadastro (1 de {lookup_counts[lt]})"
                elif lookup_counts and f"field:{field.lower()}" in lookup_counts:
                    note = ("valor real observado em capturas "
                            f"(1 de {lookup_counts[f'field:{field.lower()}']})")
            if not note:
                # Constraint vinda do VALID do fonte (ex.: "valor > 0") —
                # registrada no schema da geração (validation_rules).
                valid_expr = str(inp.get("valid_expr") or "").strip()
                if valid_expr:
                    from dakota_gateway.synthetic.validation_rules import (
                        parse_valid_expr)
                    if parse_valid_expr(valid_expr):
                        note = f"VALID: {valid_expr}"
            if not note:
                # FK sem valores reais colhidos ainda: o VALID do fonte
                # exige valor cadastrado na tabela (fValida).
                lt = str(inp.get("lookup_table") or "").strip().lower()
                if lt:
                    note = f"VALID: valor deve existir em {lt}"
            fields.append({
                "field": field,
                "original": original,
                "synthetic": synthetic,
                "kept": kept,
                "note": note,
                "method": str(inp.get("method") or ""),
                "origin": "grade" if inp.get("is_grid") else "formulario",
                "grid_source": str(inp.get("grid_source") or ""),
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
    dataset_row = _first_session_dataset_row(dataset_path)
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
                    "is_grid": getattr(i, "is_grid", False),
                    "grid_source": getattr(i, "grid_source", ""),
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

    # Campos indexados (chaves i<TABELA>.00N) também ancoram células de
    # grade sem metadados na KB — melhor esforço: sem source_dir válido,
    # segue só com as âncoras da KB.
    indexed_fields: set[str] = set()
    try:
        from dakota_gateway.source_analyzer.index_file_reader import (
            discover_data_dirs as _discover_data_dirs,
        )
        _sp = Path(str(source_dir or "").strip())
        if _sp.is_dir():
            indexed_fields = _indexed_field_names(_discover_data_dirs(_sp))
    except Exception:
        indexed_fields = set()

    key_fields = suggest_key_fields(screen_mappings, entities, indexed_fields=indexed_fields)
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
    auto_entry: bool = True,
    lookup_values: dict[str, list] | None = None,
    runner,
    hmac_key: bytes,
) -> dict[str, Any]:
    """Sintetiza dados a partir da captura e dispara um run real (1 clique).

    Encadeia: síntese (template+dataset) → substituição dos inputs mapeados
    na trilha real da captura (banner pré-sessão removido, cadeia HMAC
    re-assinada) → run determinístico ``send-anyway`` via replay_control.
    Retorna o payload da run criada + estatísticas da trilha.

    ``auto_entry`` (default ligado): quando a captura começou fora do sistema
    (preâmbulo de login/shell — capturas 13/62), corta a trilha no início do
    ERP e grava nos params da run o ``entry_preamble`` (passos de entrada
    derivados das próprias teclas da captura), executado pelo replay antes do
    primeiro checkpoint.
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
        lookup_values=lookup_values,
    )

    dataset_path = Path(synth["artifacts"]["dataset"])
    dataset_row = _first_session_dataset_row(dataset_path)

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
        start_seq="auto" if auto_entry else None,
        source_dir=source_dir if auto_entry else None,
    )
    entry = trail.get("entry") if auto_entry else None
    if entry:
        synth_warnings.append(str(entry.get("summary") or ""))

    # Manifest do de→para (original → sintético por tela) — alimenta o modal
    # "De→para" da página de replay da sessão sintética sem reprocessar nada.
    depara = {
        "capture_id": capture_id,
        "journey_id": synth.get("journey_id") or "",
        "generated_at_ms": now_ms(),
        "key_fields": effective_skip,
        "screens": _build_depara_screens(
            synth.get("screen_mappings"), dataset_row, effective_skip,
            lookup_counts=synth.get("lookup_counts") or {}
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
            # Pares (original → sintético) aplicados na trilha — permitem ao
            # replay classificar a divergência explicada pela troca como
            # synthetic_data_swap em vez de screen_divergence.
            "synthetic_substitutions": [[o, s] for o, s in substitutions if o != s],
        },
    }
    if entry:
        # Passos de entrada (menu wrapper → shell → ERP) executados pelo
        # replay antes do primeiro checkpoint, e o seq de corte do preâmbulo.
        run_body["params"]["entry_preamble"] = entry["preamble"]
        run_body["params"]["entry_trimmed_seq"] = entry["start_seq"]
        if entry.get("fallback"):
            # Entrada alternativa pelo módulo Recital — o replay a tenta
            # quando o comando shell gravado não abre o sistema (artefato
            # volátil ausente no destino, ex.: ferblo.dbo da captura 62).
            run_body["params"]["entry_fallback"] = entry["fallback"]
            synth_warnings.append(
                f"entrada alternativa disponível se o caminho gravado falhar: {entry['fallback']['send'].strip()}"
            )
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
        "dropped_entry_events": trail.get("dropped_entry") or 0,
        "entry_point": (
            {
                "start_seq": entry["start_seq"],
                "dropped": entry["dropped"],
                "summary": entry["summary"],
                "fallback": entry.get("fallback"),
            }
            if entry
            else None
        ),
        "trail_events": trail["events"],
        "trail_dir": str(trail_dir),
        "warnings": trail["warnings"] + synth_warnings,
    }

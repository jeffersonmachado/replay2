"""Capture-to-Synthetic Journey — transforma captura real em jornadas sinteticas.

Faz parte da entrega P2 — Synthetic Knowledge Base.

Fluxo:
1. Carrega captura .jsonl
2. Parametriza via CaptureParametrizer
3. Analisa fonte via SourceParser
4. Enriquece com CaptureKnowledgeIntegrator
5. Gera JourneyTemplate
6. Sintetiza N sessoes com dados ficticios validos
7. Salva template, dataset, sessoes e report
"""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from .capture_parametrizer import CaptureParametrizer, CaptureTemplate
from .capture_knowledge_integrator import (
    CaptureKnowledgeIntegrator,
    KnowledgeEnrichedTemplate,
    MappedInput,
    ScreenKnowledgeMapping,
)
from ..source_analyzer.parser import SourceParser
from ..source_analyzer.entity_catalog import EntityDefinition
from ..source_analyzer.screen_entity_linker import ScreenEntityBinding
from .template_engine import TemplateEngine
from .data_synthesizer import DataSynthesizer
from .dataset_builder import DatasetBuilder
from .schema import ScreenSchema, SyntheticSchema, FieldSchema
from .providers import default_registry


# ── Helpers ──

def _stable_int(value: str) -> int:
    """Hash estavel entre processos (substitui hash() do Python)."""
    return int(hashlib.sha256(value.encode("utf-8")).hexdigest()[:8], 16)


# Nomes de campo que indicam tipo monetario/numerico
_MONEY_FIELD_NAMES = {
    "VALOR", "PRECO", "PREÇO", "TOTAL", "SALDO", "LIMITE", "CUSTO",
    "DESCONTO", "QUANTIDADE", "QTD", "QTDE", "PESO", "PERCENTUAL",
    "ALIQUOTA", "TAXA", "JUROS", "MULTA", "SUBTOTAL", "FRETE",
}

_CPF_NAMES = {"CPF"}
_CNPJ_NAMES = {"CNPJ", "CGC"}
_EMAIL_NAMES = {"EMAIL", "E_MAIL", "E-MAIL", "MAIL"}
_PHONE_NAMES = {"TELEFONE", "FONE", "CELULAR", "WHATSAPP", "TEL", "CEL"}
_PERSON_NAME_NAMES = {"NOME", "NOME_CLIENTE", "CONTATO", "CLIENTE"}
_COMPANY_NAME_NAMES = {"RAZAO_SOCIAL", "FANTASIA", "EMPRESA"}
_ADDRESS_NAMES = {"ENDERECO", "ENDEREÇO", "LOGRADOURO", "RUA", "BAIRRO", "CIDADE", "CEP", "UF"}
_DESC_NAMES = {"DESCRICAO", "DESCRIÇÃO", "DESCR", "OBS", "OBSERVACAO", "COMPLEMENTO", "DETALHE"}


def _infer_field_format(field_name: str, original_type: str) -> str:
    """Infere o formato/provider adequado para FieldSchema a partir do nome e tipo."""
    fu = field_name.upper().strip() if field_name else ""

    if fu in _CPF_NAMES:
        return "cpf"
    if fu in _CNPJ_NAMES:
        return "cnpj"
    if fu in _EMAIL_NAMES:
        return "email"
    if fu in _PHONE_NAMES:
        return "phone"
    if fu in _MONEY_FIELD_NAMES:
        return "decimal"
    if fu in _PERSON_NAME_NAMES:
        return "person_name"
    if fu in _COMPANY_NAME_NAMES:
        return "company_name"
    if fu in _ADDRESS_NAMES:
        return "address"
    if fu in _DESC_NAMES:
        return "description"

    if original_type in ("cpf", "cnpj", "email", "phone"):
        return original_type
    if original_type == "number":
        return "decimal"
    if original_type in ("text", "text_long"):
        return "person_name"

    return "text"


# Faixas realistas por tipo de campo
_PRODUCT_PRICE_FIELDS = {"VALOR", "PRECO", "PREÇO", "CUSTO"}
_QUANTITY_FIELDS = {"QUANTIDADE", "QTD", "QTDE"}
_PERCENT_FIELDS = {"PERCENTUAL", "DESCONTO", "ALIQUOTA", "TAXA", "JUROS"}
_WIDE_MONEY_FIELDS = {"TOTAL", "SALDO", "LIMITE", "SUBTOTAL", "FRETE"}


def _value_range_for_field(field_name: str) -> tuple[float | None, float | None]:
    """Retorna (min, max) realista para campo monetario."""
    fu = field_name.upper().strip()
    if fu in _PRODUCT_PRICE_FIELDS:
        return (1.0, 9999.99)
    if fu in _QUANTITY_FIELDS:
        return (1.0, 999.0)
    if fu in _PERCENT_FIELDS:
        return (0.0, 100.0)
    if fu in _WIDE_MONEY_FIELDS:
        return (0.0, 999999.99)
    return (None, None)


@dataclass
class TemplateInput:
    """Input do template da jornada."""
    original: str = ""
    placeholder: Optional[str] = None
    field_name: Optional[str] = None
    entity_name: Optional[str] = None
    method: str = ""
    confidence: float = 0.0
    original_type: str = ""
    evidence: list[str] = field(default_factory=list)


@dataclass
class JourneyStep:
    """Passo da jornada (tela)."""
    screen_title: Optional[str] = None
    screen_signature: Optional[str] = None
    entity_name: Optional[str] = None
    operation: str = ""
    binding_confidence: float = 0.0
    inputs: list[TemplateInput] = field(default_factory=list)
    matched_fields: list[str] = field(default_factory=list)


@dataclass
class JourneyTemplate:
    """Template de jornada parametrizada."""
    journey_id: str = ""
    name: str = ""
    capture_source: str = ""
    entities_involved: list[str] = field(default_factory=list)
    steps: list[JourneyStep] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)


@dataclass
class SynthesisResult:
    """Resultado da sintese de jornadas."""
    journey_id: str = ""
    name: str = ""
    capture_source: str = ""
    samples: int = 0
    template_path: str = ""
    dataset_path: str = ""
    sessions_dir: str = ""
    report_path: str = ""
    generated_sessions: int = 0
    entities_involved: list[str] = field(default_factory=list)
    mapped_inputs: int = 0
    command_inputs: int = 0
    unmapped_inputs: int = 0
    screen_mappings: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)


class JourneySynthesizer:
    """Orquestrador da transformacao captura → jornadas sinteticas."""

    def __init__(self):
        self.parametrizer = CaptureParametrizer()
        self.integrator = CaptureKnowledgeIntegrator()
        self.template_engine = TemplateEngine()
        self.dataset_builder = DatasetBuilder(default_registry())

    # ── API principal ──

    def from_capture(
        self,
        capture_path: Path,
        source_dir: Path,
        name: str | None = None,
    ) -> JourneyTemplate:
        """Analisa captura e fonte, gera JourneyTemplate."""
        capture_path = Path(capture_path)
        source_dir = Path(source_dir)

        # 1. Parametriza captura
        capture_template = self.parametrizer.analyze_capture(str(capture_path))

        # 2. Analisa fonte
        parser = SourceParser(str(source_dir))
        entities, screens = parser.parse_all()
        bindings = parser.screen_entity_bindings()

        # 3. Enriquece com knowledge base
        enriched = self.integrator.enrich_template(capture_template, entities, bindings)

        # 4. Constroi JourneyTemplate
        journey_name = name or capture_path.stem
        journey_id = str(uuid.uuid4())[:8]

        # Indexa bindings por entity para buscar screen_title
        bindings_by_entity: dict[str, ScreenEntityBinding] = {}
        for b in bindings:
            if b.entity_name:
                bindings_by_entity[b.entity_name.upper()] = b

        steps: list[JourneyStep] = []
        evidence: list[str] = [
            f"capture_source={capture_path.name}",
            f"entities_detected={len(entities)}",
            f"screens_detected={len(screens)}",
            f"bindings={len(bindings)}",
            f"total_inputs={enriched.total_inputs}",
            f"mapped_inputs={enriched.mapped_inputs}",
            f"command_inputs={enriched.command_inputs}",
            f"unmapped_inputs={enriched.unmapped_inputs}",
        ]

        # Indexa screen_contexts por screen_sig para buscar screen_sample
        ctx_by_sig: dict[str, dict] = {}
        for ctx in capture_template.screen_contexts:
            sig = ctx.get("screen_sig", "")
            if sig:
                ctx_by_sig[sig] = ctx

        for sm in enriched.screen_mappings:
            step_inputs: list[TemplateInput] = []
            for mi in sm.mapped_inputs:
                step_inputs.append(TemplateInput(
                    original=mi.original_value,
                    placeholder=mi.placeholder if mi.placeholder else None,
                    field_name=mi.field_name if mi.field_name else None,
                    entity_name=mi.entity_name if mi.entity_name else None,
                    method=mi.method,
                    confidence=mi.confidence,
                    original_type=mi.original_type,
                    evidence=list(mi.evidence),
                ))

            # screen_title: binding > screen_context > screen_sample
            screen_title = None
            binding = bindings_by_entity.get(sm.entity_name.upper()) if sm.entity_name else None
            if binding and binding.screen_title:
                screen_title = binding.screen_title
            if not screen_title:
                ctx = ctx_by_sig.get(sm.screen_signature, {})
                screen_title = ctx.get("screen_sample") or ctx.get("screen_title")

            steps.append(JourneyStep(
                screen_title=screen_title,
                screen_signature=sm.screen_signature,
                entity_name=sm.entity_name if sm.entity_name else None,
                operation=sm.operation,
                binding_confidence=sm.binding_confidence,
                inputs=step_inputs,
                matched_fields=list(sm.matched_fields),
            ))

        return JourneyTemplate(
            journey_id=journey_id,
            name=journey_name,
            capture_source=str(capture_path),
            entities_involved=list(enriched.entities_involved),
            steps=steps,
            evidence=evidence,
        )

    def synthesize(
        self,
        template: JourneyTemplate,
        samples: int,
        out_dir: Path,
        seed: int | None = None,
    ) -> SynthesisResult:
        """Gera N sessoes sinteticas a partir do template."""
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        sessions_dir = out_dir / "sessions"
        sessions_dir.mkdir(parents=True, exist_ok=True)

        warnings: list[str] = []

        # 1. Salva template
        template_path = out_dir / "template.json"
        self.save_template(template, template_path)

        # 2. Gera dataset sintetico por entidade
        dataset_path = out_dir / "dataset.jsonl"
        all_records: list[dict] = []
        entity_field_map: dict[str, dict[str, str]] = {}  # entity -> {field_lower -> provider_format}

        for entity_name in template.entities_involved:
            fields_for_entity: list[FieldSchema] = []
            for step in template.steps:
                if step.entity_name and step.entity_name.upper() == entity_name.upper():
                    for inp in step.inputs:
                        if inp.field_name and inp.method != "command":
                            fu = (inp.field_name or "").upper().strip()
                            # Formato especifico (cpf, email, phone)
                            fmt = None
                            if fu in _CPF_NAMES:
                                fmt = "cpf"
                            elif fu in _CNPJ_NAMES:
                                fmt = "cnpj"
                            elif fu in _EMAIL_NAMES:
                                fmt = "email"
                            elif fu in _PHONE_NAMES:
                                fmt = "phone"
                            # Datatype (decimal, person_name, address, etc.)
                            dtype = "text"
                            if fu in _MONEY_FIELD_NAMES:
                                dtype = "decimal"
                            elif fu in _PERSON_NAME_NAMES:
                                dtype = "person_name"
                            elif fu in _COMPANY_NAME_NAMES:
                                dtype = "company_name"
                            elif fu in _ADDRESS_NAMES:
                                dtype = "address"
                            elif fu in _DESC_NAMES:
                                dtype = "text"
                            elif inp.original_type == "number":
                                dtype = "decimal"
                            elif inp.original_type in ("text", "text_long"):
                                dtype = "person_name"

                            fs = FieldSchema(
                                name=inp.field_name,
                                datatype=dtype,
                                format=fmt if fmt else None,
                                required=True,
                                min_value=_value_range_for_field(inp.field_name)[0],
                                max_value=_value_range_for_field(inp.field_name)[1],
                            )
                            fields_for_entity.append(fs)
                            entity_field_map.setdefault(entity_name, {})[inp.field_name.lower()] = inp.field_name
                    break

            if not fields_for_entity:
                continue

            screen_schema = ScreenSchema(
                screen_signature=entity_name,
                title=entity_name,
                program_name=entity_name,
                fields=fields_for_entity,
            )
            synth_schema = SyntheticSchema(
                screen=screen_schema,
                entity_name=entity_name,
                quantity=samples,
                seed=(seed or 42) + (_stable_int(entity_name) % 1000),
            )

            try:
                dataset = self.dataset_builder.build(synth_schema)
                for rec in dataset.records:
                    rec_data = dict(rec.data)
                    rec_data["_entity"] = entity_name
                    all_records.append(rec_data)
            except Exception as e:
                warnings.append(f"dataset_build({entity_name}): {e}")

        # Salva dataset
        with open(dataset_path, "w", encoding="utf-8") as f:
            for rec in all_records:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")

        # 3. Gera sessoes sinteticas
        generated = 0

        for sess_idx in range(samples):
            session_data: dict[str, Any] = {}
            # Coleta dados desta sessao do dataset
            for entity_name in template.entities_involved:
                entity_records = [r for r in all_records if r.get("_entity") == entity_name]
                if entity_records and sess_idx < len(entity_records):
                    rec = entity_records[sess_idx]
                    for key, val in rec.items():
                        if key != "_entity":
                            session_data[f"{entity_name}.{key}"] = val
                            session_data[key] = val

            # Renderiza cada input
            session_lines: list[dict] = []
            seq = 0
            for step in template.steps:
                for inp in step.inputs:
                    seq += 1
                    if inp.method == "command":
                        session_lines.append({
                            "seq": seq,
                            "type": "command",
                            "value": inp.original,
                        })
                    elif inp.placeholder:
                        rendered = self.template_engine.render(inp.placeholder, session_data)
                        if "{{" in rendered:
                            warnings.append(f"session {sess_idx}: placeholder nao resolvido: {inp.placeholder}")
                        session_lines.append({
                            "seq": seq,
                            "type": "input",
                            "value": rendered,
                            "placeholder": inp.placeholder,
                            "field": inp.field_name,
                            "entity": inp.entity_name,
                        })
                    else:
                        session_lines.append({
                            "seq": seq,
                            "type": "input",
                            "value": inp.original,
                            "field": inp.field_name,
                            "entity": inp.entity_name,
                        })

            # Salva sessao
            session_file = sessions_dir / f"session_{sess_idx + 1:06d}.jsonl"
            with open(session_file, "w", encoding="utf-8") as f:
                for line in session_lines:
                    f.write(json.dumps(line, ensure_ascii=False) + "\n")
            generated += 1

        # 4. Gera report
        report_path = out_dir / "report.json"
        report = {
            "journey_id": template.journey_id,
            "name": template.name,
            "capture_source": template.capture_source,
            "samples": samples,
            "entities_involved": template.entities_involved,
            "total_sessions": samples,
            "generated_sessions": generated,
            "template_file": str(template_path.name),
            "dataset_file": str(dataset_path.name),
            "sessions_dir": str(sessions_dir.name),
            "mapped_inputs": sum(
                1 for s in template.steps for i in s.inputs
                if i.method != "command" and i.field_name
            ),
            "command_inputs": sum(
                1 for s in template.steps for i in s.inputs
                if i.method == "command"
            ),
            "unmapped_inputs": sum(
                1 for s in template.steps for i in s.inputs
                if i.method not in ("command",) and not i.field_name
            ),
            "screen_mappings": [
                {
                    "screen_title": s.screen_title,
                    "screen_signature": s.screen_signature,
                    "entity_name": s.entity_name,
                    "operation": s.operation,
                    "binding_confidence": s.binding_confidence,
                    "matched_fields": s.matched_fields,
                    "inputs": [
                        {
                            "original": i.original,
                            "placeholder": i.placeholder,
                            "field_name": i.field_name,
                            "method": i.method,
                            "confidence": i.confidence,
                            "evidence": i.evidence,
                        }
                        for i in s.inputs
                    ],
                }
                for s in template.steps
            ],
            "warnings": warnings,
            "evidence": template.evidence,
        }
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)

        return SynthesisResult(
            journey_id=template.journey_id,
            name=template.name,
            capture_source=template.capture_source,
            samples=samples,
            template_path=str(template_path),
            dataset_path=str(dataset_path),
            sessions_dir=str(sessions_dir),
            report_path=str(report_path),
            generated_sessions=generated,
            entities_involved=template.entities_involved,
            mapped_inputs=report["mapped_inputs"],
            command_inputs=report["command_inputs"],
            unmapped_inputs=report["unmapped_inputs"],
            screen_mappings=report["screen_mappings"],
            warnings=warnings,
            evidence=template.evidence,
        )

    def simulate_stress(
        self,
        sessions_dir: Path,
        concurrency: int = 10,
    ) -> dict:
        """Simula execucao concorrente das sessoes sintetizadas (sem replay real).

        Retorna relatorio de stress com:
        - total_sessions, completed, failed, errors
        - timing stats (min/avg/max)
        - validation warnings por sessao
        """
        import time as _time

        sessions_dir = Path(sessions_dir)
        session_files = sorted(sessions_dir.glob("session_*.jsonl"))
        total = len(session_files)

        results: list[dict] = []
        completed = 0
        failed = 0
        errors = 0
        timings: list[float] = []
        all_warnings: list[str] = []

        start_time = _time.time()

        for sf in session_files:
            t0 = _time.time()
            status = "success"
            session_warnings: list[str] = []
            try:
                content = sf.read_text(encoding="utf-8").strip()
                if not content:
                    status = "error"
                    errors += 1
                    session_warnings.append("sessao vazia")
                else:
                    lines = [json.loads(l) for l in content.split("\n") if l.strip()]
                    # Valida estrutura
                    for obj in lines:
                        if "{{" in str(obj.get("value", "")):
                            session_warnings.append(f"placeholder nao resolvido: {obj.get('value')}")
                            status = "failed"
                    # Checa comandos preservados
                    commands = [l for l in lines if l.get("type") == "command"]
                    inputs = [l for l in lines if l.get("type") == "input"]
                    if not inputs and not commands:
                        session_warnings.append("sem inputs nem comandos")

                    completed += 1
            except Exception as e:
                status = "error"
                errors += 1
                session_warnings.append(str(e))

            elapsed = (_time.time() - t0) * 1000
            timings.append(elapsed)

            if status == "failed":
                failed += 1
            all_warnings.extend([f"{sf.name}: {w}" for w in session_warnings])

            results.append({
                "session": sf.name,
                "status": status,
                "duration_ms": round(elapsed, 2),
                "warnings": session_warnings,
            })

        total_time = (_time.time() - start_time) * 1000

        return {
            "mode": "simulated",
            "concurrency_config": concurrency,
            "total_sessions": total,
            "completed": completed,
            "failed": failed,
            "errors": errors,
            "duration_ms": round(total_time, 2),
            "timing_ms": {
                "min": round(min(timings), 2) if timings else 0,
                "avg": round(sum(timings) / len(timings), 2) if timings else 0,
                "max": round(max(timings), 2) if timings else 0,
            },
            "session_results": results[:20],  # primeiras 20
            "warnings": all_warnings[:50],
        }

    def validate_sessions(
        self,
        sessions_dir: Path,
        template: JourneyTemplate,
    ) -> dict:
        """Valida sessoes sintetizadas contra o template."""
        sessions_dir = Path(sessions_dir)
        session_files = sorted(sessions_dir.glob("session_*.jsonl"))
        total = len(session_files)
        valid = 0
        issues: list[str] = []
        field_counts: dict[str, int] = {}
        command_counts: dict[str, int] = {}
        unresolved: list[str] = []

        for sf in session_files:
            try:
                content = sf.read_text(encoding="utf-8").strip()
                if not content:
                    issues.append(f"{sf.name}: vazia")
                    continue
                lines = [json.loads(l) for l in content.split("\n") if l.strip()]
                sess_ok = True
                for obj in lines:
                    val = str(obj.get("value", ""))
                    if "{{" in val:
                        unresolved.append(f"{sf.name}: {obj.get('placeholder', '?')}")
                        sess_ok = False
                    if obj.get("type") == "command":
                        command_counts[obj.get("value", "")] = command_counts.get(obj.get("value", ""), 0) + 1
                    elif obj.get("type") == "input":
                        fn = obj.get("field", "?")
                        field_counts[fn] = field_counts.get(fn, 0) + 1
                if sess_ok:
                    valid += 1
            except Exception as e:
                issues.append(f"{sf.name}: {e}")

        template_fields = {inp.field_name for step in template.steps for inp in step.inputs if inp.field_name}
        template_commands = {inp.original for step in template.steps for inp in step.inputs if inp.method == "command"}
        missing_fields = template_fields - set(field_counts.keys())

        return {
            "total_sessions": total, "valid_sessions": valid,
            "invalid_sessions": total - valid,
            "issues": issues[:20], "unresolved_placeholders": unresolved[:20],
            "field_coverage": {
                "expected": sorted(template_fields), "found": sorted(field_counts.keys()),
                "missing": sorted(missing_fields),
                "counts": {k: v for k, v in sorted(field_counts.items())},
            },
            "command_coverage": {
                "expected": sorted(template_commands), "found": sorted(command_counts.keys()),
                "counts": {k: v for k, v in sorted(command_counts.items())},
            },
            "coverage_pct": round(len(template_fields - missing_fields) / max(len(template_fields), 1) * 100, 1),
        }

    # ── Serializacao ──

    def save_template(self, template: JourneyTemplate, path: Path) -> None:
        """Salva template em JSON."""
        path = Path(path)
        data = {
            "journey_id": template.journey_id,
            "name": template.name,
            "capture_source": template.capture_source,
            "entities_involved": template.entities_involved,
            "evidence": template.evidence,
            "steps": [
                {
                    "screen_title": s.screen_title,
                    "screen_signature": s.screen_signature,
                    "entity_name": s.entity_name,
                    "operation": s.operation,
                    "binding_confidence": s.binding_confidence,
                    "matched_fields": s.matched_fields,
                    "inputs": [
                        {
                            "original": i.original,
                            "placeholder": i.placeholder,
                            "field_name": i.field_name,
                            "entity_name": i.entity_name,
                            "method": i.method,
                            "confidence": i.confidence,
                            "original_type": i.original_type,
                            "evidence": i.evidence,
                        }
                        for i in s.inputs
                    ],
                }
                for s in template.steps
            ],
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def load_template(self, path: Path) -> JourneyTemplate:
        """Carrega template de JSON."""
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        steps: list[JourneyStep] = []
        for s in data.get("steps", []):
            steps.append(JourneyStep(
                screen_title=s.get("screen_title"),
                screen_signature=s.get("screen_signature", ""),
                entity_name=s.get("entity_name"),
                operation=s.get("operation", ""),
                binding_confidence=s.get("binding_confidence", 0.0),
                matched_fields=s.get("matched_fields", []),
                inputs=[
                    TemplateInput(
                        original=i.get("original", ""),
                        placeholder=i.get("placeholder"),
                        field_name=i.get("field_name"),
                        entity_name=i.get("entity_name"),
                        method=i.get("method", ""),
                        confidence=i.get("confidence", 0.0),
                        original_type=i.get("original_type", ""),
                        evidence=i.get("evidence", []),
                    )
                    for i in s.get("inputs", [])
                ],
            ))

        return JourneyTemplate(
            journey_id=data.get("journey_id", ""),
            name=data.get("name", ""),
            capture_source=data.get("capture_source", ""),
            entities_involved=data.get("entities_involved", []),
            steps=steps,
            evidence=data.get("evidence", []),
        )

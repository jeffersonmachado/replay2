from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, TYPE_CHECKING

from .constraints import ConstraintRule, ConstraintValidator, ConstraintViolation
from .dataset_builder import DatasetBuilder, Dataset
from .inferencer import SyntheticInferencer
from .providers import ProviderRegistry, default_registry
from .relationship_resolver import RelationshipResolver
from .schema import FieldSchema, ScreenSchema, SyntheticSchema

if TYPE_CHECKING:
    from .business_dataset_planner import BusinessDependencyGraph


@dataclass
class InferredDataPlan:
    """Plano inferido para sintetizar dados de uma tela/entidade."""

    plan_id: str
    source_dir: str
    screen: ScreenSchema
    entity_name: str = ""
    field_rules: list[ConstraintRule] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_synthetic_schema(self, quantity: int, seed: int = 0) -> SyntheticSchema:
        return SyntheticSchema(
            screen=self.screen,
            entity_name=self.entity_name or self.screen.program_name or self.screen.title or "unknown",
            quantity=quantity,
            seed=seed,
        )


@dataclass
class RecordValidationResult:
    record_index: int
    data: dict[str, Any] = field(default_factory=dict)
    violations: list[ConstraintViolation] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.violations


@dataclass
class PreflightValidationResult:
    plan_id: str = ""
    sample_size: int = 0
    records: list[RecordValidationResult] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(record.passed for record in self.records)

    @property
    def total_violations(self) -> int:
        return sum(len(record.violations) for record in self.records)


@dataclass
class BulkGenerationResult:
    plan_id: str = ""
    blocked: bool = False
    message: str = ""
    preflight: Optional[PreflightValidationResult] = None
    dataset: Optional[Dataset] = None

    @property
    def generated_count(self) -> int:
        return len(self.dataset.records) if self.dataset else 0


class DataSynthesizer:
    """Sintetiza datasets inferidos e exige validação antes do bulk."""

    def __init__(self, registry: Optional[ProviderRegistry] = None):
        self.registry = registry or default_registry()
        self.dataset_builder = DatasetBuilder(self.registry)
        self.inferencer = SyntheticInferencer()

    def infer_plans(
        self,
        source_dir: str,
        *,
        screen_filter: Optional[str] = None,
        entity_filter: Optional[str] = None,
    ) -> list[InferredDataPlan]:
        """Infere planos de síntese a partir do código-fonte."""
        result = self.inferencer.analyze_source(source_dir)
        plans: list[InferredDataPlan] = []

        for schema in result.schemas:
            screen = self._enrich_screen(copy.deepcopy(schema.screen))
            screen_id = screen.screen_id or screen.program_name or screen.title
            entity_name = schema.entity_name or screen.program_name or screen.title

            if screen_filter and screen_filter.lower() not in screen_id.lower():
                continue
            if entity_filter and entity_filter.lower() not in entity_name.lower():
                continue

            rules = [ConstraintRule.from_field_schema(field) for field in screen.fields]
            warnings: list[str] = []
            if not screen.fields:
                warnings.append("tela sem campos inferidos")

            plans.append(
                InferredDataPlan(
                    plan_id=self._build_plan_id(source_dir, screen_id, entity_name),
                    source_dir=source_dir,
                    screen=screen,
                    entity_name=entity_name,
                    field_rules=rules,
                    warnings=warnings + list(result.warnings),
                )
            )

        if not plans:
            plans.extend(self._infer_plans_from_raw_source(source_dir))

        return plans

    def generate_preflight(
        self,
        plan: InferredDataPlan,
        *,
        sample_size: int = 5,
        seed: int = 0,
        lookup_values: Optional[dict[str, list[Any]]] = None,
    ) -> PreflightValidationResult:
        """Gera uma amostra validada antes da geração em massa."""
        schema = plan.to_synthetic_schema(quantity=sample_size, seed=seed)
        dataset = self.dataset_builder.build(schema, lookup_values=lookup_values)

        records: list[RecordValidationResult] = []
        for rec in dataset.records:
            violations = self._validate_record(rec.data, plan.field_rules)
            records.append(
                RecordValidationResult(
                    record_index=rec.record_index,
                    data=rec.data,
                    violations=violations,
                )
            )

        return PreflightValidationResult(
            plan_id=plan.plan_id,
            sample_size=sample_size,
            records=records,
            warnings=list(plan.warnings),
        )

    def generate_bulk(
        self,
        plan: InferredDataPlan,
        *,
        quantity: int = 100,
        seed: int = 0,
        sample_size: int = 5,
        lookup_values: Optional[dict[str, list[Any]]] = None,
        strict_preflight: bool = True,
    ) -> BulkGenerationResult:
        """Executa preflight e só então gera o dataset em massa."""
        preflight = self.generate_preflight(
            plan,
            sample_size=sample_size,
            seed=seed,
            lookup_values=lookup_values,
        )
        if strict_preflight and not preflight.ok:
            return BulkGenerationResult(
                plan_id=plan.plan_id,
                blocked=True,
                message=(
                    f"preflight falhou com {preflight.total_violations} violacao(oes); "
                    "geracao em massa bloqueada"
                ),
                preflight=preflight,
            )

        dataset = self.dataset_builder.build(
            plan.to_synthetic_schema(quantity=quantity, seed=seed),
            lookup_values=lookup_values,
        )
        return BulkGenerationResult(
            plan_id=plan.plan_id,
            blocked=False,
            message="geracao concluida",
            preflight=preflight,
            dataset=dataset,
        )

    def generate_bulk_for_source(
        self,
        source_dir: str,
        *,
        quantity: int = 100,
        sample_size: int = 5,
        seed: int = 0,
        screen_filter: Optional[str] = None,
        entity_filter: Optional[str] = None,
        strict_preflight: bool = True,
    ) -> list[BulkGenerationResult]:
        """Infere planos do source e gera datasets por tela, com preflight."""
        plans = self.infer_plans(
            source_dir,
            screen_filter=screen_filter,
            entity_filter=entity_filter,
        )
        return [
            self.generate_bulk(
                plan,
                quantity=quantity,
                seed=seed,
                sample_size=sample_size,
                strict_preflight=strict_preflight,
            )
            for plan in plans
        ]

    def generate_ordered(
        self,
        plans: list[InferredDataPlan],
        dependency_graph: Optional["BusinessDependencyGraph"] = None,
        *,
        seed: int = 0,
        sample_size: int = 5,
        strict_preflight: bool = True,
    ) -> list[BulkGenerationResult]:
        """Gera datasets na ordem do grafo de dependencia.

        Entidades raiz (sem FK) sao geradas primeiro. Entidades dependentes
        recebem lookup_values com os dados das entidades pai ja geradas,
        garantindo integridade referencial.

        Args:
            plans: Planos inferidos pelo infer_plans()
            dependency_graph: Grafo de dependencia (do BusinessDatasetPlanner)
            seed: Seed para reproducibilidade
            sample_size: Tamanho da amostra de preflight
            strict_preflight: Bloqueia bulk se preflight falhar

        Returns:
            Lista de BulkGenerationResult na ordem de geracao
        """
        results: list[BulkGenerationResult] = []

        # Indexa planos por entity_name
        plan_index: dict[str, InferredDataPlan] = {}
        for p in plans:
            key = (p.entity_name or p.screen.program_name or p.screen.title).upper()
            plan_index[key] = p

        # Determina ordem de geracao
        if dependency_graph and dependency_graph.generation_order:
            order = dependency_graph.generation_order
        else:
            # Sem grafo, gera na ordem original
            order = list(plan_index.keys())

        # Acumula datasets gerados para resolucao de FK
        generated_datasets: dict[str, Dataset] = {}
        resolver = RelationshipResolver()

        for entity_name in order:
            plan = plan_index.get(entity_name.upper())
            if not plan:
                continue

            # Prepara lookup_values com datasets pai
            lookup_values: dict[str, list[Any]] = {}
            deps: list[str] = []
            if dependency_graph:
                for p in dependency_graph.plans:
                    if p.entity_name.upper() == entity_name.upper():
                        deps = p.dependencies
                        break

            for dep in deps:
                dep_key = dep.upper()
                if dep_key in generated_datasets:
                    dep_ds = generated_datasets[dep_key]
                    # Extrai valores de PK para FK
                    pk_values = resolver.resolve_fk_values(
                        source_entity=entity_name,
                        source_field=f"id_{dep.lower()}",
                        parent_dataset=dep_ds,
                        quantity=plan.to_synthetic_schema(0).quantity or 100,
                    )
                    lookup_values[dep] = pk_values
                    lookup_values[dep_key] = pk_values

            # Gera com lookup_values populado
            quantity = 100
            if dependency_graph:
                for p in dependency_graph.plans:
                    if p.entity_name.upper() == entity_name.upper():
                        quantity = p.suggested_quantity
                        break

            result = self.generate_bulk(
                plan,
                quantity=quantity,
                seed=seed + len(results),
                sample_size=sample_size,
                lookup_values=lookup_values if lookup_values else None,
                strict_preflight=strict_preflight,
            )
            results.append(result)

            # Armazena dataset gerado para dependentes
            if result.dataset and not result.blocked:
                generated_datasets[entity_name.upper()] = result.dataset

        return results

    def _validate_record(
        self,
        record: dict[str, Any],
        rules: list[ConstraintRule],
    ) -> list[ConstraintViolation]:
        violations = ConstraintValidator.validate_record(record, rules)

        for rule in rules:
            value = record.get(rule.field)
            if value is None or value == "":
                continue

            field_lower = rule.field.lower()
            if rule.format == "email" and isinstance(value, str):
                if "@" not in value or "." not in value.split("@")[-1]:
                    violations.append(
                        ConstraintViolation(
                            field=rule.field,
                            rule="format",
                            value=value,
                            message=f"'{rule.field}' nao parece um e-mail valido",
                        )
                    )
            elif rule.format == "cpf" and isinstance(value, str):
                digits = re.sub(r"\D", "", value)
                if len(digits) != 11:
                    violations.append(
                        ConstraintViolation(
                            field=rule.field,
                            rule="format",
                            value=value,
                            message=f"'{rule.field}' nao parece um CPF valido",
                        )
                    )
            elif rule.format == "cep" and isinstance(value, str):
                digits = re.sub(r"\D", "", value)
                if len(digits) != 8:
                    violations.append(
                        ConstraintViolation(
                            field=rule.field,
                            rule="format",
                            value=value,
                            message=f"'{rule.field}' nao parece um CEP valido",
                        )
                    )

            if any(tok in field_lower for tok in ("qtd", "quantidade", "volume", "parcela")):
                if isinstance(value, (int, float)) and value <= 0:
                    violations.append(
                        ConstraintViolation(
                            field=rule.field,
                            rule="positive",
                            value=value,
                            message=f"'{rule.field}' deve ser maior que zero",
                        )
                    )

        return violations

    def _enrich_screen(self, screen: ScreenSchema) -> ScreenSchema:
        """Ajusta formatos e constraints usando heurísticas inferidas."""
        screen.screen_id = screen.screen_id or screen.program_name or screen.title

        for field in screen.fields:
            name = field.name.lower()
            if field.datatype == "cpf" and not field.format:
                field.format = "cpf"
                field.min_length = field.min_length or 14
                field.max_length = field.max_length or 14
            elif field.datatype == "email" and not field.format:
                field.format = "email"
                field.max_length = field.max_length or 120
            elif field.datatype == "phone" and not field.format:
                field.max_length = field.max_length or 16
            elif field.datatype == "cep" and not field.format:
                field.format = "cep"
                field.min_length = field.min_length or 9
                field.max_length = field.max_length or 9

            if field.datatype in ("integer", "number", "decimal", "money"):
                if any(tok in name for tok in ("qtd", "quantidade", "volume", "parcela")):
                    field.min_value = 1 if field.min_value is None else max(1, field.min_value)
                elif any(tok in name for tok in ("valor", "preco", "total", "desconto", "frete")):
                    field.min_value = 0 if field.min_value is None else max(0, field.min_value)

            if any(tok in name for tok in ("codigo", "cod_", "id_", "cpf", "cep")):
                field.required = True

        return screen

    def _infer_plans_from_raw_source(self, source_dir: str) -> list[InferredDataPlan]:
        """Fallback quando o pipeline principal nao devolve schemas utilizaveis."""
        plans: list[InferredDataPlan] = []
        title_re = re.compile(r'(?:TITLE|TITULO|CAPTION)\s+["\']([^"\']+)["\']', re.IGNORECASE)
        get_re = re.compile(r'@\s*\d+\s*,\s*\d+\s+GET\s+(\w+)', re.IGNORECASE)

        for file_path in sorted(Path(source_dir).rglob("*.prg")):
            try:
                content = file_path.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue

            field_names = list(dict.fromkeys(get_re.findall(content)))
            if not field_names:
                continue

            title_match = title_re.search(content)
            title = title_match.group(1) if title_match else file_path.stem
            screen = ScreenSchema(
                screen_id=file_path.stem,
                title=title,
                program_name=file_path.stem,
                fields=[
                    FieldSchema(
                        name=name,
                        datatype=self._infer_raw_datatype(name),
                    )
                    for name in field_names
                ],
            )
            screen = self._enrich_screen(screen)
            plans.append(
                InferredDataPlan(
                    plan_id=self._build_plan_id(source_dir, screen.screen_id, screen.program_name),
                    source_dir=source_dir,
                    screen=screen,
                    entity_name=screen.program_name,
                    field_rules=[ConstraintRule.from_field_schema(field) for field in screen.fields],
                )
            )

        return plans

    @staticmethod
    def _infer_raw_datatype(name: str) -> str:
        low = name.lower()
        if any(tok in low for tok in ("qtd", "qtde", "quantidade", "volume", "volumes", "parcela", "parcelas")):
            return "integer"
        if any(tok in low for tok in ("valor", "preco", "total", "desconto", "frete")):
            return "decimal"
        return SyntheticInferencer._infer_type_from_name(name)

    @staticmethod
    def _build_plan_id(source_dir: str, screen_id: str, entity_name: str) -> str:
        base = re.sub(r"[^a-z0-9]+", "-", f"{source_dir}-{screen_id}-{entity_name}".lower()).strip("-")
        return f"plan-{base}"

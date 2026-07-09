"""Validador de jornadas: completude, cobertura e realismo."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .journey import JourneyDefinition, JourneyStep
from ..source_analyzer.entity_catalog import EntityDefinition


@dataclass
class JourneyValidation:
    """Resultado da validacao de uma jornada."""
    journey_id: str = ""
    journey_name: str = ""
    is_valid: bool = True
    score: float = 0.0  # 0-100

    issues: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    # Metricas
    total_steps: int = 0
    navigate_steps: int = 0
    input_steps: int = 0
    submit_steps: int = 0
    verify_steps: int = 0

    # Cobertura vs entidade
    entity_name: str = ""
    entity_fields: int = 0
    fields_covered: int = 0
    fields_missing: list[str] = field(default_factory=list)
    crud_operations_covered: list[str] = field(default_factory=list)


class JourneyValidator:
    """Valida jornadas quanto a completude, cobertura e qualidade."""

    _RE_PLACEHOLDER = re.compile(r"\{\{(\w+)\.(\w+)\}\}")

    def validate(
        self,
        journey: JourneyDefinition,
        entity: EntityDefinition | None = None,
    ) -> JourneyValidation:
        """Valida uma jornada."""
        v = JourneyValidation(
            journey_id=journey.journey_id,
            journey_name=journey.name,
            total_steps=len(journey.steps),
        )

        if entity:
            v.entity_name = entity.name
            v.entity_fields = len(entity.fields)

        # Validar passos
        seen_screens: set[str] = set()
        for step in journey.steps:
            seen_screens.add(step.screen_id)

            if step.action == "navigate":
                v.navigate_steps += 1
            elif step.action == "input":
                v.input_steps += 1
                # Extrair campos do template
                for match in self._RE_PLACEHOLDER.finditer(step.input_template):
                    field_name = match.group(2)
                    if field_name not in seen_screens:
                        v.fields_covered += 1
            elif step.action == "submit":
                v.submit_steps += 1
            elif step.action == "verify":
                v.verify_steps += 1

        # Regras de validacao
        self._check_has_entry(v, journey)
        self._check_has_submit(v, journey)
        self._check_step_order(v, journey)
        self._check_duplicate_screens(v, journey)
        if entity:
            self._check_field_coverage(v, entity, journey)
            self._check_crud_coverage(v, journey)

        # Score
        v.score = self._calculate_score(v)
        v.is_valid = len(v.issues) == 0 and v.score >= 50

        return v

    def validate_all(
        self,
        journeys: list[JourneyDefinition],
        entities: dict[str, EntityDefinition] | None = None,
    ) -> list[JourneyValidation]:
        """Valida multiplas jornadas."""
        entities = entities or {}
        return [self.validate(j, entities.get(j.journey_id.replace("crud_", "").upper())) for j in journeys]

    def summary(self, validations: list[JourneyValidation]) -> dict:
        """Resumo de validacoes."""
        total = len(validations)
        valid = sum(1 for v in validations if v.is_valid)
        avg_score = round(sum(v.score for v in validations) / max(1, total), 1)
        all_issues = [i for v in validations for i in v.issues]

        return {
            "total_journeys": total,
            "valid_journeys": valid,
            "invalid_journeys": total - valid,
            "average_score": avg_score,
            "total_issues": len(all_issues),
            "top_issues": all_issues[:10],
        }

    # ------------------------------------------------------------------
    # Checks
    # ------------------------------------------------------------------

    def _check_has_entry(self, v: JourneyValidation, j: JourneyDefinition) -> None:
        if not j.entry_screen:
            v.issues.append("entry_screen nao definido")
        first_step = j.steps[0] if j.steps else None
        if not first_step or first_step.action != "navigate":
            v.issues.append("primeiro passo deve ser navigate (entrada)")

    def _check_has_submit(self, v: JourneyValidation, j: JourneyDefinition) -> None:
        has_submit = any(s.action == "submit" for s in j.steps)
        has_verify = any(s.action == "verify" for s in j.steps)
        if not has_submit and not has_verify:
            v.warnings.append("jornada sem passo submit ou verify")

    def _check_step_order(self, v: JourneyValidation, j: JourneyDefinition) -> None:
        orders = [s.step_order for s in j.steps]
        if orders != sorted(orders):
            v.issues.append("step_order fora de ordem")
        if len(set(orders)) != len(orders):
            v.issues.append("step_order com duplicatas")

    def _check_duplicate_screens(self, v: JourneyValidation, j: JourneyDefinition) -> None:
        screens = [s.screen_id for s in j.steps]
        dups = {s for s in screens if screens.count(s) > 2}
        if dups:
            v.warnings.append(f"telas repetidas: {', '.join(sorted(dups))}")

    def _check_field_coverage(self, v: JourneyValidation, entity: EntityDefinition, j: JourneyDefinition) -> None:
        entity_fields = {f.name.lower() for f in entity.fields}
        covered: set[str] = set()
        for step in j.steps:
            for match in self._RE_PLACEHOLDER.finditer(step.input_template):
                covered.add(match.group(2).lower())

        missing = entity_fields - covered
        v.fields_missing = sorted(missing)
        if missing:
            v.warnings.append(f"campos nao cobertos: {', '.join(sorted(missing)[:10])}")

    def _check_crud_coverage(self, v: JourneyValidation, j: JourneyDefinition) -> None:
        actions = {s.action for s in j.steps}
        crud_ops = []
        if "input" in actions:
            crud_ops.append("create")
        if "verify" in actions:
            crud_ops.append("read")
        if "submit" in actions:
            crud_ops.append("update")
        v.crud_operations_covered = crud_ops

    def _calculate_score(self, v: JourneyValidation) -> float:
        score = 100.0
        score -= len(v.issues) * 15
        score -= len(v.warnings) * 5
        if v.total_steps < 3:
            score -= 30
        if v.total_steps == 0:
            return 0.0
        action_mix = len({s.action for s in []}) / 5 * 10  # simplified
        score = max(0.0, min(100.0, score))
        return round(score, 1)

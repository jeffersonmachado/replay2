"""Gera jornadas CRUD automaticamente a partir de entidades descobertas.

Inclui geracao de JourneyReport que justifica cada decisao tomada."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from ..source_analyzer.entity_catalog import EntityDefinition
from ..source_analyzer.crud_detector import CRUDDetector, CRUDCoverage
from ..source_analyzer.field_classifier import FieldClassifier, FieldClassification
from .journey import JourneyDefinition, JourneyStep
from .journey_report import JourneyReport, build_journey_report


@dataclass
class CRUDJourneyConfig:
    """Configuracao para geracao de jornada CRUD."""
    entity: EntityDefinition
    coverage: CRUDCoverage
    field_classifications: list[FieldClassification]
    module_prefix: str = ""  # cad, fin, cop, etc.
    session_count: int = 10
    seed: int = 0


class CRUDJourneyGenerator:
    """Gera jornadas de validacao CRUD a partir de entidades."""

    # Acoes mapeadas para cada tipo de operacao
    _ACTION_MAP = {
        "create": "navigate",
        "read": "navigate",
        "update": "navigate",
        "delete": "navigate",
    }

    def generate(self, config: CRUDJourneyConfig) -> Optional[JourneyDefinition]:
        """Gera jornada CRUD completa para uma entidade."""
        if not config.coverage.is_complete:
            return None

        entity = config.entity
        entity_name = entity.name
        entity_lower = entity_name.lower()
        prefix = config.module_prefix or entity_name[:3].lower()

        steps: list[JourneyStep] = []
        order = 0

        # Passo 1: Acessar menu do modulo
        steps.append(JourneyStep(
            step_order=order,
            screen_id=f"menu_{prefix}",
            screen_title=f"Menu {prefix.upper()}",
            action="navigate",
            trigger="",
            description=f"Acessa menu do modulo {prefix.upper()}",
        ))
        order += 1

        # Passo 2: Abrir programa de cadastro
        prog_name = self._guess_program_name(entity_name, "include")
        steps.append(JourneyStep(
            step_order=order,
            screen_id=prog_name,
            screen_title=f"Inclusao de {entity_name}",
            action="navigate",
            trigger="ENTER",
            description=f"Abre tela de inclusao de {entity_name}",
        ))
        order += 1

        # Passo 3: Preencher campos (CREATE)
        input_lines: list[str] = []
        for fc in config.field_classifications:
            if fc.is_required or fc.semantic_category:
                placeholder = f"{{{{{entity_lower}.{fc.field_name}}}}}"
                input_lines.append(placeholder)
            else:
                input_lines.append("")

        if input_lines:
            steps.append(JourneyStep(
                step_order=order,
                screen_id=prog_name,
                screen_title=f"Preenchimento {entity_name}",
                action="input",
                trigger="F10",
                input_template="\n".join(input_lines),
                description=f"Preenche campos de {entity_name} e grava (F10)",
            ))
            order += 1

        # Passo 4: Confirmacao (CREATE)
        steps.append(JourneyStep(
            step_order=order,
            screen_id=f"{prog_name}_confirm",
            screen_title=f"Confirmacao {entity_name}",
            action="verify",
            trigger="ENTER",
            description=f"Confirma inclusao de {entity_name}",
        ))
        order += 1

        # Passo 5: Consulta (READ)
        consult_prog = self._guess_program_name(entity_name, "consult")
        steps.append(JourneyStep(
            step_order=order,
            screen_id=consult_prog,
            screen_title=f"Consulta de {entity_name}",
            action="navigate",
            trigger="",
            description=f"Abre consulta de {entity_name}",
        ))
        order += 1

        # Localiza pelo primeiro campo
        if config.field_classifications:
            first_field = config.field_classifications[0]
            steps.append(JourneyStep(
                step_order=order,
                screen_id=consult_prog,
                screen_title=f"Localizar {entity_name}",
                action="input",
                trigger="ENTER",
                input_template=f"{{{{{entity_lower}.{first_field.field_name}}}}}",
                description=f"Localiza {entity_name} pelo campo {first_field.field_name}",
            ))
            order += 1

        # Passo 6: Alteracao (UPDATE)
        alter_prog = self._guess_program_name(entity_name, "alter")
        steps.append(JourneyStep(
            step_order=order,
            screen_id=alter_prog,
            screen_title=f"Alteracao de {entity_name}",
            action="navigate",
            trigger="",
            description=f"Abre alteracao de {entity_name}",
        ))
        order += 1

        steps.append(JourneyStep(
            step_order=order,
            screen_id=alter_prog,
            screen_title=f"Confirmar alteracao {entity_name}",
            action="submit",
            trigger="F10",
            description=f"Confirma alteracao de {entity_name}",
        ))
        order += 1

        # Passo 7: Exclusao (DELETE)
        delete_prog = self._guess_program_name(entity_name, "delete")
        steps.append(JourneyStep(
            step_order=order,
            screen_id=delete_prog,
            screen_title=f"Exclusao de {entity_name}",
            action="navigate",
            trigger="",
            description=f"Abre exclusao de {entity_name}",
        ))
        order += 1

        steps.append(JourneyStep(
            step_order=order,
            screen_id=delete_prog,
            screen_title=f"Confirmar exclusao {entity_name}",
            action="submit",
            trigger="ENTER",
            description=f"Confirma exclusao (ENTER para SIM)",
        ))
        order += 1

        # Dataset bindings
        bindings: dict[str, str] = {}
        for step in steps:
            if step.action in ("input",):
                bindings[step.screen_id] = f"ds_{entity_lower}"

        return JourneyDefinition(
            journey_id=f"crud_{entity_lower}",
            name=f"CRUD {entity_name}",
            description=f"Jornada de validacao CRUD para {entity_name} ({len(entity.fields)} campos, {len(entity.operations)} operacoes)",
            category="crud",
            entry_screen=f"menu_{prefix}",
            steps=steps,
            dataset_bindings=bindings,
            tags=[prefix, "crud", entity_lower],
        )

    def generate_all(
        self,
        entities: list[EntityDefinition],
        coverages: list[CRUDCoverage],
        classifications: dict[str, list[FieldClassification]],
    ) -> list[JourneyDefinition]:
        """Gera jornadas para todas as entidades com CRUD completo."""
        journeys: list[JourneyDefinition] = []
        for entity, coverage in zip(entities, coverages):
            fields = classifications.get(entity.name, [])
            config = CRUDJourneyConfig(
                entity=entity,
                coverage=coverage,
                field_classifications=fields,
            )
            journey = self.generate(config)
            if journey:
                journeys.append(journey)
        return journeys


    def generate_with_report(self, config: CRUDJourneyConfig) -> tuple:
        """Gera jornada E relatorio de decisoes."""
        journey = self.generate(config)
        if journey is None:
            return None, None

        # Constroi nomes de programa inferidos
        entity = config.entity
        entity_name = entity.name
        prefix = config.module_prefix or entity_name[:3].lower()
        program_names = {
            "include": self._guess_program_name(entity_name, "include"),
            "query": self._guess_program_name(entity_name, "consult"),
            "update": self._guess_program_name(entity_name, "alter"),
            "delete": self._guess_program_name(entity_name, "delete"),
            "menu": f"menu_{prefix}",
        }

        # Conta passos
        input_count = sum(1 for s in journey.steps if s.action in ("input",))
        verify_count = sum(1 for s in journey.steps if s.action in ("verify", "submit"))

        report = build_journey_report(
            entity_name=entity_name,
            coverage=config.coverage,
            field_classifications=config.field_classifications,
            module_prefix=prefix,
            program_names=program_names,
            steps_count=len(journey.steps),
            input_count=input_count,
            verify_count=verify_count,
            dataset_bindings=journey.dataset_bindings or {},
        )
        return journey, report

    def generate_all_with_reports(
        self,
        entities: list,
        coverages: list,
        classifications: dict,
    ) -> tuple:
        """Gera jornadas E relatorios para todas as entidades."""
        journeys = []
        reports = []
        skipped = []
        for entity, coverage in zip(entities, coverages):
            fields = classifications.get(entity.name, [])
            config = CRUDJourneyConfig(
                entity=entity, coverage=coverage, field_classifications=fields,
            )
            journey, report = self.generate_with_report(config)
            if journey:
                journeys.append(journey)
                reports.append(report)
            else:
                reports.append(build_journey_report(
                    entity_name=entity.name, coverage=coverage,
                    field_classifications=fields, module_prefix=entity.name[:3].lower(),
                    program_names={}, steps_count=0, input_count=0, verify_count=0,
                    dataset_bindings={},
                ))
                skipped.append(entity.name)
        return journeys, reports, skipped


    @staticmethod
    def _guess_program_name(entity_name: str, operation: str) -> str:
        """Infere nome do programa a partir da entidade e operacao."""
        name = entity_name.upper()
        prefixes = {
            "include": ["CAD", "INC", "INCLUI", "CADAST"],
            "consult": ["CON", "CONS", "CONSUL", "PESQ", "LOC"],
            "alter": ["ALT", "ALTER", "ATUAL", "MANUT"],
            "delete": ["EXC", "EXCL", "DEL", "EXCLUI"],
        }
        for prefix in prefixes.get(operation, [name[:3]]):
            return f"{prefix}{name}"
        return f"{name[:3]}{name}"

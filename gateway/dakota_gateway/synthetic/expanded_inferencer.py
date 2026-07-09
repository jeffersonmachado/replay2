"""Inferência expandida: fluxos condicionais, dependências de dados e transações."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from .journey import JourneyDefinition, JourneyStep


# ---------------------------------------------------------------------------
# Padrões expandidos de inferência
# ---------------------------------------------------------------------------

_RE_IF = re.compile(r"IF\s+(.+?)(?:\s+THEN)?", re.IGNORECASE)
_RE_ELSE = re.compile(r"\bELSE\b", re.IGNORECASE)
_RE_ENDIF = re.compile(r"\bENDIF\b|ENDI\b", re.IGNORECASE)
_RE_DO_CASE = re.compile(r"DO\s+CASE", re.IGNORECASE)
_RE_CASE = re.compile(r"CASE\s+(.+?)$", re.IGNORECASE)
_RE_ENDCASE = re.compile(r"\bENDCASE\b|ENDC\b", re.IGNORECASE)
_RE_DO_WHILE = re.compile(r"DO\s+WHILE\s+(.+)", re.IGNORECASE)
_RE_ENDDO = re.compile(r"\bENDDO\b|ENDD\b", re.IGNORECASE)
_RE_FOR = re.compile(r"(?:FOR|SCAN)\s+(.+)", re.IGNORECASE)
_RE_ENDFOR = re.compile(r"\bENDFOR\b|ENDSCAN\b|NEXT\b", re.IGNORECASE)

# Dependências de dados: campo da tela A -> campo da tela B
_RE_ALIAS_FIELD = re.compile(r"(\w+)->(\w+)", re.IGNORECASE)  # alias->campo
_RE_SEEK_FIELD = re.compile(r"SEEK\s+(\w+)", re.IGNORECASE)
_RE_SET_RELATION = re.compile(r"SET\s+RELATION\s+(?:TO\s+)?(\w+)\s+INTO\s+(\w+)", re.IGNORECASE)
_RE_STORE_TO = re.compile(r"(?:STORE|v_\w+)\s+(.+?)\s+TO\s+(\w+)", re.IGNORECASE)

# Transações
_RE_BEGIN_TRANS = re.compile(r"(?:BEGIN\s+TRANS(?:ACTION)?|BEGINTRAN)", re.IGNORECASE)
_RE_COMMIT = re.compile(r"(?:COMMIT|END\s+TRANS(?:ACTION)?|ENDTRAN)", re.IGNORECASE)
_RE_ROLLBACK = re.compile(r"ROLLBACK", re.IGNORECASE)


@dataclass
class ConditionalFlow:
    """Fluxo condicional (IF/ELSE/DO CASE) detectado."""
    flow_type: str  # if, do_case, do_while, for
    condition: str = ""
    branches: list[list[str]] = field(default_factory=list)  # cada branch = lista de screen_ids
    source_file: str = ""
    start_line: int = 0
    end_line: int = 0


@dataclass
class DataDependency:
    """Dependência de dados entre telas/entidades."""
    source_screen: str = ""
    source_field: str = ""
    target_screen: str = ""
    target_field: str = ""
    dependency_type: str = ""  # seek, relation, store, alias_reference
    source_file: str = ""
    line_number: int = 0


@dataclass
class TransactionBlock:
    """Bloco de transação (BEGIN/COMMIT/ROLLBACK)."""
    screens: list[str] = field(default_factory=list)  # telas dentro da transação
    operations: list[str] = field(default_factory=list)  # insert, update, delete
    source_file: str = ""
    start_line: int = 0
    end_line: int = 0


class ExpandedInferencer:
    """Inferência expandida para jornadas: condicionais, dependências, transações."""

    def __init__(self, source_dir: Optional[str] = None):
        self.source_dir = Path(source_dir) if source_dir else None

    # ------------------------------------------------------------------
    # Fluxos condicionais
    # ------------------------------------------------------------------

    def infer_conditional_flows(self, source_dir: str) -> list[ConditionalFlow]:
        """Detecta IF/ELSE/ENDIF, DO CASE, DO WHILE, FOR nos fontes."""
        flows: list[ConditionalFlow] = []
        base = Path(source_dir)

        for prg_file in sorted(base.rglob("*.prg")):
            try:
                content = prg_file.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue

            lines = content.split("\n")
            flows.extend(self._extract_if_blocks(lines, str(prg_file)))
            flows.extend(self._extract_do_case_blocks(lines, str(prg_file)))
            flows.extend(self._extract_loop_blocks(lines, str(prg_file)))

        return flows

    def enrich_journey_with_conditionals(
        self,
        journey: JourneyDefinition,
        flows: list[ConditionalFlow],
    ) -> JourneyDefinition:
        """Enriquece jornada com passos condicionais detectados."""
        new_steps: list[JourneyStep] = list(journey.steps)

        for flow in flows:
            for branch_idx, branch_screens in enumerate(flow.branches):
                branch_label = f"{flow.flow_type}_branch_{branch_idx}"
                cond_step = JourneyStep(
                    step_order=len(new_steps),
                    screen_id=branch_label,
                    screen_title=f"[{flow.flow_type.upper()}] {flow.condition[:50]}",
                    action="conditional",
                    trigger="",
                    input_template="",
                    description=f"Branch {branch_idx}: {flow.condition[:80]}",
                )
                new_steps.append(cond_step)

                for screen_id in branch_screens:
                    new_steps.append(JourneyStep(
                        step_order=len(new_steps),
                        screen_id=screen_id,
                        screen_title=screen_id,
                        action="navigate" if branch_idx == 0 else "navigate_alt",
                        description=f"Dentro de {flow.flow_type}: {screen_id}",
                    ))

        journey.steps = new_steps
        return journey

    # ------------------------------------------------------------------
    # Dependências de dados
    # ------------------------------------------------------------------

    def infer_data_dependencies(self, source_dir: str) -> list[DataDependency]:
        """Detecta dependências de dados entre telas (alias->campo, SEEK, SET RELATION)."""
        deps: list[DataDependency] = []
        base = Path(source_dir)

        for prg_file in sorted(base.rglob("*.prg")):
            try:
                content = prg_file.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue

            lines = content.split("\n")
            for line_no, line in enumerate(lines, 1):
                # alias->campo: contrato->cliente
                for m in _RE_ALIAS_FIELD.finditer(line):
                    deps.append(DataDependency(
                        source_screen=m.group(1).upper(),
                        source_field=m.group(2).upper(),
                        target_screen="",
                        target_field="",
                        dependency_type="alias_reference",
                        source_file=str(prg_file),
                        line_number=line_no,
                    ))

                # SET RELATION TO campo INTO alias
                for m in _RE_SET_RELATION.finditer(line):
                    deps.append(DataDependency(
                        source_screen="",
                        source_field=m.group(1).upper(),
                        target_screen=m.group(2).upper(),
                        target_field=m.group(1).upper(),
                        dependency_type="relation",
                        source_file=str(prg_file),
                        line_number=line_no,
                    ))

        return deps

    def enrich_journey_with_dependencies(
        self,
        journey: JourneyDefinition,
        deps: list[DataDependency],
    ) -> JourneyDefinition:
        """Adiciona dependências de dados entre passos da jornada."""
        for dep in deps:
            for step in journey.steps:
                if step.screen_id.upper() == dep.source_screen.upper():
                    dep_ref = f"{dep.source_screen}.{dep.source_field}"
                    if dep_ref not in step.depends_on:
                        step.depends_on.append(dep_ref)
                if step.screen_id.upper() == dep.target_screen.upper():
                    dep_ref = f"{dep.source_screen}.{dep.source_field}"
                    if dep_ref not in step.depends_on:
                        step.depends_on.append(dep_ref)

        return journey

    # ------------------------------------------------------------------
    # Transações
    # ------------------------------------------------------------------

    def infer_transactions(self, source_dir: str) -> list[TransactionBlock]:
        """Detecta blocos BEGIN TRANSACTION / COMMIT / ROLLBACK."""
        transactions: list[TransactionBlock] = []
        base = Path(source_dir)

        for prg_file in sorted(base.rglob("*.prg")):
            try:
                content = prg_file.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue

            lines = content.split("\n")
            current_tx: Optional[TransactionBlock] = None

            for line_no, line in enumerate(lines, 1):
                if _RE_BEGIN_TRANS.search(line):
                    current_tx = TransactionBlock(
                        source_file=str(prg_file),
                        start_line=line_no,
                    )

                elif current_tx and (_RE_COMMIT.search(line) or _RE_ROLLBACK.search(line)):
                    current_tx.end_line = line_no
                    transactions.append(current_tx)
                    current_tx = None

                elif current_tx:
                    # Detectar operações dentro da transação
                    op_match = re.search(
                        r"(REPLACE|INSERT|UPDATE|DELETE|APPEND)\s",
                        line, re.IGNORECASE,
                    )
                    if op_match:
                        current_tx.operations.append(op_match.group(1).lower())

                    # Detectar telas chamadas
                    do_match = re.search(r"DO\s+(\w+)", line, re.IGNORECASE)
                    if do_match:
                        current_tx.screens.append(do_match.group(1).upper())

        return transactions

    def enrich_journey_with_transactions(
        self,
        journey: JourneyDefinition,
        transactions: list[TransactionBlock],
    ) -> JourneyDefinition:
        """Adiciona passos de transação à jornada."""
        for tx in transactions:
            tx_step = JourneyStep(
                step_order=len(journey.steps),
                screen_id=f"tx_{tx.start_line}",
                screen_title=f"TRANSAÇÃO: {', '.join(tx.operations[:3])}",
                action="transaction",
                trigger="",
                description=f"BEGIN...COMMIT/ROLLBACK com {len(tx.operations)} operações",
            )
            journey.steps.append(tx_step)

        return journey

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_if_blocks(lines: list[str], source_file: str) -> list[ConditionalFlow]:
        flows: list[ConditionalFlow] = []
        in_if = False
        in_else = False
        if_condition = ""
        if_branch: list[str] = []
        else_branch: list[str] = []

        for line_no, line in enumerate(lines, 1):
            m = _RE_IF.search(line)
            if m and not _RE_ENDIF.search(line):
                in_if = True
                in_else = False
                if_condition = m.group(1).strip()
                if_branch = []
                else_branch = []
                continue

            if in_if and _RE_ELSE.search(line):
                in_else = True
                continue

            if in_if and _RE_ENDIF.search(line):
                flows.append(ConditionalFlow(
                    flow_type="if",
                    condition=if_condition,
                    branches=[if_branch[:], else_branch[:]],
                    source_file=source_file,
                    start_line=line_no - 50,
                    end_line=line_no,
                ))
                in_if = False
                in_else = False
                continue

            if in_if:
                # Coletar screen_id (chamadas DO)
                do_m = re.search(r"DO\s+(\w+(?:/\w+)*)", line, re.IGNORECASE)
                if do_m:
                    screen = do_m.group(1).replace("/", "_").upper()
                    if in_else:
                        else_branch.append(screen)
                    else:
                        if_branch.append(screen)

        return flows

    @staticmethod
    def _extract_do_case_blocks(lines: list[str], source_file: str) -> list[ConditionalFlow]:
        flows: list[ConditionalFlow] = []
        in_case = False
        branches: list[list[str]] = []
        current_branch: list[str] = []
        condition = "DO CASE"

        for line_no, line in enumerate(lines, 1):
            if _RE_DO_CASE.search(line):
                in_case = True
                branches = []
                current_branch = []
                continue

            if in_case and _RE_CASE.search(line):
                if current_branch:
                    branches.append(current_branch)
                current_branch = []
                cond_match = _RE_CASE.search(line)
                if cond_match:
                    condition = f"DO CASE: {cond_match.group(1).strip()}"
                continue

            if in_case and _RE_ENDCASE.search(line):
                if current_branch:
                    branches.append(current_branch)
                flows.append(ConditionalFlow(
                    flow_type="do_case",
                    condition=condition,
                    branches=branches,
                    source_file=source_file,
                    start_line=line_no - 50,
                    end_line=line_no,
                ))
                in_case = False
                continue

            if in_case:
                do_m = re.search(r"DO\s+(\w+(?:/\w+)*)", line, re.IGNORECASE)
                if do_m:
                    screen = do_m.group(1).replace("/", "_").upper()
                    current_branch.append(screen)

        return flows

    @staticmethod
    def _extract_loop_blocks(lines: list[str], source_file: str) -> list[ConditionalFlow]:
        flows: list[ConditionalFlow] = []
        in_loop = False
        loop_type = ""
        loop_condition = ""
        loop_screens: list[str] = []

        for line_no, line in enumerate(lines, 1):
            dw = _RE_DO_WHILE.search(line)
            fm = _RE_FOR.search(line)

            if dw:
                in_loop = True
                loop_type = "do_while"
                loop_condition = dw.group(1).strip()
                loop_screens = []
                continue
            elif fm:
                in_loop = True
                loop_type = "for"
                loop_condition = fm.group(1).strip()
                loop_screens = []
                continue

            if in_loop and _RE_ENDDO.search(line):
                flows.append(ConditionalFlow(
                    flow_type=loop_type,
                    condition=loop_condition,
                    branches=[loop_screens],
                    source_file=source_file,
                    start_line=line_no - 50,
                    end_line=line_no,
                ))
                in_loop = False
                continue

            if in_loop:
                do_m = re.search(r"DO\s+(\w+(?:/\w+)*)", line, re.IGNORECASE)
                if do_m:
                    screen = do_m.group(1).replace("/", "_").upper()
                    loop_screens.append(screen)

        return flows

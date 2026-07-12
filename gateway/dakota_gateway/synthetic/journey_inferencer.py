from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from .journey import JourneyDefinition, JourneyStep, JourneyDataset
from ..source_analyzer.parser import SourceParser
from ..source_analyzer.entity_catalog import ScreenDefinition
from ..source_analyzer.source_inventory import collect_preferred_source_files

# Padrões para detectar navegação em código legado Recital/xBase
_RE_DO_PROGRAM = re.compile(r"DO\s+(\w+(?:/\w+)*)", re.IGNORECASE)
_RE_PROCEDURE = re.compile(r"(?:PROCEDURE|FUNCTION)\s+(\w+)", re.IGNORECASE)
_RE_MENU_OPTION = re.compile(r"(?:PROMPT|MENU\s+OPTION|OPTION)\s+['\"]([^'\"]+)['\"]", re.IGNORECASE)
_RE_MENU_CALL = re.compile(r"(?:DO|RUN|CALL)\s+['\"]?(\w+(?:/\w+)*)['\"]?", re.IGNORECASE)
_RE_KEY_TRIGGER = re.compile(r"(?:ON\s+KEY|SET\s+KEY|KEYBOARD)\s+(?:LABEL\s+)?['\"]?(\w+)['\"]?", re.IGNORECASE)
_RE_SCREEN_TITLE = re.compile(r"(?:TITLE|TITULO|HEADER)\s+['\"]([^'\"]+)['\"]", re.IGNORECASE)
_RE_AT_SAY = re.compile(r"@\s+\d+\s*,\s*\d+\s+SAY\s+['\"]([^'\"]+)['\"]", re.IGNORECASE)


class JourneyInferencer:
    """Infere jornadas a partir de código-fonte, capturas e estruturas de menu."""

    def __init__(self, source_dir: Optional[str] = None):
        self.source_dir = Path(source_dir) if source_dir else None
        self._parser: Optional[SourceParser] = None

    # ------------------------------------------------------------------
    # Inferência a partir de código-fonte
    # ------------------------------------------------------------------

    def infer_from_source(self, source_dir: str) -> list[JourneyDefinition]:
        """Analisa código-fonte e infere jornadas baseadas em chamadas DO e menus."""
        self.source_dir = Path(source_dir)
        self._parser = SourceParser(str(source_dir))

        journeys: list[JourneyDefinition] = []
        source_files = self._collect_source_files()

        # Mapa: programa → procedimentos chamados
        call_graph: dict[str, list[str]] = {}
        # Mapa: programa → título de tela
        screen_titles: dict[str, str] = {}

        for file_path in source_files:
            try:
                content = file_path.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue

            prog_name = file_path.stem.upper()
            calls = self._extract_calls(content)
            if calls:
                call_graph[prog_name] = calls

            title = self._extract_title(content)
            if title:
                screen_titles[prog_name] = title

        # Agrupar por módulo (prefixo de 3 letras: cad, cop, fin, etc.)
        module_groups: dict[str, list[str]] = {}
        for prog in call_graph:
            prefix = prog[:3].lower() if len(prog) >= 3 else prog.lower()
            module_groups.setdefault(prefix, []).append(prog)

        # Criar jornada por módulo
        for module, programs in module_groups.items():
            if len(programs) < 2:
                continue

            # Ordenar programas do módulo
            programs_sorted = sorted(set(programs))

            steps: list[JourneyStep] = []
            step_order = 0

            # Primeiro passo: entrada pelo menu do módulo
            steps.append(JourneyStep(
                step_order=step_order,
                screen_id=f"menu_{module}",
                screen_title=f"Menu {module.upper()}",
                action="navigate",
                trigger="ENTER",
                description=f"Acessa módulo {module.upper()}",
            ))
            step_order += 1

            for prog in programs_sorted[:10]:  # Limitar a 10 programas por jornada
                title = screen_titles.get(prog, prog)
                steps.append(JourneyStep(
                    step_order=step_order,
                    screen_id=prog,
                    screen_signature=prog,
                    screen_title=title,
                    action="navigate",
                    trigger="ENTER",
                    description=f"Abre programa {prog}",
                ))
                step_order += 1

                # Adicionar input genérico
                steps.append(JourneyStep(
                    step_order=step_order,
                    screen_id=prog,
                    screen_title=title,
                    action="input",
                    input_template="{{dados.campos}}",
                    description=f"Preenche campos de {prog}",
                ))
                step_order += 1

            if steps:
                journeys.append(JourneyDefinition(
                    journey_id=f"modulo_{module}",
                    name=f"Jornada do Módulo {module.upper()}",
                    description=f"Navegação completa pelo módulo {module.upper()}",
                    category=module,
                    entry_screen=f"menu_{module}",
                    steps=steps,
                    tags=[module, "auto_inferida"],
                ))

        return journeys

    def infer_from_menus(self, menu_file: str) -> Optional[JourneyDefinition]:
        """Analisa um arquivo de menu e infere a jornada de opções."""
        path = Path(menu_file)
        if not path.exists():
            return None

        content = path.read_text(encoding="utf-8", errors="replace")
        steps: list[JourneyStep] = []
        step_order = 0

        # Detectar título do menu
        title_match = _RE_SCREEN_TITLE.search(content)
        menu_title = title_match.group(1) if title_match else path.stem

        # Entrada
        steps.append(JourneyStep(
            step_order=step_order,
            screen_id="menu_principal",
            screen_title=menu_title,
            action="navigate",
            trigger="ENTER",
            description="Abre menu principal",
        ))
        step_order += 1

        # Extrair opções de menu
        for m in _RE_MENU_OPTION.finditer(content):
            option_text = m.group(1)
            steps.append(JourneyStep(
                step_order=step_order,
                screen_id=f"menu_option_{step_order}",
                screen_title=option_text,
                action="select",
                trigger=str(step_order),  # número da opção
                description=f"Seleciona: {option_text}",
            ))
            step_order += 1

        # Extrair chamadas DO
        for m in _RE_DO_PROGRAM.finditer(content):
            called_prog = m.group(1).replace("/", "_").upper()
            steps.append(JourneyStep(
                step_order=step_order,
                screen_id=called_prog,
                screen_title=called_prog,
                action="navigate",
                trigger="ENTER",
                description=f"Executa {called_prog}",
            ))
            step_order += 1

        return JourneyDefinition(
            journey_id=f"menu_{path.stem}",
            name=f"Jornada: {menu_title}",
            description=f"Navegação pelas opções do menu {menu_title}",
            category="menu",
            entry_screen="menu_principal",
            steps=steps,
            tags=["menu", "auto_inferida"],
        )

    # ------------------------------------------------------------------
    # Inferência a partir de capturas (screen transitions)
    # ------------------------------------------------------------------

    def infer_from_captures(self, capture_dir: str) -> list[JourneyDefinition]:
        """Analisa diretório de capturas e infere jornadas por transições de tela."""
        cap_path = Path(capture_dir)
        if not cap_path.exists():
            return []

        # Coletar arquivos de log de captura
        log_files = sorted(cap_path.rglob("*.log"))
        if not log_files:
            log_files = sorted(cap_path.rglob("*"))

        transitions: list[tuple[str, str, str]] = []  # (from_sig, to_sig, input)
        seen_sigs: set[str] = set()

        for log_file in log_files[:5]:  # Limitar a 5 arquivos
            try:
                content = log_file.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue

            # Extrair screen signatures e inputs
            sigs = re.findall(r"screen_sig['\"]?\s*[:=]\s*['\"]?([^'\"\n]+)", content)
            inputs = re.findall(r"key_text['\"]?\s*[:=]\s*['\"]?([^'\"\n]*)", content)

            for i in range(len(sigs) - 1):
                from_sig = sigs[i]
                to_sig = sigs[i + 1]
                inp = inputs[i] if i < len(inputs) else ""
                transitions.append((from_sig, to_sig, inp))
                seen_sigs.add(from_sig)
                seen_sigs.add(to_sig)

        if not transitions:
            return []

        # Agrupar transições por tela inicial
        journeys: list[JourneyDefinition] = []
        by_start: dict[str, list[tuple]] = {}
        for t in transitions:
            by_start.setdefault(t[0], []).append(t)

        for start_sig, trans_list in list(by_start.items())[:5]:
            steps: list[JourneyStep] = []
            for i, (from_s, to_s, inp) in enumerate(trans_list[:10]):
                steps.append(JourneyStep(
                    step_order=i,
                    screen_signature=from_s,
                    action="input",
                    input_template=inp,
                    expected_signature=to_s,
                    description=f"Transição {from_s} → {to_s}",
                ))

            journeys.append(JourneyDefinition(
                journey_id=f"capture_{start_sig[:20]}",
                name=f"Jornada: {start_sig[:30]}",
                description=f"Jornada inferida de capturas a partir de {start_sig[:40]}",
                category="capture",
                entry_screen=start_sig,
                steps=steps,
                tags=["capture", "auto_inferida"],
            ))

        return journeys

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _collect_source_files(self) -> list[Path]:
        if not self.source_dir:
            return []
        return collect_preferred_source_files(self.source_dir, {".prg", ".src", ".dbo"})

    @staticmethod
    def _extract_calls(content: str) -> list[str]:
        """Extrai chamadas DO/PROCEDURE de um arquivo."""
        calls: list[str] = []
        for m in _RE_DO_PROGRAM.finditer(content):
            call = m.group(1).strip()
            if call:
                calls.append(call)
        return calls

    @staticmethod
    def _extract_title(content: str) -> str:
        """Tenta extrair título de tela/programa."""
        # Padrão: *PROG: titulo
        m = re.search(r"\*PROG:\s*(.+)", content)
        if m:
            return m.group(1).strip()
        # TITLE "titulo"
        m = _RE_SCREEN_TITLE.search(content)
        if m:
            return m.group(1).strip()
        return ""

"""Descoberta automática de telas: navega pelo sistema e mapeia estrutura."""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from .journey import JourneyDefinition, JourneyStep


@dataclass
class DiscoveredScreen:
    """Tela descoberta durante exploração automática."""
    screen_id: str = ""
    screen_signature: str = ""
    title: str = ""
    fields_detected: list[str] = field(default_factory=list)
    menu_options: list[str] = field(default_factory=list)
    parent_screen: str = ""
    depth: int = 0
    raw_text: str = ""


@dataclass
class ExplorationResult:
    """Resultado da exploração automática."""
    screens: list[DiscoveredScreen] = field(default_factory=list)
    total_screens: int = 0
    max_depth: int = 0
    duration_ms: int = 0
    journey: Optional[JourneyDefinition] = None


class ScreenExplorer:
    """Explora automaticamente um sistema legado, descobrindo telas e menus.

    Estratégia:
    1. Conecta ao sistema
    2. Navega pelo menu principal (opções numéricas)
    3. Para cada opção, captura a tela resultante
    4. Detecta campos, prompts e sub-menus
    5. Constrói mapa completo de navegação
    """

    # Padrões para detectar campos e menus em telas
    _FIELD_PATTERNS = [
        (re.compile(r"(\w[\w\s]{1,30}):\s*[\._]{2,}"), "prompt_field"),   # "Nome: ....."
        (re.compile(r"(\w[\w\s]{1,30})\s*\.\.\.\.\.+\s*\[?\s*\]?"), "prompt_field"),  # "Nome ....."
        (re.compile(r"\[(\w[\w\s]{1,30})\]\s*_+"), "bracket_field"),  # "[Nome] ____"
        (re.compile(r"(\d+)\.\s+(.+)"), "menu_option"),  # "1. Cadastros"
    ]

    _MENU_PATTERNS = [
        re.compile(r"(?:MENU|OP[ÇC][ÃA]O|OPCAO|SELECIONE|ESCOLHA)", re.IGNORECASE),
        re.compile(r"\d+\s*[\.\-)]\s*\w+"),  # "1. Cadastros" ou "1- Cadastros"
    ]

    def __init__(self, mode: str = "passive"):
        self.mode = mode  # passive (análise de fonte) ou active (conexão real)
        self._visited: set[str] = set()

    # ------------------------------------------------------------------
    # Modo passivo: análise de código-fonte
    # ------------------------------------------------------------------

    def explore_from_source(self, source_dir: str) -> ExplorationResult:
        """Analisa código-fonte e descobre estrutura de telas."""
        from ..source_analyzer.parser import SourceParser

        result = ExplorationResult()
        start_ms = int(time.time() * 1000)
        parser = SourceParser(source_dir)
        entities, screens = parser.parse_all()

        discovered: list[DiscoveredScreen] = []

        for src_screen in screens:
            ds = DiscoveredScreen(
                screen_id=src_screen.program_name or src_screen.title,
                screen_signature=src_screen.screen_signature,
                title=src_screen.title or src_screen.program_name,
                fields_detected=[f.name for f in src_screen.fields],
            )

            # Detectar se é menu (tem opções numéricas nos campos)
            has_menu = any(
                re.match(r"^\d+[\.\-\)]", f.name) for f in src_screen.fields
            )
            if has_menu:
                ds.menu_options = [f.name for f in src_screen.fields if re.match(r"^\d+[\.\-\)]", f.name)]

            discovered.append(ds)

        # Agrupar por módulo e construir jornada de exploração
        result.screens = discovered
        result.total_screens = len(discovered)

        # Construir jornada de exploração
        steps: list[JourneyStep] = []
        for i, ds in enumerate(discovered[:30]):  # Limitar a 30 telas
            steps.append(JourneyStep(
                step_order=i * 2,
                screen_id=ds.screen_id,
                screen_title=ds.title,
                screen_signature=ds.screen_signature,
                action="navigate",
                trigger="ENTER" if i == 0 else "",
                description=f"Tela: {ds.title}",
            ))

        result.journey = JourneyDefinition(
            journey_id="exploration",
            name="Jornada de Exploração Automática",
            description=f"Mapa de {len(discovered)} telas descobertas",
            category="exploration",
            steps=steps,
            tags=["auto_explored"],
        )

        result.duration_ms = int(time.time() * 1000) - start_ms
        return result

    # ------------------------------------------------------------------
    # Modo ativo: navegação real (conceitual)
    # ------------------------------------------------------------------

    def explore_active(self, target_host: str = "", target_user: str = "",
                       max_depth: int = 3, max_screens: int = 50) -> ExplorationResult:
        """Navega ativamente pelo sistema (requer conexão SSH).

        Fluxo:
        1. Conecta → captura menu principal
        2. Para cada opção do menu, navega → captura tela
        3. Recursivamente explora sub-menus até max_depth
        4. Constrói mapa completo
        """
        result = ExplorationResult()
        start_ms = int(time.time() * 1000)
        self._visited = set()

        if self.mode == "active" and target_host:
            # Explorar a partir do menu principal
            main_screen = self._capture_and_analyze(target_host, target_user, "")
            if main_screen:
                self._visited.add(main_screen.screen_signature)
                result.screens.append(main_screen)

                # Explorar recursivamente
                self._explore_recursive(
                    main_screen, target_host, target_user,
                    depth=1, max_depth=max_depth, result=result,
                    max_screens=max_screens,
                )

        result.total_screens = len(result.screens)
        result.max_depth = max((s.depth for s in result.screens), default=0)
        result.duration_ms = int(time.time() * 1000) - start_ms
        return result

    # ------------------------------------------------------------------
    # Análise de tela
    # ------------------------------------------------------------------

    def analyze_screen(self, screen_text: str) -> DiscoveredScreen:
        """Analisa texto de tela e extrai campos, opções e título."""
        ds = DiscoveredScreen(raw_text=screen_text)

        # Extrair título
        lines = screen_text.split("\n")
        for line in lines[:5]:
            line = line.strip()
            if len(line) > 3 and not re.match(r"^[+\-|=]+$", line):
                ds.title = line[:60]
                break

        # Detectar campos
        for pattern, field_type in self._FIELD_PATTERNS:
            for m in pattern.finditer(screen_text):
                if field_type == "menu_option":
                    ds.menu_options.append(m.group(2).strip()[:40])
                else:
                    ds.fields_detected.append(m.group(1).strip())

        # Gerar signature simplificada
        ds.screen_signature = self._generate_signature(screen_text)

        return ds

    def is_menu_screen(self, screen_text: str) -> bool:
        """Determina se a tela é um menu."""
        for pattern in self._MENU_PATTERNS:
            if pattern.search(screen_text):
                return True
        return False

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _explore_recursive(self, screen: DiscoveredScreen, host: str, user: str,
                           depth: int, max_depth: int, result: ExplorationResult,
                           max_screens: int):
        """Explora recursivamente a partir de um menu."""
        if depth > max_depth or len(result.screens) >= max_screens:
            return

        for i, option in enumerate(screen.menu_options[:10]):
            if len(result.screens) >= max_screens:
                break

            # Simular seleção da opção
            option_num = str(i + 1)
            child = self._capture_and_analyze(host, user, option_num)
            if child and child.screen_signature not in self._visited:
                self._visited.add(child.screen_signature)
                child.parent_screen = screen.screen_signature
                child.depth = depth
                result.screens.append(child)

                # Se for menu, explorar recursivamente
                if child.menu_options:
                    self._explore_recursive(
                        child, host, user, depth + 1, max_depth, result, max_screens,
                    )

    def _capture_and_analyze(self, host: str, user: str, input_str: str) -> Optional[DiscoveredScreen]:
        """Captura tela (simulada em dry_run) e analisa."""
        # dry_run: gerar tela simulada
        screen_text = f"+-- MENU PRINCIPAL --+\n"
        if input_str:
            screen_text += f"| Tela após opção {input_str} |\n"
        else:
            screen_text += "| 1. Cadastros         |\n| 2. Financeiro        |\n| 3. Relatorios        |\n"
        screen_text += "+" + "-" * 30 + "+"

        return self.analyze_screen(screen_text)

    @staticmethod
    def _generate_signature(screen_text: str) -> str:
        """Gera assinatura simplificada de tela."""
        lines = [l.strip() for l in screen_text.split("\n") if l.strip()]
        num_lines = len(lines)
        max_width = max((len(l) for l in lines), default=0)
        return f"L={num_lines} W={max_width}"

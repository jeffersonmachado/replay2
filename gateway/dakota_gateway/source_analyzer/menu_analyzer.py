"""Analisa hierarquia de menus extraindo arvore de navegacao."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class MenuNode:
    """No da arvore de navegacao (menu ou programa)."""
    node_id: str = ""
    label: str = ""
    node_type: str = "menu"  # menu, program, option
    parent_id: str = ""
    program_name: str = ""
    key: str = ""  # tecla de atalho ou numero da opcao
    source_file: str = ""
    source_line: int = 0
    children: list[MenuNode] = field(default_factory=list)
    depth: int = 0


@dataclass
class MenuTree:
    """Arvore completa de navegacao do sistema."""
    root: Optional[MenuNode] = None
    total_menus: int = 0
    total_programs: int = 0
    max_depth: int = 0
    orphan_programs: list[str] = field(default_factory=list)


class MenuAnalyzer:
    """Extrai hierarquia de menus de codigo-fonte legado (Recital/xBase)."""

    _RE_MENU_OPTION = re.compile(
        r"(?:@\s+\d+\s*,\s*\d+\s+PROMPT|MENU\s+OPTION)\s+['\"]([^'\"]+)['\"]",
        re.IGNORECASE,
    )
    _RE_DO_PROGRAM = re.compile(
        r"DO\s+(?:['\"]?)(\w+(?:/\w+)*)(?:['\"]?)",
        re.IGNORECASE,
    )
    _RE_MENU_LABEL = re.compile(
        r"(?:TITLE|TITULO|HEADER)\s+['\"]([^'\"]+)['\"]",
        re.IGNORECASE,
    )
    _RE_KEY_LABEL = re.compile(
        r"ON\s+KEY\s+(?:LABEL\s+)?(\w+)\s+DO\s+(\w+)",
        re.IGNORECASE,
    )
    _RE_OPTION_NUM = re.compile(
        r"@\s+\d+\s*,\s*\d+\s+SAY\s+['\"]\s*(\d+)[\.\-\)]\s*([^'\"]+)['\"]",
        re.IGNORECASE,
    )

    def __init__(self, source_dir: str = ""):
        self.source_dir = Path(source_dir) if source_dir else None

    def analyze(self, source_dir: str) -> MenuTree:
        """Analisa diretorio e constroi arvore de menus."""
        base = Path(source_dir)
        tree = MenuTree()

        # Mapa: nome do arquivo -> MenuNode
        menu_nodes: dict[str, MenuNode] = {}
        # Programas chamados por menus
        all_programs: set[str] = set()
        programs_in_menus: set[str] = set()

        # Primeira passagem: encontrar menus
        for menu_file in sorted(base.rglob("menu*.prg")):
            node = self._parse_menu_file(menu_file)
            if node:
                menu_nodes[str(menu_file)] = node

        for menu_file in sorted(base.rglob("*.prg")):
            if str(menu_file) in menu_nodes:
                continue
            content = self._read_file(menu_file)
            if not content:
                continue

            # Detecta se e menu (tem opcoes numericas + DO)
            has_options = bool(self._RE_OPTION_NUM.search(content))
            has_do = bool(self._RE_DO_PROGRAM.search(content))
            has_title = bool(self._RE_MENU_LABEL.search(content))

            if has_options and (has_do or has_title):
                node = self._parse_menu_file(menu_file)
                if node:
                    menu_nodes[str(menu_file)] = node

        # Segunda passagem: extrair chamadas DO de todos os arquivos
        for prg_file in sorted(base.rglob("*.prg")):
            content = self._read_file(prg_file)
            if not content:
                continue

            for match in self._RE_DO_PROGRAM.finditer(content):
                prog_name = match.group(1).upper()
                all_programs.add(prog_name)

                if str(prg_file) in menu_nodes:
                    programs_in_menus.add(prog_name)
                    menu = menu_nodes[str(prg_file)]
                    child = MenuNode(
                        node_id=prog_name,
                        label=prog_name,
                        node_type="program",
                        parent_id=menu.node_id,
                        program_name=prog_name,
                        source_file=str(prg_file),
                    )
                    menu.children.append(child)

        # Terceira passagem: detectar hierarquia
        # Menus que sao chamados por outros menus
        for menu_path, node in menu_nodes.items():
            for other_path, other_node in menu_nodes.items():
                if menu_path == other_path:
                    continue
                other_content = self._read_file(Path(other_path))
                if not other_content:
                    continue
                # Se o outro menu chama este via DO
                menu_stem = Path(menu_path).stem.upper()
                if re.search(rf"\bDO\s+['\"]?{re.escape(menu_stem)}\b", other_content, re.IGNORECASE):
                    node.parent_id = other_node.node_id
                    node.depth = other_node.depth + 1
                    other_node.children.append(node)

        # Montar arvore
        root_menus = [n for n in menu_nodes.values() if not n.parent_id]
        if len(root_menus) == 1:
            tree.root = root_menus[0]
        elif root_menus:
            tree.root = MenuNode(
                node_id="root",
                label="Sistema",
                node_type="menu",
                children=root_menus,
            )

        tree.total_menus = len(menu_nodes)
        tree.total_programs = len(all_programs)
        tree.orphan_programs = sorted(all_programs - programs_in_menus)

        if tree.root:
            tree.max_depth = self._calc_max_depth(tree.root)

        return tree

    def _parse_menu_file(self, file_path: Path) -> Optional[MenuNode]:
        content = self._read_file(file_path)
        if not content:
            return None

        node_id = str(file_path)
        label = file_path.stem

        # Extrair titulo
        title_match = self._RE_MENU_LABEL.search(content)
        if title_match:
            label = title_match.group(1).strip()

        node = MenuNode(
            node_id=node_id,
            label=label,
            node_type="menu",
            source_file=node_id,
        )

        # Extrair opcoes numericas
        for match in self._RE_OPTION_NUM.finditer(content):
            num = match.group(1).strip()
            opt_label = match.group(2).strip()
            child = MenuNode(
                node_id=f"{node_id}#{num}",
                label=f"{num}. {opt_label}",
                node_type="option",
                parent_id=node_id,
                key=num,
                source_file=node_id,
            )
            node.children.append(child)

        return node

    def _calc_max_depth(self, node: MenuNode) -> int:
        if not node.children:
            return node.depth
        return max(self._calc_max_depth(c) for c in node.children)

    @staticmethod
    def _read_file(path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return ""

    @staticmethod
    def to_dict(node: MenuNode) -> dict:
        """Serializa arvore para JSON."""
        return {
            "node_id": node.node_id,
            "label": node.label,
            "node_type": node.node_type,
            "program_name": node.program_name,
            "key": node.key,
            "source_file": node.source_file,
            "depth": node.depth,
            "children": [MenuAnalyzer.to_dict(c) for c in node.children],
        }

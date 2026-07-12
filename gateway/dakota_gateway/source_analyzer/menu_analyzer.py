"""Analisa hierarquia de menus extraindo arvore de navegacao."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .source_inventory import collect_preferred_source_files


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
    depth: int = 0
    route_code: str = ""
    module_key: str = ""
    children: list[MenuNode] = field(default_factory=list)


@dataclass
class MenuTree:
    """Arvore completa de navegacao do sistema."""
    root: Optional[MenuNode] = None
    total_menus: int = 0
    total_programs: int = 0
    max_depth: int = 0
    orphan_programs: list[str] = field(default_factory=list)


@dataclass
class MenuBlock:
    label: str = ""
    route_code: str = ""
    module_key: str = ""
    source_line: int = 0
    options: list[tuple[str, str]] = field(default_factory=list)
    programs: list[str] = field(default_factory=list)


class MenuAnalyzer:
    """Extrai hierarquia de menus de codigo-fonte legado (Recital/xBase)."""

    _RE_MENU_LABEL = re.compile(
        r"(?:TITLE|TITULO|HEADER)\s+['\"]([^'\"]+)['\"]",
        re.IGNORECASE,
    )
    _RE_ROUTINE_LABEL = re.compile(
        r"rotina\s*=\s*(?:fTraduz\([^)]*?\"([^\"]+)\"|\[([^\]]+)\]|\"([^\"]+)\")",
        re.IGNORECASE,
    )
    _RE_SAY_LABEL = re.compile(
        r"@\s*\d+\s*,\s*\d+\s+say\s+[\"[]([^\"\]]+)[\"\]]",
        re.IGNORECASE,
    )
    _RE_NUMROT = re.compile(
        r"numrot\s*=\s*(?:\[([^\]]+)\]|\"([^\"]+)\")",
        re.IGNORECASE,
    )
    _RE_KEY_LABEL = re.compile(
        r"ON\s+KEY\s+(?:LABEL\s+)?(\w+)\s+DO\s+(\w+)",
        re.IGNORECASE,
    )
    _RE_OPTION_NUM = re.compile(
        r"@\s+\d+\s*,\s*\d+\s+SAY\s+['\"]\s*([0-9A-Z]+)[\.\-\)]\s*([^'\"]+)['\"]",
        re.IGNORECASE,
    )
    _RE_PROMPT_NUM = re.compile(r"prompt\s+\"?\s*([0-9A-Z]+)[\.\-\)]", re.IGNORECASE)
    _RE_FTRADUZ_LABEL = re.compile(r'fTraduz\([^)]*?\"([^\"]+)\"', re.IGNORECASE)
    _RE_INLINE_LABEL = re.compile(r'["\[]([^"\]\']+)["\]]')
    _RE_DO_LINE = re.compile(r"^\s*do\s+['\"]?([\w/]+)['\"]?", re.IGNORECASE)
    _RE_GMENU = re.compile(r"\bgmenu\s*\(", re.IGNORECASE)
    _RE_MENU_TO = re.compile(r"\bmenu\s+to\b", re.IGNORECASE)
    _RESERVED_PROGRAMS = {
        "case", "close", "do", "else", "endcase", "enddo", "endif", "exit",
        "for", "if", "next", "return", "scan", "while",
    }

    def __init__(self, source_dir: str = ""):
        self.source_dir = Path(source_dir) if source_dir else None

    def analyze(self, source_dir: str) -> MenuTree:
        """Analisa diretorio e constroi arvore de menus."""
        base = Path(source_dir)
        tree = MenuTree()
        source_files = collect_preferred_source_files(base, {".prg", ".dbo"})

        menu_nodes: dict[str, MenuNode] = {}
        all_programs: set[str] = set()
        programs_in_menus: set[str] = set()

        for source_file in source_files:
            content = self._read_file(source_file)
            if not content:
                continue

            block = self._find_menu_block(source_file, content)
            if not block:
                continue

            node = self._menu_node_from_block(source_file, block)
            menu_nodes[str(source_file)] = node
            all_programs.update(block.programs)

            for prog_name in block.programs:
                programs_in_menus.add(prog_name)
                node.children.append(MenuNode(
                    node_id=prog_name,
                    label=prog_name,
                    node_type="program",
                    parent_id=node.node_id,
                    program_name=prog_name,
                    source_file=str(source_file),
                    module_key=node.module_key,
                ))

        self._link_hierarchy(menu_nodes)

        root_menus = [n for n in menu_nodes.values() if not n.parent_id]
        if len(root_menus) == 1:
            tree.root = root_menus[0]
        elif root_menus:
            self._disambiguate_root_labels(root_menus)
            tree.root = MenuNode(
                node_id="root",
                label="Sistema",
                node_type="menu",
                children=sorted(root_menus, key=lambda n: (n.module_key, n.route_code, n.label)),
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
        block = self._find_menu_block(file_path, content)
        if not block:
            return None
        return self._menu_node_from_block(file_path, block)

    def _menu_node_from_block(self, file_path: Path, block: MenuBlock) -> MenuNode:
        node_id = str(file_path)
        label = block.label or file_path.stem
        if (not block.route_code) and label.lower() == file_path.stem.lower():
            label = self._fallback_label_from_path(file_path)
        node = MenuNode(
            node_id=node_id,
            label=label,
            node_type="menu",
            source_file=node_id,
            source_line=block.source_line,
            route_code=block.route_code,
            module_key=block.module_key,
        )

        seen_options: set[str] = set()
        for num, opt_label in block.options:
            child_id = f"{node_id}#{num}"
            if child_id in seen_options:
                continue
            seen_options.add(child_id)
            node.children.append(MenuNode(
                node_id=child_id,
                label=f"{num}. {opt_label}",
                node_type="option",
                parent_id=node_id,
                key=num,
                source_file=node_id,
                module_key=node.module_key,
            ))

        return node

    def _find_menu_block(self, file_path: Path, content: str) -> Optional[MenuBlock]:
        lines = content.splitlines()
        driver_indexes = [
            idx for idx, line in enumerate(lines)
            if self._RE_GMENU.search(line) or self._RE_MENU_TO.search(line)
        ]
        if file_path.stem.lower().startswith("menu") and not driver_indexes:
            driver_indexes = [0]

        best: Optional[MenuBlock] = None
        for idx in driver_indexes:
            block = self._extract_block(lines, file_path, idx)
            if not block:
                continue
            score = (
                1 if block.route_code else 0,
                1 if block.label and block.label.lower() != file_path.stem.lower() else 0,
                len(block.options),
                len(block.programs),
            )
            best_score = (
                1 if best and best.route_code else 0,
                1 if best and best.label and best.label.lower() != file_path.stem.lower() else 0,
                len(best.options) if best else -1,
                len(best.programs) if best else -1,
            )
            if best is None or score > best_score:
                best = block
        return best

    def _extract_block(self, lines: list[str], file_path: Path, driver_idx: int) -> Optional[MenuBlock]:
        option_lines = lines[max(0, driver_idx - 20):driver_idx + 1]
        options: list[tuple[str, str]] = []
        seen_keys: set[str] = set()
        for line in option_lines:
            option = self._extract_option_from_line(line)
            if option and option[0] not in seen_keys:
                seen_keys.add(option[0])
                options.append(option)

        if not options:
            return None

        label = self._extract_nearest_label(lines, driver_idx) or file_path.stem
        route_code = self._extract_nearest_numrot(lines, driver_idx)
        module_key = self._infer_module_key(file_path)

        follow_lines = lines[driver_idx:min(len(lines), driver_idx + 80)]
        programs = sorted(self._extract_called_programs("\n".join(follow_lines)))
        if not programs:
            return None

        return MenuBlock(
            label=label,
            route_code=route_code,
            module_key=module_key,
            source_line=driver_idx + 1,
            options=options,
            programs=programs,
        )

    def _extract_nearest_label(self, lines: list[str], driver_idx: int) -> str:
        for idx in range(driver_idx, max(-1, driver_idx - 180), -1):
            line = lines[idx]
            title_match = self._RE_MENU_LABEL.search(line)
            if title_match:
                return title_match.group(1).strip()
            routine_match = self._RE_ROUTINE_LABEL.search(line)
            if routine_match:
                for group in routine_match.groups():
                    if group and group.strip():
                        return group.strip()
            say_match = self._RE_SAY_LABEL.search(line)
            if say_match:
                label = say_match.group(1).strip(" |")
                if label and label.upper() != "MENU PRINCIPAL":
                    return label
        return ""

    def _extract_nearest_numrot(self, lines: list[str], driver_idx: int) -> str:
        for idx in range(driver_idx, max(-1, driver_idx - 120), -1):
            match = self._RE_NUMROT.search(lines[idx])
            if not match:
                continue
            value = (match.group(1) or match.group(2) or "").strip()
            if value:
                return value
        return ""

    def _infer_module_key(self, file_path: Path) -> str:
        if self.source_dir:
            try:
                rel_parts = file_path.relative_to(self.source_dir).parts
                if rel_parts:
                    top = rel_parts[0].lower()
                    if re.fullmatch(r"[a-z]{2,4}", top):
                        return top[:3]
            except Exception:
                pass

        stem = file_path.stem.lower()
        match = re.match(r"([a-z]{3})", stem)
        if match:
            return match.group(1)

        parts = [p.lower() for p in file_path.parts]
        for part in reversed(parts):
            if re.fullmatch(r"[a-z]{3,4}", part):
                return part[:3]
        return stem[:3]

    def _fallback_label_from_path(self, file_path: Path) -> str:
        if self.source_dir:
            try:
                rel_parts = file_path.relative_to(self.source_dir).parts[:-1]
                if len(rel_parts) >= 2:
                    folder_label = self._prettify_name(rel_parts[-1])
                    stem_label = self._prettify_name(file_path.stem)
                    sibling_menus = list(file_path.parent.glob("*.prg"))
                    if len(sibling_menus) > 1 and stem_label.lower() not in folder_label.lower():
                        return f"{folder_label} - {stem_label}"
                    return folder_label
            except Exception:
                pass
        return self._prettify_name(file_path.stem)

    def _prettify_name(self, raw: str) -> str:
        text = raw.replace("_", " ").replace("-", " ").strip()
        words = [w for w in text.split() if w]
        if not words:
            return raw
        return " ".join(word.upper() if len(word) <= 3 else word.capitalize() for word in words)

    def _link_hierarchy(self, menu_nodes: dict[str, MenuNode]) -> None:
        paths = list(menu_nodes.items())
        for menu_path, node in paths:
            parent = self._find_parent(menu_path, node, menu_nodes)
            if not parent:
                continue
            node.parent_id = parent.node_id
            node.depth = parent.depth + 1
            if not any(child.node_id == node.node_id for child in parent.children):
                parent.children.append(node)

    def _find_parent(self, menu_path: str, node: MenuNode, menu_nodes: dict[str, MenuNode]) -> Optional[MenuNode]:
        explicit_parent = None
        best_explicit_depth = -1
        stem = Path(menu_path).stem.upper()

        for other_path, other_node in menu_nodes.items():
            if other_path == menu_path:
                continue
            content = self._read_file(Path(other_path))
            if not content:
                continue
            if stem in self._extract_called_programs(content):
                depth = self._route_depth(other_node.route_code)
                if depth > best_explicit_depth:
                    explicit_parent = other_node
                    best_explicit_depth = depth

        if explicit_parent:
            return explicit_parent

        candidates: list[tuple[int, int, MenuNode]] = []
        for other_path, other_node in menu_nodes.items():
            if other_path == menu_path or other_node.module_key != node.module_key:
                continue
            if not other_node.route_code:
                continue
            if node.route_code:
                if other_node.route_code == "0":
                    candidates.append((0, self._route_depth(other_node.route_code), other_node))
                elif self._is_route_parent(other_node.route_code, node.route_code):
                    candidates.append((1, self._route_depth(other_node.route_code), other_node))
            elif other_node.route_code == "0":
                candidates.append((0, self._route_depth(other_node.route_code), other_node))

        if not candidates:
            return None

        candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return candidates[0][2]

    def _disambiguate_root_labels(self, root_menus: list[MenuNode]) -> None:
        counts: dict[str, int] = {}
        for node in root_menus:
            counts[node.label] = counts.get(node.label, 0) + 1

        for node in root_menus:
            if counts.get(node.label, 0) <= 1:
                continue
            if node.module_key:
                node.label = f"{node.module_key.upper()} - {node.label}"

    def _is_route_parent(self, parent_code: str, child_code: str) -> bool:
        if parent_code == child_code or not parent_code or not child_code:
            return False
        parent_parts = self._split_route(parent_code)
        child_parts = self._split_route(child_code)
        if len(parent_parts) >= len(child_parts):
            return False
        return child_parts[:len(parent_parts)] == parent_parts

    def _split_route(self, route_code: str) -> list[str]:
        return [part for part in re.split(r"[.\-]", route_code.lower()) if part]

    def _route_depth(self, route_code: str) -> int:
        return len(self._split_route(route_code))

    def _extract_option_from_line(self, line: str) -> Optional[tuple[str, str]]:
        match = self._RE_OPTION_NUM.search(line)
        if match:
            return (match.group(1).strip(), match.group(2).strip())

        prompt_num = self._RE_PROMPT_NUM.search(line)
        if not prompt_num:
            return None

        num = prompt_num.group(1).strip()
        label = ""
        traduz_match = self._RE_FTRADUZ_LABEL.search(line)
        if traduz_match:
            label = traduz_match.group(1).strip()
        else:
            quoted = [m.group(1).strip() for m in self._RE_INLINE_LABEL.finditer(line)]
            if quoted:
                label = quoted[-1]
                if label.startswith(f"{num}."):
                    label = label[len(num) + 1:].strip()

        if not label or not re.search(r"[A-Za-z]", label):
            return None
        return (num, label)

    def _extract_called_programs(self, content: str) -> set[str]:
        programs: set[str] = set()
        for raw_line in content.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("*") or line.startswith("&&"):
                continue
            match = self._RE_DO_LINE.match(line)
            if match:
                prog_name = match.group(1).upper()
                if self._is_program_name(prog_name):
                    programs.add(prog_name)

        for key_match in self._RE_KEY_LABEL.finditer(content):
            prog_name = key_match.group(2).upper()
            if self._is_program_name(prog_name):
                programs.add(prog_name)

        return programs

    def _is_program_name(self, program_name: str) -> bool:
        low = program_name.lower()
        if low in self._RESERVED_PROGRAMS:
            return False
        if len(program_name) < 2:
            return False
        if not re.search(r"[a-z]", low):
            return False
        return True

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
            "source_line": node.source_line,
            "depth": node.depth,
            "route_code": node.route_code,
            "module_key": node.module_key,
            "children": [MenuAnalyzer.to_dict(c) for c in node.children],
        }

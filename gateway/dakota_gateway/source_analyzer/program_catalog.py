"""Catalogo de programas com metadados semanticos.

Faz parte da entrega P2-A — Synthetic Knowledge Base.
Indexa arquivos .prg, extrai modulo, entidades referenciadas,
telas associadas (via ScreenEntityLinker) e operacoes CRUD.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .entity_catalog import EntityDefinition
from .screen_entity_linker import ScreenEntityBinding
from .source_inventory import collect_preferred_source_files


@dataclass
class ProgramEntry:
    """Entrada no catalogo de programas."""
    name: str = ""                          # nome base (ex: cadcli)
    source_file: str = ""                   # caminho completo
    module: str = ""                        # modulo inferido (cad, est, fat, etc.)
    submodule: str = ""                     # submodulo (cliente, produto, etc.)
    entity_references: list[str] = field(default_factory=list)   # entidades referenciadas
    screen_bindings: list[ScreenEntityBinding] = field(default_factory=list)
    operations: list[str] = field(default_factory=list)          # create, read, update, delete
    menu_path: list[str] = field(default_factory=list)           # caminho no menu
    source_lines: tuple[int, int] = (0, 0)
    metadata: dict = field(default_factory=dict)


# ── Padroes para extracao de modulo ──

_RE_MODULE_FROM_PATH = re.compile(
    r"(?:^|[/\\])([a-z]{2,4})\d{2,4}[/\\]?|[\\/]([a-z]{2,4})[\\/].*\.prg$",
    re.IGNORECASE,
)
_RE_MODULE_FROM_NAME = re.compile(
    r"^([a-z]{2,4})(\d{2,4})", re.IGNORECASE,
)

# Operacoes detectaveis (mesmo padrao do screen_entity_linker)
_CREATE_PATTERNS = [
    r"\bAPPEND\s+BLANK\b", r"\bINSERT\s+INTO\b", r"\bCREATE\b",
]
_READ_PATTERNS = [
    r"\bSEEK\b", r"\bLOCATE\b", r"\bSELECT\b", r"\bSCATTER\b", r"\bFIND\b",
]
_UPDATE_PATTERNS = [
    r"\bREPLACE\b", r"\bUPDATE\b", r"\bGATHER\b",
]
_DELETE_PATTERNS = [
    r"\bDELETE\b", r"\bPACK\b", r"\bZAP\b", r"\bERASE\b",
]

# Mapeamento de prefixo → nome amigavel do modulo
_MODULE_NAMES: dict[str, str] = {
    "cad": "Cadastros",
    "con": "Consultas",
    "alt": "Alteracoes",
    "exc": "Exclusoes",
    "est": "Estoque",
    "fat": "Faturamento",
    "ped": "Pedidos",
    "com": "Compras",
    "fin": "Financeiro",
    "ctb": "Contabilidade",
    "rh": "Recursos Humanos",
    "fis": "Fiscal",
    "rel": "Relatorios",
    "uti": "Utilitarios",
    "mov": "Movimentacoes",
    "epp": "Estoque/Produtos",
    "ven": "Vendas",
    "nfe": "Nota Fiscal",
    "nf": "Nota Fiscal",
}


class ProgramCatalog:
    """Catalogo indexado de programas do sistema legado."""

    def __init__(self, source_dir: str = ""):
        self.source_dir = Path(source_dir) if source_dir else None
        self._entries: dict[str, ProgramEntry] = {}
        self._by_module: dict[str, list[ProgramEntry]] = {}
        self._by_entity: dict[str, list[ProgramEntry]] = {}

    # ── API publica ──

    def build(
        self,
        entities: list[EntityDefinition],
        bindings: list[ScreenEntityBinding],
    ) -> list[ProgramEntry]:
        """Constroi o catalogo a partir de entidades e bindings."""
        self._entries.clear()
        self._by_module.clear()
        self._by_entity.clear()

        # Agrupa bindings por source_file
        bindings_by_file: dict[str, list[ScreenEntityBinding]] = {}
        for b in bindings:
            if b.source_file:
                bindings_by_file.setdefault(b.source_file, []).append(b)

        # Cria entries a partir dos bindings
        for src_file, file_bindings in bindings_by_file.items():
            name = Path(src_file).stem
            module, submodule = self._infer_module(src_file, name)

            # Entidades referenciadas
            entity_refs: list[str] = []
            for b in file_bindings:
                if b.entity_name and b.entity_name not in entity_refs:
                    entity_refs.append(b.entity_name)

            # Operacoes (uniao de todas as operacoes dos bindings)
            operations: list[str] = []
            for b in file_bindings:
                if b.operation and b.operation not in operations:
                    operations.append(b.operation)

            entry = ProgramEntry(
                name=name,
                source_file=src_file,
                module=module,
                submodule=submodule,
                entity_references=entity_refs,
                screen_bindings=file_bindings,
                operations=operations,
                source_lines=file_bindings[0].source_lines if file_bindings else (0, 0),
            )
            self._entries[name.upper()] = entry

        # Indexa por modulo
        for entry in self._entries.values():
            self._by_module.setdefault(entry.module, []).append(entry)

        # Indexa por entidade
        for entry in self._entries.values():
            for entity_name in entry.entity_references:
                self._by_entity.setdefault(entity_name.upper(), []).append(entry)

        # Programas sem bindings (detectados via arquivos .prg)
        if self.source_dir:
            self._add_unbound_programs(entities, bindings_by_file)

        return list(self._entries.values())

    def get(self, program_name: str) -> Optional[ProgramEntry]:
        """Busca entrada pelo nome do programa."""
        return self._entries.get(program_name.upper())

    def by_module(self, module: str) -> list[ProgramEntry]:
        """Lista programas de um modulo."""
        return self._by_module.get(module.lower(), [])

    def by_entity(self, entity_name: str) -> list[ProgramEntry]:
        """Lista programas que referenciam uma entidade."""
        return self._by_entity.get(entity_name.upper(), [])

    def modules(self) -> dict[str, int]:
        """Retorna modulos e contagem de programas."""
        return {m: len(entries) for m, entries in self._by_module.items()}

    def to_report(self) -> dict:
        """Relatorio serializavel do catalogo."""
        return {
            "total_programs": len(self._entries),
            "total_modules": len(self._by_module),
            "modules": {
                m: {
                    "friendly_name": _MODULE_NAMES.get(m, m.upper()),
                    "program_count": len(entries),
                    "programs": [
                        {
                            "name": e.name,
                            "source_file": e.source_file,
                            "submodule": e.submodule,
                            "entity_references": e.entity_references,
                            "operations": e.operations,
                            "screen_count": len(e.screen_bindings),
                        }
                        for e in sorted(entries, key=lambda x: x.name)
                    ],
                }
                for m, entries in sorted(self._by_module.items())
            },
            "by_entity": {
                ent: [e.name for e in entries]
                for ent, entries in sorted(self._by_entity.items())
            },
        }

    # ── Internals ──

    @staticmethod
    def _infer_module(file_path: str, program_name: str) -> tuple[str, str]:
        """Infere modulo e submodulo a partir do caminho e nome."""
        module = ""
        submodule = ""

        # Tenta extrair do caminho (ex: /dakota/prg/cad/cad110.prg)
        path_match = _RE_MODULE_FROM_PATH.search(file_path)
        if path_match:
            module = (path_match.group(1) or path_match.group(2) or "").lower()

        # Tenta extrair do nome (ex: cad110 → cad)
        if not module:
            name_match = _RE_MODULE_FROM_NAME.match(program_name.lower())
            if name_match:
                module = name_match.group(1)

        # Submodulo: parte restante do nome apos o prefixo do modulo
        if module and program_name.lower().startswith(module):
            rest = program_name[len(module):].strip("0123456789 _-")
            if rest:
                submodule = rest

        return module, submodule

    def _add_unbound_programs(
        self,
        entities: list[EntityDefinition],
        bindings_by_file: dict[str, list[ScreenEntityBinding]],
    ) -> None:
        """Adiciona programas .prg que nao tem bindings mas referenciam entidades."""
        if not self.source_dir:
            return

        entity_names = {e.name.upper() for e in entities}

        for source_file in collect_preferred_source_files(self.source_dir, {".prg", ".dbo"}):
            src_str = str(source_file)
            if src_str in bindings_by_file:
                continue

            try:
                content = source_file.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue

            # Detecta entidades referenciadas
            refs: list[str] = []
            for ename in entity_names:
                if re.search(rf"\b{re.escape(ename)}\b", content, re.IGNORECASE):
                    refs.append(ename)

            if not refs:
                continue

            # Detecta operacoes
            operations: list[str] = []
            for pattern, op_name in [
                (_CREATE_PATTERNS, "create"),
                (_READ_PATTERNS, "read"),
                (_UPDATE_PATTERNS, "update"),
                (_DELETE_PATTERNS, "delete"),
            ]:
                for p in pattern:
                    if re.search(p, content, re.IGNORECASE):
                        if op_name not in operations:
                            operations.append(op_name)

            name = source_file.stem
            module, submodule = self._infer_module(src_str, name)

            entry = ProgramEntry(
                name=name,
                source_file=src_str,
                module=module,
                submodule=submodule,
                entity_references=refs,
                operations=operations,
            )
            self._entries[name.upper()] = entry
            self._by_module.setdefault(module, []).append(entry)
            for ref in refs:
                self._by_entity.setdefault(ref.upper(), []).append(entry)
